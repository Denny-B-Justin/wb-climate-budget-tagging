"""
src/constants.py
----------------
Everything that is *configuration or static data* for the Climate Budget Tagging
(CBT) web app, in one place: names, labels, colours, limits, the country list and
the exact schema of the two Unity Catalog tables.

Nothing in here touches the network, the file system or Dash, so it can be
imported anywhere (including tests) without side effects.

Python 3.9 compatible (the Posit Connect runtime used by the CBD app).
"""

from __future__ import annotations

import os
from collections import namedtuple
from pathlib import Path

# ══════════════════════════════════════════════════════════════════════════
# Identity
# ══════════════════════════════════════════════════════════════════════════

APP_NAME = "Climate Budget Tagging Tool"
APP_SHORT = "CBT"
APP_TAGLINE = "Tag government budget lines against the Rio Markers and the EU Taxonomy"
METHODOLOGY_VERSION = "1.0"          # bump when keyword lists or rules change; stored on every row
SUITE_URL = "https://datanalytics.worldbank.org/wkpgf-pimpam-digital-tools/"
SUITE_NAME = "CBT"

ROOT_DIR = Path(__file__).resolve().parent.parent
ASSETS_DIR = ROOT_DIR / "assets"
SHARED_DIR = ROOT_DIR / "shared"
COFOG_MAPPING_PATH = SHARED_DIR / "cofog_mapping.json"
SAMPLE_DIR = ROOT_DIR / "sample_data"

# ══════════════════════════════════════════════════════════════════════════
# Routing (query-string based so it works behind Posit Connect, like the CBD app)
# ══════════════════════════════════════════════════════════════════════════

PAGE_HOME = "home"
PAGE_TAG = "tag"
PAGE_DASHBOARD = "dashboard"
PAGE_DATA = "data"
PAGE_METHODOLOGY = "methodologies"
PAGES = [
    # key, nav label, href
    (PAGE_HOME, "Overview", "?page=home"),
    (PAGE_TAG, "Tag a budget", "?page=tag"),
    (PAGE_DASHBOARD, "Dashboard", "?page=dashboard"),
    (PAGE_DATA, "Download data", "?page=data"),
    (PAGE_METHODOLOGY, "Methodologies", "?page=methodologies"),
]
DEFAULT_PAGE = PAGE_HOME

# ══════════════════════════════════════════════════════════════════════════
# Environment variables (uploads / work folder; the Databricks ones are read in queries.py)
# ══════════════════════════════════════════════════════════════════════════

ENV_WORK_DIR = "CBT_WORK_DIR"                   # where uploaded files / job state live
ENV_MAX_UPLOAD_MB = "CBT_MAX_UPLOAD_MB"
ENV_MAX_ROWS = "CBT_MAX_ROWS"

DEFAULT_MAX_UPLOAD_MB = 60
DEFAULT_MAX_ROWS = 500_000

# ══════════════════════════════════════════════════════════════════════════
# Unity Catalog tables
# ══════════════════════════════════════════════════════════════════════════

TABLE_PREFIX = "cbt_"
KIND_RIO = "rio"
KIND_EU = "eu"
KINDS = (KIND_RIO, KIND_EU)
TABLE_NAMES = {
    KIND_RIO: TABLE_PREFIX + "global_rio_markers",
    KIND_EU: TABLE_PREFIX + "global_eu_taxonomy",
}
KIND_LABELS = {KIND_RIO: "Rio Markers", KIND_EU: "EU Taxonomy"}

# Fully qualified Unity Catalog tables (created once by an admin; the app only reads and writes rows)
CATALOG, SCHEMA = "prd_mega", "sgpbpi163"
TABLES = {kind: "`%s`.`%s`.`%s`" % (CATALOG, SCHEMA, name) for kind, name in TABLE_NAMES.items()}

# Query cache tuning (same variable names as GoAT)
QUERY_CACHE_TTL_SECONDS = int(os.getenv("QUERY_CACHE_TTL_SECONDS", "300"))
QUERY_CACHE_MAX_ENTRIES = int(os.getenv("QUERY_CACHE_MAX_ENTRIES", "256"))
TABLE_COMMENTS = {
    KIND_RIO: "Climate budget tagging - Rio Markers (0/1/2) per budget line. One row per source line; "
              "validity=TRUE marks the current version for a (country_iso3, year).",
    KIND_EU: "Climate budget tagging - EU Taxonomy objective flags per budget line. One row per source line; "
             "validity=TRUE marks the current version for a (country_iso3, year).",
}

# Allowed identifier shape for catalog / schema / table names that come from env vars.
IDENTIFIER_RE = r"^[A-Za-z_][A-Za-z0-9_]{0,254}$"

Col = namedtuple("Col", "name sql group doc")
# sql: STRING | INT | DOUBLE | BOOLEAN | TIMESTAMP        group: key | meta | line | result | audit

# --- identity of a row and of a version ------------------------------------------------------------
_KEY_COLS = [
    Col("record_id", "STRING", "key", "Unique id of the stored line: <batch_id>:<source_row>"),
    Col("country", "STRING", "key", "Country name (World Bank style)"),
    Col("country_iso3", "STRING", "key", "ISO 3166-1 alpha-3 code (Kosovo = XKX)"),
    Col("year", "INT", "key", "Budget year the line belongs to"),
    Col("validity", "BOOLEAN", "key", "TRUE = current version for (country_iso3, year); FALSE = replaced, deleted or staged"),
    Col("batch_id", "STRING", "key", "One id per save operation (upload)"),
]
# --- the budget line as uploaded (only the standard fields that were mapped) -----------------------
LINE_TEXT_FIELDS = ["admin1", "admin2", "admin3", "func1", "func2", "func3", "func4",
                    "econ1", "econ2", "econ3", "econ4", "prog1", "prog2"]
_LINE_COLS = [Col(f, "STRING", "line", "Budget line field '%s' as uploaded" % f) for f in LINE_TEXT_FIELDS] + [
    Col("amount", "DOUBLE", "line", "Budget amount in `currency` (units as uploaded)"),
]
# --- result columns shared by both methodologies ---------------------------------------------------
_COMMON_RESULT_COLS = [
    Col("climate_tag", "STRING", "result", "GREEN | NEUTRAL | BROWN | AMBIGUOUS"),
    Col("confidence", "STRING", "result", "HIGH | MEDIUM | LOW"),
    Col("tag_method", "STRING", "result", "Which rule decided, e.g. RIO_MARKER, RIO_PREDETERMINED, RIO_EXCLUSION"),
    Col("environmental_objective", "STRING", "result", "mitigation | adaptation | ... | none"),
    Col("subcategory", "STRING", "result", "principal | significant | taxonomy_aligned | NULL"),
    Col("rationale", "STRING", "result", "Plain-language reason, including the matched keyword"),
]
_RIO_RESULT_COLS = [
    Col("rio_marker", "INT", "result", "Combined Rio Marker: 0 none, 1 significant, 2 principal"),
    Col("rio_mitigation", "INT", "result", "Rio Marker for mitigation (0/1/2)"),
    Col("rio_adaptation", "INT", "result", "Rio Marker for adaptation (0/1/2)"),
]
EU_OBJECTIVES = ["mitigation", "adaptation", "water", "circular_economy", "pollution", "biodiversity"]
EU_FLAG_COLUMNS = {k: "eu_" + k for k in EU_OBJECTIVES}
_EU_RESULT_COLS = [
    Col(EU_FLAG_COLUMNS[k], "BOOLEAN", "result", "EU environmental objective '%s' is served" % k) for k in EU_OBJECTIVES
] + [Col("eu_primary_objective", "STRING", "result", "First objective that matched (or 'none')")]
# --- who / when / how --------------------------------------------------------------------------------
_META_COLS = [
    Col("currency", "STRING", "meta", "Currency code or label entered by the analyst"),
    Col("data_source", "STRING", "meta", "Where the budget data came from (free text)"),
    Col("notes", "STRING", "meta", "Analyst notes for this upload"),
]
_AUDIT_COLS = [
    Col("source_file", "STRING", "audit", "File name as uploaded"),
    Col("source_row", "INT", "audit", "1-based row number in the data sheet (header = row 1)"),
    Col("content_hash", "STRING", "audit", "Fingerprint of the stored values for this (country, year, batch)"),
    Col("methodology_version", "STRING", "audit", "Version of the keyword lists / rules"),
    Col("uploaded_at", "TIMESTAMP", "audit", "UTC time the batch was saved"),
    Col("deactivated_at", "TIMESTAMP", "audit", "UTC time validity became FALSE (NULL = never active or still active)"),
    Col("deactivation_reason", "STRING", "audit", "replaced | deleted | discarded"),
    Col("replaced_by", "STRING", "audit", "batch_id of the version that replaced this one (reason = replaced)"),
]

TABLE_SCHEMAS = {
    KIND_RIO: _KEY_COLS + _LINE_COLS + _COMMON_RESULT_COLS + _RIO_RESULT_COLS + _META_COLS + _AUDIT_COLS,
    KIND_EU: _KEY_COLS + _LINE_COLS + _COMMON_RESULT_COLS + _EU_RESULT_COLS + _META_COLS + _AUDIT_COLS,
}
INT_COLUMNS = {kind: [c.name for c in cols if c.sql == "INT"] for kind, cols in TABLE_SCHEMAS.items()}
BOOL_COLUMNS = {kind: [c.name for c in cols if c.sql == "BOOLEAN"] for kind, cols in TABLE_SCHEMAS.items()}
TS_COLUMNS = ["uploaded_at", "deactivated_at"]

REASON_REPLACED = "replaced"
REASON_DELETED = "deleted"
REASON_DISCARDED = "discarded"

# ══════════════════════════════════════════════════════════════════════════
# Tagging vocabulary
# ══════════════════════════════════════════════════════════════════════════

RIO_WEIGHTS = {0: 0.0, 1: 0.40, 2: 1.0}      # share of a line's amount counted as climate-relevant
RIO_LABELS = {0: "Not targeted", 1: "Significant", 2: "Principal"}
RIO_SHORT = {0: "0", 1: "1", 2: "2"}
RIO_SQL_WEIGHT = ("CASE rio_marker WHEN 2 THEN 1.0 WHEN 1 THEN 0.4 ELSE 0.0 END")
RIO_TARGET_SHARE = 0.10                       # headline target used in the original Streamlit app

EU_LABELS = {
    "mitigation": "Climate change mitigation",
    "adaptation": "Climate change adaptation",
    "water": "Water and marine resources",
    "circular_economy": "Circular economy",
    "pollution": "Pollution prevention",
    "biodiversity": "Biodiversity and ecosystems",
}

METHOD_RIO = "rio"
METHOD_EU = "eu"
METHOD_BOTH = "both"
METHOD_OPTIONS = [
    {"label": "Rio Markers and EU Taxonomy", "value": METHOD_BOTH},
    {"label": "Rio Markers only", "value": METHOD_RIO},
    {"label": "EU Taxonomy only", "value": METHOD_EU},
]

# ══════════════════════════════════════════════════════════════════════════
# The upload: standard fields the tagger understands
# ══════════════════════════════════════════════════════════════════════════
# role: how the engine uses the field.  text = matched against keyword lists,
#       cofog = looked up in the COFOG mapping, context = stored but not keyword-matched,
#       amount / year = numeric.

StdField = namedtuple("StdField", "key label role hint")
STD_FIELDS = [
    StdField("prog1", "Programme / project title", "text", "Matched against the keyword lists"),
    StdField("prog2", "Programme description / sub-programme", "text", "Matched against the keyword lists"),
    StdField("admin1", "Ministry / sector (admin level 1)", "text", "Matched against the keyword lists"),
    StdField("func1", "Function level 1 (COFOG)", "cofog", "COFOG code or name; drives the pre-determined tags"),
    StdField("func2", "Function level 2", "context", "Stored with the line; not keyword-matched"),
    StdField("func3", "Function level 3", "context", ""),
    StdField("func4", "Function level 4", "context", ""),
    StdField("econ1", "Economic classification level 1", "context", "Stored with the line; not keyword-matched"),
    StdField("econ2", "Economic classification level 2", "context", ""),
    StdField("econ3", "Economic classification level 3", "context", ""),
    StdField("econ4", "Economic classification level 4", "context", ""),
    StdField("admin2", "Department (admin level 2)", "context", ""),
    StdField("admin3", "Unit (admin level 3)", "context", ""),
    StdField("amount", "Amount", "amount", "Needed for amount-weighted shares"),
    StdField("year", "Year", "year", "Optional: lets one file hold several years"),
]
STD_FIELD_KEYS = [f.key for f in STD_FIELDS]
TEXT_MATCH_FIELDS = ["prog1", "prog2", "admin1"]
YEAR_MIN, YEAR_MAX = 1990, 2100
PREVIEW_ROWS = 8
RESULT_TABLE_PAGE_SIZE = 15

# ══════════════════════════════════════════════════════════════════════════
# Design tokens (mirrors the CBD app / pim-pam.net: assets/style.css holds the CSS)
# ══════════════════════════════════════════════════════════════════════════

NAVY = "#0F3B2A"                # dark green (the name is kept so the CSS tokens and Python stay in step)
NAVY_DEEP = "#0A2C1F"
INDIGO = "#1B6B3A"              # brand green for controls and single-series bars
TEAL = "#8ED1A3"
ORANGE = "#F2B33D"
INK = "#14291F"
INK_SOFT = "#42564C"
INK_MUTE = "#637569"
LINE = "#D3DDD6"
NO_DATA_COLOR = "#C5CCC6"
FONT_STACK = "'Fira Sans', 'Segoe UI', Arial, sans-serif"

# Chart colours follow the dataviz rules (validated with the dataviz skill's validate_palette.js on a white surface):
#   * Green and brown carry MEANING and are used for nothing else. The pair stays distinct for colour-blind readers
#     (deuteranopia dE 14.6, normal vision dE 24.8); every pie and stacked bar also carries direct text labels.
#   * Rio levels are ORDINAL - one green hue, light -> dark; "not targeted" recedes as neutral grey.
#   * Countries on the trend chart take the validated categorical order (fixed order, never cycled).
GREEN_COLOR = "#2E9E5B"
BROWN_COLOR = "#8B4513"
AMBIGUOUS_COLOR = "#D9A100"
NEUTRAL_COLOR = NO_DATA_COLOR
TAG_COLORS = {"GREEN": GREEN_COLOR, "BROWN": BROWN_COLOR, "AMBIGUOUS": AMBIGUOUS_COLOR, "NEUTRAL": NEUTRAL_COLOR}
TAG_LABELS = {"GREEN": "Green", "BROWN": "Brown", "AMBIGUOUS": "Unclear", "NEUTRAL": "Neutral"}
RIO_COLORS = {0: NO_DATA_COLOR, 1: "#4FAE6F", 2: "#1B6B3A"}
CATEGORICAL = ["#2A78D6", "#EB6834", "#1BAF7A", "#EDA100", "#E87BA4", "#008300", "#6250D6", "#E34948"]
SEQUENTIAL = ["#EAF6EE", "#C3E6CD", "#93D0A6", "#5DB57D", "#2E9E5B", "#1B6B3A", "#0E4526"]   # green 100 -> 700
MAX_COMPARE_COUNTRIES = 6

# ══════════════════════════════════════════════════════════════════════════
# Countries (World Bank naming, ISO 3166-1 alpha-3)
# ══════════════════════════════════════════════════════════════════════════

COUNTRIES = [
    ("Afghanistan", "AFG"), ("Albania", "ALB"), ("Algeria", "DZA"), ("American Samoa", "ASM"), ("Andorra", "AND"),
    ("Angola", "AGO"), ("Antigua and Barbuda", "ATG"), ("Argentina", "ARG"), ("Armenia", "ARM"), ("Aruba", "ABW"),
    ("Australia", "AUS"), ("Austria", "AUT"), ("Azerbaijan", "AZE"), ("Bahamas, The", "BHS"), ("Bahrain", "BHR"),
    ("Bangladesh", "BGD"), ("Barbados", "BRB"), ("Belarus", "BLR"), ("Belgium", "BEL"), ("Belize", "BLZ"),
    ("Benin", "BEN"), ("Bermuda", "BMU"), ("Bhutan", "BTN"), ("Bolivia", "BOL"), ("Bosnia and Herzegovina", "BIH"),
    ("Botswana", "BWA"), ("Brazil", "BRA"), ("British Virgin Islands", "VGB"), ("Brunei Darussalam", "BRN"),
    ("Bulgaria", "BGR"), ("Burkina Faso", "BFA"), ("Burundi", "BDI"), ("Cabo Verde", "CPV"), ("Cambodia", "KHM"),
    ("Cameroon", "CMR"), ("Canada", "CAN"), ("Cayman Islands", "CYM"), ("Central African Republic", "CAF"),
    ("Chad", "TCD"), ("Chile", "CHL"), ("China", "CHN"), ("Colombia", "COL"), ("Comoros", "COM"),
    ("Congo, Dem. Rep.", "COD"), ("Congo, Rep.", "COG"), ("Costa Rica", "CRI"), ("Côte d'Ivoire", "CIV"),
    ("Croatia", "HRV"), ("Cuba", "CUB"), ("Curaçao", "CUW"), ("Cyprus", "CYP"), ("Czechia", "CZE"),
    ("Denmark", "DNK"), ("Djibouti", "DJI"), ("Dominica", "DMA"), ("Dominican Republic", "DOM"), ("Ecuador", "ECU"),
    ("Egypt, Arab Rep.", "EGY"), ("El Salvador", "SLV"), ("Equatorial Guinea", "GNQ"), ("Eritrea", "ERI"),
    ("Estonia", "EST"), ("Eswatini", "SWZ"), ("Ethiopia", "ETH"), ("Faroe Islands", "FRO"), ("Fiji", "FJI"),
    ("Finland", "FIN"), ("France", "FRA"), ("French Polynesia", "PYF"), ("Gabon", "GAB"), ("Gambia, The", "GMB"),
    ("Georgia", "GEO"), ("Germany", "DEU"), ("Ghana", "GHA"), ("Gibraltar", "GIB"), ("Greece", "GRC"),
    ("Greenland", "GRL"), ("Grenada", "GRD"), ("Guam", "GUM"), ("Guatemala", "GTM"), ("Guinea", "GIN"),
    ("Guinea-Bissau", "GNB"), ("Guyana", "GUY"), ("Haiti", "HTI"), ("Honduras", "HND"),
    ("Hong Kong SAR, China", "HKG"), ("Hungary", "HUN"), ("Iceland", "ISL"), ("India", "IND"), ("Indonesia", "IDN"),
    ("Iran, Islamic Rep.", "IRN"), ("Iraq", "IRQ"), ("Ireland", "IRL"), ("Isle of Man", "IMN"), ("Israel", "ISR"),
    ("Italy", "ITA"), ("Jamaica", "JAM"), ("Japan", "JPN"), ("Jordan", "JOR"), ("Kazakhstan", "KAZ"),
    ("Kenya", "KEN"), ("Kiribati", "KIR"), ("Korea, Dem. People's Rep.", "PRK"), ("Korea, Rep.", "KOR"),
    ("Kosovo", "XKX"), ("Kuwait", "KWT"), ("Kyrgyz Republic", "KGZ"), ("Lao PDR", "LAO"), ("Latvia", "LVA"),
    ("Lebanon", "LBN"), ("Lesotho", "LSO"), ("Liberia", "LBR"), ("Libya", "LBY"), ("Liechtenstein", "LIE"),
    ("Lithuania", "LTU"), ("Luxembourg", "LUX"), ("Macao SAR, China", "MAC"), ("Madagascar", "MDG"),
    ("Malawi", "MWI"), ("Malaysia", "MYS"), ("Maldives", "MDV"), ("Mali", "MLI"), ("Malta", "MLT"),
    ("Marshall Islands", "MHL"), ("Mauritania", "MRT"), ("Mauritius", "MUS"), ("Mexico", "MEX"),
    ("Micronesia, Fed. Sts.", "FSM"), ("Moldova", "MDA"), ("Monaco", "MCO"), ("Mongolia", "MNG"),
    ("Montenegro", "MNE"), ("Morocco", "MAR"), ("Mozambique", "MOZ"), ("Myanmar", "MMR"), ("Namibia", "NAM"),
    ("Nauru", "NRU"), ("Nepal", "NPL"), ("Netherlands", "NLD"), ("New Caledonia", "NCL"), ("New Zealand", "NZL"),
    ("Nicaragua", "NIC"), ("Niger", "NER"), ("Nigeria", "NGA"), ("North Macedonia", "MKD"),
    ("Northern Mariana Islands", "MNP"), ("Norway", "NOR"), ("Oman", "OMN"), ("Pakistan", "PAK"), ("Palau", "PLW"),
    ("Panama", "PAN"), ("Papua New Guinea", "PNG"), ("Paraguay", "PRY"), ("Peru", "PER"), ("Philippines", "PHL"),
    ("Poland", "POL"), ("Portugal", "PRT"), ("Puerto Rico", "PRI"), ("Qatar", "QAT"), ("Romania", "ROU"),
    ("Russian Federation", "RUS"), ("Rwanda", "RWA"), ("Samoa", "WSM"), ("San Marino", "SMR"),
    ("São Tomé and Príncipe", "STP"), ("Saudi Arabia", "SAU"), ("Senegal", "SEN"), ("Serbia", "SRB"),
    ("Seychelles", "SYC"), ("Sierra Leone", "SLE"), ("Singapore", "SGP"), ("Sint Maarten (Dutch part)", "SXM"),
    ("Slovak Republic", "SVK"), ("Slovenia", "SVN"), ("Solomon Islands", "SLB"), ("Somalia", "SOM"),
    ("South Africa", "ZAF"), ("South Sudan", "SSD"), ("Spain", "ESP"), ("Sri Lanka", "LKA"),
    ("St. Kitts and Nevis", "KNA"), ("St. Lucia", "LCA"), ("St. Martin (French part)", "MAF"),
    ("St. Vincent and the Grenadines", "VCT"), ("Sudan", "SDN"), ("Suriname", "SUR"), ("Sweden", "SWE"),
    ("Switzerland", "CHE"), ("Syrian Arab Republic", "SYR"), ("Tajikistan", "TJK"), ("Tanzania", "TZA"),
    ("Thailand", "THA"), ("Timor-Leste", "TLS"), ("Togo", "TGO"), ("Tonga", "TON"), ("Trinidad and Tobago", "TTO"),
    ("Tunisia", "TUN"), ("Türkiye", "TUR"), ("Turkmenistan", "TKM"), ("Turks and Caicos Islands", "TCA"),
    ("Tuvalu", "TUV"), ("Uganda", "UGA"), ("Ukraine", "UKR"), ("United Arab Emirates", "ARE"),
    ("United Kingdom", "GBR"), ("United States", "USA"), ("Uruguay", "URY"), ("Uzbekistan", "UZB"),
    ("Vanuatu", "VUT"), ("Venezuela, RB", "VEN"), ("Viet Nam", "VNM"), ("Virgin Islands (U.S.)", "VIR"),
    ("West Bank and Gaza", "PSE"), ("Yemen, Rep.", "YEM"), ("Zambia", "ZMB"), ("Zimbabwe", "ZWE"),
]
COUNTRY_BY_ISO3 = {iso: name for name, iso in COUNTRIES}
COUNTRY_OPTIONS = [{"label": "%s (%s)" % (name, iso), "value": iso} for name, iso in COUNTRIES]

# ══════════════════════════════════════════════════════════════════════════
# Small helpers that only depend on the constants above
# ══════════════════════════════════════════════════════════════════════════




def column_names(kind: str):
    """Ordered list of column names of the table for `kind` ('rio' | 'eu')."""
    return [c.name for c in TABLE_SCHEMAS[kind]]


def sql_type_map(kind: str):
    return {c.name: c.sql for c in TABLE_SCHEMAS[kind]}
