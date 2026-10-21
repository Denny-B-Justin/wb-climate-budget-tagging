"""
src/utils.py
------------
Reusable pieces for the Dash UI: number/date formatting, who-is-the-user, Excel export,
Plotly figure builders and small layout components (header, footer, KPI tiles, notices ...).

Mirrors the role of utils.py in the CBD app (colours, figure builders, components) so the two
tools feel like one suite. No storage access happens here.

Python 3.9 compatible.
"""

from __future__ import annotations

import io
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from dash import dash_table, dcc, get_asset_url, html

from . import constants as C

# ══════════════════════════════════════════════════════════════════════════
# Formatting
# ══════════════════════════════════════════════════════════════════════════


def is_missing(v: Any) -> bool:
    if v is None:
        return True
    try:
        return bool(pd.isna(v))
    except (TypeError, ValueError):
        return False


def fmt_int(v: Any) -> str:
    return "-" if is_missing(v) else "{:,.0f}".format(float(v))


def fmt_pct(v: Any, digits: int = 1) -> str:
    return "-" if is_missing(v) else ("{:.%df}%%" % digits).format(float(v) * 100.0)


def fmt_amount(v: Any, currency: Optional[str] = None) -> str:
    """12 345 678 -> '12.3M'. Currency, when given, is a prefix."""
    if is_missing(v):
        return "-"
    x = float(v)
    a = abs(x)
    for div, suffix in ((1e12, "T"), (1e9, "B"), (1e6, "M"), (1e3, "K")):
        if a >= div:
            body = "{:.1f}{}".format(x / div, suffix)
            break
    else:
        body = "{:,.0f}".format(x)
    return ("%s %s" % (currency, body)) if currency else body


def fmt_dt(v: Any) -> str:
    if is_missing(v):
        return "-"
    ts = pd.Timestamp(v)
    ts = ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")
    return ts.strftime("%d %b %Y %H:%M UTC")




def plural(n: int, one: str, many: Optional[str] = None) -> str:
    return "%s %s" % (fmt_int(n), one if int(n) == 1 else (many or one + "s"))


def year_span(years: Iterable[int]) -> str:
    ys = sorted({int(y) for y in years})
    if not ys:
        return "-"
    return str(ys[0]) if len(ys) == 1 else ("%d–%d" % (ys[0], ys[-1]) if ys[-1] - ys[0] + 1 == len(ys) else ", ".join(map(str, ys)))


# ══════════════════════════════════════════════════════════════════════════
# Who is the user?  (authentication is done by the hosting platform)
# ══════════════════════════════════════════════════════════════════════════





# ══════════════════════════════════════════════════════════════════════════
# Excel export of a tagged result
# ══════════════════════════════════════════════════════════════════════════

def _summary_rows(kind: str, s: Dict[str, Any]) -> List[List[Any]]:
    rows = [["Budget lines", s.get("lines")], ["Share basis", "amount" if s.get("basis") == "amount" else "number of lines"]]
    if kind == C.KIND_RIO:
        rows += [["Rio-weighted share (100% / 40% / 0%)", s.get("weighted_share")],
                 ["Principal (2) share", s.get("principal_share")], ["Significant (1) share", s.get("significant_share")],
                 ["Mitigation-tagged share", s.get("mitigation_share")], ["Adaptation-tagged share", s.get("adaptation_share")]]
    else:
        rows += [["Green share (any EU objective)", s.get("green_share")], ["Brown-screen share", s.get("brown_share")]]
        rows += [["Objective: " + C.EU_LABELS[k], v] for k, v in (s.get("by_objective") or {}).items()]
    return rows


def export_workbook(result: Dict[str, Any]) -> bytes:
    """A reviewable .xlsx of a tagging result: read-me, one sheet per methodology, plus a decision-rule count sheet."""
    from src import pipeline as P                              # local import: pipeline imports the tagger modules
    meta = result["meta"]
    stats = result["stats"]
    out = io.BytesIO()
    with pd.ExcelWriter(out, engine="openpyxl") as xl:
        info = [
            ["Climate Budget Tagging - tagged result (not yet saved unless you pressed Save)", ""],
            ["Country", "%s (%s)" % (meta.country, meta.country_iso3)],
            ["Years", year_span(stats.get("years", []))],
            ["Currency", meta.currency], ["Data source", meta.data_source], ["Notes", meta.notes],
            ["Source file", meta.source_file],
            ["Tagging method", "Rule-based (COFOG lookup + keyword dictionaries)"],
            ["Methodology version", C.METHODOLOGY_VERSION],
            ["Budget lines", stats.get("lines")], ["Unique classifications tagged", stats.get("unique_classifications")],
            ["Generated", fmt_dt(datetime.now(timezone.utc))],
        ]
        pd.DataFrame(info, columns=["Item", "Value"]).to_excel(xl, sheet_name="Read_me", index=False)
        srows = []
        for kind in (C.KIND_RIO, C.KIND_EU):
            if result.get(kind) is not None:
                for label, val in _summary_rows(kind, P.summarize(kind, result[kind])):
                    srows.append([C.KIND_LABELS[kind], label, val])
        pd.DataFrame(srows, columns=["Methodology", "Metric", "Value"]).to_excel(xl, sheet_name="Summary", index=False)
        for kind, name in ((C.KIND_RIO, "Rio_Markers"), (C.KIND_EU, "EU_Taxonomy")):
            if result.get(kind) is not None:
                result[kind].to_excel(xl, sheet_name=name, index=False)
        counts = []
        for kind, key in ((C.KIND_RIO, "rio_methods"), (C.KIND_EU, "eu_methods")):
            for m, n in (stats.get(key) or {}).items():
                counts.append([C.KIND_LABELS[kind], m, n])
        pd.DataFrame(counts, columns=["Methodology", "Decision rule (tag_method)", "Unique classifications"]).to_excel(
            xl, sheet_name="Decision_rules", index=False)
        for ws in xl.book.worksheets:
            ws.freeze_panes = "A2"
            for idx, col in enumerate(ws.iter_cols(min_row=1, max_row=min(ws.max_row, 200)), start=1):
                width = max((len(str(c.value)) for c in col if c.value is not None), default=8)
                ws.column_dimensions[ws.cell(row=1, column=idx).column_letter].width = min(max(10, width + 2), 60)
    return out.getvalue()


def to_csv_bytes(df: pd.DataFrame) -> bytes:
    return df.to_csv(index=False).encode("utf-8-sig")


# ══════════════════════════════════════════════════════════════════════════
# Plotly figures
# ══════════════════════════════════════════════════════════════════════════

_GRID = "#E4EBE6"
_AXIS_FONT = dict(family=C.FONT_STACK, size=12, color=C.INK_SOFT)
_HOVER = dict(bgcolor="white", font_size=13, font_family=C.FONT_STACK, align="left", bordercolor=C.LINE)


def _base_layout(fig: go.Figure, height: int = 320, margin: Optional[Dict[str, int]] = None, legend: bool = False,
                 legend_top: bool = False) -> go.Figure:
    """legend_top puts the legend above the plot - use it whenever the x axis has a title, so the two never collide."""
    legend_pos = dict(y=1.0, yanchor="bottom") if legend_top else dict(y=-0.18)
    fig.update_layout(
        height=height, margin=margin or dict(l=8, r=16, t=8, b=8),
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        font=dict(family=C.FONT_STACK, size=13, color=C.INK), hoverlabel=_HOVER,
        showlegend=legend, legend=dict(orientation="h", x=0, font=_AXIS_FONT, title_text="", traceorder="normal", **legend_pos),
        bargap=0.5, uirevision="keep",
    )
    fig.update_xaxes(gridcolor=_GRID, zerolinecolor=C.LINE, tickfont=_AXIS_FONT, title_font=_AXIS_FONT, automargin=True)
    fig.update_yaxes(gridcolor=_GRID, zerolinecolor=C.LINE, tickfont=_AXIS_FONT, title_font=_AXIS_FONT, automargin=True)
    return fig


def empty_figure(message: str = "No data to show", height: int = 240) -> go.Figure:
    fig = go.Figure()
    fig.add_annotation(text=message, x=0.5, y=0.5, xref="paper", yref="paper", showarrow=False,
                       font=dict(size=14, color=C.INK_MUTE, family=C.FONT_STACK))
    fig.update_xaxes(visible=False)
    fig.update_yaxes(visible=False)
    return _base_layout(fig, height)


def _bar_marker(color: Any) -> Dict[str, Any]:
    return dict(color=color, cornerradius=4, line=dict(width=0))




def fig_donut(slices: Sequence[Tuple[str, float, str]], unit: str = "amount", height: int = 300) -> go.Figure:
    """Donut of (label, value, colour) slices in the given order; every slice is labelled with its name and share."""
    slices = [(l, float(v), c) for l, v, c in slices if not is_missing(v) and float(v) > 0]
    if not slices:
        return empty_figure("Nothing to show", height)
    fig = go.Figure(go.Pie(
        labels=[l for l, _, _ in slices], values=[v for _, v, _ in slices], sort=False, direction="clockwise", hole=0.58,
        marker=dict(colors=[c for _, _, c in slices], line=dict(color="white", width=2)),
        texttemplate="%{label}<br>%{percent:.0%}", textposition="outside", textfont=dict(size=12, color=C.INK, family=C.FONT_STACK),
        hovertemplate="%%{label}<br>%%{percent:.1%%} of %s<extra></extra>" % unit))
    fig = _base_layout(fig, height, legend=True, margin=dict(l=40, r=40, t=56, b=32))
    fig.update_layout(legend=dict(orientation="h", x=0.5, xanchor="center", y=1.12, yanchor="bottom"))
    return fig


def fig_rio_donut(principal: float, significant: float, unit: str = "amount", height: int = 300) -> go.Figure:
    """Principal / significant / not targeted as shares of the whole (inputs are shares 0..1)."""
    rest = max(0.0, 1.0 - (principal or 0) - (significant or 0))
    return fig_donut([("%s (2)" % C.RIO_LABELS[2], principal, C.RIO_COLORS[2]),
                      ("%s (1)" % C.RIO_LABELS[1], significant, C.RIO_COLORS[1]),
                      (C.RIO_LABELS[0], rest, C.RIO_COLORS[0])], unit, height)


def fig_tag_donut(green: float, brown: float, other: Optional[Dict[str, float]] = None, unit: str = "amount",
                  height: int = 300) -> go.Figure:
    """Green / brown / (unclear) / neutral shares. Without `other`, everything that is not green or brown is neutral."""
    if other is None:
        other = {"NEUTRAL": max(0.0, 1.0 - (green or 0) - (brown or 0))}
    parts = [("GREEN", green), ("BROWN", brown)] + [(t, other.get(t, 0.0)) for t in ("AMBIGUOUS", "NEUTRAL")]
    return fig_donut([(C.TAG_LABELS[t], v, C.TAG_COLORS[t]) for t, v in parts], unit, height)


def fig_green_brown(df: pd.DataFrame) -> go.Figure:
    """Per country: green share and brown share as two bars side by side (fixed semantic colours, legend on top)."""
    if df is None or df.empty:
        return empty_figure()
    d = df.sort_values("green_share")
    fig = go.Figure()
    for col, name, colour in (("brown_share", "Brown", C.BROWN_COLOR), ("green_share", "Green", C.GREEN_COLOR)):
        fig.add_trace(go.Bar(
            y=d["country"], x=d[col], orientation="h", name=name, marker=_bar_marker(colour),
            text=[fmt_pct(v) for v in d[col]], textposition="outside", cliponaxis=False,
            textfont=dict(color=C.INK, size=11, family=C.FONT_STACK),
            hovertemplate="%%{y}<br>%s: %%{x:.1%%}<extra></extra>" % name))
    fig.update_layout(barmode="group", bargap=0.35, bargroupgap=0.12, legend=dict(traceorder="reversed"))
    top = float(max(d["green_share"].max(), d["brown_share"].max()) or 0)
    fig.update_xaxes(tickformat=".0%", title="Share of the budget", range=[0, max(top * 1.2, 0.05)])
    return _base_layout(fig, max(200, 52 * len(d) + 110), legend=True, legend_top=True, margin=dict(l=8, r=48, t=32, b=8))


def fig_bar_h(labels: Sequence[str], values: Sequence[float], title: str = "", pct: bool = False,
              color: str = C.INDIGO, height: Optional[int] = None, reference: Optional[float] = None,
              reference_label: str = "") -> go.Figure:
    """Horizontal single-series bars, largest on top, value written at the bar end."""
    labels, values = list(labels), [0.0 if is_missing(v) else float(v) for v in values]
    if not labels:
        return empty_figure()
    order = np.argsort(values)                                 # plotly draws the first category at the bottom
    labels = [labels[i] for i in order]
    values = [values[i] for i in order]
    text = [fmt_pct(v) if pct else fmt_int(v) for v in values]
    fig = go.Figure(go.Bar(
        x=values, y=labels, orientation="h", marker=_bar_marker(color), text=text, textposition="outside",
        cliponaxis=False, textfont=dict(color=C.INK, size=12, family=C.FONT_STACK),
        hovertemplate="%{y}<br>%{text}<extra></extra>"))
    if reference is not None:
        fig.add_vline(x=reference, line=dict(color=C.INK_MUTE, width=1, dash="dot"),
                      annotation_text=reference_label, annotation_position="top",
                      annotation_font=dict(size=11, color=C.INK_MUTE, family=C.FONT_STACK))
    fig.update_xaxes(tickformat=".0%" if pct else None, title=title,
                     range=[0, max(max(values) * 1.18, (reference or 0) * 1.15, 1e-9)])
    h = height or max(150, 34 * len(labels) + 70)
    return _base_layout(fig, h, margin=dict(l=8, r=48, t=20 if reference is not None else 8, b=8))


def fig_eu_objectives(summary: Dict[str, Any]) -> go.Figure:
    by = summary.get("by_objective") or {}
    total = sum(by.values()) or 0
    basis_total = None
    if summary.get("basis") == "amount":
        basis_total = summary.get("total_amount") or 0
    else:
        basis_total = summary.get("lines") or 0
    vals = [(by[k] / basis_total) if basis_total else 0.0 for k in C.EU_OBJECTIVES]
    return fig_bar_h([C.EU_LABELS[k] for k in C.EU_OBJECTIVES], vals, pct=True,
                     title="Share of %s serving the objective (a line can serve several)" % summary.get("basis", "amount"))


def fig_rio_stack(df: pd.DataFrame) -> go.Figure:
    """Per country: principal and significant share side by side in one bar (ordinal colours, 2px gaps)."""
    if df is None or df.empty:
        return empty_figure()
    d = df.sort_values("weighted_share")
    fig = go.Figure()
    for level, col in ((2, "principal_share"), (1, "significant_share")):
        fig.add_trace(go.Bar(
            y=d["country"], x=d[col], orientation="h", name="%s (%d)" % (C.RIO_LABELS[level], level),
            marker=dict(color=C.RIO_COLORS[level], line=dict(color="white", width=2)),
            hovertemplate="%%{y}<br>%s: %%{x:.1%%}<extra></extra>" % C.RIO_LABELS[level]))
    fig.update_layout(barmode="stack", bargap=0.45)
    fig.update_xaxes(tickformat=".0%", title="Share of amount (before weighting)")
    return _base_layout(fig, max(190, 34 * len(d) + 110), legend=True, legend_top=True, margin=dict(l=8, r=16, t=32, b=8))


def fig_methods(counts: Dict[str, int], title: str = "Unique classifications") -> go.Figure:
    if not counts:
        return empty_figure()
    items = sorted(counts.items(), key=lambda kv: kv[1], reverse=True)
    return fig_bar_h([k for k, _ in items], [v for _, v in items], title=title, color="#5E7A6B")


def fig_share_trend(df: pd.DataFrame, value_col: str, order: Sequence[str], names: Dict[str, str],
                    title: str = "Share") -> go.Figure:
    """Lines per country; colour = the country's position in the user's selection (fixed validated order)."""
    if df is None or df.empty:
        return empty_figure("No data for this selection")
    fig = go.Figure()
    labelled: List[float] = []
    top = float(df[df["country_iso3"].isin(list(order))][value_col].max() or 0) or 1.0
    for i, iso in enumerate(order):
        g = df[df["country_iso3"] == iso].sort_values("year")
        if g.empty:
            continue
        col = C.CATEGORICAL[i % len(C.CATEGORICAL)]
        fig.add_trace(go.Scatter(
            x=g["year"], y=g[value_col], mode="lines+markers", name=names.get(iso, iso),
            line=dict(color=col, width=2), marker=dict(size=8, color=col, line=dict(color="white", width=2)),
            hovertemplate="%s · %%{x}<br>%%{y:.1%%}<extra></extra>" % names.get(iso, iso)))
        last = g.iloc[-1]
        end_val = float(last[value_col])
        if len(order) <= 4 and all(abs(end_val - v) > 0.04 * top for v in labelled):   # direct label at the line end
            labelled.append(end_val)
            fig.add_annotation(x=last["year"], y=last[value_col], text=names.get(iso, iso), showarrow=False,
                               xanchor="left", xshift=10, font=dict(size=12, color=C.INK, family=C.FONT_STACK))
    fig.update_xaxes(dtick=1, tickformat="d", title="")
    fig.update_yaxes(tickformat=".0%", rangemode="tozero", title=title)
    return _base_layout(fig, 340, legend=True, margin=dict(l=8, r=90, t=8, b=8))


def fig_heatmap(z: pd.DataFrame, title: str = "", fmt: str = ".0%", height: Optional[int] = None) -> go.Figure:
    """Rows x columns grid, one-hue sequential scale, value written in each cell."""
    if z is None or z.empty:
        return empty_figure("No data for this selection")
    vals = z.values.astype(float)
    text = [[("" if np.isnan(v) else (fmt_pct(v, 0) if fmt == ".0%" else fmt_int(v))) for v in row] for row in vals]
    top = np.nanmax(vals) if np.isfinite(vals).any() else 1.0
    fig = go.Figure(go.Heatmap(
        z=vals, x=[str(c) for c in z.columns], y=list(z.index), text=text, texttemplate="%{text}",
        colorscale=[[i / (len(C.SEQUENTIAL) - 1), c] for i, c in enumerate(C.SEQUENTIAL)], zmin=0, zmax=max(top, 1e-9),
        xgap=2, ygap=2, hoverongaps=False, colorbar=dict(thickness=10, tickformat=fmt, outlinewidth=0, title=dict(text=title, side="top")),
        textfont=dict(size=12, family=C.FONT_STACK),
        hovertemplate="%{y} · %{x}<br>%{text}<extra></extra>"))
    fig.update_yaxes(autorange="reversed", showgrid=False)
    fig.update_xaxes(showgrid=False, side="top", type="category")
    return _base_layout(fig, height or max(180, 34 * len(z.index) + 80), margin=dict(l=8, r=8, t=30, b=8))


# ══════════════════════════════════════════════════════════════════════════
# Dashboard numbers from the warehouse aggregates (same formulas as pipeline.summarize)
# ══════════════════════════════════════════════════════════════════════════

def add_shares(kind: str, agg: pd.DataFrame, basis: str = "amount") -> pd.DataFrame:
    """Add *_share columns to queries.aggregates() output. basis: 'amount' (falls back to lines) | 'lines'."""
    df = agg.copy()
    if df.empty:
        return df
    use_amount = (basis == "amount") & (df["n_with_amount"] > 0) & (df["total_amount"] > 0)
    if kind == C.KIND_RIO:
        denom_a = df["total_amount"].where(use_amount)
        w_lines = (df["principal_lines"] * 1.0 + df["significant_lines"] * C.RIO_WEIGHTS[1])
        df["weighted_share"] = np.where(use_amount, df["weighted_amount"] / denom_a, w_lines / df["n_lines"])
        df["principal_share"] = np.where(use_amount, df["principal_amount"] / denom_a, df["principal_lines"] / df["n_lines"])
        df["significant_share"] = np.where(use_amount, df["significant_amount"] / denom_a, df["significant_lines"] / df["n_lines"])
        df["share_basis"] = np.where(use_amount, "amount", "lines")
    else:
        denom_a = df["total_amount"].where(use_amount)
        df["green_share"] = np.where(use_amount, df["green_amount"] / denom_a, df["green_lines"] / df["n_lines"])
        df["brown_share"] = np.where(use_amount, df["brown_amount"] / denom_a, df["brown_lines"] / df["n_lines"])
        for k in C.EU_OBJECTIVES:
            df[k + "_share"] = np.where(use_amount, df[k + "_amount"] / denom_a, np.nan)
        df["share_basis"] = np.where(use_amount, "amount", "lines")
    return df


# ══════════════════════════════════════════════════════════════════════════
# Layout components (class names live in assets/style.css)
# ══════════════════════════════════════════════════════════════════════════

def icon(name: str, cls: str = "") -> html.Span:
    return html.Span(className=("ico ico-%s %s" % (name, cls)).strip(), **{"aria-hidden": "true"})


def build_header(current_page: str, wb_logo: Optional[str] = None) -> html.Header:
    links = []
    short = {C.PAGE_DATA: "Download"}                          # phone widths: the long label would push 'Methodologies' off screen
    for key, label, href in C.PAGES:
        text = [html.Span(label, className="lbl-full"), html.Span(short[key], className="lbl-short")] if key in short else label
        cls = "nav-link" + (" is-current" if key == current_page else "")
        if key == C.PAGE_METHODOLOGY:                          # reference reading: opens in its own browser tab
            links.append(html.A([text, icon("external")], href=href, target="_blank", rel="noopener", className=cls))
        else:
            links.append(dcc.Link(text, href=href, className=cls))
    # links.append(html.A(["%s" % C.SUITE_NAME, icon("external")], href=C.SUITE_URL, target="_blank",
                        # rel="noopener noreferrer", className="nav-link"))
    return html.Header(className="app-header", children=html.Div(className="app-header-inner", children=[
        dcc.Link([html.Img(src=get_asset_url("cbt_logo.svg"), className="brand-mark", alt=""),
                  html.Span(C.APP_NAME, className="header-name")], href="?page=%s" % C.DEFAULT_PAGE, className="header-brand"),
        html.Nav(className="header-nav", **{"aria-label": "Main"}, children=links),
    ]))


def build_footer(wb_logo: Optional[str] = None) -> html.Footer:
    return html.Footer(className="site-footer", children=html.Div(className="site-footer-inner", children=[
        html.Div(className="footer-about", children=[
            html.Img(src=wb_logo, className="footer-wb", alt="World Bank Group") if wb_logo else None,
            html.P("A World Bank initiative promoting digital tools for smarter "
                   "public investment."),
        ]),
        # html.Div(className="footer-links", children=[
        #     html.A("PIM-PAM", href=C.SUITE_URL, target="_blank", rel="noopener noreferrer"),
        # ]),
    ]))


def page_shell(current_page: str, body: Any, wb_logo: Optional[str] = None) -> html.Div:
    return html.Div(className="page", children=[build_header(current_page, wb_logo), html.Main(id="main", children=body),
                                                build_footer(wb_logo)])


def page_intro(title: str, lede: str, eyebrow: Optional[str] = None) -> html.Section:
    return html.Section(className="page-intro", children=html.Div(className="section-inner", children=[
        html.P(eyebrow, className="eyebrow") if eyebrow else None,
        html.H1(title, className="page-title"),
        html.P(lede, className="page-lede"),
    ]))


def section(children: Any, title: Optional[str] = None, lede: Optional[str] = None, cls: str = "", sid: Optional[str] = None) -> html.Section:
    kw = {"id": sid} if sid else {}
    return html.Section(className=("section %s" % cls).strip(), **kw, children=html.Div(className="section-inner", children=[
        html.H2(title, className="section-title") if title else None,
        html.P(lede, className="section-lede") if lede else None,
        children]))


def step_card(n: int, title: str, hint: str, body: Any, sid: Optional[str] = None, cls: str = "") -> html.Div:
    kw = {"id": sid} if sid else {}
    return html.Div(className=("card step-card %s" % cls).strip(), **kw, children=[
        html.Div(className="card-head", children=[
            html.Span(str(n), className="step-n"),
            html.Div([html.H3(title, className="card-title"), html.P(hint, className="card-hint")]),
        ]),
        html.Div(className="card-body", children=body),
    ])


def field(label: str, control: Any, hint: Optional[str] = None, required: bool = False, cls: str = "") -> html.Div:
    return html.Div(className=("field %s" % cls).strip(), children=[
        html.Label([label, html.Span(" *", className="req") if required else None], className="field-label"),
        control,
        html.Div(hint, className="field-hint") if hint else None,
    ])


def kpi(label: str, value: str, sub: Optional[str] = None, tone: Optional[str] = None) -> html.Div:
    return html.Div(className="kpi" + (" kpi-%s" % tone if tone else ""), children=[
        html.Div(label, className="kpi-label"), html.Div(value, className="kpi-value"),
        html.Div(sub, className="kpi-sub") if sub else None])


def kpi_strip(items: Sequence[html.Div]) -> html.Div:
    return html.Div(className="kpis", children=list(items))


def notice(kind: str, children: Any, title: Optional[str] = None) -> html.Div:
    """kind: info | warn | error | ok"""
    marks = {"info": "i", "warn": "!", "error": "×", "ok": "✓"}
    return html.Div(className="notice notice-%s" % kind, role="status" if kind != "error" else "alert", children=[
        html.Span(marks.get(kind, "i"), className="notice-mark", **{"aria-hidden": "true"}),
        html.Div([html.Strong(title) if title else None, html.Div(children, className="notice-text")])])


def chip(text: str, tone: str = "neutral") -> html.Span:
    return html.Span(text, className="chip chip-%s" % tone)


def status_chip(status: str) -> html.Span:
    tones = {"active": "ok", "staged": "warn", "replaced": "neutral", "deleted": "bad", "discarded": "neutral"}
    return chip(status.capitalize(), tones.get(status, "neutral"))


def empty_state(title: str, text: str) -> html.Div:
    return html.Div(className="empty", children=[html.Strong(title), html.P(text)])


def progress_bar(done: int, total: int, label: str) -> html.Div:
    pct = 0 if not total else max(0.0, min(100.0, 100.0 * done / total))
    return html.Div(className="progress", children=[
        html.Div(className="progress-track", role="progressbar", **{"aria-valuenow": str(int(pct)), "aria-valuemin": "0",
                                                                  "aria-valuemax": "100", "aria-label": label},
                 children=html.Div(className="progress-fill", style={"width": "%.1f%%" % pct})),
        html.Div(label, className="progress-label")])


def table(df: pd.DataFrame, table_id: Any, page_size: int = C.RESULT_TABLE_PAGE_SIZE, columns: Optional[List[Dict[str, Any]]] = None,
          sortable: bool = True, filterable: bool = False, selectable: Optional[str] = None, **kwargs: Any) -> dash_table.DataTable:
    """DataTable with the app's look (the rest is CSS). `style_data_conditional` rules are added in front of the defaults."""
    extra_conditional = kwargs.pop("style_data_conditional", None) or []
    data = df.copy()
    for c in data.columns:
        if pd.api.types.is_datetime64_any_dtype(data[c]):
            data[c] = data[c].map(lambda v: "" if pd.isna(v) else fmt_dt(v))
        elif data[c].dtype == bool:
            data[c] = data[c].map({True: "TRUE", False: "FALSE"})
    data = data.astype(object).where(data.notna(), None)
    cols = columns or [{"name": c, "id": c} for c in data.columns]
    return dash_table.DataTable(
        id=table_id, data=data.to_dict("records"), columns=cols, page_size=page_size, page_action="native",
        sort_action="native" if sortable else "none", filter_action="native" if filterable else "none",
        row_selectable=selectable or False, selected_rows=[], cell_selectable=False,
        style_as_list_view=True, css=[{"selector": ".dash-spreadsheet-container", "rule": "font-family: inherit;"}],
        style_table={"overflowX": "auto"},
        style_header={"fontFamily": C.FONT_STACK, "fontWeight": "600", "backgroundColor": "#F3F7F4", "color": C.INK,
                      "border": "0", "borderBottom": "1px solid " + C.LINE, "fontSize": "12px", "textTransform": "none"},
        style_cell={"fontFamily": C.FONT_STACK, "fontSize": "13px", "color": C.INK, "padding": "8px 10px",
                    "textAlign": "left", "border": "0", "borderBottom": "1px solid #E4EBE6", "maxWidth": "320px",
                    "overflow": "hidden", "textOverflow": "ellipsis", "whiteSpace": "nowrap", "backgroundColor": "white"},
        style_data_conditional=list(extra_conditional) + [{"if": {"state": "active"}, "backgroundColor": "#EDF4EF", "border": "0"},
                                                          {"if": {"state": "selected"}, "backgroundColor": "#DCEFE2", "border": "0"}],
        tooltip_data=[{c: {"value": str(v) if v is not None else "", "type": "text"} for c, v in row.items()}
                      for row in data.head(500).to_dict("records")],
        tooltip_duration=None, **kwargs)
