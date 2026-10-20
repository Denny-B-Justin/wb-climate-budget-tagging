"""
src/pipeline.py
---------------
Everything between "a file was uploaded" and "a tagged data frame is ready to be saved".

The tagging rules themselves are NOT in here. They live, unchanged, in the project root:

    green_budget_tagger.py            ExcelProcessor (column detection), COFOGTagger, enums
    green_budget_tagger_optimized.py  DeduplicationHelper (tag each unique classification once)
    rio_marker_tagger.py              RioMarkerTagger   -> Rio Marker 0/1/2 (mitigation / adaptation / combined)
    eu_taxonomy_tagger.py             EUTaxonomyTagger  -> six EU environmental-objective flags
    shared/                           keyword dictionaries and the COFOG mapping

This module only (1) reads and validates the upload, (2) feeds those classes, (3) shapes their output into
the schema of the two Unity Catalog tables, and (4) runs long jobs in a background thread whose progress
is kept in small files, so any worker process of the web server can report it.

Python 3.9 compatible.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import logging
import os
import pickle
import re
import sys
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from . import constants as C

# The tagger modules sit in the project root (unchanged); make them importable from anywhere.
if str(C.ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(C.ROOT_DIR))

from green_budget_tagger import (                                    # noqa: E402  (unchanged module)
    ClimateTag, Confidence, EnvironmentalObjective, ExcelProcessor, TaggingResult,
)
from green_budget_tagger_optimized import DeduplicationHelper       # noqa: E402  (unchanged module)
from rio_marker_tagger import RioMarkerResult, RioMarkerTagger      # noqa: E402  (unchanged module)
from eu_taxonomy_tagger import EUTaxonomyResult, EUTaxonomyTagger   # noqa: E402  (unchanged module)

log = logging.getLogger(__name__)


class PipelineError(RuntimeError):
    """A problem with the upload or the settings that the user can fix (message is user-presentable)."""


class JobCancelled(PipelineError):
    pass


# ══════════════════════════════════════════════════════════════════════════
# Reading the upload
# ══════════════════════════════════════════════════════════════════════════

ALLOWED_SUFFIXES = (".xlsx", ".xlsm", ".csv", ".tsv", ".txt")


def max_upload_bytes() -> int:
    try:
        mb = int(os.getenv(C.ENV_MAX_UPLOAD_MB, C.DEFAULT_MAX_UPLOAD_MB))
    except ValueError:
        mb = C.DEFAULT_MAX_UPLOAD_MB
    return max(1, mb) * 1024 * 1024


def max_rows() -> int:
    try:
        return max(100, int(os.getenv(C.ENV_MAX_ROWS, C.DEFAULT_MAX_ROWS)))
    except ValueError:
        return C.DEFAULT_MAX_ROWS


@dataclass
class UploadInfo:
    filename: str
    suffix: str
    sheets: List[str]
    sheet: Optional[str]
    header_row: int
    columns: List[str]
    mapping: Dict[str, str]
    preview: pd.DataFrame
    notes: List[str] = field(default_factory=list)


def _clean_headers(cols) -> List[str]:
    out, seen = [], {}
    for i, c in enumerate(cols):
        name = str(c).strip() if c is not None and not (isinstance(c, float) and np.isnan(c)) else ""
        if not name or name.lower().startswith("unnamed:"):
            name = "column_%d" % (i + 1)
        if name in seen:                                   # duplicate header -> make it unique
            seen[name] += 1
            name = "%s_%d" % (name, seen[name])
        else:
            seen[name] = 1
        out.append(name)
    return out


def _sniff_csv(path: Path) -> Tuple[str, str]:
    for enc in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            with open(path, "r", encoding=enc, newline="") as fh:
                sample = fh.read(65536)
            try:
                dialect = csv.Sniffer().sniff(sample.split("\n", 1)[0] + "\n" + sample[:4000], delimiters=",;\t|")
                return enc, dialect.delimiter
            except csv.Error:
                return enc, ","
        except UnicodeDecodeError:
            continue
    raise PipelineError("The file is not readable text (tried UTF-8 and Windows-1252).")


def detect_mapping(columns: List[str]) -> Dict[str, str]:
    """Column auto-detection - the exact patterns of the original ExcelProcessor (unchanged class)."""
    proc = ExcelProcessor("unused.xlsx")                      # the constructor does no I/O
    proc.df = pd.DataFrame(columns=columns)
    return proc._identify_columns()


def read_sheet(path: Path, suffix: str, sheet: Optional[str] = None, header_row: int = 1,
               nrows: Optional[int] = None) -> pd.DataFrame:
    """Read one sheet / CSV with the header on `header_row` (1-based). Cells keep their text (codes like 0501)."""
    header = max(0, int(header_row) - 1)
    try:
        if suffix in (".xlsx", ".xlsm"):
            df = pd.read_excel(path, sheet_name=sheet or 0, header=header, nrows=nrows, engine="openpyxl",
                               dtype=object)
        else:
            enc, sep = _sniff_csv(path)
            df = pd.read_csv(path, sep=sep, encoding=enc, header=header, nrows=nrows, dtype=str,
                             keep_default_na=True, skipinitialspace=True, engine="python" if sep == "|" else "c")
    except PipelineError:
        raise
    except Exception as exc:
        raise PipelineError("Could not read the file: %s" % " ".join(str(exc).split())[:300]) from exc
    df.columns = _clean_headers(df.columns)
    df = df.dropna(how="all")
    return df


def inspect_upload(path: Path, filename: str, sheet: Optional[str] = None, header_row: int = 1) -> UploadInfo:
    suffix = Path(filename).suffix.lower()
    if suffix not in ALLOWED_SUFFIXES:
        raise PipelineError("Unsupported file type %r. Upload an Excel (.xlsx) or CSV file." % suffix)
    sheets: List[str] = []
    notes: List[str] = []
    if suffix in (".xlsx", ".xlsm"):
        try:
            xl = pd.ExcelFile(path, engine="openpyxl")
        except Exception as exc:
            raise PipelineError("This is not a valid Excel file: %s" % " ".join(str(exc).split())[:200]) from exc
        sheets = list(xl.sheet_names)
        if sheet not in sheets:
            proc = ExcelProcessor(str(path))
            proc.excel_file = xl
            sheet = proc._find_raw_sheet()                   # same rule as the original: 'Raw' sheet, else first
            if sheet.lower() != "raw" and "raw" not in sheet.lower() and len(sheets) > 1:
                notes.append("No sheet called 'Raw' was found; using '%s'. Change it below if needed." % sheet)
    preview = read_sheet(path, suffix, sheet, header_row, nrows=200)
    if preview.empty:
        raise PipelineError("The selected sheet has no data below the header row.")
    return UploadInfo(filename=filename, suffix=suffix, sheets=sheets, sheet=sheet, header_row=header_row,
                      columns=list(preview.columns), mapping=detect_mapping(list(preview.columns)),
                      preview=preview.head(C.PREVIEW_ROWS), notes=notes)


def upload_to_store(info: UploadInfo) -> Dict[str, Any]:
    """JSON-safe form of UploadInfo for the browser-side session store."""
    prev = info.preview.astype(object)
    prev = prev.where(prev.notna(), "").astype(str)
    return {"filename": info.filename, "suffix": info.suffix, "sheets": info.sheets, "sheet": info.sheet,
            "header_row": info.header_row, "columns": info.columns, "mapping": info.mapping,
            "preview": prev.to_dict("records"), "notes": info.notes}


# ══════════════════════════════════════════════════════════════════════════
# Cleaning: amounts, years, text
# ══════════════════════════════════════════════════════════════════════════

_NBSP = "   "


def _parse_amount_text(raw: Any) -> float:
    if raw is None:
        return np.nan
    s = str(raw).strip()
    if not s or s.lower() in ("nan", "none", "null", "n/a", "na", "-", "--", "–", "—"):
        return np.nan
    neg = s.startswith("(") and s.endswith(")") or s.endswith("-") and s[:-1].replace(".", "").replace(",", "").isdigit()
    s = re.sub(r"[()\s%s]" % _NBSP, "", s)
    s = re.sub(r"^[^\d\-+.,]+|[^\d.,]+$", "", s.lstrip("+"))        # currency symbols / letters around the number
    if s.startswith("-"):
        neg, s = True, s[1:]
    s = s.rstrip("-")
    if not s or not re.search(r"\d", s):
        return np.nan
    if "," in s and "." in s:
        dec = "," if s.rfind(",") > s.rfind(".") else "."
        s = s.replace("." if dec == "," else ",", "").replace(dec, ".")
    elif "," in s:
        s = s.replace(",", "") if re.fullmatch(r"\d{1,3}(,\d{3})+", s) else s.replace(",", ".")
    elif s.count(".") > 1:
        s = s.replace(".", "")
    try:
        val = float(s)
    except ValueError:
        return np.nan
    return -val if neg else val


def parse_amount(series: pd.Series) -> pd.Series:
    """Numbers as numbers; text such as '1,234.50', '(1 234)', '1.234,5 ZMW' is converted, junk becomes NaN."""
    if pd.api.types.is_numeric_dtype(series) and not pd.api.types.is_bool_dtype(series):
        return series.astype("float64")
    num = pd.to_numeric(series, errors="coerce").astype("float64")
    retry = num.isna() & series.notna()
    if retry.any():
        num[retry] = series[retry].map(_parse_amount_text).astype("float64")
    return num


def parse_year(series: pd.Series) -> pd.Series:
    """2019, 2019.0, '2019', 'FY2019/20', '2019-20' -> 2019 (first plausible 4-digit year); anything else -> <NA>."""
    def one(v):
        if v is None or (isinstance(v, float) and np.isnan(v)):
            return pd.NA
        if isinstance(v, (int, np.integer)):
            y = int(v)
        elif isinstance(v, (float, np.floating)):
            y = int(v) if float(v).is_integer() else None
        else:
            m = re.search(r"(?<!\d)(19\d{2}|20\d{2}|21\d{2})(?!\d)", str(v))
            y = int(m.group(1)) if m else None
        return y if y is not None and C.YEAR_MIN <= y <= C.YEAR_MAX else pd.NA

    return series.map(one).astype("Int64")


def _text_cell(v: Any) -> Optional[str]:
    if v is None or (not isinstance(v, str) and pd.isna(v)):
        return None
    if isinstance(v, (float, np.floating)) and float(v).is_integer():
        v = int(v)                                          # 5.0 -> "5" (Excel numbers read as floats)
    out = re.sub(r"\s+", " ", str(v)).strip()
    return None if out == "" or out.lower() == "nan" else out


def _clean_text(series: pd.Series) -> pd.Series:
    return series.map(_text_cell).astype(object)


@dataclass
class Metadata:
    """What the analyst typed in step 1 (identity of the data in the global tables = country + year)."""
    country_iso3: str = ""
    year_mode: str = "column"                  # "column" -> take each row's year from the file | "single"
    single_year: Optional[int] = None
    currency: str = ""
    data_source: str = ""
    notes: str = ""
    uploaded_by: str = ""
    source_file: str = ""

    def errors(self) -> List[str]:
        out = []
        if self.country_iso3 not in C.COUNTRY_BY_ISO3:
            out.append("Choose the country.")
        if self.year_mode == "single":
            if self.single_year is None or not (C.YEAR_MIN <= int(self.single_year) <= C.YEAR_MAX):
                out.append("Enter a year between %d and %d." % (C.YEAR_MIN, C.YEAR_MAX))
        if not (self.uploaded_by or "").strip():
            out.append("Enter your name or e-mail (stored with the data).")
        return out

    @property
    def country(self) -> str:
        return C.COUNTRY_BY_ISO3.get(self.country_iso3, "")


@dataclass
class Prepared:
    lines: pd.DataFrame                         # standard column names, cleaned; includes year + source_row
    issues: List[Dict[str, str]]
    n_raw: int
    n_dropped_empty: int
    bad_amounts: int
    years: List[int]


def prepare_lines(raw: pd.DataFrame, mapping: Dict[str, str], meta: Metadata, header_row: int = 1) -> Prepared:
    """Map the user's columns to the standard names, clean them and attach year and source row."""
    issues: List[Dict[str, str]] = []
    if len(raw) > max_rows():
        raise PipelineError("The file has %d rows; the limit is %d (set %s to change it)."
                            % (len(raw), max_rows(), C.ENV_MAX_ROWS))
    mapping = {k: v for k, v in (mapping or {}).items() if v and v in raw.columns and k in C.STD_FIELD_KEYS}
    if not any(k in mapping for k in C.TEXT_MATCH_FIELDS):
        raise PipelineError("Map at least one of: programme title, programme description or ministry. "
                            "These are the columns the keyword rules read.")
    used = [v for v in mapping.values()]
    if len(set(used)) != len(used):
        dupes = sorted({v for v in used if used.count(v) > 1})
        raise PipelineError("The same file column is mapped to more than one field: %s." % ", ".join(dupes))

    out = pd.DataFrame(index=raw.index)
    for f in C.LINE_TEXT_FIELDS:
        out[f] = _clean_text(raw[mapping[f]]) if f in mapping else None
    out["source_row"] = (np.arange(len(raw)) + int(header_row) + 1).astype(int)

    bad_amounts = 0
    if "amount" in mapping:
        parsed = parse_amount(raw[mapping["amount"]])
        had_value = raw[mapping["amount"]].notna() & (raw[mapping["amount"]].astype(str).str.strip() != "")
        bad_amounts = int((parsed.isna() & had_value).sum())
        out["amount"] = parsed
        if bad_amounts:
            issues.append({"level": "warn", "message":
                           "%d amount cell(s) are not numbers and were stored as empty (e.g. row %d)."
                           % (bad_amounts, int(out.loc[parsed.isna() & had_value, "source_row"].iloc[0]))})
        if parsed.notna().sum() == 0:
            issues.append({"level": "warn", "message":
                           "No amount could be read - shares will be based on the number of lines, not money."})
    else:
        out["amount"] = np.nan
        issues.append({"level": "warn", "message":
                       "No amount column mapped - shares will be based on the number of lines, not money."})

    has_data = out[C.LINE_TEXT_FIELDS].notna().any(axis=1) | out["amount"].notna()
    if meta.year_mode == "column":
        if "year" not in mapping:
            raise PipelineError("You chose 'use the year column', but no column is mapped to Year. "
                                "Map it, or switch to 'one year for all rows'.")
        years = parse_year(raw[mapping["year"]])
        bad = years.isna() & has_data
        if bad.any():
            ex = raw.loc[bad, mapping["year"]].head(3).tolist()
            first_row = int(out.loc[bad, "source_row"].iloc[0])
            raise PipelineError(
                "%d row(s) have no readable year (first at row %d; value(s): %s). Fix the file or choose "
                "'one year for all rows'." % (int(bad.sum()), first_row, ", ".join(repr(x) for x in ex)))
        out["year"] = years
    else:
        out["year"] = int(meta.single_year)

    keep = has_data & out["year"].notna()
    dropped = int((~keep).sum())
    out = out[keep].reset_index(drop=True)
    if out.empty:
        raise PipelineError("No usable rows were found after removing empty ones.")
    out["year"] = out["year"].astype(int)
    years_found = sorted(out["year"].unique().tolist())
    if len(years_found) > 12:
        issues.append({"level": "warn", "message": "%d different years in one file - check the year column." % len(years_found)})
    if not any(k in mapping for k in ("func1",)):
        issues.append({"level": "info", "message":
                       "No Function (COFOG) column mapped - the COFOG pre-determined tags cannot apply; keywords only."})
    return Prepared(lines=out, issues=issues, n_raw=len(raw), n_dropped_empty=dropped, bad_amounts=bad_amounts,
                    years=years_found)


# ══════════════════════════════════════════════════════════════════════════
# Tagging
# ══════════════════════════════════════════════════════════════════════════

@dataclass
class TaggingSettings:
    methods: str = C.METHOD_BOTH               # rio | eu | both


@dataclass
class Tagged:
    rio: Optional[pd.DataFrame]
    eu: Optional[pd.DataFrame]
    stats: Dict[str, Any]


def _error_result_rio(exc: Exception) -> RioMarkerResult:
    return RioMarkerResult(
        tagging_result=TaggingResult(ClimateTag.NEUTRAL, Confidence.LOW, "RIO_ERROR", EnvironmentalObjective.NONE, None,
                                     "Error during Rio Marker tagging: %s" % str(exc)[:80]),
        rio_marker=0, rio_mitigation=0, rio_adaptation=0)


def _error_result_eu(exc: Exception) -> EUTaxonomyResult:
    return EUTaxonomyResult(
        tagging_result=TaggingResult(ClimateTag.NEUTRAL, Confidence.LOW, "EU_ERROR", EnvironmentalObjective.NONE, None,
                                     "Error during EU Taxonomy tagging: %s" % str(exc)[:80]),
        eu_objectives={k: False for k in C.EU_OBJECTIVES}, primary_objective="error")


def make_taggers(settings: TaggingSettings):
    """Instantiate the unchanged keyword taggers; returns (rio, eu) - None for a method that was not asked for."""
    kw = dict(use_llm=False, mapping_path=str(C.COFOG_MAPPING_PATH))
    rio = RioMarkerTagger(**kw) if settings.methods in (C.METHOD_RIO, C.METHOD_BOTH) else None
    eu = EUTaxonomyTagger(**kw) if settings.methods in (C.METHOD_EU, C.METHOD_BOTH) else None
    return rio, eu


def tag_lines(lines: pd.DataFrame, settings: TaggingSettings,
              progress: Optional[Callable[[int, int, str], None]] = None,
              cancelled: Optional[Callable[[], bool]] = None) -> Tagged:
    """
    Tag every budget line.

    1. Only the standard columns that were mapped are shown to the taggers, under their standard names,
       so a stray column called "sector" or "project_title" in the user's file can never change a result.
    2. Identical classifications are tagged once (DeduplicationHelper) and the answer is copied to all
       lines that share it - this is what makes 100k-line budgets quick, and it is exact because the
       taggers only read these columns.
    """
    rio_t, eu_t = make_taggers(settings)
    fields = [f for f in C.LINE_TEXT_FIELDS if lines[f].notna().any()]
    colmap = {f: f for f in fields}
    frame = lines[fields].reset_index(drop=True)            # RangeIndex: required by expand_results()
    helper = DeduplicationHelper(colmap)
    unique, mapping, _ = helper.deduplicate(frame)
    n_unique = len(unique)
    t0 = time.time()
    rio_res: List[Any] = []
    eu_res: List[Any] = []
    errors = 0
    step = 25                                              # check for "cancel" / report progress every 25 classifications
    for i in range(n_unique):
        row = unique.iloc[i]
        if rio_t is not None:
            try:
                rio_res.append(rio_t.tag_row(row, colmap))
            except Exception as exc:                          # one bad line must not stop the batch
                errors += 1
                rio_res.append(_error_result_rio(exc))
        if eu_t is not None:
            try:
                eu_res.append(eu_t.tag_row(row, colmap))
            except Exception as exc:
                errors += 1
                eu_res.append(_error_result_eu(exc))
        if (i + 1) % step == 0 or i + 1 == n_unique:
            if cancelled and cancelled():
                raise JobCancelled("Cancelled by the user.")
            if progress:
                progress(i + 1, n_unique, "Tagging %d of %d unique classifications" % (i + 1, n_unique))
    n = len(frame)
    stats: Dict[str, Any] = {
        "lines": n, "unique_classifications": n_unique, "errors": errors,
        "seconds": round(time.time() - t0, 2), "methods": settings.methods,
    }
    rio_df = eu_df = None
    if rio_t is not None:
        rio_df = _rio_frame(lines, helper.expand_results(rio_res, mapping, n))
        stats["rio_methods"] = rio_df["tag_method"].value_counts().to_dict()
    if eu_t is not None:
        eu_df = _eu_frame(lines, helper.expand_results(eu_res, mapping, n))
        stats["eu_methods"] = eu_df["tag_method"].value_counts().to_dict()
    return Tagged(rio=rio_df, eu=eu_df, stats=stats)


def _base_frame(lines: pd.DataFrame, results: List[Any]) -> pd.DataFrame:
    df = lines[["year", "source_row"] + C.LINE_TEXT_FIELDS + ["amount"]].copy().reset_index(drop=True)
    tr = [r.tagging_result for r in results]
    df["climate_tag"] = [t.climate_tag.name for t in tr]
    df["confidence"] = [t.confidence.name for t in tr]
    df["tag_method"] = [t.method for t in tr]
    df["environmental_objective"] = [getattr(t.environmental_objective, "value", str(t.environmental_objective)) for t in tr]
    df["subcategory"] = [t.subcategory for t in tr]
    df["rationale"] = [(t.rationale or "")[:2000] for t in tr]
    return df


def _rio_frame(lines: pd.DataFrame, results: List[RioMarkerResult]) -> pd.DataFrame:
    df = _base_frame(lines, results)
    df["rio_marker"] = [int(r.rio_marker) for r in results]
    df["rio_mitigation"] = [int(r.rio_mitigation) for r in results]
    df["rio_adaptation"] = [int(r.rio_adaptation) for r in results]
    return df


def _eu_frame(lines: pd.DataFrame, results: List[EUTaxonomyResult]) -> pd.DataFrame:
    df = _base_frame(lines, results)
    for k in C.EU_OBJECTIVES:
        df[C.EU_FLAG_COLUMNS[k]] = [bool((r.eu_objectives or {}).get(k, False)) for r in results]
    df["eu_primary_objective"] = [str(r.primary_objective or "none") for r in results]
    return df


# ══════════════════════════════════════════════════════════════════════════
# From tagged frame to a saveable frame
# ══════════════════════════════════════════════════════════════════════════

_HASH_COLUMNS = {
    C.KIND_RIO: C.LINE_TEXT_FIELDS + ["amount", "climate_tag", "confidence", "tag_method", "environmental_objective",
                                      "subcategory", "rio_marker", "rio_mitigation", "rio_adaptation", "currency"],
    C.KIND_EU: C.LINE_TEXT_FIELDS + ["amount", "climate_tag", "confidence", "tag_method", "environmental_objective",
                                     "subcategory", "eu_primary_objective", "currency"]
               + [C.EU_FLAG_COLUMNS[k] for k in C.EU_OBJECTIVES],
}


def content_hash(kind: str, df: pd.DataFrame) -> str:
    """
    Order-independent fingerprint of the *values* of a (country, year) slice: the budget line fields, the amount
    (rounded to 6 decimals), the tags and the currency. Notes, file name, user and time are deliberately excluded,
    so re-uploading the same numbers is recognised as "identical" and nothing is rewritten.
    """
    cols = [c for c in _HASH_COLUMNS[kind] if c in df.columns]
    parts = []
    for c in cols:
        s = df[c]
        if c == "amount":
            parts.append(s.map(lambda v: "" if pd.isna(v) else "%.6f" % float(v)))
        else:
            parts.append(s.map(lambda v: "" if v is None or (not isinstance(v, str) and pd.isna(v)) else str(v)))
    rows = sorted("\x1f".join(t) for t in zip(*parts))
    h = hashlib.sha256()
    h.update(("%s|%d|" % (kind, len(rows))).encode())
    for r in rows:
        h.update(r.encode("utf-8", "replace"))
        h.update(b"\x1e")
    return h.hexdigest()[:32]


def new_batch_id() -> str:
    return "%s-%s" % (datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S"), uuid.uuid4().hex[:8])


def finalize_for_save(kind: str, tagged: pd.DataFrame, meta: Metadata, stats: Dict[str, Any],
                      batch_id: str, uploaded_at: Optional[datetime] = None) -> pd.DataFrame:
    """Attach identity, metadata, audit columns and per-year content hashes; order columns like the table."""
    df = tagged.copy()
    now = pd.Timestamp(uploaded_at or datetime.now(timezone.utc))
    now = now.tz_localize("UTC") if now.tzinfo is None else now.tz_convert("UTC")
    df["country"] = meta.country
    df["country_iso3"] = meta.country_iso3
    df["validity"] = False                                    # staged; the atomic flip makes it TRUE
    df["batch_id"] = batch_id
    df["record_id"] = [batch_id + ":" + str(r) for r in df["source_row"]]
    df["currency"] = (meta.currency or "").strip() or None
    df["data_source"] = (meta.data_source or "").strip() or None
    df["notes"] = (meta.notes or "").strip() or None
    df["source_file"] = meta.source_file or None
    df["methodology_version"] = C.METHODOLOGY_VERSION
    df["uploaded_by"] = meta.uploaded_by.strip()
    df["uploaded_at"] = now
    df["deactivated_at"] = pd.NaT
    df["deactivated_by"] = None
    df["deactivation_reason"] = None
    df["replaced_by"] = None
    hashes = {int(y): content_hash(kind, g) for y, g in df.groupby("year")}
    df["content_hash"] = df["year"].astype(int).map(hashes)
    return df[C.column_names(kind)].reset_index(drop=True)


# ══════════════════════════════════════════════════════════════════════════
# Summary numbers for the results page (same formulas as the SQL in queries.aggregates)
# ══════════════════════════════════════════════════════════════════════════

def _share(num: float, den: float) -> Optional[float]:
    return float(num) / float(den) if den and den > 0 and np.isfinite(den) else None


def summarize(kind: str, df: pd.DataFrame) -> Dict[str, Any]:
    """Headline metrics of a tagged frame (amount-weighted when amounts exist, otherwise by line count)."""
    amt = df["amount"].fillna(0.0).astype(float)
    has_amount = bool(df["amount"].notna().any())
    n = len(df)
    out: Dict[str, Any] = {"lines": n, "has_amount": has_amount, "total_amount": float(amt.sum())}
    basis = amt if has_amount else pd.Series(np.ones(n), index=df.index)
    total = float(basis.sum())
    out["basis"] = "amount" if has_amount else "lines"
    if kind == C.KIND_RIO:
        w = df["rio_marker"].map(C.RIO_WEIGHTS).astype(float)
        out["weighted_share"] = _share(float((basis * w).sum()), total)
        out["principal_share"] = _share(float(basis[df["rio_marker"] == 2].sum()), total)
        out["significant_share"] = _share(float(basis[df["rio_marker"] == 1].sum()), total)
        out["tagged_lines"] = int((df["rio_marker"] > 0).sum())
        out["mitigation_share"] = _share(float(basis[df["rio_mitigation"] > 0].sum()), total)
        out["adaptation_share"] = _share(float(basis[df["rio_adaptation"] > 0].sum()), total)
        out["by_marker"] = {int(m): float(basis[df["rio_marker"] == m].sum()) for m in (0, 1, 2)}
    else:
        green = df["climate_tag"] == "GREEN"
        brown = df["climate_tag"] == "BROWN"
        out["green_share"] = _share(float(basis[green].sum()), total)
        out["brown_share"] = _share(float(basis[brown].sum()), total)
        out["tagged_lines"] = int(green.sum())
        out["by_objective"] = {k: float(basis[df[C.EU_FLAG_COLUMNS[k]]].sum()) for k in C.EU_OBJECTIVES}
        out["objective_lines"] = {k: int(df[C.EU_FLAG_COLUMNS[k]].sum()) for k in C.EU_OBJECTIVES}
    out["by_method"] = df["tag_method"].value_counts().to_dict()
    return out


# ══════════════════════════════════════════════════════════════════════════
# Background jobs (thread + files, so every worker process can answer "how far is it?")
# ══════════════════════════════════════════════════════════════════════════

_JOB_ID_RE = re.compile(r"^[0-9a-f]{32}$")


def work_dir() -> Path:
    base = os.getenv(C.ENV_WORK_DIR) or os.path.join(tempfile.gettempdir(), "cbt_work")
    p = Path(base)
    p.mkdir(parents=True, exist_ok=True)
    return p


def _job_path(job_id: str) -> Path:
    if not isinstance(job_id, str) or not _JOB_ID_RE.match(job_id):
        raise PipelineError("Invalid session id.")
    return work_dir() / job_id


def new_job_id() -> str:
    jid = uuid.uuid4().hex
    _job_path(jid).mkdir(parents=True, exist_ok=True)
    return jid


def cleanup_old_jobs(max_age_hours: float = 24.0) -> int:
    """Delete job folders (uploaded files, intermediate frames) older than `max_age_hours`."""
    removed = 0
    cutoff = time.time() - max_age_hours * 3600
    try:
        for p in work_dir().iterdir():
            if p.is_dir() and _JOB_ID_RE.match(p.name) and p.stat().st_mtime < cutoff:
                _rmtree(p)
                removed += 1
    except OSError:                                          # pragma: no cover
        pass
    return removed


def _rmtree(p: Path) -> None:
    import shutil
    shutil.rmtree(p, ignore_errors=True)


def job_file(job_id: str, name: str) -> Path:
    if "/" in name or "\\" in name or name.startswith("."):
        raise PipelineError("Invalid file name.")
    return _job_path(job_id) / name


def put_object(job_id: str, name: str, obj: Any) -> None:
    """Server-side pickle (we only ever load files this process wrote into a random, validated folder)."""
    tmp = job_file(job_id, name + ".tmp")
    with open(tmp, "wb") as fh:
        pickle.dump(obj, fh, protocol=4)
    os.replace(tmp, job_file(job_id, name))


def get_object(job_id: str, name: str, default: Any = None) -> Any:
    p = job_file(job_id, name)
    if not p.exists():
        return default
    with open(p, "rb") as fh:
        return pickle.load(fh)


def write_status(job_id: str, channel: str, **fields: Any) -> Dict[str, Any]:
    """Merge `fields` into <job>/<channel>.json atomically (channel = 'tag' | 'save')."""
    p = job_file(job_id, channel + ".json")
    cur: Dict[str, Any] = {}
    if p.exists():
        try:
            cur = json.loads(p.read_text())
        except (OSError, ValueError):
            cur = {}
    cur.update(fields)
    cur["heartbeat"] = time.time()
    tmp = job_file(job_id, channel + ".json.tmp")
    tmp.write_text(json.dumps(cur, default=str))
    os.replace(tmp, p)
    return cur


def read_status(job_id: str, channel: str) -> Dict[str, Any]:
    try:
        p = job_file(job_id, channel + ".json")
        if p.exists():
            return json.loads(p.read_text())
    except (OSError, ValueError, PipelineError):
        pass
    return {"state": "idle"}


def request_cancel(job_id: str, channel: str) -> None:
    job_file(job_id, channel + ".cancel").write_text("1")


def is_cancelled(job_id: str, channel: str) -> bool:
    return job_file(job_id, channel + ".cancel").exists()


def clear_cancel(job_id: str, channel: str) -> None:
    try:
        job_file(job_id, channel + ".cancel").unlink()
    except FileNotFoundError:
        pass


STALL_SECONDS = 180


def effective_state(status: Dict[str, Any]) -> str:
    """'running' with no heartbeat for a while means the worker died (e.g. the app was restarted)."""
    st = status.get("state", "idle")
    if st == "running" and time.time() - float(status.get("heartbeat", 0)) > STALL_SECONDS:
        return "stalled"
    return st


def start_thread(target: Callable[..., None], *args: Any) -> threading.Thread:
    t = threading.Thread(target=target, args=args, daemon=True)
    t.start()
    return t


# -- the two jobs -------------------------------------------------------------------------------------------------

def run_tag_job(job_id: str, upload_name: str, suffix: str, sheet: Optional[str], header_row: int,
                mapping: Dict[str, str], meta: Metadata, settings: TaggingSettings) -> None:
    """Background job: read -> clean -> tag -> store result frames in the job folder. Never raises."""
    ch = "tag"
    try:
        clear_cancel(job_id, ch)
        write_status(job_id, ch, state="running", phase="Reading the file", done=0, total=0, error=None,
                     started=time.time(), finished=None)
        raw = read_sheet(job_file(job_id, upload_name), suffix, sheet, header_row)
        write_status(job_id, ch, phase="Checking and cleaning %d rows" % len(raw))
        prepared = prepare_lines(raw, mapping, meta, header_row)
        issues = list(prepared.issues)

        last = {"t": 0.0}

        def progress(done: int, total: int, msg: str) -> None:
            now = time.time()
            if now - last["t"] >= 0.4 or done >= total:      # at most ~2 status writes per second
                last["t"] = now
                write_status(job_id, ch, phase=msg, done=done, total=total)

        tagged = tag_lines(prepared.lines, settings, progress=progress, cancelled=lambda: is_cancelled(job_id, ch))
        stats = dict(tagged.stats)
        if stats.get("errors"):
            issues.append({"level": "warn", "message": "%d line(s) raised an error and were tagged as neutral." % stats["errors"]})
        stats.update(raw_rows=prepared.n_raw, dropped_empty=prepared.n_dropped_empty, years=prepared.years,
                     bad_amounts=prepared.bad_amounts)
        result = {"rio": tagged.rio, "eu": tagged.eu, "stats": stats, "issues": issues,
                  "meta": meta, "settings": settings}
        put_object(job_id, "result.pkl", result)
        summaries = {}
        if tagged.rio is not None:
            summaries["rio"] = summarize(C.KIND_RIO, tagged.rio)
        if tagged.eu is not None:
            summaries["eu"] = summarize(C.KIND_EU, tagged.eu)
        write_status(job_id, ch, state="done", phase="Done", done=stats["unique_classifications"],
                     total=stats["unique_classifications"], finished=time.time(), stats=stats, issues=issues,
                     summaries=summaries)
    except JobCancelled:
        write_status(job_id, ch, state="cancelled", phase="Cancelled", finished=time.time())
    except PipelineError as exc:
        write_status(job_id, ch, state="error", error=str(exc), finished=time.time())
    except Exception as exc:                                  # pragma: no cover - last resort, keep the UI alive
        log.exception("Tagging job failed")
        write_status(job_id, ch, state="error", error="Unexpected error: %s" % " ".join(str(exc).split())[:300],
                     finished=time.time())


def run_save_job(job_id: str, actor: str, replace: bool, queries_module: Any) -> None:
    """Background job: write the tagged frames of this session to Unity Catalog (staging + atomic flip)."""
    ch = "save"
    try:
        write_status(job_id, ch, state="running", phase="Preparing", done=0, total=0, error=None, report=None,
                     started=time.time(), finished=None)
        res = get_object(job_id, "result.pkl")
        if not res:
            raise PipelineError("There is no tagged result in this session. Run the tagging first.")
        meta: Metadata = res["meta"]
        stats = res["stats"]
        batch_id = new_batch_id()
        now = datetime.now(timezone.utc)
        reports, total_rows = [], 0
        todo = [(k, res[k]) for k in (C.KIND_RIO, C.KIND_EU) if res.get(k) is not None]
        for idx, (kind, tagged) in enumerate(todo):
            frame = finalize_for_save(kind, tagged, meta, stats, batch_id, now)
            total_rows += len(frame)

            def prog(done, total, _k=kind, _i=idx):
                write_status(job_id, ch, phase="Writing %s (%d of %d rows)" % (C.KIND_LABELS[_k], done, total),
                             done=done, total=total)

            write_status(job_id, ch, phase="Checking existing data for %s" % C.KIND_LABELS[kind])
            reports.append(queries_module.save(kind, frame, actor, replace=replace, progress=prog))
        write_status(job_id, ch, state="done", phase="Saved", finished=time.time(),
                     report={"batch_id": batch_id, "reports": reports, "rows": total_rows})
    except queries_module.NeedsConfirmation:
        write_status(job_id, ch, state="needs_confirmation", phase="Waiting for confirmation", finished=time.time())
    except (PipelineError, queries_module.StoreError) as exc:
        write_status(job_id, ch, state="error", error=str(exc), finished=time.time())
    except Exception as exc:                                  # pragma: no cover
        log.exception("Save job failed")
        write_status(job_id, ch, state="error", error="Unexpected error: %s" % " ".join(str(exc).split())[:300],
                     finished=time.time())


def build_save_plan(job_id: str, queries_module: Any) -> Dict[str, pd.DataFrame]:
    """For the confirmation dialog: what would saving do for each table / year (no writes)."""
    res = get_object(job_id, "result.pkl")
    if not res:
        raise PipelineError("There is no tagged result in this session. Run the tagging first.")
    plans = {}
    for kind in (C.KIND_RIO, C.KIND_EU):
        if res.get(kind) is None:
            continue
        frame = finalize_for_save(kind, res[kind], res["meta"], res["stats"], "plan-only", None)
        plans[kind] = queries_module.plan_save(kind, frame)
    return plans
