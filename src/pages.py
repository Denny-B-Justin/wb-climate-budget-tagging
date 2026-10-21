"""
pages.py
--------
Layout builders for the four pages. Layouts only describe structure and ids; behaviour lives in callbacks.py.

    ?page=home           Overview: a short introduction and the Launch Tool button
    ?page=tag            Tag a budget: metadata -> upload -> run -> review -> save to the dashboard
    ?page=dashboard      Cross-country view of the *latest saved* data
    ?page=data           Versions in the tables; soft-delete / restore / download
    ?page=methodologies  How the tagging works

Functions that build result fragments (results_content, save_status_content ...) are shared by the page
layouts (state restored after a reload) and by the polling callbacks.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import pandas as pd
from dash import dcc, html

from src import constants as C
from src import methodology
from src import pipeline as P
from src import queries as q
from src import utils as u

GRAPH_CONFIG = {"displayModeBar": False, "responsive": True}


def graph(fig: Any) -> dcc.Graph:
    """A Graph whose container has the figure's own height. Without it the container collapses inside an auto-height
    parent (Plotly's `responsive` mode sizes the plot to the container, and the container to the plot)."""
    h = getattr(fig.layout, "height", None) or 320
    return dcc.Graph(figure=fig, config=GRAPH_CONFIG, style={"height": "%dpx" % int(h), "width": "100%"})


# ══════════════════════════════════════════════════════════════════════════
# Overview (landing page)
# ══════════════════════════════════════════════════════════════════════════

def home_layout(map_url: str) -> html.Section:
    return html.Section(className="hero", style={"--hero-map": "url(%s)" % map_url}, children=html.Div(className="hero-inner", children=[
        html.P("CBT", className="eyebrow"),
        html.H1("Climate Budget Tagging Tool", className="hero-title"),
        # html.H1("Find the climate share of a government budget", className="hero-title"),
        html.P("The Climate Budget Tagging Tool tags every budget line against the Rio Markers and the EU Taxonomy. "
               "Upload a budget, review the tags, and compare countries side by side.", className="hero-lede"),
        html.Div(className="hero-actions", children=[
            dcc.Link("Launch Tool", href="?page=%s" % C.PAGE_TAG, className="btn btn-primary btn-lg"),
            dcc.Link("Dashboard", href="?page=%s" % C.PAGE_DASHBOARD, className="btn btn-teal btn-lg"),
            html.A("How it works", href="?page=%s" % C.PAGE_METHODOLOGY, target="_blank", rel="noopener", className="btn btn-ghost btn-lg"),
        ]),
    ]))


# ══════════════════════════════════════════════════════════════════════════
# Tag page
# ══════════════════════════════════════════════════════════════════════════

def metadata_card() -> html.Div:
    return u.step_card(1, "About the budget", "This identifies the data in the global tables: country and year decide what is replaced.", [
        html.Div(className="form-grid", children=[
            u.field("Country", dcc.Dropdown(id="meta-country", options=C.COUNTRY_OPTIONS, placeholder="Search a country…",
                                            searchable=True, clearable=True, className="select",
                                            persistence=True, persistence_type="session"), required=True),
            u.field("Currency", dcc.Input(id="meta-currency", type="text", maxLength=30, className="input",
                                          placeholder="e.g. ZMW or 'USD millions'", persistence=True, persistence_type="session"),
                    "Free text; shares do not depend on it."),
            u.field("Data source", dcc.Input(id="meta-source", type="text", maxLength=300, className="input",
                                             placeholder="e.g. BOOST, approved budget 2024", persistence=True, persistence_type="session")),
        ]),
        html.Div(className="form-grid", children=[
            u.field("Which year are the lines?", dcc.RadioItems(
                id="meta-year-mode", value="column", className="radio-stack", persistence=True, persistence_type="session",
                options=[{"label": "Use the Year column in my file (one file can hold several years)", "value": "column"},
                         {"label": "One year for the whole file", "value": "single"}]), required=True),
            html.Div(id="meta-year-wrap", style={"display": "none"}, children=u.field(
                "Year", dcc.Input(id="meta-year", type="number", min=C.YEAR_MIN, max=C.YEAR_MAX, step=1, className="input input-narrow",
                                  placeholder="2024", debounce=True, persistence=True, persistence_type="session"), required=True)),
        ]),
        u.field("Notes", dcc.Textarea(id="meta-notes", className="input textarea", maxLength=1000, rows=2,
                                      placeholder="Anything a colleague should know about this file (optional)",
                                      persistence=True, persistence_type="session")),
    ])


def upload_card(upload: Optional[Dict[str, Any]]) -> html.Div:
    return u.step_card(2, "Upload the budget file", "Excel (.xlsx) or CSV with one row per budget line. Columns are matched automatically; you can correct them.", [
        dcc.Upload(id="upload", multiple=False, max_size=P.max_upload_bytes(), accept=".xlsx,.xlsm,.csv,.tsv,.txt",
                   className="dropzone", className_active="dropzone is-active", children=html.Div(className="dropzone-inner", children=[
                       html.Strong("Drop a file here"), html.Span(" or "), html.Span("browse", className="link"),
                       html.Div("Up to %d MB · .xlsx, .csv" % (P.max_upload_bytes() // (1024 * 1024)), className="dropzone-hint")])),
        dcc.Loading(type="circle", color=C.INDIGO, children=html.Div(id="upload-panel", children=upload_panel(upload))),
    ], sid="step-upload")


def upload_panel(upload: Optional[Dict[str, Any]], error: Optional[str] = None) -> Any:
    """Everything shown after a file arrived: file facts, sheet / header row, preview and the column mapping."""
    if error:
        return u.notice("error", error, "That file cannot be used")
    if not upload:
        return html.Div(className="muted-note", children="No file yet.")
    cols = upload["columns"]
    options = [{"label": c, "value": c} for c in cols]
    mapping = upload.get("mapping", {})
    prev = pd.DataFrame(upload["preview"])

    def map_field(f: C.StdField) -> html.Div:
        return u.field(f.label, dcc.Dropdown(id={"type": "map", "field": f.key}, options=options, value=mapping.get(f.key),
                                            placeholder="- not in my file -", clearable=True, searchable=True, className="select"),
                       f.hint or None, cls="map-field")

    primary = [f for f in C.STD_FIELDS if f.key in ("prog1", "prog2", "admin1", "func1", "amount", "year")]
    rest = [f for f in C.STD_FIELDS if f not in primary]
    sheet_controls = []
    if upload.get("sheets"):
        sheet_controls.append(u.field("Sheet", dcc.Dropdown(id="up-sheet", options=[{"label": s, "value": s} for s in upload["sheets"]],
                                                            value=upload.get("sheet"), clearable=False, className="select")))
    else:
        sheet_controls.append(dcc.Dropdown(id="up-sheet", options=[], value=None, style={"display": "none"}))
    sheet_controls.append(u.field("Header row", dcc.Input(id="up-header", type="number", min=1, max=100, step=1, debounce=True,
                                                         value=upload.get("header_row", 1), className="input input-narrow"),
                                  "Row that holds the column names."))
    return html.Div(className="upload-panel", children=[
        html.Div(className="file-chip", children=[
            html.Strong(upload["filename"]), u.chip("%s" % upload["suffix"].lstrip(".").upper()),
            html.Span("%d columns" % len(cols), className="muted")]),
        *[u.notice("info", n) for n in upload.get("notes", [])],
        html.Div(className="form-grid form-grid-3", children=sheet_controls),
        html.Div(className="preview", children=[
            html.Div("Preview - first %d rows" % len(prev), className="subhead"),
            u.table(prev, "preview-table", page_size=C.PREVIEW_ROWS, sortable=False)]),
        html.Div(className="subhead", children="Match your columns to the standard fields"),
        html.P("The rules read the three text fields (programme title, description, ministry); the function code switches on the COFOG "
               "pre-determined tags. At least one text field is needed.", className="muted-note"),
        html.Div(className="map-grid", children=[map_field(f) for f in primary]),
        html.Details(className="doc-details", children=[
            html.Summary("More classification fields (economic, function levels 2-4, admin 2-3)"),
            html.Div(className="map-grid", children=[map_field(f) for f in rest])]),
    ])


def options_card() -> html.Div:
    return u.step_card(3, "Choose what to run", "Both methodologies run on the same cleaned lines. Identical classifications are tagged once, "
                       "and the rules are deterministic: the same file always gives the same tags.", [
        u.field("Methodologies", dcc.RadioItems(id="opt-methods", options=C.METHOD_OPTIONS, value=C.METHOD_BOTH,
                                                className="radio-stack", persistence=True, persistence_type="session")),
        html.Div(className="actions", children=[
            html.Button("Run tagging", id="run-btn", n_clicks=0, className="btn btn-primary"),
            html.Button("Cancel", id="cancel-btn", n_clicks=0, className="btn btn-outline", style={"display": "none"}),
        ]),
        html.Div(id="run-msg", className="msg"),
    ], sid="step-run")


def tag_layout(job_id: Optional[str], upload: Optional[Dict[str, Any]]) -> html.Div:
    """Tag page. If a job already exists in this browser session its state (progress / results / save) is restored."""
    tag_status = P.read_status(job_id, "tag") if job_id else {"state": "idle"}
    save_status = P.read_status(job_id, "save") if job_id else {"state": "idle"}
    tag_state, save_state = P.effective_state(tag_status), P.effective_state(save_status)
    results = results_content(job_id, tag_status) if tag_state == "done" else None
    key = "%s:%s" % (job_id, tag_status.get("finished")) if tag_state == "done" else ""
    steps = [
        ("Describe", "country, year, source"), ("Upload", "and match columns"), ("Run", "the tagging rules"),
        ("Review", "numbers and rules"), ("Save", "to the dashboard")]
    body = [
        u.page_intro("Tag a government budget",
                     "Upload a budget, tag every line against the Rio Markers and the EU Taxonomy, review the result and save it to the "
                     "shared dashboard.", eyebrow="Climate budget tagging"),
        html.Section(className="section tight", children=html.Div(className="section-inner", children=[
            html.Ol(className="stepper", children=[html.Li([html.Span(str(i), className="step-n"), html.Div([html.Strong(a), html.Span(" " + b)])])
                                                    for i, (a, b) in enumerate(steps, start=1)]),
            html.Div(className="stack", children=[
                metadata_card(), upload_card(upload), options_card(),
                html.Div(id="tag-progress", children=progress_content(tag_status, tag_state)),
                html.Div(id="results-region", children=results),
                html.Div(id="save-region", children=save_status_content(save_status, save_state)),
            ]),
        ])),
        save_modal(),
        dcc.Interval(id="poll-tag", interval=1000, disabled=tag_state != "running"),
        dcc.Interval(id="poll-save", interval=1000, disabled=save_state != "running"),
        dcc.Store(id="tag-rendered", data=key),
        dcc.Store(id="save-replace", data=False),
        dcc.Download(id="download-xlsx"),
    ]
    return html.Div(body)


def progress_content(status: Dict[str, Any], state: Optional[str] = None) -> Any:
    state = state or P.effective_state(status)
    if state == "running":
        return u.step_card(3, "Tagging in progress", "Loading; it can take a while.", [
            u.progress_bar(int(status.get("done", 0)), int(status.get("total", 0)), status.get("phase") or "Working…")], cls="card-live")
    if state == "stalled":
        return u.notice("error", "The tagging job stopped reporting progress (the app may have restarted). Run it again.", "Job interrupted")
    if state == "error":
        return u.notice("error", status.get("error") or "Unknown error", "Tagging stopped")
    if state == "cancelled":
        return u.notice("warn", "The run was cancelled. Nothing was saved.", "Cancelled")
    return None


def _result_tables(job_id: str, which: str, limit: int = 2000) -> Any:
    res = P.get_object(job_id, "result.pkl")
    if not res or res.get(which) is None:
        return u.empty_state("No data", "This methodology was not run.")
    df = res[which].copy()
    df["_abs"] = df["amount"].abs().fillna(-1)
    df = df.sort_values("_abs", ascending=False).drop(columns="_abs")
    shown = df.head(limit)
    if which == C.KIND_RIO:
        cols = ["source_row", "year", "prog1", "prog2", "admin1", "func1", "amount", "rio_marker", "rio_mitigation", "rio_adaptation",
                "climate_tag", "tag_method", "rationale"]
    else:
        cols = ["source_row", "year", "prog1", "prog2", "admin1", "func1", "amount", "climate_tag", "eu_primary_objective"] + \
               [C.EU_FLAG_COLUMNS[k] for k in C.EU_OBJECTIVES] + ["tag_method", "rationale"]
    cols = [c for c in cols if c in shown.columns and (shown[c].notna().any() or c in ("source_row", "amount"))]
    note = "Showing the %s largest lines by amount%s." % (u.fmt_int(len(shown)), "" if len(df) <= limit else " of %s" % u.fmt_int(len(df)))
    return html.Div([
        html.Div(note + " Download the workbook for every line.", className="muted-note"),
        u.table(shown[cols], "result-table-%s" % which, filterable=True,
                columns=[{"name": c, "id": c, "type": "numeric", "format": {"specifier": ",.0f"}} if c == "amount" else {"name": c, "id": c}
                         for c in cols])])


def _tag_rest(summary: Dict[str, Any]) -> Dict[str, float]:
    """Unclear and neutral shares from summarize()'s per-tag totals."""
    by = summary.get("by_tag") or {}
    total = sum(by.values()) or 1.0
    return {t: by.get(t, 0.0) / total for t in ("AMBIGUOUS", "NEUTRAL")}


def results_content(job_id: Optional[str], status: Dict[str, Any]) -> Any:
    """Review step: headline numbers, charts per rule, the tagged lines, downloads and the save button."""
    if not job_id:
        return None
    res = P.get_object(job_id, "result.pkl")
    if not res:
        return u.notice("warn", "The tagged result is no longer on the server. Run the tagging again.", "Result expired")
    stats, meta, issues = res["stats"], res["meta"], res.get("issues", [])
    cur = (meta.currency or "").strip()
    head = html.Div(className="result-head", children=[
        html.Div([html.H3("%s · %s" % (meta.country, u.year_span(stats.get("years", []))), className="result-title"),
                  html.Div("%s lines · %s distinct classifications tagged · %.1f s" % (
                      u.fmt_int(stats["lines"]), u.fmt_int(stats["unique_classifications"]), stats.get("seconds", 0)),
                      className="muted")]),
        html.Button("Download workbook (.xlsx)", id="download-btn", n_clicks=0, className="btn btn-outline")])
    blocks: List[Any] = [head]
    blocks += [u.notice({"warn": "warn", "info": "info"}.get(i["level"], "info"), i["message"]) for i in issues]
    cols = []
    if res.get("rio") is not None:
        s = P.summarize(C.KIND_RIO, res["rio"])
        basis = "of amount" if s["basis"] == "amount" else "of lines"
        cols.append(html.Div(className="result-col", children=[
            html.H3("Rio Markers", className="card-title"),
            u.kpi_strip([
                u.kpi("Rio-weighted share", u.fmt_pct(s["weighted_share"]), "100% principal · 40% significant · " + basis, tone="primary"),
                u.kpi("Principal (2)", u.fmt_pct(s["principal_share"]), basis),
                u.kpi("Significant (1)", u.fmt_pct(s["significant_share"]), basis),
                u.kpi("Lines with a marker", u.fmt_int(s["tagged_lines"]), "of %s" % u.fmt_int(s["lines"]))]),
            html.Div("How the budget splits by Rio level", className="subhead"),
            graph(u.fig_rio_donut(s["principal_share"], s["significant_share"], s["basis"])),
            html.Div("What decided each classification", className="subhead"),
            graph(u.fig_methods(stats.get("rio_methods", {})))]))
    if res.get("eu") is not None:
        s = P.summarize(C.KIND_EU, res["eu"])
        basis = "of amount" if s["basis"] == "amount" else "of lines"
        cols.append(html.Div(className="result-col", children=[
            html.H3("EU Taxonomy", className="card-title"),
            u.kpi_strip([
                u.kpi("Green share", u.fmt_pct(s["green_share"]), "any objective · " + basis, tone="primary"),
                u.kpi("Brown-screen share", u.fmt_pct(s["brown_share"]), basis, tone="brown"),
                u.kpi("GREEN lines", u.fmt_int(s["tagged_lines"]), "of %s" % u.fmt_int(s["lines"]))]),
            html.Div("Green, brown and neutral spending", className="subhead"),
            graph(u.fig_tag_donut(s["green_share"], s["brown_share"], _tag_rest(s), s["basis"])),
            html.Div("Objectives served", className="subhead"),
            graph(u.fig_eu_objectives(s)),
            html.Div("What decided each classification", className="subhead"),
            graph(u.fig_methods(stats.get("eu_methods", {})))]))
    blocks.append(html.Div(className="result-cols", children=cols))
    tabs_default = C.KIND_RIO if res.get("rio") is not None else C.KIND_EU
    blocks.append(html.Div(className="subhead-row", children=[
        html.Div("Tagged lines", className="subhead"),
        dcc.RadioItems(id="table-which", value=tabs_default, className="segmented", inputClassName="seg-input", labelClassName="seg-label",
                       options=[{"label": C.KIND_LABELS[k], "value": k} for k in C.KINDS if res.get(k) is not None])]))
    blocks.append(html.Div(id="result-table-wrap", children=_result_tables(job_id, tabs_default)))
    blocks.append(html.Div(className="save-card", children=[
        html.Div([html.H3("Save to the dashboard", className="card-title"),
                  html.P(["Adds the results for ", html.Strong("%s" % meta.country), " (", u.year_span(stats.get("years", [])),
                          ") to the dashboard. If this country and year already exist you will be asked before anything is replaced."],
                         className="muted")]),
        html.Button("Save…", id="save-btn", n_clicks=0, className="btn btn-primary")]))
    return html.Div(className="results", children=blocks)


def save_modal() -> html.Div:
    return html.Div(id="save-modal", className="modal", hidden=True, children=[
        html.Div(className="modal-backdrop"),
        html.Div(className="modal-card", role="dialog", **{"aria-modal": "true", "aria-labelledby": "save-modal-title"}, children=[
            html.H3("Confirm save", id="save-modal-title", className="modal-title"),
            html.Div(id="save-modal-body", className="modal-body"),
            html.Div(className="modal-actions", children=[
                html.Button("Cancel", id="save-cancel", n_clicks=0, className="btn btn-outline"),
                html.Button("Save", id="save-confirm", n_clicks=0, className="btn btn-primary")])])])


def plan_table(plans: Dict[str, pd.DataFrame]) -> Any:
    """Rows: methodology x year, with what saving will do. Shown in the confirmation dialog."""
    labels = {q.ACTION_NEW: ("Add", "ok", "nothing saved for this country and year yet"),
              q.ACTION_SAME: ("Skip", "neutral", "the same results are already saved"),
              q.ACTION_CHANGED: ("Replace", "warn", "different results are already saved; the old version is kept as an earlier version")}
    rows = []
    for kind, plan in plans.items():
        for _, r in plan.iterrows():
            text, tone, why = labels[r["action"]]
            old = ""
            if r["action"] != q.ACTION_NEW:
                old = "Current: %s lines saved %s" % (u.fmt_int(r["old_lines"]), u.fmt_dt(r["old_uploaded_at"]))
            rows.append(html.Tr([html.Td(C.KIND_LABELS[kind]), html.Td(str(int(r["year"]))), html.Td(u.fmt_int(r["new_lines"])),
                                 html.Td(u.chip(text, tone)), html.Td([why, html.Div(old, className="muted")] if old else why)]))
    return html.Div(className="doc-table-wrap", children=html.Table(className="doc-table", children=[
        html.Thead(html.Tr([html.Th(h) for h in ("Method", "Year", "New lines", "Action", "Why")])), html.Tbody(rows)]))


def save_status_content(status: Dict[str, Any], state: Optional[str] = None) -> Any:
    state = state or P.effective_state(status)
    if state == "running":
        return u.step_card(5, "Saving", "The previous version stays in place until the new one is complete.", [
            u.progress_bar(int(status.get("done", 0)), int(status.get("total", 0)), status.get("phase") or "Working…")], cls="card-live")
    if state == "stalled":
        return u.notice("error", "The save stopped reporting progress. Check the dashboard, then try again.", "Save interrupted")
    if state == "error":
        return u.notice("error", status.get("error") or "Unknown error", "Saving failed")
    if state == "done":
        rep = status.get("report") or {}
        lines = []
        for r in rep.get("reports", []):
            bits = []
            if r["rows_written"]:
                bits.append("%s lines written (%s)" % (u.fmt_int(r["rows_written"]), u.year_span(r["years_added"] + r["years_replaced"])))
            if r["years_replaced"]:
                bits.append("replaced %s (the earlier version is kept)" % u.year_span(r["years_replaced"]))
            if r["years_unchanged"]:
                bits.append("unchanged, skipped: %s" % u.year_span(r["years_unchanged"]))
            lines.append(html.Li([html.Strong(C.KIND_LABELS[r["kind"]] + ": "), "; ".join(bits) or "nothing to write"]))
        return u.notice("ok", [html.Ul(lines, className="doc-list"),
                               html.Div([dcc.Link("See it on the dashboard", href="?page=%s" % C.PAGE_DASHBOARD)])], "Saved")
    return None


# ══════════════════════════════════════════════════════════════════════════
# Dashboard
# ══════════════════════════════════════════════════════════════════════════

def dashboard_layout() -> html.Div:
    return html.Div([
        u.page_intro("Dashboard", "Climate-relevant share of the budget, country by country - based on the latest saved version of each country.",
                     eyebrow="Cross-country view"),
        html.Section(className="section tight", children=html.Div(className="section-inner", children=[
            html.Div(className="filterbar", children=[
                u.field("Methodology", dcc.RadioItems(id="dash-kind", value=C.KIND_RIO, className="segmented", inputClassName="seg-input",
                                                    labelClassName="seg-label",
                                                    options=[{"label": C.KIND_LABELS[k], "value": k} for k in C.KINDS])),
                u.field("Measure", dcc.RadioItems(id="dash-basis", value="amount", className="segmented", inputClassName="seg-input",
                                                  labelClassName="seg-label",
                                                  options=[{"label": "By amount", "value": "amount"}, {"label": "By number of lines", "value": "lines"}])),
                u.field("Year", dcc.Dropdown(id="dash-year", options=[{"label": "Latest available per country", "value": "latest"}],
                                             value="latest", clearable=False, className="select")),
                u.field("Compare countries over time", dcc.Dropdown(id="dash-countries", options=[], value=[], multi=True,
                                                                     placeholder="Choose up to %d countries" % C.MAX_COMPARE_COUNTRIES,
                                                                     className="select select-wide")),
                html.Button("Refresh", id="dash-refresh", n_clicks=0, className="btn btn-outline"),
            ]),
            dcc.Loading(type="circle", color=C.INDIGO, children=html.Div(id="dash-body")),
        ])),
    ])


# ══════════════════════════════════════════════════════════════════════════
# Download data
# ══════════════════════════════════════════════════════════════════════════

def data_layout() -> html.Div:
    return html.Div([
        u.page_intro("Download data",
                     "Choose a method and the countries you need, then download the tagged budget lines as a CSV file that opens in Excel.",
                     eyebrow="Download"),
        html.Section(className="section tight", children=html.Div(className="section-inner", children=[
            html.Div(className="card download-card", children=[
                html.Div(className="download-form", children=[
                    u.field("Method", dcc.RadioItems(id="dl-kind", value=C.KIND_RIO, className="segmented", inputClassName="seg-input",
                                                     labelClassName="seg-label",
                                                     options=[{"label": C.KIND_LABELS[k], "value": k} for k in C.KINDS])),
                    u.field("Countries", dcc.Dropdown(id="dl-countries", options=[], value=[], multi=True,
                                                      placeholder="All countries", className="select select-wide"),
                            "Leave empty to download every country."),
                    u.field("Years", dcc.Dropdown(id="dl-years", options=[], value=[], multi=True,
                                                  placeholder="All years", className="select select-wide"),
                            "Leave empty to download every year."),
                ]),
                dcc.Loading(type="circle", color=C.INDIGO, children=html.Div(id="dl-summary")),
                html.Div(className="actions", children=[
                    html.Button("Download CSV", id="dl-btn", n_clicks=0, className="btn btn-primary btn-lg", disabled=True)]),
                html.Div(id="dl-msg"),
            ]),
        ])),
        dcc.Download(id="dl-file"),
    ])


def methodology_layout() -> html.Div:
    return methodology.layout()


def not_found_layout() -> html.Div:
    return html.Div(className="notfound", children=[
        html.H1("Page not found", className="page-title"),
        html.P("That page does not exist.", className="page-lede"),
        dcc.Link("Back to the overview", href="?page=%s" % C.DEFAULT_PAGE, className="btn btn-primary")])
