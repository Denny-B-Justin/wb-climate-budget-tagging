"""
Climate Budget Tagging — Databricks SQL query service (same pattern as GoAT).

Design:
  • OAuth2 service-principal credentials (no PAT)
  • In-memory TTL cache, thread-safe via a lock
  • All SQL assembled here from parameters — never hand-written in callbacks

Tables (one row per budget line, see constants.TABLES):
    prd_mega.sgpbpi163.cbt_global_rio_markers
    prd_mega.sgpbpi163.cbt_global_eu_taxonomy

Versioning uses the `validity` column — nothing is ever physically deleted. A save writes its rows with
validity = FALSE, checks the row count, then switches old -> FALSE / new -> TRUE in ONE UPDATE, so the
dashboard never loses a country-year if a save fails halfway.

All public methods return plain pandas DataFrames / dicts; callers never import `sql`.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from datetime import datetime, timezone
from typing import Callable, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
from databricks import sql
from databricks.sdk.core import Config, oauth_service_principal

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    logging.warning("python-dotenv not installed — reading env vars from system only")

from src import constants as C

logger = logging.getLogger(__name__)

# ── Environment variable validation ───────────────────────────────────────────
SERVER_HOSTNAME = (os.getenv("DATABRICKS_SERVER_HOSTNAME") or "").strip()
HTTP_PATH = (os.getenv("DATABRICKS_HTTP_PATH") or "").strip()

_REQUIRED: Dict[str, Optional[str]] = {
    "DATABRICKS_SERVER_HOSTNAME": SERVER_HOSTNAME,
    "DATABRICKS_HTTP_PATH":       HTTP_PATH,
    "DATABRICKS_CLIENT_ID":       os.getenv("DATABRICKS_CLIENT_ID"),
    "DATABRICKS_CLIENT_SECRET":   os.getenv("DATABRICKS_CLIENT_SECRET"),
}
_missing = [k for k, v in _REQUIRED.items() if not v]
if _missing:
    raise EnvironmentError(
        "\n\nMissing required environment variables:\n"
        + "\n".join(f"  {k}" for k in _missing)
        + "\n\nCreate a .env file in the project root. See .env.example for reference.\n"
    )


# ── OAuth2 credentials provider ────────────────────────────────────────────────

def credentials_provider():
    """Return OAuth2 service-principal credentials for the Databricks SQL connector."""
    config = Config(
        host          = f"https://{SERVER_HOSTNAME}",
        client_id     = os.getenv("DATABRICKS_CLIENT_ID"),
        client_secret = os.getenv("DATABRICKS_CLIENT_SECRET"),
    )
    return oauth_service_principal(config)


# ═══════════════════════════════════════════════════════════════════════════════
# Errors shown to users (plain language; the technical detail goes to the log)
# ═══════════════════════════════════════════════════════════════════════════════

SERVICE_UNAVAILABLE = ("The data service is not available right now. Please try again in a few minutes; "
                       "if the problem continues, contact the administrator of this tool.")


class StoreError(RuntimeError):
    """A storage problem whose message can be shown to the user."""


class NeedsConfirmation(StoreError):
    """The save would replace existing, different data and the user has not confirmed yet."""

    def __init__(self, plan: pd.DataFrame):
        super().__init__("Existing data would be replaced; confirmation required.")
        self.plan = plan


# ═══════════════════════════════════════════════════════════════════════════════
# SQL builder helpers
# ═══════════════════════════════════════════════════════════════════════════════

ACTION_NEW, ACTION_SAME, ACTION_CHANGED = "new", "identical", "changed"


def _lit(value) -> str:
    """A SQL string literal; internal single quotes are doubled to prevent injection."""
    return "'" + str(value).replace("'", "''") + "'"


def _quote_list(values) -> str:
    """('val1', 'val2', ...) for an IN clause."""
    return "(" + ", ".join(_lit(v) for v in values) + ")"


def _int_list(values) -> str:
    return "(" + ", ".join(str(int(v)) for v in values) + ")"


def build_aggregates_query(kind: str) -> str:
    """Amount- and count-based totals per (country, year) of the current data."""
    amt = "COALESCE(amount, 0)"
    if kind == C.KIND_RIO:
        measures = (
            f"SUM({amt} * {C.RIO_SQL_WEIGHT}) AS weighted_amount,\n"
            f"    SUM(CASE WHEN rio_marker = 2 THEN {amt} ELSE 0 END) AS principal_amount,\n"
            f"    SUM(CASE WHEN rio_marker = 1 THEN {amt} ELSE 0 END) AS significant_amount,\n"
            f"    SUM(CASE WHEN rio_marker = 2 THEN 1 ELSE 0 END) AS principal_lines,\n"
            f"    SUM(CASE WHEN rio_marker = 1 THEN 1 ELSE 0 END) AS significant_lines,\n"
            f"    SUM(CASE WHEN rio_mitigation > 0 THEN {amt} ELSE 0 END) AS mitigation_amount,\n"
            f"    SUM(CASE WHEN rio_adaptation > 0 THEN {amt} ELSE 0 END) AS adaptation_amount"
        )
    else:
        flags = ",\n    ".join(f"SUM(CASE WHEN {C.EU_FLAG_COLUMNS[k]} THEN {amt} ELSE 0 END) AS {k}_amount"
                               for k in C.EU_OBJECTIVES)
        measures = (
            f"SUM(CASE WHEN climate_tag = 'GREEN' THEN {amt} ELSE 0 END) AS green_amount,\n"
            f"    SUM(CASE WHEN climate_tag = 'BROWN' THEN {amt} ELSE 0 END) AS brown_amount,\n"
            f"    SUM(CASE WHEN climate_tag = 'GREEN' THEN 1 ELSE 0 END) AS green_lines,\n"
            f"    SUM(CASE WHEN climate_tag = 'BROWN' THEN 1 ELSE 0 END) AS brown_lines,\n"
            f"    {flags}"
        )
    return (
        f"SELECT\n"
        f"    country, country_iso3, year,\n"
        f"    COUNT(*) AS n_lines,\n"
        f"    SUM(CASE WHEN amount IS NULL THEN 0 ELSE 1 END) AS n_with_amount,\n"
        f"    SUM({amt}) AS total_amount,\n"
        f"    MAX(currency) AS currency,\n"
        f"    {measures}\n"
        f"FROM {C.TABLES[kind]}\n"
        f"WHERE validity = TRUE\n"
        f"GROUP BY country, country_iso3, year"
    )


def build_download_query(kind: str, iso3s: Optional[List[str]] = None, years: Optional[List[int]] = None) -> str:
    """The current lines of the chosen countries / years (empty filter = all)."""
    where = ["validity = TRUE"]
    if iso3s:
        where.append(f"country_iso3 IN {_quote_list(iso3s)}")
    if years:
        where.append(f"year IN {_int_list(years)}")
    return (f"SELECT *\nFROM {C.TABLES[kind]}\nWHERE " + "\n  AND ".join(where)
            + "\nORDER BY country, year, source_row")


def build_current_versions_query(kind: str, iso3: str, years: List[int]) -> str:
    """The batches that are current for one country and the given years (used to plan a save)."""
    return (
        f"SELECT year, batch_id, COUNT(*) AS n_lines, MAX(content_hash) AS content_hash, MIN(uploaded_at) AS uploaded_at\n"
        f"FROM {C.TABLES[kind]}\n"
        f"WHERE validity = TRUE AND country_iso3 = {_lit(iso3)} AND year IN {_int_list(years)}\n"
        f"GROUP BY year, batch_id"
    )


_JSON_TYPE = {"STRING": "string", "INT": "int", "DOUBLE": "double", "BOOLEAN": "boolean", "TIMESTAMP": "string"}


def build_insert_query(kind: str) -> str:
    """INSERT … SELECT from ONE JSON parameter (:payload) holding many rows — avoids huge VALUES strings."""
    cols = C.TABLE_SCHEMAS[kind]
    names = ", ".join(f"`{c.name}`" for c in cols)
    select = ", ".join(f"CAST(`{c.name}` AS TIMESTAMP)" if c.sql == "TIMESTAMP" else f"`{c.name}`" for c in cols)
    struct = ",".join(f"{c.name}:{_JSON_TYPE[c.sql]}" for c in cols)
    return (f"INSERT INTO {C.TABLES[kind]} ({names})\n"
            f"SELECT {select}\nFROM (SELECT inline(from_json(:payload, 'array<struct<{struct}>>')))")


def build_activate_query(kind: str, iso3: str, years: List[int], batch_id: str) -> str:
    """ONE UPDATE: this batch -> validity TRUE, every other current row of the same country-years -> FALSE."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    bid, yrs = _lit(batch_id), _int_list(years)
    return (
        f"UPDATE {C.TABLES[kind]} SET\n"
        f"    validity            = CASE WHEN batch_id = {bid} THEN TRUE ELSE FALSE END,\n"
        f"    deactivated_at      = CASE WHEN batch_id = {bid} THEN NULL ELSE CAST('{now}' AS TIMESTAMP) END,\n"
        f"    deactivation_reason = CASE WHEN batch_id = {bid} THEN NULL ELSE {_lit(C.REASON_REPLACED)} END,\n"
        f"    replaced_by         = CASE WHEN batch_id = {bid} THEN NULL ELSE {bid} END\n"
        f"WHERE (batch_id = {bid} AND year IN {yrs})\n"
        f"   OR (validity = TRUE AND country_iso3 = {_lit(iso3)} AND year IN {yrs})"
    )


def build_discard_query(kind: str, batch_id: str) -> str:
    """Mark an incomplete save as discarded (it stays validity = FALSE and never shows)."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    return (f"UPDATE {C.TABLES[kind]} SET deactivated_at = CAST('{now}' AS TIMESTAMP), "
            f"deactivation_reason = {_lit(C.REASON_DISCARDED)}\n"
            f"WHERE batch_id = {_lit(batch_id)} AND validity = FALSE AND deactivated_at IS NULL")


def _json_value(value, sql_type: str):
    """One cell -> a JSON-safe value (None for missing / non-finite)."""
    if value is None or (not isinstance(value, str) and pd.isna(value)):
        return None
    if sql_type == "STRING":
        return str(value)
    if sql_type == "INT":
        return int(value)
    if sql_type == "DOUBLE":
        v = float(value)
        return v if np.isfinite(v) else None
    if sql_type == "BOOLEAN":
        return bool(value)
    ts = pd.Timestamp(value)
    ts = ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")
    return ts.strftime("%Y-%m-%dT%H:%M:%S.%fZ")


# ═══════════════════════════════════════════════════════════════════════════════
# Query service
# ═══════════════════════════════════════════════════════════════════════════════

class QueryService:
    """
    Singleton Databricks SQL data-access object with in-memory TTL cache.

    Usage:
        qs = QueryService.get_instance()
        df = qs.get_aggregates("rio")
    """

    _instance: Optional["QueryService"] = None

    @staticmethod
    def get_instance() -> "QueryService":
        """Return the process-wide singleton, creating it on first call."""
        if QueryService._instance is None:
            QueryService._instance = QueryService()
        return QueryService._instance

    def __init__(self) -> None:
        self._cache: Dict[str, Tuple[float, pd.DataFrame]] = {}
        self._lock = threading.Lock()

    # ── Cache helpers ──────────────────────────────────────────────────────────

    def _cache_get(self, key: str) -> Optional[pd.DataFrame]:
        with self._lock:
            entry = self._cache.get(key)
            if not entry:
                return None
            expires_at, df = entry
            if time.time() >= expires_at:
                del self._cache[key]
                return None
            return df

    def _cache_set(self, key: str, df: pd.DataFrame) -> None:
        with self._lock:
            if len(self._cache) >= C.QUERY_CACHE_MAX_ENTRIES:
                del self._cache[next(iter(self._cache))]
            self._cache[key] = (time.time() + C.QUERY_CACHE_TTL_SECONDS, df)

    def clear_cache(self) -> None:
        """Flush the entire query cache (after every write, or from the Refresh button)."""
        with self._lock:
            self._cache.clear()

    # ── Core executors ─────────────────────────────────────────────────────────

    def execute_query(self, query: str, use_cache: bool = True) -> pd.DataFrame:
        """Run a SELECT and return a DataFrame. Cached by query text for QUERY_CACHE_TTL_SECONDS."""
        if use_cache:
            cached = self._cache_get(query)
            if cached is not None:
                return cached.copy(deep=True)
        t0 = time.time()
        try:
            with sql.connect(server_hostname=SERVER_HOSTNAME, http_path=HTTP_PATH,
                             credentials_provider=credentials_provider) as conn:
                cursor = conn.cursor()
                cursor.execute(query)
                rows = cursor.fetchall()
                columns = [desc[0] for desc in cursor.description]
                df = pd.DataFrame([tuple(r) for r in rows], columns=columns)
        except Exception as exc:
            logger.exception("[CBT] Query failed: %.200s", query)
            raise StoreError(SERVICE_UNAVAILABLE) from exc
        logger.info("[CBT] DB — %.2fs: %.60s…", time.time() - t0, query)
        if use_cache:
            self._cache_set(query, df)
        return df.copy(deep=True)

    def execute_write(self, statement: str, parameters: Optional[Dict[str, str]] = None) -> None:
        """Run an INSERT / UPDATE. Never cached; the whole cache is cleared afterwards."""
        t0 = time.time()
        try:
            with sql.connect(server_hostname=SERVER_HOSTNAME, http_path=HTTP_PATH,
                             credentials_provider=credentials_provider) as conn:
                cursor = conn.cursor()
                if parameters:
                    cursor.execute(statement, parameters=parameters)
                else:
                    cursor.execute(statement)
        except Exception as exc:
            logger.exception("[CBT] Write failed: %.200s", statement)
            raise StoreError(SERVICE_UNAVAILABLE) from exc
        finally:
            self.clear_cache()
        logger.info("[CBT] WRITE — %.2fs: %.60s…", time.time() - t0, statement)

    # ── Dashboard / Download data ──────────────────────────────────────────────

    def get_aggregates(self, kind: str) -> pd.DataFrame:
        """Per (country, year) totals of the current data, for the dashboard and the download summary."""
        df = self.execute_query(build_aggregates_query(kind))
        for c in df.columns:
            if c not in ("country", "country_iso3", "currency"):
                df[c] = pd.to_numeric(df[c], errors="coerce")
        if len(df):
            df["year"] = df["year"].astype(int)
            df = df.sort_values(["country", "year"])
        return df.reset_index(drop=True)

    def get_download_data(self, kind: str, iso3s: Optional[List[str]] = None,
                          years: Optional[List[int]] = None) -> pd.DataFrame:
        return self.execute_query(build_download_query(kind, iso3s, years), use_cache=False)

    # ── Saving: plan -> stage (validity FALSE) -> count check -> one switch ────

    def plan_save(self, kind: str, df: pd.DataFrame) -> pd.DataFrame:
        """
        What would saving `df` do for each year?
            new        nothing saved yet for this country-year -> rows are added
            identical  the current version has the same content -> nothing to write
            changed    different data is saved -> needs the user's OK; the old version is kept
        """
        if df is None or len(df) == 0:
            raise StoreError("Nothing to save: the result is empty.")
        iso3 = str(df["country_iso3"].iloc[0])
        new = df.groupby("year").agg(new_lines=("record_id", "size"), new_hash=("content_hash", "first")).reset_index()
        old = self.execute_query(build_current_versions_query(kind, iso3, new["year"].tolist()), use_cache=False)
        rows = []
        for _, r in new.iterrows():
            y = int(r["year"])
            olds = old[old["year"].astype(int) == y] if len(old) else old
            entry = {"year": y, "new_lines": int(r["new_lines"]), "old_lines": 0, "old_uploaded_at": None}
            if len(olds) == 0:
                entry["action"] = ACTION_NEW
            else:
                first = olds.iloc[0]
                entry.update(old_lines=int(olds["n_lines"].sum()), old_uploaded_at=first["uploaded_at"])
                same = len(olds) == 1 and str(first["content_hash"]) == str(r["new_hash"])
                entry["action"] = ACTION_SAME if same else ACTION_CHANGED
            rows.append(entry)
        return pd.DataFrame(rows).sort_values("year").reset_index(drop=True)

    def save(self, kind: str, df: pd.DataFrame, replace: bool = False,
             progress: Optional[Callable[[int, int], None]] = None) -> Dict:
        """Save a tagged batch. Raises NeedsConfirmation when different data exists and `replace` is False."""
        plan = self.plan_save(kind, df)
        changed = plan[plan["action"] == ACTION_CHANGED]
        if len(changed) and not replace:
            raise NeedsConfirmation(plan)
        years = plan[plan["action"].isin([ACTION_NEW, ACTION_CHANGED])]["year"].tolist()
        report = {
            "kind": kind, "rows_written": 0,
            "years_added": plan[plan["action"] == ACTION_NEW]["year"].tolist(),
            "years_replaced": changed["year"].tolist(),
            "years_unchanged": plan[plan["action"] == ACTION_SAME]["year"].tolist(),
        }
        if not years:
            return report
        iso3, batch_id = str(df["country_iso3"].iloc[0]), str(df["batch_id"].iloc[0])
        work = df[df["year"].astype(int).isin(years)]

        # 1. Stage the rows (validity = FALSE), 300 rows per statement
        self._insert_dataframe(kind, work, progress)

        # 2. Count check: every row must have arrived before anything is switched
        counted = self.execute_query(
            f"SELECT COUNT(*) AS n FROM {C.TABLES[kind]} WHERE batch_id = {_lit(batch_id)}", use_cache=False)
        n_staged = int(counted["n"].iloc[0]) if len(counted) else 0
        if n_staged != len(work):
            logger.error("[CBT] Row count check failed for %s batch %s: expected %d, found %d — discarded.",
                         kind, batch_id, len(work), n_staged)
            try:
                self.execute_write(build_discard_query(kind, batch_id))
            except StoreError:
                pass
            raise StoreError("The data could not be saved completely, so nothing was changed. Please try saving again.")

        # 3. One UPDATE switches the new version on and the old one off
        try:
            self.execute_write(build_activate_query(kind, iso3, years, batch_id))
        except StoreError as exc:
            logger.error("[CBT] Batch %s is staged in %s but could not be activated.", batch_id, kind)
            raise StoreError("The data was uploaded but could not be switched on, so the dashboard still shows the "
                             "previous version. Please save again.") from exc
        report["rows_written"] = len(work)
        return report

    def _insert_dataframe(self, kind: str, df: pd.DataFrame,
                          progress: Optional[Callable[[int, int], None]] = None, chunk_size: int = 300) -> None:
        cols = C.TABLE_SCHEMAS[kind]
        statement = build_insert_query(kind)
        for start in range(0, len(df), chunk_size):
            chunk = df.iloc[start:start + chunk_size]
            rows = [{c.name: _json_value(row[c.name], c.sql) for c in cols} for _, row in chunk.iterrows()]
            self.execute_write(statement, {"payload": json.dumps(rows, separators=(",", ":"), allow_nan=False)})
            if progress:
                progress(min(start + chunk_size, len(df)), len(df))
        logger.info("[CBT] Inserted %d rows into %s", len(df), C.TABLES[kind])
