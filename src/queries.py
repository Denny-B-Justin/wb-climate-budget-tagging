"""
src/queries.py
--------------
All storage access for the Climate Budget Tagging (CBT) app.

The final tagged data lives in two *global* Unity Catalog Delta tables (one row per
budget line, with ``country_iso3`` and ``year`` columns):

    <catalog>.<schema>.cbt_global_rio_markers
    <catalog>.<schema>.cbt_global_eu_taxonomy

Versioning is done with a boolean ``validity`` column (soft delete - nothing is ever
physically deleted by the app):

    * exactly ONE ``batch_id`` may have ``validity = TRUE`` for a (country_iso3, year);
    * saving new numbers for a (country, year) that already has data first *stages* the new
      rows with ``validity = FALSE`` and then flips old -> FALSE / new -> TRUE in one atomic
      Delta ``UPDATE`` (a failed upload therefore never leaves a country-year without data);
    * a country/year that does not exist yet is simply appended;
    * if the numbers are byte-for-byte the same (content hash) nothing is written.

The data is stored in Unity Catalog only (databricks-sql-connector). Missing connection settings are a
:class:`ConfigError`; :func:`startup_check` logs them when the app starts. Users never see table names,
connection details or SQL errors: infrastructure failures surface as :class:`ServiceUnavailable`, whose
message is plain language, and the technical detail goes to the log.

The test-suite plugs a local SQLite stand-in in through :func:`use_backend`.

Python 3.9 compatible.
"""

from __future__ import annotations

import contextlib
import json
import logging
import math
import os
import re
import threading
import time
from collections import OrderedDict
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

from . import constants as C

log = logging.getLogger(__name__)

try:                                           # optional: .env for local development
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:                            # pragma: no cover
    pass


# ══════════════════════════════════════════════════════════════════════════
# Errors
# ══════════════════════════════════════════════════════════════════════════

class StoreError(RuntimeError):
    """Anything that went wrong while talking to the storage layer (message is user-presentable)."""


class ServiceUnavailable(StoreError):
    """The data service cannot be reached or refused the request. The message is for end users; details are logged."""

    USER_MESSAGE = ("The data service is not available right now. Please try again in a few minutes; "
                    "if the problem continues, contact the administrator of this tool.")

    def __init__(self, detail: str = ""):
        super().__init__(self.USER_MESSAGE)
        self.detail = detail


class ConfigError(ServiceUnavailable):
    """Missing or invalid configuration (environment variables). `detail` names what to fix."""


class NeedsConfirmation(StoreError):
    """The save would replace existing, different data and the caller did not confirm."""

    def __init__(self, plan: pd.DataFrame):
        super().__init__("Existing data would be replaced; confirmation required.")
        self.plan = plan


# ══════════════════════════════════════════════════════════════════════════
# Configuration (read lazily, so importing this module never needs credentials)
# ══════════════════════════════════════════════════════════════════════════

_IDENT = re.compile(C.IDENTIFIER_RE)


def _ident(value: str, what: str) -> str:
    if not value or not _IDENT.match(value):
        raise ConfigError("%s must contain only letters, digits and underscores (got %r)." % (what, value))
    return value


def _q(name: str) -> str:
    """Backtick-quote an already validated identifier."""
    return "`%s`" % name


class Config(object):
    """Resolved settings. Plain attributes; build with :func:`get_config`."""

    def __init__(self) -> None:
        env = os.environ.get
        self.server_hostname = (env(C.ENV_SERVER_HOSTNAME) or env(C.ENV_HOST) or "").strip()
        self.server_hostname = re.sub(r"^https?://", "", self.server_hostname).rstrip("/")
        self.http_path = (env(C.ENV_HTTP_PATH) or "").strip()
        self.client_id = (env(C.ENV_CLIENT_ID) or "").strip()
        self.client_secret = (env(C.ENV_CLIENT_SECRET) or "").strip()
        self.token = (env(C.ENV_TOKEN) or "").strip()
        self.catalog = (env(C.ENV_CATALOG) or "").strip()
        self.schema = (env(C.ENV_SCHEMA) or "").strip()

        self.insert_mode = (env(C.ENV_INSERT_MODE) or "json").strip().lower()
        self.insert_chunk_rows = _int_env(C.ENV_INSERT_CHUNK, C.DEFAULT_INSERT_CHUNK_ROWS, 1, 20000)
        self.cache_ttl = _int_env(C.ENV_CACHE_TTL, C.DEFAULT_CACHE_TTL, 0, 86400)
        self.cache_max = _int_env(C.ENV_CACHE_MAX, C.DEFAULT_CACHE_MAX, 1, 5000)
        self.auto_create = C.env_flag(C.ENV_AUTO_CREATE, True)

    def validate(self) -> None:
        """Raise ConfigError (detail = what to fix) unless every Unity Catalog setting is present and valid."""
        missing = []
        if not self.server_hostname:
            missing.append(C.ENV_SERVER_HOSTNAME)
        if not self.http_path:
            missing.append(C.ENV_HTTP_PATH)
        if not self.token and not (self.client_id and self.client_secret):
            missing.append("%s + %s (or %s)" % (C.ENV_CLIENT_ID, C.ENV_CLIENT_SECRET, C.ENV_TOKEN))
        if not self.catalog:
            missing.append(C.ENV_CATALOG)
        if not self.schema:
            missing.append(C.ENV_SCHEMA)
        if missing:
            raise ConfigError(
                "Unity Catalog credentials are missing: " + ", ".join(missing)
                + ". Set them in .env (local) or in the Posit Connect / Databricks Apps environment variables.")
        _ident(self.catalog, C.ENV_CATALOG)
        _ident(self.schema, C.ENV_SCHEMA)
        if self.insert_mode not in ("json", "values"):
            raise ConfigError("%s must be 'json' or 'values'." % C.ENV_INSERT_MODE)

    @property
    def label(self) -> str:
        return "Unity Catalog: %s.%s" % (self.catalog, self.schema)


def _int_env(name: str, default: int, lo: int, hi: int) -> int:
    raw = os.environ.get(name)
    if raw is None or not str(raw).strip():
        return default
    try:
        return max(lo, min(hi, int(str(raw).strip())))
    except ValueError:
        log.warning("%s must be an integer (got %r); using %d.", name, raw, default)
        return default


_state_lock = threading.RLock()
_config: Optional[Config] = None
_backend: Optional["_Backend"] = None
_ready = False


def get_config() -> Config:
    global _config
    with _state_lock:
        if _config is None:
            _config = Config()
        return _config


def reset() -> None:
    """Forget the cached config / back-end / cache (used by tests and by a manual "reconnect")."""
    global _config, _backend, _ready
    with _state_lock:
        _config = None
        _backend = None
        _ready = False
    invalidate_cache()


def table_name(kind: str) -> str:
    """Bare table name, e.g. cbt_global_rio_markers."""
    return C.TABLE_NAMES[kind]


def table_fqn(kind: str) -> str:
    """Fully qualified, quoted table name used in SQL."""
    return _get_backend().table_ref(_q(_ident(table_name(kind), "table name")))


def table_display_name(kind: str) -> str:
    """catalog.schema.table - for log messages only, never shown to users."""
    cfg = get_config()
    return "%s.%s.%s" % (cfg.catalog, cfg.schema, table_name(kind))


# ══════════════════════════════════════════════════════════════════════════
# Small TTL cache for read queries (cleared on every write in this process)
# ══════════════════════════════════════════════════════════════════════════

_cache: "OrderedDict[Any, Tuple[float, pd.DataFrame]]" = OrderedDict()
_cache_lock = threading.Lock()


def invalidate_cache() -> None:
    with _cache_lock:
        _cache.clear()


def _cache_get(key) -> Optional[pd.DataFrame]:
    ttl = get_config().cache_ttl
    if ttl <= 0:
        return None
    with _cache_lock:
        hit = _cache.get(key)
        if hit and time.time() - hit[0] < ttl:
            _cache.move_to_end(key)
            return hit[1].copy()
        if hit:
            _cache.pop(key, None)
    return None


def _cache_put(key, df: pd.DataFrame) -> None:
    cfg = get_config()
    if cfg.cache_ttl <= 0:
        return
    with _cache_lock:
        _cache[key] = (time.time(), df.copy())
        _cache.move_to_end(key)
        while len(_cache) > cfg.cache_max:
            _cache.popitem(last=False)


# ══════════════════════════════════════════════════════════════════════════
# Value conversion shared by both back-ends
# ══════════════════════════════════════════════════════════════════════════

def utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _column_values(series: pd.Series, sql_type: str) -> List[Any]:
    """One DataFrame column -> list of plain Python values (None for missing / non-finite)."""
    if sql_type == "STRING":
        mask = series.isna().to_numpy()
        vals = series.astype(object).tolist()
        return [None if m else str(v) for v, m in zip(vals, mask)]
    if sql_type == "INT":
        num = pd.to_numeric(series, errors="coerce")
        mask = num.isna().to_numpy()
        vals = num.fillna(0).astype("int64").tolist()
        return [None if m else v for v, m in zip(vals, mask)]
    if sql_type == "DOUBLE":
        arr = pd.to_numeric(series, errors="coerce").to_numpy(dtype="float64")
        mask = ~np.isfinite(arr)
        return [None if m else v for v, m in zip(arr.tolist(), mask)]
    if sql_type == "BOOLEAN":
        mask = series.isna().to_numpy()
        vals = series.astype(object).tolist()
        return [None if m else bool(v) for v, m in zip(vals, mask)]
    if sql_type == "TIMESTAMP":
        ts = pd.to_datetime(series, utc=True, errors="coerce")
        mask = ts.isna().to_numpy()
        txt = ts.dt.strftime("%Y-%m-%dT%H:%M:%S.%fZ").tolist()
        return [None if m else v for v, m in zip(txt, mask)]
    raise StoreError("Unsupported column type %s" % sql_type)


def to_records(kind: str, df: pd.DataFrame) -> List[Dict[str, Any]]:
    """DataFrame -> list of dicts holding only schema columns, with plain Python values."""
    cols = C.TABLE_SCHEMAS[kind]
    missing = [c.name for c in cols if c.name not in df.columns]
    if missing:
        raise StoreError("Cannot save: the data frame lacks column(s) %s." % ", ".join(missing))
    names = [c.name for c in cols]
    series = {c.name: _column_values(df[c.name], c.sql) for c in cols}
    return [dict(zip(names, row)) for row in zip(*(series[n] for n in names))]


def _check_frame(kind: str, df: pd.DataFrame) -> None:
    if df is None or len(df) == 0:
        raise StoreError("Nothing to save: the result table is empty.")
    for col in ("batch_id", "country_iso3", "year", "content_hash"):
        if col not in df.columns:
            raise StoreError("Cannot save: column %r is missing." % col)
        if df[col].isna().any():
            raise StoreError("Cannot save: column %r has empty values." % col)
    if df["batch_id"].nunique() != 1:
        raise StoreError("Cannot save: one save operation must carry exactly one batch_id.")
    if df["country_iso3"].nunique() != 1:
        raise StoreError("Cannot save: one save operation must cover exactly one country.")
    if df["record_id"].duplicated().any():
        raise StoreError("Cannot save: record_id values must be unique.")
    if not df["validity"].astype(bool).eq(False).all():
        raise StoreError("Cannot save: rows must be staged with validity=FALSE (activation flips them).")


# ══════════════════════════════════════════════════════════════════════════
# Back-ends
# ══════════════════════════════════════════════════════════════════════════

_SPARK_JSON_TYPE = {"STRING": "string", "INT": "int", "DOUBLE": "double", "BOOLEAN": "boolean", "TIMESTAMP": "string"}


def _spark_str(text: str) -> str:
    """Escape a Python string for use inside a single-quoted Spark SQL literal (DDL comments only)."""
    return text.replace("\\", "\\\\").replace("'", "\\'")


class _Session(object):
    """One connection. `ts(name)` renders a timestamp parameter in the dialect of the back-end."""

    def __init__(self, backend: "_Backend", conn: Any) -> None:
        self.backend = backend
        self.conn = conn

    # -- to be implemented -------------------------------------------------------------------
    def execute(self, sql: str, params: Optional[Dict[str, Any]] = None) -> int:
        raise NotImplementedError

    def query(self, sql: str, params: Optional[Dict[str, Any]] = None) -> pd.DataFrame:
        raise NotImplementedError

    def insert(self, kind: str, records: List[Dict[str, Any]]) -> None:
        raise NotImplementedError

    def ts(self, name: str) -> str:
        raise NotImplementedError


class _Backend(object):
    name = "base"

    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg

    def table_ref(self, quoted_table: str) -> str:
        return quoted_table

    @contextlib.contextmanager
    def session(self, write: bool = False):
        raise NotImplementedError
        yield  # pragma: no cover

    def create_table_sql(self, kind: str) -> str:
        raise NotImplementedError

    def existing_columns(self, s: _Session, kind: str) -> List[str]:
        raise NotImplementedError

    def add_column_sql(self, kind: str, col: C.Col) -> str:
        raise NotImplementedError


# ------------------------------------------------------------- Databricks -------------------------

def _cursor_execute(cur: Any, sql: str, params: Optional[Dict[str, Any]]) -> None:
    if params:
        cur.execute(sql, parameters=params)
    else:
        cur.execute(sql)


class _DatabricksSession(_Session):
    def _cursor(self):
        return self.conn.cursor()

    def execute(self, sql, params=None):
        with self._cursor() as cur:
            _cursor_execute(cur, sql, params)
            affected = 0
            try:                                            # Delta DML returns a one-row summary
                if cur.description:
                    names = [d[0] for d in cur.description]
                    rows = cur.fetchall()
                    if rows and "num_affected_rows" in names:
                        affected = int(rows[0][names.index("num_affected_rows")] or 0)
            except Exception:                               # pragma: no cover - summary is optional
                affected = 0
            return affected

    def query(self, sql, params=None):
        with self._cursor() as cur:
            _cursor_execute(cur, sql, params)
            rows = cur.fetchall()
            cols = [d[0] for d in cur.description] if cur.description else []
            return pd.DataFrame([tuple(r) for r in rows], columns=cols)

    def insert(self, kind, records):
        if not records:
            return
        cols = C.TABLE_SCHEMAS[kind]
        if self.backend.cfg.insert_mode == "values":
            self._insert_values(kind, records, cols)
        else:
            self._insert_json(kind, records, cols)

    # json mode: ONE parameter per statement, thousands of rows per statement ----------------
    def _insert_json(self, kind, records, cols):
        names = [c.name for c in cols]
        struct = ",".join("%s:%s" % (c.name, _SPARK_JSON_TYPE[c.sql]) for c in cols)
        select = ", ".join(
            ("CAST(%s AS TIMESTAMP) AS %s" % (_q(c.name), _q(c.name))) if c.sql == "TIMESTAMP" else _q(c.name)
            for c in cols)
        stmt = (
            "INSERT INTO %s (%s) SELECT %s FROM "
            "(SELECT inline(from_json(:payload, 'array<struct<%s>>')))"
            % (table_fqn(kind), ", ".join(_q(n) for n in names), select, struct)
        )
        for chunk in _json_chunks(records, self.backend.cfg.insert_chunk_rows):
            self.execute(stmt, {"payload": chunk})

    # values mode: classic multi-row VALUES, limited by the 255-parameter cap -------------------
    def _insert_values(self, kind, records, cols):
        names = [c.name for c in cols]
        per_stmt = max(1, 250 // len(names))
        head = "INSERT INTO %s (%s) VALUES " % (table_fqn(kind), ", ".join(_q(n) for n in names))
        for start in range(0, len(records), per_stmt):
            part = records[start:start + per_stmt]
            params: Dict[str, Any] = {}
            groups = []
            for i, rec in enumerate(part):
                marks = []
                for c in cols:
                    key = "p%d_%s" % (i, c.name)
                    marks.append("CAST(:%s AS TIMESTAMP)" % key if c.sql == "TIMESTAMP" else ":" + key)
                    params[key] = rec[c.name]
                groups.append("(" + ", ".join(marks) + ")")
            self.execute(head + ", ".join(groups), params)

    def ts(self, name):
        return "CAST(:%s AS TIMESTAMP)" % name


def _json_chunks(records: List[Dict[str, Any]], max_rows: int, max_bytes: int = 600_000):
    """Yield JSON arrays (as strings) holding at most `max_rows` rows and about `max_bytes` bytes."""
    buf: List[str] = []
    size = 0
    for rec in records:
        piece = json.dumps(rec, ensure_ascii=True, separators=(",", ":"), allow_nan=False)
        if buf and (len(buf) >= max_rows or size + len(piece) > max_bytes):
            yield "[" + ",".join(buf) + "]"
            buf, size = [], 0
        buf.append(piece)
        size += len(piece) + 1
    if buf:
        yield "[" + ",".join(buf) + "]"


class _DatabricksBackend(_Backend):
    name = "databricks"

    def table_ref(self, quoted_table):
        return "%s.%s.%s" % (_q(self.cfg.catalog), _q(self.cfg.schema), quoted_table)

    def _connect(self):
        try:
            from databricks import sql as dbsql
        except ImportError as exc:                           # pragma: no cover
            raise ConfigError("databricks-sql-connector is not installed (pip install databricks-sql-connector).") from exc
        cfg = self.cfg
        kwargs: Dict[str, Any] = dict(server_hostname=cfg.server_hostname, http_path=cfg.http_path)
        if cfg.client_id and cfg.client_secret:
            try:
                from databricks.sdk.core import Config as SdkConfig, oauth_service_principal
            except ImportError as exc:                       # pragma: no cover
                raise ConfigError("databricks-sdk is not installed (pip install databricks-sdk).") from exc

            def credentials_provider():
                sdk_cfg = SdkConfig(host="https://%s" % cfg.server_hostname,
                                    client_id=cfg.client_id, client_secret=cfg.client_secret)
                return oauth_service_principal(sdk_cfg)

            kwargs["credentials_provider"] = credentials_provider
        else:
            kwargs["access_token"] = cfg.token
        return dbsql.connect(**kwargs)

    @contextlib.contextmanager
    def session(self, write: bool = False):
        conn = self._connect()
        try:
            yield _DatabricksSession(self, conn)
        finally:
            try:
                conn.close()
            except Exception:                                # pragma: no cover
                pass

    def create_table_sql(self, kind):
        cols = ",\n  ".join("%s %s COMMENT '%s'" % (_q(c.name), c.sql, _spark_str(c.doc)) for c in C.TABLE_SCHEMAS[kind])
        return (
            "CREATE TABLE IF NOT EXISTS %s (\n  %s\n) USING DELTA COMMENT '%s' "
            "TBLPROPERTIES ('delta.autoOptimize.optimizeWrite' = 'true', 'delta.autoOptimize.autoCompact' = 'true')"
            % (table_fqn(kind), cols, _spark_str(C.TABLE_COMMENTS[kind]))
        )

    def existing_columns(self, s, kind):
        df = s.query("DESCRIBE TABLE %s" % table_fqn(kind))
        out = []
        for name in df["col_name"].tolist() if len(df) else []:
            if not name or str(name).startswith("#"):
                break
            out.append(str(name))
        return out

    def add_column_sql(self, kind, col):
        return "ALTER TABLE %s ADD COLUMNS (%s %s COMMENT '%s')" % (
            table_fqn(kind), _q(col.name), col.sql, _spark_str(col.doc))


def _get_backend() -> _Backend:
    global _backend
    with _state_lock:
        if _backend is None:
            cfg = get_config()
            cfg.validate()
            _backend = _DatabricksBackend(cfg)
        return _backend


def use_backend(backend: _Backend) -> None:
    """Replace the Unity Catalog back-end (the tests use a local SQLite stand-in)."""
    global _backend, _ready
    with _state_lock:
        _backend = backend
        _ready = False
    invalidate_cache()


_CONFLICT_MARKERS = ("concurrentappend", "concurrentdelete", "concurrentmodification", "concurrenttransaction",
                     "delta_concurrent", "conflicting commit", "protocolchanged", "metadatachanged",
                     "database is locked", "write conflict")


def _is_conflict(exc: BaseException) -> bool:
    text = ("%s %s" % (type(exc).__name__, exc)).lower()
    return any(m in text for m in _CONFLICT_MARKERS)


def _with_retry(fn: Callable[[], Any], attempts: int = 4, what: str = "operation") -> Any:
    """Run `fn`, retrying a few times when Delta reports a concurrent-write conflict."""
    delay = 0.6
    for attempt in range(1, attempts + 1):
        try:
            return fn()
        except StoreError:
            raise
        except Exception as exc:
            if _is_conflict(exc) and attempt < attempts:
                log.warning("%s hit a concurrent write (attempt %d/%d): %s", what, attempt, attempts, exc)
                time.sleep(delay)
                delay *= 2
                continue
            log.error("%s failed: %s", what, _short(exc, 2000))
            raise ServiceUnavailable("%s failed: %s" % (what, _short(exc))) from exc
    raise ServiceUnavailable("%s failed." % what)            # pragma: no cover


def _short(exc: BaseException, n: int = 400) -> str:
    text = " ".join(str(exc).split())
    return text if len(text) <= n else text[:n] + "..."


# ══════════════════════════════════════════════════════════════════════════
# Set-up
# ══════════════════════════════════════════════════════════════════════════

def ensure_tables(force: bool = False) -> None:
    """Create the two tables when missing and add columns that a newer app version introduced."""
    global _ready
    with _state_lock:
        if _ready and not force:
            return
    cfg = get_config()
    be = _get_backend()
    if not cfg.auto_create:
        _ready = True
        return
    try:
        with be.session(write=True) as s:
            for kind in C.KINDS:
                s.execute(be.create_table_sql(kind))
                have = set(be.existing_columns(s, kind))
                for col in C.TABLE_SCHEMAS[kind]:
                    if have and col.name not in have:
                        log.info("Adding column %s to %s", col.name, table_name(kind))
                        s.execute(be.add_column_sql(kind, col))
    except StoreError:
        raise
    except Exception as exc:
        detail = ("Could not create or check the tables %s. The service principal needs USE CATALOG, USE SCHEMA, "
                  "CREATE TABLE, SELECT and MODIFY on %s.%s. (%s)"
                  % (", ".join(table_name(k) for k in C.KINDS), cfg.catalog, cfg.schema, _short(exc, 2000)))
        log.error(detail)
        raise ServiceUnavailable(detail) from exc
    with _state_lock:
        _ready = True


def health() -> bool:
    """True when the tables can be reached. Never raises; the reason for a failure is logged."""
    try:
        ensure_tables()
        return True
    except ServiceUnavailable as exc:
        log.error("Unity Catalog is not available: %s", exc.detail or exc)
        return False
    except Exception:                                        # pragma: no cover
        log.exception("Unity Catalog health check failed")
        return False


def startup_check() -> None:
    """Log at start-up whether Unity Catalog is configured and reachable. Never raises."""
    cfg = get_config()
    try:
        cfg.validate()
    except ConfigError as exc:
        log.error("STORAGE NOT CONFIGURED - %s Saving, the dashboard and downloads will not work "
                  "until this is fixed.", exc.detail)
        return
    if health():
        log.info("Connected to %s (tables %s)", cfg.label, ", ".join(table_name(k) for k in C.KINDS))


# ══════════════════════════════════════════════════════════════════════════
# Reading
# ══════════════════════════════════════════════════════════════════════════

def _in_clause(prefix: str, values: Sequence[Any], params: Dict[str, Any]) -> str:
    names = []
    for i, v in enumerate(values):
        key = "%s%d" % (prefix, i)
        params[key] = v
        names.append(":" + key)
    return ", ".join(names) if names else "NULL"


def _run_query(sql: str, params: Optional[Dict[str, Any]] = None, cache: bool = True) -> pd.DataFrame:
    ensure_tables()
    key = (sql, tuple(sorted((params or {}).items())))
    if cache:
        hit = _cache_get(key)
        if hit is not None:
            return hit
    be = _get_backend()

    def go():
        with be.session() as s:
            return s.query(sql, params)

    df = _with_retry(go, attempts=2, what="Reading from storage")
    if cache:
        _cache_put(key, df)
    return df


def _frame_types(df: pd.DataFrame, kind: Optional[str] = None) -> pd.DataFrame:
    """Normalise dtypes that differ between back-ends (booleans, timestamps)."""
    if kind:
        for c in C.BOOL_COLUMNS[kind]:
            if c in df.columns:
                df[c] = df[c].map(lambda v: None if v is None or (isinstance(v, float) and math.isnan(v)) else bool(v))
    if "validity" in df.columns:
        df["validity"] = df["validity"].map(lambda v: bool(v) if v is not None and not pd.isna(v) else False)
    for c in C.TS_COLUMNS:
        if c in df.columns:
            df[c] = pd.to_datetime(df[c], utc=True, errors="coerce")
    return df


STATUS_ACTIVE, STATUS_STAGED = "active", "staged"


def list_versions(kind: Optional[str] = None, iso3: Optional[str] = None) -> pd.DataFrame:
    """One row per (kind, country, year, batch): what is stored, by whom, and whether it is the current version."""
    kinds = [kind] if kind else list(C.KINDS)
    frames = []
    for k in kinds:
        params: Dict[str, Any] = {}
        where = ""
        if iso3:
            where = "WHERE country_iso3 = :iso"
            params["iso"] = iso3
        sql = (
            "SELECT country, country_iso3, year, batch_id, COUNT(*) AS n_lines, "
            "MAX(CASE WHEN validity THEN 1 ELSE 0 END) AS is_valid, "
            "MIN(uploaded_at) AS uploaded_at, MAX(source_file) AS source_file, "
            "MAX(content_hash) AS content_hash, MAX(deactivated_at) AS deactivated_at, "
            "MAX(deactivation_reason) AS deactivation_reason, "
            "MAX(currency) AS currency, MAX(data_source) AS data_source, "
            "MAX(methodology_version) AS methodology_version "
            "FROM %s %s GROUP BY country, country_iso3, year, batch_id" % (table_fqn(k), where)
        )
        df = _run_query(sql, params)
        if len(df):
            df.insert(0, "kind", k)
            frames.append(df)
    if not frames:
        return pd.DataFrame(columns=[
            "kind", "country", "country_iso3", "year", "batch_id", "n_lines", "is_valid", "uploaded_at",
            "source_file", "content_hash", "deactivated_at",
            "deactivation_reason", "currency", "data_source", "methodology_version", "status"])
    out = pd.concat(frames, ignore_index=True)
    out = _frame_types(out)
    out["year"] = out["year"].astype(int)
    out["n_lines"] = out["n_lines"].astype(int)
    out["is_valid"] = out["is_valid"].astype(int).astype(bool)

    def status(r):
        if r["is_valid"]:
            return STATUS_ACTIVE
        reason = r.get("deactivation_reason")
        if reason is None or (isinstance(reason, float) and math.isnan(reason)) or not str(reason):
            return STATUS_STAGED
        return str(reason)

    out["status"] = out.apply(status, axis=1)
    return out.sort_values(["country", "year", "kind", "uploaded_at"], ascending=[True, False, True, False]).reset_index(drop=True)


def active_scopes(kind: str, iso3: str, years: Iterable[int]) -> pd.DataFrame:
    """Currently valid batches for the given country and years (uncached - used to decide what a save does)."""
    years = sorted({int(y) for y in years})
    params: Dict[str, Any] = {"iso": iso3}
    sql = (
        "SELECT year, batch_id, COUNT(*) AS n_lines, MAX(content_hash) AS content_hash, "
        "MIN(uploaded_at) AS uploaded_at, MAX(source_file) AS source_file "
        "FROM %s WHERE validity = TRUE AND country_iso3 = :iso AND year IN (%s) GROUP BY year, batch_id"
        % (table_fqn(kind), _in_clause("y", years, params))
    )
    df = _run_query(sql, params, cache=False)
    if len(df):
        df["year"] = df["year"].astype(int)
        df["n_lines"] = df["n_lines"].astype(int)
        df = _frame_types(df)
    return df


def aggregates(kind: str, valid_only: bool = True, iso3: Optional[str] = None,
               years: Optional[Iterable[int]] = None, batch_id: Optional[str] = None) -> pd.DataFrame:
    """Amount- and count-based totals per (country, year) computed inside the warehouse."""
    params: Dict[str, Any] = {}
    where = []
    if valid_only:
        where.append("validity = TRUE")
    if iso3:
        where.append("country_iso3 = :iso")
        params["iso"] = iso3
    if years is not None:
        ys = sorted({int(y) for y in years})
        where.append("year IN (%s)" % _in_clause("y", ys, params))
    if batch_id:
        where.append("batch_id = :bid")
        params["bid"] = batch_id
    amt = "COALESCE(amount, 0)"
    if kind == C.KIND_RIO:
        measures = (
            "SUM(%(a)s * %(w)s) AS weighted_amount, "
            "SUM(CASE WHEN rio_marker = 2 THEN %(a)s ELSE 0 END) AS principal_amount, "
            "SUM(CASE WHEN rio_marker = 1 THEN %(a)s ELSE 0 END) AS significant_amount, "
            "SUM(CASE WHEN rio_marker = 2 THEN 1 ELSE 0 END) AS principal_lines, "
            "SUM(CASE WHEN rio_marker = 1 THEN 1 ELSE 0 END) AS significant_lines, "
            "SUM(CASE WHEN rio_mitigation > 0 THEN %(a)s ELSE 0 END) AS mitigation_amount, "
            "SUM(CASE WHEN rio_adaptation > 0 THEN %(a)s ELSE 0 END) AS adaptation_amount"
            % {"a": amt, "w": C.RIO_SQL_WEIGHT}
        )
    else:
        flags = ", ".join(
            "SUM(CASE WHEN %s THEN %s ELSE 0 END) AS %s_amount" % (C.EU_FLAG_COLUMNS[k], amt, k) for k in C.EU_OBJECTIVES)
        measures = (
            "SUM(CASE WHEN climate_tag = 'GREEN' THEN %(a)s ELSE 0 END) AS green_amount, "
            "SUM(CASE WHEN climate_tag = 'BROWN' THEN %(a)s ELSE 0 END) AS brown_amount, "
            "SUM(CASE WHEN climate_tag = 'GREEN' THEN 1 ELSE 0 END) AS green_lines, "
            "SUM(CASE WHEN climate_tag = 'BROWN' THEN 1 ELSE 0 END) AS brown_lines, %(f)s"
            % {"a": amt, "f": flags}
        )
    group = "country, country_iso3, year" + (", batch_id" if (not valid_only or batch_id) else "")
    sel = group
    sql = (
        "SELECT %s, COUNT(*) AS n_lines, SUM(CASE WHEN amount IS NULL THEN 0 ELSE 1 END) AS n_with_amount, "
        "SUM(%s) AS total_amount, MAX(currency) AS currency, %s FROM %s %s GROUP BY %s"
        % (sel, amt, measures, table_fqn(kind), ("WHERE " + " AND ".join(where)) if where else "", group)
    )
    df = _run_query(sql, params)
    for c in df.columns:
        if c not in ("country", "country_iso3", "batch_id", "currency"):
            df[c] = pd.to_numeric(df[c], errors="coerce")
    if len(df):
        df["year"] = df["year"].astype(int)
    return df.sort_values(["country", "year"]).reset_index(drop=True)


def fetch_lines(kind: str, iso3: str, year: Optional[int] = None, batch_id: Optional[str] = None,
                valid_only: Optional[bool] = True, limit: int = 5000) -> pd.DataFrame:
    """Stored budget lines (newest batch first). `valid_only=None` returns every version."""
    params: Dict[str, Any] = {"iso": iso3}
    where = ["country_iso3 = :iso"]
    if year is not None:
        where.append("year = :yr")
        params["yr"] = int(year)
    if batch_id:
        where.append("batch_id = :bid")
        params["bid"] = batch_id
    if valid_only is True:
        where.append("validity = TRUE")
    elif valid_only is False:
        where.append("validity = FALSE")
    limit = max(1, min(int(limit), 1_000_000))
    sql = "SELECT * FROM %s WHERE %s ORDER BY year, batch_id, source_row LIMIT %d" % (
        table_fqn(kind), " AND ".join(where), limit)
    df = _run_query(sql, params)
    return _frame_types(df, kind)


def fetch_current(kind: str, iso3s: Optional[Sequence[str]] = None, years: Optional[Iterable[int]] = None,
                  limit: int = 2_000_000) -> pd.DataFrame:
    """The current (valid) lines of several countries / years - what the dashboard shows. Empty filters mean all."""
    params: Dict[str, Any] = {}
    where = ["validity = TRUE"]
    if iso3s:
        where.append("country_iso3 IN (%s)" % _in_clause("c", list(iso3s), params))
    if years:
        where.append("year IN (%s)" % _in_clause("y", sorted({int(y) for y in years}), params))
    limit = max(1, min(int(limit), 5_000_000))
    sql = "SELECT * FROM %s WHERE %s ORDER BY country, year, source_row LIMIT %d" % (
        table_fqn(kind), " AND ".join(where), limit)
    return _frame_types(_run_query(sql, params, cache=False), kind)


def integrity_issues() -> pd.DataFrame:
    """(kind, country_iso3, year) combinations that have more than one valid batch (should be empty)."""
    frames = []
    for k in C.KINDS:
        sql = ("SELECT country_iso3, year, COUNT(DISTINCT batch_id) AS valid_batches FROM %s "
               "WHERE validity = TRUE GROUP BY country_iso3, year HAVING COUNT(DISTINCT batch_id) > 1" % table_fqn(k))
        df = _run_query(sql, cache=False)
        if len(df):
            df.insert(0, "kind", k)
            frames.append(df)
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=["kind", "country_iso3", "year", "valid_batches"])


# ══════════════════════════════════════════════════════════════════════════
# Writing: plan -> stage -> atomic flip
# ══════════════════════════════════════════════════════════════════════════

ACTION_NEW, ACTION_SAME, ACTION_CHANGED = "new", "identical", "changed"


def plan_save(kind: str, df: pd.DataFrame) -> pd.DataFrame:
    """
    What would saving `df` do for each (country, year)?

        new        no valid data yet -> rows are appended
        identical  a valid batch with the same content hash exists -> nothing to write
        changed    valid data exists but the values differ -> needs the user's OK to soft-delete the old rows
    """
    _check_frame(kind, df)
    iso3 = str(df["country_iso3"].iloc[0])
    new = (df.groupby("year")
             .agg(new_lines=("record_id", "size"), new_hash=("content_hash", "first"))
             .reset_index())
    new["year"] = new["year"].astype(int)
    old = active_scopes(kind, iso3, new["year"].tolist())
    rows = []
    for _, r in new.iterrows():
        y = int(r["year"])
        olds = old[old["year"] == y] if len(old) else old
        entry = {"country_iso3": iso3, "year": y, "new_lines": int(r["new_lines"]), "new_hash": r["new_hash"],
                 "old_batch_id": None, "old_lines": 0, "old_uploaded_at": None,
                 "old_source_file": None, "old_batches": 0}
        if len(olds) == 0:
            entry["action"] = ACTION_NEW
        else:
            first = olds.iloc[0]
            entry.update(old_batch_id=first["batch_id"], old_lines=int(olds["n_lines"].sum()),
                         old_uploaded_at=first["uploaded_at"],
                         old_source_file=first["source_file"], old_batches=int(len(olds)))
            same = len(olds) == 1 and str(first["content_hash"]) == str(r["new_hash"])
            entry["action"] = ACTION_SAME if same else ACTION_CHANGED
        rows.append(entry)
    return pd.DataFrame(rows).sort_values("year").reset_index(drop=True)


def _flip(kind: str, iso3: str, years: Sequence[int], batch_id: Optional[str], reason_for_others: str) -> int:
    """
    One Delta UPDATE: `batch_id` rows -> validity TRUE, every other currently valid row of the same
    (country, years) -> FALSE. With batch_id=None it only switches the valid rows off.
    """
    be = _get_backend()
    years = sorted({int(y) for y in years})

    def go():
        with be.session(write=True) as s:
            params: Dict[str, Any] = {"iso": iso3, "now": utc_now_iso(), "reason": reason_for_others}
            ylist = _in_clause("y", years, params)
            if batch_id:
                params["bid"] = batch_id
                sql = (
                    "UPDATE %(t)s SET "
                    "validity = CASE WHEN batch_id = :bid THEN TRUE ELSE FALSE END, "
                    "deactivated_at = CASE WHEN batch_id = :bid THEN NULL ELSE %(now)s END, "
                                        "deactivation_reason = CASE WHEN batch_id = :bid THEN NULL ELSE :reason END, "
                    "replaced_by = CASE WHEN batch_id = :bid THEN NULL ELSE :bid END "
                    "WHERE (batch_id = :bid AND year IN (%(y)s)) OR (validity = TRUE AND country_iso3 = :iso AND year IN (%(y)s))"
                    % {"t": table_fqn(kind), "now": s.ts("now"), "y": ylist}
                )
            else:
                sql = (
                    "UPDATE %(t)s SET validity = FALSE, deactivated_at = %(now)s, "
                    "deactivation_reason = :reason "
                    "WHERE validity = TRUE AND country_iso3 = :iso AND year IN (%(y)s)"
                    % {"t": table_fqn(kind), "now": s.ts("now"), "y": ylist}
                )
            return s.execute(sql, params)

    n = _with_retry(go, what="Switching validity")
    invalidate_cache()
    return n


def save(kind: str, df: pd.DataFrame, replace: bool = False, skip_identical: bool = True,
         progress: Optional[Callable[[int, int], None]] = None) -> Dict[str, Any]:
    """
    Persist a tagged batch.

    Raises :class:`NeedsConfirmation` when valid data with different values exists and `replace` is False.
    Otherwise stages the rows (validity FALSE), verifies the row count, then activates the batch in one UPDATE.
    """
    ensure_tables()
    plan = plan_save(kind, df)
    changed = plan[plan["action"] == ACTION_CHANGED]
    if len(changed) and not replace:
        raise NeedsConfirmation(plan)
    keep_actions = [ACTION_NEW, ACTION_CHANGED] + ([] if skip_identical else [ACTION_SAME])
    todo = plan[plan["action"].isin(keep_actions)]
    iso3 = str(df["country_iso3"].iloc[0])
    batch_id = str(df["batch_id"].iloc[0])
    report: Dict[str, Any] = {
        "kind": kind, "table": table_display_name(kind), "batch_id": batch_id, "country_iso3": iso3,
        "rows_written": 0,
        "years_added": plan[plan["action"] == ACTION_NEW]["year"].tolist(),
        "years_replaced": changed["year"].tolist(),
        "years_unchanged": plan[plan["action"] == ACTION_SAME]["year"].tolist() if skip_identical else [],
        "replaced_batches": sorted({b for b in changed["old_batch_id"].tolist() if b}),
    }
    if len(todo) == 0:
        return report
    years = todo["year"].tolist()
    work = df[df["year"].astype(int).isin(years)].copy()
    expected = len(work)                                     # what the caller handed us - the yardstick for the check
    records = to_records(kind, work)
    total = len(records)
    be = _get_backend()
    chunk = 5000                                             # progress granularity; the back-end re-chunks

    def stage():
        with be.session(write=True) as s:
            for start in range(0, total, chunk):
                s.insert(kind, records[start:start + chunk])
                if progress:
                    progress(min(total, start + chunk), total)

    _with_retry(stage, attempts=2, what="Writing rows to %s" % table_name(kind))
    invalidate_cache()
    counted = _run_query("SELECT COUNT(*) AS n FROM %s WHERE batch_id = :bid" % table_fqn(kind),
                         {"bid": batch_id}, cache=False)
    n_staged = int(counted["n"].iloc[0]) if len(counted) else 0
    if n_staged != expected:
        _flip_discard(kind, batch_id)
        log.error("Row count check failed for %s batch %s: expected %d rows, found %d; batch discarded.",
                  table_display_name(kind), batch_id, expected, n_staged)
        raise StoreError("The data could not be saved completely, so nothing was changed. Please try saving again.")
    try:
        _flip(kind, iso3, years, batch_id, C.REASON_REPLACED)
    except StoreError as exc:
        log.error("%d rows staged in %s as batch %s could not be activated: %s",
                  expected, table_display_name(kind), batch_id, getattr(exc, "detail", "") or exc)
        raise StoreError(
            "The data was uploaded but could not be switched on, so the dashboard still shows the previous version. "
            "Please save again.") from exc
    report["rows_written"] = expected
    return report


def _flip_discard(kind: str, batch_id: str) -> None:
    be = _get_backend()

    def go():
        with be.session(write=True) as s:
            s.execute(
                "UPDATE %s SET deactivated_at = %s, deactivation_reason = :reason "
                "WHERE batch_id = :bid AND validity = FALSE AND deactivated_at IS NULL"
                % (table_fqn(kind), s.ts("now")),
                {"now": utc_now_iso(), "reason": C.REASON_DISCARDED, "bid": batch_id})

    try:
        _with_retry(go, what="Discarding a staged batch")
    except StoreError:                                       # pragma: no cover - best effort
        log.exception("Could not discard staged batch %s", batch_id)
    invalidate_cache()


def soft_delete(kind: str, iso3: str, years: Sequence[int], batch_id: str) -> int:
    """Mark the current rows of (country, years) as validity = FALSE ("deleted") if `batch_id` is still the valid one."""
    ensure_tables()
    cur = active_scopes(kind, iso3, years)
    if len(cur) == 0:
        return 0
    if set(cur["batch_id"]) != {batch_id}:
        raise StoreError("The data changed in the meantime (another version is now active). Refresh and try again.")
    return _flip(kind, iso3, years, None, C.REASON_DELETED)


def restore(kind: str, iso3: str, batch_id: str, years: Optional[Sequence[int]] = None) -> int:
    """Make a previously replaced / deleted batch the valid one again (all its years, or only `years`)."""
    ensure_tables()
    versions = list_versions(kind, iso3)
    mine = versions[versions["batch_id"] == batch_id]
    if years is not None:
        mine = mine[mine["year"].isin([int(y) for y in years])]
    if len(mine) == 0:
        raise StoreError("Batch %s was not found." % batch_id)
    status = set(mine["status"])
    if status & {C.REASON_DISCARDED, STATUS_STAGED}:
        raise StoreError("A discarded or incomplete batch cannot be restored.")
    if status == {STATUS_ACTIVE}:
        return 0
    return _flip(kind, iso3, mine["year"].astype(int).tolist(), batch_id, C.REASON_REPLACED)


def discard_staged(kind: str, batch_id: str) -> None:
    """Soft-discard a batch that was staged but never activated."""
    ensure_tables()
    _flip_discard(kind, batch_id)
