"""
callbacks.py
------------
All Dash callbacks. Imported by app.py after the app object exists.

Design notes
  * Long work (tagging, saving) runs in a background thread (src/pipeline.py); the browser only holds a
    session id (`job-store`) and polls small status files. Reloading the page restores progress or results.
  * Callbacks talk to each other through stores and ids, never through hidden globals.
  * Every storage error is caught and shown as a notice - a warehouse that is stopped or a missing grant
    must never produce a blank page.
"""

from __future__ import annotations

import base64
import binascii
import logging
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qs

import pandas as pd
from dash import ALL, Input, Output, State, callback, ctx, dcc, get_asset_url, html, no_update
from dash.exceptions import PreventUpdate
from flask import request

import pages
from src import constants as C
from src import pipeline as P
from src import queries as q
from src import utils as u

log = logging.getLogger(__name__)

SHOW = {"display": "inline-flex"}
HIDE = {"display": "none"}


def _page_key(search: Optional[str]) -> str:
    if not search:
        return C.DEFAULT_PAGE
    return parse_qs(search.lstrip("?")).get("page", [C.DEFAULT_PAGE])[0] or C.DEFAULT_PAGE


def _actor() -> str:
    try:
        return u.actor_from_headers(request.headers)
    except RuntimeError:                                   # outside a request (tests)
        return ""


def _job_id(job: Optional[Dict[str, Any]]) -> Optional[str]:
    jid = (job or {}).get("job_id")
    try:
        P.job_file(jid, "x")                               # validates the id shape
        return jid
    except P.PipelineError:
        return None


# ══════════════════════════════════════════════════════════════════════════
# Routing, title, storage badge
# ══════════════════════════════════════════════════════════════════════════

@callback(Output("page-content", "children"), Input("url", "search"),
          State("job-store", "data"), State("upload-store", "data"))
def render_page(search, job, upload):
    page = _page_key(search)
    logo = get_asset_url("The-World-Bank-group-white.png")
    if page == C.PAGE_TAG:
        P.cleanup_old_jobs()
        body = pages.tag_layout(_actor(), _job_id(job), upload if _job_id(job) else None)
    elif page == C.PAGE_DASHBOARD:
        body = pages.dashboard_layout()
    elif page == C.PAGE_DATA:
        body = pages.data_layout(_actor())
    elif page == C.PAGE_METHODOLOGY:
        body = pages.methodology_layout()
    else:
        return u.page_shell("", pages.not_found_layout(), logo)
    return u.page_shell(page, body, logo)


@callback(Output("storage-badge", "children"), Output("storage-badge", "className"),
          Input("badge-tick", "n_intervals"))
def storage_badge(_n):
    h = q.health()
    if not h["ok"]:
        return [u.icon("info"), " Storage unavailable - ", h.get("error", "")[:180]], "storage-badge is-bad"
    if h["demo"]:
        return [u.icon("info"), " Demo storage (local SQLite) - not Unity Catalog"], "storage-badge is-demo"
    return ["● ", h["label"]], "storage-badge is-ok"


# ══════════════════════════════════════════════════════════════════════════
# Tag page - step 1/2: metadata and upload
# ══════════════════════════════════════════════════════════════════════════

@callback(Output("meta-year-wrap", "style"), Input("meta-year-mode", "value"))
def toggle_year(mode):
    return {"display": "block"} if mode == "single" else {"display": "none"}


@callback(Output("upload-store", "data"), Output("job-store", "data"), Output("upload-panel", "children"),
          Output("tag-progress", "children", allow_duplicate=True), Output("results-region", "children", allow_duplicate=True),
          Output("save-region", "children", allow_duplicate=True), Output("tag-rendered", "data", allow_duplicate=True),
          Input("upload", "contents"), State("upload", "filename"), prevent_initial_call=True)
def on_upload(contents, filename):
    if not contents or not filename:
        raise PreventUpdate
    try:
        if not isinstance(contents, str) or "," not in contents:
            raise P.PipelineError("The upload could not be read.")
        raw = base64.b64decode(contents.split(",", 1)[1], validate=False)
        if len(raw) > P.max_upload_bytes():
            raise P.PipelineError("The file is larger than the %d MB limit." % (P.max_upload_bytes() // (1024 * 1024)))
        suffix = "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
        if suffix not in P.ALLOWED_SUFFIXES:
            raise P.PipelineError("Unsupported file type %r. Upload an Excel (.xlsx) or CSV file." % suffix)
        jid = P.new_job_id()                                # a new file starts a new session: old results are dropped
        path = P.job_file(jid, "upload" + suffix)
        path.write_bytes(raw)
        info = P.inspect_upload(path, filename)
        data = P.upload_to_store(info)
        return data, {"job_id": jid}, pages.upload_panel(data), None, None, None, ""
    except (P.PipelineError, binascii.Error) as exc:
        return None, no_update, pages.upload_panel(None, error=str(exc)), None, None, None, ""
    except Exception:                                       # pragma: no cover
        log.exception("Upload failed")
        return None, no_update, pages.upload_panel(None, error="The file could not be processed."), None, None, None, ""


@callback(Output("upload-store", "data", allow_duplicate=True), Output("upload-panel", "children", allow_duplicate=True),
          Input("up-sheet", "value"), Input("up-header", "value"),
          State("upload-store", "data"), State("job-store", "data"), prevent_initial_call=True)
def reinspect(sheet, header_row, upload, job):
    jid = _job_id(job)
    if not upload or not jid:
        raise PreventUpdate
    header_row = int(header_row or 1)
    if (sheet or None) == (upload.get("sheet") or None) and header_row == int(upload.get("header_row", 1)):
        raise PreventUpdate                                 # re-render of the panel itself, nothing changed
    try:
        info = P.inspect_upload(P.job_file(jid, "upload" + upload["suffix"]), upload["filename"], sheet, header_row)
    except P.PipelineError as exc:
        return no_update, u.notice("error", str(exc), "Cannot read this sheet / header row")
    data = P.upload_to_store(info)
    return data, pages.upload_panel(data)


# ══════════════════════════════════════════════════════════════════════════
# Tag page - step 3: run
# ══════════════════════════════════════════════════════════════════════════

@callback(
    Output("run-msg", "children"), Output("tag-progress", "children"), Output("results-region", "children"),
    Output("save-region", "children"), Output("poll-tag", "disabled"), Output("tag-rendered", "data"),
    Output("run-btn", "disabled"), Output("cancel-btn", "style"), Output("cancel-btn", "disabled", allow_duplicate=True),
    Input("run-btn", "n_clicks"),
    State("meta-country", "value"), State("meta-by", "value"), State("meta-currency", "value"), State("meta-source", "value"),
    State("meta-notes", "value"), State("meta-year-mode", "value"), State("meta-year", "value"),
    State("opt-methods", "value"),
    State({"type": "map", "field": ALL}, "value"), State({"type": "map", "field": ALL}, "id"),
    State("upload-store", "data"), State("job-store", "data"),
    prevent_initial_call=True)
def run_tagging(n, country, by, currency, source, notes, year_mode, year, methods, map_values, map_ids, upload, job):
    def stop(msg):
        return (u.notice("error", msg), None, None, None, True, "", False, HIDE, False)

    jid = _job_id(job)
    if not upload or not jid:
        return stop("Upload a budget file first (step 2).")
    meta = P.Metadata(country_iso3=country or "", year_mode=year_mode or "column", single_year=int(year) if year not in (None, "") else None,
                      currency=currency or "", data_source=source or "", notes=notes or "", uploaded_by=(by or "").strip(),
                      source_file=upload["filename"])
    errors = meta.errors()
    mapping = {i["field"]: v for i, v in zip(map_ids or [], map_values or []) if v}
    if not any(k in mapping for k in C.TEXT_MATCH_FIELDS):
        errors.append("Match at least one text column (programme title, description or ministry) in step 2.")
    if meta.year_mode == "column" and "year" not in mapping:
        errors.append("Match the Year column in step 2, or choose 'One year for the whole file' in step 1.")
    if errors:
        return stop(html.Ul([html.Li(e) for e in errors], className="doc-list"))
    settings = P.TaggingSettings(methods=methods or C.METHOD_BOTH)
    P.write_status(jid, "save", state="idle", report=None, error=None)
    P.write_status(jid, "tag", state="running", phase="Starting…", done=0, total=0, error=None, started=None, finished=None)
    P.start_thread(P.run_tag_job, jid, "upload" + upload["suffix"], upload["suffix"], upload.get("sheet"),
                   int(upload.get("header_row", 1)), mapping, meta, settings)
    return (None, pages.progress_content(P.read_status(jid, "tag"), "running"), None, None, False, "", True, SHOW, False)


@callback(
    Output("tag-progress", "children", allow_duplicate=True), Output("results-region", "children", allow_duplicate=True),
    Output("poll-tag", "disabled", allow_duplicate=True), Output("tag-rendered", "data", allow_duplicate=True),
    Output("run-btn", "disabled", allow_duplicate=True), Output("cancel-btn", "style", allow_duplicate=True),
    Input("poll-tag", "n_intervals"), State("job-store", "data"), State("tag-rendered", "data"), prevent_initial_call=True)
def poll_tag(_n, job, rendered):
    jid = _job_id(job)
    if not jid:
        raise PreventUpdate
    status = P.read_status(jid, "tag")
    state = P.effective_state(status)
    if state == "running":
        return pages.progress_content(status, state), no_update, False, no_update, True, SHOW
    if state == "done":
        key = "%s:%s" % (jid, status.get("finished"))
        results = no_update if key == rendered else pages.results_content(jid, status)
        return None, results, True, key, False, HIDE
    return pages.progress_content(status, state), None, True, "", False, HIDE


@callback(Output("cancel-btn", "disabled"), Input("cancel-btn", "n_clicks"), State("job-store", "data"), prevent_initial_call=True)
def cancel_tagging(n, job):
    jid = _job_id(job)
    if not jid or not n:
        raise PreventUpdate
    P.request_cancel(jid, "tag")
    return True


# ══════════════════════════════════════════════════════════════════════════
# Tag page - step 4: review
# ══════════════════════════════════════════════════════════════════════════

@callback(Output("result-table-wrap", "children"), Input("table-which", "value"), State("job-store", "data"), prevent_initial_call=True)
def switch_table(which, job):
    jid = _job_id(job)
    if not jid or which not in C.KINDS:
        raise PreventUpdate
    return pages._result_tables(jid, which)


@callback(Output("download-xlsx", "data"), Input("download-btn", "n_clicks"), State("job-store", "data"), prevent_initial_call=True)
def download_xlsx(n, job):
    jid = _job_id(job)
    res = P.get_object(jid, "result.pkl") if jid and n else None
    if not res:
        raise PreventUpdate
    meta = res["meta"]
    name = "cbt_%s_%s_tagged.xlsx" % (meta.country_iso3, "-".join(str(y) for y in (res["stats"].get("years") or [])[:3]) or "all")
    return dcc.send_bytes(u.export_workbook(res), name)


# ══════════════════════════════════════════════════════════════════════════
# Tag page - step 5: save (confirmation dialog -> background job)
# ══════════════════════════════════════════════════════════════════════════

@callback(
    Output("save-modal", "hidden"), Output("save-modal-body", "children"), Output("save-confirm", "children"),
    Output("save-replace", "data"), Output("save-region", "children", allow_duplicate=True), Output("poll-save", "disabled"),
    Input("save-btn", "n_clicks"), Input("save-cancel", "n_clicks"), Input("save-confirm", "n_clicks"),
    State("job-store", "data"), State("save-replace", "data"), prevent_initial_call=True)
def save_flow(n_save, n_cancel, n_confirm, job, replace):
    jid = _job_id(job)
    trig = ctx.triggered_id
    if not jid or not trig or not ctx.triggered[0].get("value"):
        raise PreventUpdate
    if trig == "save-cancel":
        return True, no_update, no_update, no_update, no_update, no_update
    if trig == "save-btn":
        try:
            plans = P.build_save_plan(jid, q)
        except (P.PipelineError, q.StoreError) as exc:
            return False, u.notice("error", str(exc), "Cannot check the existing data"), "Save", False, no_update, no_update
        changed = any((p["action"] == q.ACTION_CHANGED).any() for p in plans.values())
        nothing = all((p["action"] == q.ACTION_SAME).all() for p in plans.values())
        intro: List[Any] = []
        if changed:
            intro.append(u.notice("warn", [
                "Data for the same country and year already exists with different values. If you continue, the old rows are kept but "
                "marked ", html.Code("validity = FALSE"), " (soft-delete) and the new rows are saved as ", html.Code("validity = TRUE"),
                ", in one atomic step. You can restore the old version later."], "Replace existing data?"))
        elif nothing:
            intro.append(u.notice("info", "Everything in this file is already stored with identical values. Nothing needs to be written.",
                                  "Already saved"))
        else:
            intro.append(u.notice("info", "New country-years are appended with validity = TRUE; nothing existing is touched."))
        label = "Replace and save" if changed else ("Close" if nothing else "Save")
        return False, [*intro, pages.plan_table(plans)], label, bool(changed), no_update, no_update
    # confirm
    status = P.read_status(jid, "save")
    if P.effective_state(status) == "running":
        raise PreventUpdate
    meta = (P.get_object(jid, "result.pkl") or {}).get("meta")
    actor = _actor() or (meta.uploaded_by if meta else "")
    P.write_status(jid, "save", state="running", phase="Preparing", done=0, total=0, error=None, report=None)
    P.start_thread(P.run_save_job, jid, actor or "unknown", bool(replace), q)
    return True, no_update, no_update, no_update, pages.save_status_content(P.read_status(jid, "save"), "running"), False


@callback(Output("save-region", "children", allow_duplicate=True), Output("poll-save", "disabled", allow_duplicate=True),
          Input("poll-save", "n_intervals"), State("job-store", "data"), prevent_initial_call=True)
def poll_save(_n, job):
    jid = _job_id(job)
    if not jid:
        raise PreventUpdate
    status = P.read_status(jid, "save")
    state = P.effective_state(status)
    if state == "running":
        return pages.save_status_content(status, state), False
    if state == "needs_confirmation":
        return u.notice("warn", "The data for this country and year changed while you were reviewing. Press 'Save to Unity Catalog…' again "
                                "to see the current situation.", "Please review again"), True
    return pages.save_status_content(status, state), True


# ══════════════════════════════════════════════════════════════════════════
# Dashboard
# ══════════════════════════════════════════════════════════════════════════

def _main_col(kind: str) -> str:
    return "weighted_share" if kind == C.KIND_RIO else "green_share"


def _main_label(kind: str) -> str:
    return "Rio-weighted share" if kind == C.KIND_RIO else "Green share (any EU objective)"


@callback(Output("dash-year", "options"), Output("dash-year", "value"), Output("dash-countries", "options"),
          Output("dash-countries", "value"),
          Input("dash-kind", "value"), Input("dash-refresh", "n_clicks"),
          State("dash-year", "value"), State("dash-countries", "value"))
def dash_filters(kind, _n, year, countries):
    if ctx.triggered_id == "dash-refresh":
        q.invalidate_cache()
    try:
        agg = q.aggregates(kind or C.KIND_RIO)
    except q.StoreError:
        return [{"label": "Latest available per country", "value": "latest"}], "latest", [], []
    years = sorted(agg["year"].unique().tolist(), reverse=True) if len(agg) else []
    year_opts = [{"label": "Latest available per country", "value": "latest"}] + [{"label": str(y), "value": y} for y in years]
    year_val = year if year == "latest" or year in years else "latest"
    names = agg.drop_duplicates("country_iso3").set_index("country_iso3")["country"].to_dict() if len(agg) else {}
    opts = [{"label": n, "value": i} for i, n in sorted(names.items(), key=lambda kv: kv[1])]
    keep = [c for c in (countries or []) if c in names][:C.MAX_COMPARE_COUNTRIES]
    if not keep and len(agg):
        keep = agg.groupby("country_iso3").size().sort_values(ascending=False).head(4).index.tolist()
    return year_opts, year_val, opts, keep


@callback(Output("dash-body", "children"),
          Input("dash-kind", "value"), Input("dash-basis", "value"), Input("dash-year", "value"), Input("dash-countries", "value"))
def render_dashboard(kind, basis, year, countries):
    kind = kind or C.KIND_RIO
    try:
        agg = q.aggregates(kind)
    except q.StoreError as exc:
        return u.notice("error", str(exc), "Could not read the tables")
    if agg.empty:
        return u.empty_state("No data yet", "Nothing has been saved to the %s table. Tag a budget and save it, and it appears here." % C.KIND_LABELS[kind])
    df = u.add_shares(kind, agg, basis or "amount")
    names = df.drop_duplicates("country_iso3").set_index("country_iso3")["country"].to_dict()
    sel = df.sort_values("year").groupby("country_iso3").tail(1) if year in (None, "latest") else df[df["year"] == int(year)]
    col = _main_col(kind)
    if sel.empty:
        return u.empty_state("No data for this year", "Choose another year.")
    best = sel.sort_values(col, ascending=False).iloc[0]
    n_lines_fallback = (df["share_basis"] == "lines").any() if (basis or "amount") == "amount" else False
    blocks: List[Any] = [
        u.kpi_strip([
            u.kpi("Countries", u.fmt_int(df["country_iso3"].nunique()), "with valid data"),
            u.kpi("Country-years", u.fmt_int(len(df)), "current versions"),
            u.kpi("Budget lines", u.fmt_int(df["n_lines"].sum()), "valid rows"),
            u.kpi("Highest %s" % ("Rio-weighted share" if kind == C.KIND_RIO else "green share"), u.fmt_pct(best[col]),
                  "%s, %d" % (best["country"], best["year"]), tone="primary")])]
    if n_lines_fallback:
        blocks.append(u.notice("info", "Some country-years have no usable amounts; for those the share is based on the number of lines."))
    ref = C.RIO_TARGET_SHARE if kind == C.KIND_RIO else None
    year_note = "latest year per country" if year in (None, "latest") else str(year)
    chosen = [c for c in (countries or []) if c in names][:C.MAX_COMPARE_COUNTRIES]
    bar = html.Div(className="chart-card", children=[
        html.H3("%s by country" % _main_label(kind), className="card-title"), html.Div(year_note, className="muted"),
        pages.graph(u.fig_bar_h(sel["country"].tolist(), sel[col].tolist(), pct=True, title=_main_label(kind),
                                reference=ref, reference_label="10% reference" if ref else ""))])
    trend = html.Div(className="chart-card" + ("" if kind == C.KIND_EU else " chart-wide"), children=[
        html.H3("%s over time" % _main_label(kind), className="card-title"),
        html.Div("Countries chosen above (colour follows the order of your selection)" if chosen else "Choose countries above to compare",
                 className="muted"),
        pages.graph(u.fig_share_trend(df, col, chosen, names, _main_label(kind)) if chosen else u.empty_figure("Choose countries to compare"))])
    cover = df.pivot_table(index="country", columns="year", values=col, aggfunc="first")
    coverage = html.Div(className="chart-card chart-wide", children=[
        html.H3("Coverage - what is saved", className="card-title"),
        html.Div("Each cell is a saved country-year (%s); empty = no valid data" % _main_label(kind).lower(), className="muted"),
        pages.graph(u.fig_heatmap(cover, _main_label(kind)))])
    if kind == C.KIND_RIO:
        stack = html.Div(className="chart-card", children=[
            html.H3("Principal and significant share", className="card-title"), html.Div(year_note, className="muted"),
            pages.graph(u.fig_rio_stack(sel))])
        cards = [bar, stack, trend, coverage]
    else:
        z = sel.set_index("country")[[k + "_share" for k in C.EU_OBJECTIVES]]
        z.columns = [C.EU_LABELS[k] for k in C.EU_OBJECTIVES]
        objectives = html.Div(className="chart-card chart-wide", children=[
            html.H3("Objectives served, by country", className="card-title"), html.Div(year_note + " · share of amount", className="muted"),
            pages.graph(u.fig_heatmap(z, "Share"))])
        cards = [bar, trend, objectives, coverage]
    blocks.append(html.Div(className="chart-grid", children=cards))
    show = sel[["country", "year", "n_lines", col]].rename(columns={col: _main_label(kind)}).copy()
    show[_main_label(kind)] = show[_main_label(kind)].map(u.fmt_pct)
    blocks.append(html.Details(className="doc-details", children=[
        html.Summary("View the numbers as a table"), u.table(show.sort_values("country"), "dash-table", page_size=15)]))
    return html.Div(blocks)


# ══════════════════════════════════════════════════════════════════════════
# Data in Unity Catalog
# ══════════════════════════════════════════════════════════════════════════

_VERSION_COLS = ["kind", "country", "year", "status", "n_lines", "uploaded_at", "uploaded_by", "source_file", "batch_id",
                 "deactivated_at", "deactivated_by"]


def _where_block() -> Any:
    try:
        rio, eu = q.table_display_name(C.KIND_RIO), q.table_display_name(C.KIND_EU)
    except q.StoreError:
        return None
    return html.Div([
        html.P(["Two global tables, one row per budget line: ", html.Code(rio), " and ", html.Code(eu), "."], className="doc-p"),
        html.Pre(html.Code("-- current version of one country-year\nSELECT * FROM %s\nWHERE validity = TRUE AND country_iso3 = 'ZMB' AND year = 2024;\n\n"
                           "-- every version ever saved, newest first\nSELECT batch_id, validity, uploaded_at, uploaded_by, COUNT(*) AS lines\n"
                           "FROM %s\nWHERE country_iso3 = 'ZMB'\nGROUP BY batch_id, validity, uploaded_at, uploaded_by\nORDER BY uploaded_at DESC;" % (rio, rio)),
                 className="doc-code")])


@callback(
    Output("data-kpis", "children"), Output("data-integrity", "children"), Output("data-table-wrap", "children"),
    Output("data-country", "options"), Output("data-rows", "data"), Output("data-where", "children"),
    Input("data-refresh", "n_clicks"), Input("data-country", "value"), Input("data-kind", "value"), Input("data-status", "value"),
    Input("data-version", "data"))
def load_versions(_n, country, kind, status, _ver):
    if ctx.triggered_id == "data-refresh":
        q.invalidate_cache()
    try:
        v = q.list_versions()
        issues = q.integrity_issues()
    except q.StoreError as exc:
        return u.notice("error", str(exc), "Could not read the tables"), None, None, [], [], None
    if v.empty:
        return None, None, u.empty_state("Nothing saved yet", "Versions appear here after you save a tagged budget."), [], [], _where_block()
    opts = [{"label": n, "value": i} for i, n in sorted(v.drop_duplicates("country_iso3").set_index("country_iso3")["country"].items(), key=lambda kv: kv[1])]
    act = v[v["status"] == q.STATUS_ACTIVE]
    kpis = u.kpi_strip([
        u.kpi("Countries with current data", u.fmt_int(act["country_iso3"].nunique())),
        u.kpi("Current country-years", u.fmt_int(len(act.drop_duplicates(["country_iso3", "year"]))), "in either table"),
        u.kpi("Current lines", u.fmt_int(act["n_lines"].sum()), "both tables together"),
        u.kpi("Older / inactive versions", u.fmt_int((v["status"] != q.STATUS_ACTIVE).sum()))])
    warn = None
    if len(issues):
        warn = u.notice("error", "More than one valid version exists for: " + "; ".join(
            "%s %s %s" % (C.KIND_LABELS[r.kind], r.country_iso3, r.year) for r in issues.itertuples()) +
            ". Restore the version you want as current to fix it.", "Integrity warning")
    f = v
    if country:
        f = f[f["country_iso3"] == country]
    if kind:
        f = f[f["kind"] == kind]
    if status:
        f = f[f["status"] == status]
    f = f.reset_index(drop=True)
    show = f[_VERSION_COLS].copy()
    show["kind"] = show["kind"].map(C.KIND_LABELS)
    table = u.table(show, "data-table", page_size=12, sortable=False, selectable="single",
                    style_data_conditional=[
                        {"if": {"filter_query": '{status} = "active"', "column_id": "status"}, "color": "#1E7B4B", "fontWeight": "600"},
                        {"if": {"filter_query": '{status} = "staged"', "column_id": "status"}, "color": "#9A5B00", "fontWeight": "600"},
                        {"if": {"filter_query": '{status} = "deleted"', "column_id": "status"}, "color": "#B3261E", "fontWeight": "600"}]) if len(f) else \
        u.empty_state("No versions match", "Change the filters above.")
    rows = f[["kind", "country_iso3", "country", "year", "batch_id", "status", "n_lines"]].to_dict("records")
    return kpis, warn, table, opts, rows, _where_block()


@callback(
    Output("act-view", "disabled"), Output("act-download", "disabled"), Output("act-delete", "disabled"),
    Output("act-restore", "disabled"), Output("act-discard", "disabled"), Output("data-selected-label", "children"),
    Input("data-table", "selected_rows"), State("data-rows", "data"))
def on_select(selected, rows):
    if not selected or not rows or selected[0] >= len(rows):
        return True, True, True, True, True, "Select a row to see what you can do with it."
    r = rows[selected[0]]
    ok = u.can_manage(_actor())
    st = r["status"]
    label = "Selected: %s · %s · %s · %s" % (C.KIND_LABELS[r["kind"]], r["country"], r["year"], st)
    return (False, False, not (ok and st == "active"), not (ok and st in ("replaced", "deleted")), not (ok and st == "staged"), label)


@callback(Output("data-lines", "children"), Input("act-view", "n_clicks"),
          State("data-table", "selected_rows"), State("data-rows", "data"), prevent_initial_call=True)
def view_lines(n, selected, rows):
    if not n or not selected or not rows:
        raise PreventUpdate
    r = rows[selected[0]]
    try:
        df = q.fetch_lines(r["kind"], r["country_iso3"], int(r["year"]), r["batch_id"], valid_only=None, limit=500)
    except q.StoreError as exc:
        return u.notice("error", str(exc))
    keep = [c for c in ("source_row", "prog1", "prog2", "admin1", "func1", "amount", "climate_tag", "rio_marker", "rio_mitigation",
                        "rio_adaptation", "eu_primary_objective", "tag_method", "rationale", "validity") if c in df.columns]
    return html.Div([html.Div("First %s lines of this version (batch %s)" % (u.fmt_int(len(df)), r["batch_id"]), className="subhead"),
                     u.table(df[keep], "lines-table", filterable=True)])


@callback(Output("data-download", "data"), Input("act-download", "n_clicks"),
          State("data-table", "selected_rows"), State("data-rows", "data"), prevent_initial_call=True)
def download_version(n, selected, rows):
    if not n or not selected or not rows:
        raise PreventUpdate
    r = rows[selected[0]]
    df = q.fetch_lines(r["kind"], r["country_iso3"], int(r["year"]), r["batch_id"], valid_only=None, limit=1_000_000)
    return dcc.send_bytes(u.to_csv_bytes(df), "cbt_%s_%s_%s_%s.csv" % (r["kind"], r["country_iso3"], r["year"], r["batch_id"]))


_ACTIONS = {
    "act-delete": ("delete", "Soft-delete this version?",
                   "The rows stay in the table but get validity = FALSE, so they no longer count as current data. You can restore them later."),
    "act-restore": ("restore", "Restore this version as current?",
                    "These rows become validity = TRUE. Any other current version of the same country and year is set to FALSE."),
    "act-discard": ("discard", "Discard this staged batch?",
                    "These rows were written but never activated (an interrupted save). They are marked as discarded and never become current."),
}


@callback(
    Output("data-modal", "hidden"), Output("data-modal-title", "children"), Output("data-modal-body", "children"),
    Output("data-action", "data"), Output("data-msg", "children"), Output("data-version", "data"),
    Input("act-delete", "n_clicks"), Input("act-restore", "n_clicks"), Input("act-discard", "n_clicks"),
    Input("data-cancel", "n_clicks"), Input("data-confirm", "n_clicks"),
    State("data-table", "selected_rows"), State("data-rows", "data"), State("data-action", "data"), State("data-version", "data"),
    prevent_initial_call=True)
def data_action_flow(_d, _r, _x, _c, _ok, selected, rows, action, version):
    trig = ctx.triggered_id
    if not trig or not ctx.triggered[0].get("value"):
        raise PreventUpdate
    if trig == "data-cancel":
        return True, no_update, no_update, None, no_update, no_update
    if trig in _ACTIONS:
        if not selected or not rows:
            raise PreventUpdate
        r = rows[selected[0]]
        key, title, text = _ACTIONS[trig]
        body = [html.P(text, className="doc-p"),
                html.P([html.Strong("%s · %s · %s" % (C.KIND_LABELS[r["kind"]], r["country"], r["year"])), " · batch ", html.Code(r["batch_id"]),
                        " · %s lines" % u.fmt_int(r["n_lines"])], className="doc-p")]
        return False, title, body, dict(action=key, **{k: r[k] for k in ("kind", "country_iso3", "year", "batch_id")}), no_update, no_update
    # confirm
    if not action:
        raise PreventUpdate
    actor = _actor() or "app user"
    if not u.can_manage(_actor()):
        return True, no_update, no_update, None, u.notice("error", "You are not on the list of users allowed to change versions."), no_update
    try:
        if action["action"] == "delete":
            n = q.soft_delete(action["kind"], action["country_iso3"], [int(action["year"])], action["batch_id"], actor)
            msg = u.notice("ok", "%s lines are now validity = FALSE (soft-deleted)." % u.fmt_int(n))
        elif action["action"] == "restore":
            n = q.restore(action["kind"], action["country_iso3"], action["batch_id"], actor, years=[int(action["year"])])
            msg = u.notice("ok", "Restored: this version is current again (%s rows updated)." % u.fmt_int(n))
        else:
            q.discard_staged(action["kind"], action["batch_id"], actor)
            msg = u.notice("ok", "The staged batch was marked as discarded.")
    except q.StoreError as exc:
        return True, no_update, no_update, None, u.notice("error", str(exc), "Not changed"), no_update
    return True, no_update, no_update, None, msg, (version or 0) + 1
