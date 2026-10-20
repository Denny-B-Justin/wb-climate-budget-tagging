# Climate Budget Tagging Tool

A Dash web app, part of the PIM-PAM suite, that tags the lines of a government budget with the **Rio Markers** and the **EU Taxonomy**
objectives, lets the analyst review the result, and saves it to two **global Unity Catalog tables**.

* **Tag a budget** – enter metadata (country, year, source, who prepared it), upload an Excel/CSV file, match columns, run the tagging, review, save.
* **Dashboard** – cross-country view of the *current* data (`validity = TRUE`).
* **Data in Unity Catalog** – every saved version; view lines, download CSV, soft-delete, restore, discard interrupted saves.
* **Methodologies** – the full written methodology (decision cascade, indicators, keyword lists, COFOG table, storage rules, limits). Worked examples
  on that page are produced live by the same tagger code.

There is **no LLM in this app**: tagging is rule-based and deterministic, and nothing is sent to an external service.
The tagging engine is the original code, **copied without any change** to the project root:
`green_budget_tagger.py`, `green_budget_tagger_optimized.py`, `rio_marker_tagger.py`, `eu_taxonomy_tagger.py` and `shared/`
(without the Streamlit-only modules). Everything else lives in `src/` and the Dash layer.

```
app.py            Dash object, page frame, routing (?page=tag|dashboard|data|methodologies)
callbacks.py      all callbacks
pages.py          layouts            methodology.py   the Methodologies page
src/constants.py  names, table schemas, colours, country list, environment-variable names
src/utils.py      formatting, charts, reusable components, Excel export
src/queries.py    ALL storage access (Unity Catalog via Databricks SQL; SQLite for development and tests)
src/pipeline.py   reading uploads, cleaning, running the taggers, background jobs
assets/           style.css, self-hosted Fira Sans (SIL OFL), logo, favicon
sql/              create_tables.sql (generated)      scripts/   print_ddl.py, write_manifest.py
tests/            pytest suite
```

## Run it

```bash
pip install -r requirements.txt
python app.py                  # http://127.0.0.1:8050  (set PORT to change)
```

With no `DATABRICKS_*` variables the app uses a **local SQLite demo store** and shows an amber *Demo storage* badge in the footer.
Copy `.env.example` to `.env` and fill it in to write to Unity Catalog. A sample file is in `sample_data/sample_input_budget.xlsx`.

## Storage model (answer to "one table per country?")

Two **global** tables – `cbt_global_rio_markers` and `cbt_global_eu_taxonomy` – each with `country`, `country_iso3`, `year`, `validity` (BOOLEAN)
and `batch_id`. One row per budget line, with the line as uploaded, the tags, the rule that decided each tag and an audit trail.
Per-country tables would need dynamic DDL and grants, would make cross-country queries a `UNION` of N tables, and would give no benefit:
`country_iso3` + `year` already identify a slice.

| Situation on save | What happens |
|---|---|
| New country or year | Rows are appended with `validity = TRUE`. |
| Same country + year, **identical** values | Nothing is written; the user is told. |
| Same country + year, **different** values | The user must confirm. Old rows become `validity = FALSE`, new rows `TRUE`. |

The switch is safe by construction: new rows are first written with `validity = FALSE` and counted; only if the count equals the file does **one
Delta `UPDATE`** flip old → FALSE and new → TRUE. A crash halfway leaves the previous data valid (the half-written batch shows as *staged* and can be
discarded). Nothing is ever hard-deleted; soft-delete and restore only flip the flag and record who/when/why (`deactivated_*`, `replaced_by`).
Concurrent saves of the same country-year end with exactly one valid version (the Data page warns if that invariant is ever broken).

Always filter on validity when querying:

```sql
SELECT country_iso3, year,
       SUM(amount * CASE rio_marker WHEN 2 THEN 1.0 WHEN 1 THEN 0.4 ELSE 0 END) / SUM(amount) AS rio_weighted_share
FROM <catalog>.<schema>.cbt_global_rio_markers
WHERE validity = TRUE
GROUP BY country_iso3, year;
```

## Configuration (environment variables)

| Variable | Meaning |
|---|---|
| `DATABRICKS_SERVER_HOSTNAME` (or `DATABRICKS_HOST`), `DATABRICKS_HTTP_PATH`, `DATABRICKS_CLIENT_ID`, `DATABRICKS_CLIENT_SECRET` | Same names as the Country Benchmarking Dashboard, so one service principal serves both. `DATABRICKS_TOKEN` works for local development. |
| `CBT_CATALOG`, `CBT_SCHEMA` | Where the two tables live (no defaults in code; `.env.example` shows `prd_mega` / `sgpbpi163`). |
| `CBT_BACKEND` | `databricks` or `sqlite`. Unset: Databricks if any `DATABRICKS_*` variable is set (then all required ones must be), else SQLite. |
| `CBT_AUTO_CREATE_TABLES` | `true` (default) creates missing tables and adds missing columns at start-up. |
| `CBT_INSERT_MODE`, `CBT_INSERT_CHUNK_ROWS` | `json` (default; one parameter per statement, thousands of rows each) or `values` (portable multi-row VALUES). |
| `CBT_ADMIN_USERS` | Comma-separated e-mails allowed to soft-delete/restore. Empty = every signed-in user. The user is read from `X-Forwarded-Email` / Posit Connect credentials headers. |
| `CBT_MAX_UPLOAD_MB`, `CBT_MAX_ROWS`, `CBT_WORK_DIR` | Upload limits and where background-job files live. |
| `QUERY_CACHE_TTL_SECONDS`, `QUERY_CACHE_MAX_ENTRIES` | Read cache for the Dashboard/Data pages (same names as the CBD app). |

### Unity Catalog permissions

The app's principal needs `USE CATALOG`, `USE SCHEMA`, and either `CREATE TABLE` on the schema (the app creates the tables) **or** the tables
created once by an admin – `python scripts/print_ddl.py --catalog <c> --schema <s> > sql/create_tables.sql` – plus `SELECT` and `MODIFY` on them.
`CAN USE` on the SQL warehouse is needed as well.

## Deploy

* **Posit Connect** – `python scripts/write_manifest.py` (or `rsconnect write-manifest dash`), deploy, set the variables above on the content item.
  The code is Python 3.9 compatible (the version the CBD app uses).
* **Databricks Apps** – `app.yaml` is included; add the SQL warehouse as an app resource. The app listens on `DATABRICKS_APP_PORT`.
* Background tagging/saving runs in threads inside the web process and keeps its state in `CBT_WORK_DIR`; use **one worker process** (or sticky
  sessions) so a browser can find its job again after a reload.

## Tests

```bash
pip install -r requirements-dev.txt
pytest -q          # 68 tests: storage (soft-delete, restore, row-count guard, SQL text), pipeline, wiring of every callback id
```

Verified for this delivery: the full suite on Python 3.13 / Dash 4.4 / pandas 3 **and** Python 3.9 / Dash 3.4 / pandas 2.2; a headless-browser run of the
whole flow on both stacks; every Databricks statement the app produces parses with Spark 4.2's SQL parser, and the `json` and `values` INSERT
statements were executed in local Spark and read back (quotes, unicode, newlines, NULLs, timestamps round-trip). **Not tested here:** a live
Databricks warehouse – in particular the Delta `UPDATE` that flips `validity` and the service-principal sign-in. Do a first save with a throw-away country
in a dev schema before using real data.

## Known limits (also on the Methodologies page)

* Keyword rules are English-only, whole-word and **not stemmed** (`renewable` matches, `renewables` does not); non-English budget text is tagged neutral.
* Only COFOG divisions 01, 02, 03, 05, 07, 09 and 10 have pre-determined tags; the rest go through the keyword rules.
* The "EU Taxonomy" result is objective relevance of the wording, not a technical-screening-criteria assessment.
* Dash 4 prints a deprecation notice for `dash_table.DataTable`; it still works. Moving to Dash AG Grid is a contained change in `src/utils.py::table`.
