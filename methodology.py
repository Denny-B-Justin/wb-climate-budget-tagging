"""
methodology.py
--------------
The "Methodologies" tab: a detailed, plain-language write-up of how budget lines are tagged.

Nothing here is hand-copied from the tagger code where it could drift: keyword counts and lists, the COFOG
pre-determined tags and the worked examples are read from the same `shared/` files and produced by the same
tagger classes that do the real work, so the page cannot disagree with the behaviour.
"""

from __future__ import annotations

import json
from functools import lru_cache
from typing import Any, List, Sequence

import pandas as pd
from dash import html

from src import constants as C
from src import pipeline as P
from src import utils as u
from shared import eu_taxonomy_keywords as EUK
from shared import rio_keywords as RK


# ── tiny builders ─────────────────────────────────────────────────────────────────────────────────

def _sec(sid: str, title: str, *children: Any) -> html.Section:
    return html.Section(id=sid, className="doc-section", children=[html.H2(title, className="doc-h2"), *children])


def _p(*children: Any, cls: str = "") -> html.P:
    return html.P(list(children), className=("doc-p %s" % cls).strip())


def _h3(text: str) -> html.H3:
    return html.H3(text, className="doc-h3")


def _ul(items: Sequence[Any], ordered: bool = False) -> Any:
    tag = html.Ol if ordered else html.Ul
    return tag([html.Li(i) for i in items], className="doc-list")


def _table(headers: Sequence[str], rows: Sequence[Sequence[Any]], cls: str = "") -> html.Div:
    return html.Div(className="doc-table-wrap", children=html.Table(className=("doc-table %s" % cls).strip(), children=[
        html.Thead(html.Tr([html.Th(h) for h in headers])),
        html.Tbody([html.Tr([html.Td(c) for c in r]) for r in rows])]))


def _callout(kind: str, title: str, *children: Any) -> html.Div:
    return html.Div(className="callout callout-%s" % kind, children=[html.Strong(title), html.Div(list(children))])


def _code(text: str) -> html.Pre:
    return html.Pre(html.Code(text), className="doc-code")


def _flow(steps: Sequence[Sequence[str]]) -> html.Ol:
    """Numbered decision cascade. Each step = (title, text, outcome-or-empty)."""
    items = []
    for i, st in enumerate(steps, start=1):
        title, text = st[0], st[1]
        outcome = st[2] if len(st) > 2 else ""
        items.append(html.Li(className="flow-step", children=[
            html.Span(str(i), className="flow-n"),
            html.Div([html.Strong(title), html.Div(text, className="flow-text"),
                      html.Div(outcome, className="flow-out") if outcome else None])]))
    return html.Ol(className="flow", children=items)


def _keywords(title: str, words: List[str]) -> html.Details:
    uniq = list(dict.fromkeys(words))
    return html.Details(className="doc-details", children=[
        html.Summary("%s (%d)" % (title, len(uniq))),
        html.Div([html.Span(w, className="kw") for w in uniq], className="kw-list")])


# ── data-driven content ───────────────────────────────────────────────────────────────────────────

@lru_cache(maxsize=1)
def _cofog_rows():
    data = json.loads(C.COFOG_MAPPING_PATH.read_text(encoding="utf-8"))
    names = {"01": "General public services", "02": "Defence", "03": "Public order and safety",
             "05": "Environmental protection", "07": "Health", "09": "Education", "10": "Social protection"}
    rows = []
    for code in sorted({k[:2] for k in data["rio"]}):
        rio, eu = data["rio"].get(code) or data["rio"].get(code + "00"), data["eu"].get(code) or data["eu"].get(code + "00")
        if rio["rio_mitigation"] == 2:
            rio_txt = "Rio 2 (principal, mitigation) - GREEN"
        elif rio["rio_adaptation"] == 2:
            rio_txt = "Rio 2 (principal, adaptation) - GREEN"
        else:
            rio_txt = "Rio 0 - NEUTRAL"
        eu_txt = ("GREEN - %s" % EUK.EU_OBJECTIVE_LABELS.get(eu["primary_objective"], eu["primary_objective"])
                  if eu["climate_tag"] == "GREEN" else "NEUTRAL - no objective")
        rows.append(["%s  %s" % (code, names.get(code, "")), rio_txt, eu_txt])
    return rows


@lru_cache(maxsize=1)
def _examples() -> pd.DataFrame:
    """Run a handful of illustrative lines through the real taggers."""
    lines = [
        ("Solar power plant under the NDC", None, "Ministry of Energy", None),
        ("Solar power plant", None, "Ministry of Energy", None),
        ("Flood protection and climate adaptation", None, "Ministry of Works", None),
        ("Wastewater treatment plant upgrade", None, "Ministry of Water", None),
        ("Solar power plant salary top-up", None, "Ministry of Energy", None),
        ("Coal fired thermal power station", None, "Ministry of Energy", None),
        ("Rural school construction", None, "Ministry of Education", "09 Education"),
        ("Waste management and recycling", None, "Ministry of Environment", "05 Environmental protection"),
        ("Renewables expansion programme", None, "Ministry of Energy", None),
    ]
    df = pd.DataFrame(lines, columns=["prog1", "prog2", "admin1", "func1"])
    for f in C.LINE_TEXT_FIELDS:
        if f not in df:
            df[f] = None
    df["year"], df["source_row"], df["amount"] = 2024, range(2, 2 + len(df)), 1.0
    out = P.tag_lines(df, P.TaggingSettings())
    rows = []
    for i, r in df.iterrows():
        rio, eu = out.rio.iloc[i], out.eu.iloc[i]
        flags = [C.EU_LABELS[k] for k in C.EU_OBJECTIVES if eu[C.EU_FLAG_COLUMNS[k]]]
        rows.append({
            "line": " · ".join(x for x in (r["prog1"], r["admin1"], ("COFOG: " + r["func1"]) if isinstance(r["func1"], str) else None)
                               if isinstance(x, str) and x),
            "rio": "%d (mitigation %d, adaptation %d)" % (rio["rio_marker"], rio["rio_mitigation"], rio["rio_adaptation"]),
            "rio_rule": rio["tag_method"], "rio_why": rio["rationale"],
            "eu": eu["climate_tag"] + (" - " + ", ".join(flags) if flags else ""), "eu_rule": eu["tag_method"],
        })
    return pd.DataFrame(rows)


def _example_table() -> html.Div:
    ex = _examples()
    return _table(["Budget line (what the rules read)", "Rio marker", "Rio rule that decided", "EU Taxonomy", "EU rule that decided",
                   "Why (Rio rationale)"],
                  [[r["line"], r["rio"], html.Code(r["rio_rule"]), r["eu"], html.Code(r["eu_rule"]), r["rio_why"]]
                   for _, r in ex.iterrows()], cls="doc-table-wide")


# ── page ──────────────────────────────────────────────────────────────────────────────────────────

TOC = [
    ("overview", "Overview"), ("input", "Input data and preparation"), ("cascade", "The decision cascade"),
    ("rio", "Rio Markers"), ("eu", "EU Taxonomy"), ("cofog", "COFOG pre-determined tags"),
    ("keywords", "Keyword dictionaries"), ("indicators", "Indicators"),
    ("storage", "Storage and versioning"), ("limits", "Limitations and good practice"), ("glossary", "Glossary and sources"),
]


def layout() -> html.Div:
    n_mit, n_ada, n_exp, n_exc = (len(set(x)) for x in (RK.MITIGATION_KEYWORDS, RK.ADAPTATION_KEYWORDS,
                                                         RK.EXPLICIT_CLIMATE_TERMS, RK.EXCLUSION_TERMS))
    eu_counts = {k: len(set(v)) for k, v in EUK.EU_OBJECTIVES_KEYWORDS.items()}
    return html.Div(className="methodology", children=[
        u.page_intro("Methodologies",
                     "How a line of a government budget becomes a Rio Marker and an EU Taxonomy tag - rule by rule, with the "
                     "limits you should know before using the numbers.", eyebrow="Documentation · methodology version %s" % C.METHODOLOGY_VERSION),
        html.Div(className="section-inner doc-layout", children=[
            html.Nav(className="doc-toc", **{"aria-label": "On this page"}, children=[
                html.Div("On this page", className="doc-toc-title"),
                *[html.A(label, href="#%s" % sid) for sid, label in TOC]]),
            html.Div(className="doc-body", children=[

                _sec("overview", "Overview",
                     _p("The tool reads a government budget - one row per budget line, each with administrative, functional (COFOG), "
                        "economic and programme classifications and an amount - and gives every line two independent tags:"),
                     _ul([
                         [html.Strong("Rio Markers (0 / 1 / 2). "), "How strongly a line targets climate change: 0 not targeted, 1 significant "
                          "objective, 2 principal objective - separately for mitigation and adaptation, and combined."],
                         [html.Strong("EU Taxonomy objectives. "), "Whether a line serves each of the six environmental objectives of the EU Taxonomy "
                          "(one yes/no flag per objective), plus a brown-screen for fossil and polluting activities."],
                     ]),
                     _p("Both are ", html.Strong("rule-based and deterministic"), ": the same file always gives the same tags. The rules are "
                        "transparent keyword and code lookups on the English text of the budget line, applied in a fixed order (see "
                        "the cascade below). Each stored line carries the rule that decided it and a plain-language rationale, so any "
                        "tag can be traced and challenged. No language model is involved at any step."),
                     _callout("info", "What the tags are - and are not",
                              "They are a screening of budget wording against climate and environmental vocabulary, designed to be "
                              "reproducible and reviewable at scale. They are not a verified measurement of climate finance: no project "
                              "documents are read, no technical screening criteria are tested, and a line is tagged as a whole. Treat the "
                              "output as a first, auditable pass that analysts review - especially the biggest lines."),
                     ),

                _sec("input", "Input data and preparation",
                     _p("Upload an Excel (.xlsx) or CSV file. In Excel, a sheet called ", html.Code("Raw"), " is used when present, otherwise "
                        "the first sheet (you can pick another). The header row is row 1 unless you change it. Columns are matched to the standard "
                        "fields automatically (patterns such as ", html.Code("prog1"), ", ", html.Code("func_1"), ", ", html.Code("econ 2"),
                        ", ", html.Code("amount"), "); you can correct every match before running."),
                     _table(["Standard field", "How the rules use it"], [
                         [f.label + " (" + f.key + ")", {"text": "Read by the keyword rules (together with the other text fields)",
                                                         "cofog": "Looked up in the COFOG pre-determined table (first 2-4 digits)",
                                                         "context": "Stored with the line; not keyword-matched",
                                                         "amount": "Used for amount-weighted shares; text such as 1,234.50 or (500) is converted",
                                                         "year": "Which year each line belongs to (or give one year for the whole file)"}[f.role]]
                         for f in C.STD_FIELDS]),
                     _p("Only the fields you map are passed to the tagging rules, under their standard names. Extra columns in your file never "
                        "influence a result."),
                     _h3("Cleaning"),
                     _ul([
                         "Text: whitespace is collapsed; empty cells and the word 'nan' become empty. Numbers read as 5.0 are written 5.",
                         "Amounts: numbers are used as they are; text is converted (thousands separators, decimal comma, brackets for negatives, "
                         "currency symbols). Anything unreadable is stored as empty and reported - never silently turned into zero.",
                         "Years: 2019, 2019.0, '2019', 'FY2019/20' and '2019-20' all give 2019. A row that has data but no readable year stops the run "
                         "with the row number, so no line is dropped by accident. Completely empty rows are removed.",
                     ]),
                     _h3("One tag per distinct classification"),
                     _p("Budget files repeat the same classification on thousands of lines. The tool tags each distinct combination of "
                        "function, economic, programme and administrative fields once and copies the result to every line that shares it. "
                        "This is exact, not an approximation - the rules read nothing else - and it is what keeps 100,000-line budgets fast "
                        "and makes a re-run on the same file take seconds."),
                     ),

                _sec("cascade", "The decision cascade",
                     _p("For every distinct classification the rules run in a fixed order. The first rule that applies decides; later rules "
                        "are not consulted. The text the keyword rules read is the programme title, the programme description and the "
                        "ministry (admin level 1), joined and lower-cased."),
                     html.Div(className="two-col", children=[
                         html.Div([_h3("Rio Markers"), _flow([
                             ("COFOG pre-determined tag", "If the function code is in the COFOG table (see below), use that tag.",
                              "→ RIO_PREDETERMINED"),
                             ("Exclusion terms", "If the text contains an exclusion term (salary, pension, debt service ...), the line is not targeted.",
                              "→ RIO_EXCLUSION · marker 0"),
                             ("Keyword evidence", "Look for mitigation keywords, adaptation keywords and explicit climate terms.", ""),
                             ("Markers", "Derive mitigation, adaptation and combined markers (table below).", "→ RIO_MARKER"),
                             ("Default", "Nothing found: marker 0, neutral.", "→ RIO_MARKER · 'No climate relevance detected'"),
                         ])]),
                         html.Div([_h3("EU Taxonomy"), _flow([
                             ("COFOG pre-determined tag", "If the function code is in the COFOG table, use that tag.", "→ EU_PREDETERMINED"),
                             ("Exclusion terms", "Same exclusion list as Rio: not aligned.", "→ EU_EXCLUSION"),
                             ("Brown screen", "If the text contains a fossil or polluting activity (coal, diesel, mining, landfill ...), tag BROWN.",
                              "→ EU_BROWN"),
                             ("Six objectives", "Check the keyword list of each objective; every objective with a hit is flagged.",
                              "→ EU_TAXONOMY · GREEN"),
                             ("Default", "Nothing found: neutral, no objective.", "→ EU_TAXONOMY_NONE"),
                         ])]),
                     ]),
                     _p("Matching is ", html.Strong("whole-word and case-insensitive"), ": 'solar' matches 'Solar power plant' but not "
                        "'solarium'; multi-word terms such as 'energy efficiency' must appear together. There is no stemming, so a plural or other "
                        "inflection counts only if it is listed - 'renewable' matches, 'renewables' does not (see the last example below). "
                        "Keyword lists are English only."),
                     _h3("Worked examples (generated live by the same tagger code)"),
                     _example_table(),
                     ),

                _sec("rio", "Rio Markers",
                     _p("The Rio Markers were introduced by the OECD Development Assistance Committee to flag aid that targets the objectives "
                        "of the Rio Conventions. Here they are applied to budget lines: each line is scored 0, 1 or 2 for climate change "
                        "mitigation and for adaptation, and the combined marker is the higher of the two."),
                     _table(["Marker", "Meaning", "Rule in this tool"], [
                         ["2 · Principal", "Climate is the main purpose of the line.",
                          "A mitigation (or adaptation) keyword AND an explicit climate term appear in the text - or the COFOG table says so."],
                         ["1 · Significant", "Climate is one of several purposes.",
                          "A mitigation (or adaptation) keyword appears, without an explicit climate term."],
                         ["0 · Not targeted", "No evidence of climate relevance.",
                          "No keyword, an exclusion term, or a neutral COFOG division."],
                     ]),
                     _p("'Explicit climate terms' are general climate vocabulary such as ", html.Em("climate change, NDC, Paris Agreement, greenhouse gas, "
                        "climate finance"), ". On their own they do not score: ", html.Em("'Climate change awareness workshop'"),
                        " has no mitigation or adaptation keyword and scores 0. Together with a mitigation or adaptation keyword they lift a "
                        "line from 1 to 2. The combined marker is 2 if either marker is 2, else 1 if either is 1, else 0 (never a sum, so a line "
                        "serving both objectives is not counted twice)."),
                     _p("Each line also gets ", html.Code("climate_tag"), " (GREEN when the marker is above 0, otherwise NEUTRAL; Rio never produces "
                        "BROWN), ", html.Code("confidence"), " (HIGH for principal and for 'nothing found', MEDIUM for significant), the "
                        "environmental objective (mitigation, adaptation, or none when both apply) and a rationale naming the matched keyword."),
                     _callout("info", "Weights used for the headline indicator",
                              "Principal counts at 100% of a line's amount, significant at 40%, not targeted at 0% - the coefficients the European "
                              "Commission uses to track climate-relevant spending in the EU budget. The tables store the raw 0/1/2 markers, so any other "
                              "weighting can be applied later without re-tagging."),
                     ),

                _sec("eu", "EU Taxonomy",
                     _p("The EU Taxonomy Regulation (EU) 2020/852 defines six environmental objectives. Each is a separate yes/no flag per line "
                        "(a line may serve several):"),
                     _table(["Objective", "Stored column", "Keywords"], [
                         [C.EU_LABELS[k], html.Code(C.EU_FLAG_COLUMNS[k]), "%d terms" % eu_counts.get(k, 0)] for k in C.EU_OBJECTIVES]),
                     _ul([
                         [html.Strong("Brown screen. "), "Before the objectives are checked, the text is screened for fossil-fuel and polluting activities "
                          "(coal, diesel, mining, cement production, landfill ...). A hit tags the line BROWN with no objective. The screen "
                          "comes before the objectives, so a line that mentions both coal and solar is BROWN."],
                         [html.Strong("Green. "), "Any objective with a keyword hit: the line is GREEN and the flags show which objectives. "
                          "The primary objective is the first hit in the order mitigation, adaptation, water, circular economy, pollution, biodiversity. "
                          "Confidence is HIGH with one objective, MEDIUM with several."],
                         [html.Strong("Neutral. "), "Nothing found (or an exclusion term / neutral COFOG division)."],
                     ]),
                     _callout("warn", "'EU Taxonomy' here means objective relevance, not alignment",
                              "The legal Taxonomy tests activities against technical screening criteria, 'do no significant harm' and minimum "
                              "safeguards. A budget line has none of that detail, so this tag only says which objectives the wording points to. "
                              "Do not report it as Taxonomy-aligned expenditure."),
                     ),

                _sec("cofog", "COFOG pre-determined tags",
                     _p("The first rule looks at the Function level 1 field (COFOG). The leading 2 to 4 digits are read ('05', '0500' or "
                        "'05 Environmental protection' all give 05) and looked up in this table. If the division is listed, its tag is used and "
                        "no keyword is checked."),
                     _table(["COFOG division", "Rio Markers", "EU Taxonomy"], _cofog_rows()),
                     _ul([
                         "Divisions not listed (e.g. 04 Economic affairs, 06 Housing and community amenities, 08 Recreation and culture) go on to the "
                         "keyword rules - that is where energy, transport, water and agriculture lines are found.",
                         "Division 05 is treated as wholly green (principal, mitigation / EU mitigation) because it is the environment function. "
                         "This is a deliberate simplification: waste, wastewater, pollution and biodiversity spending under 05 is not split by objective.",
                         "The neutral divisions are neutral for every line under them, whatever the text says (a school with solar panels under "
                         "09 Education stays neutral). If this matters for your country, tag those lines in a separate pass.",
                         "Only 2- or 4-digit codes match. A 3-digit COFOG group such as '051' is not in the table and falls through to the keyword rules.",
                     ]),
                     ),

                _sec("keywords", "Keyword dictionaries",
                     _p("The dictionaries live in ", html.Code("shared/rio_keywords.py"), " and ", html.Code("shared/eu_taxonomy_keywords.py"),
                        ". Changing them changes results, so the methodology version stored with every row (currently ",
                        html.Strong(C.METHODOLOGY_VERSION), ") should be raised when they change; the identical-data check then lets you "
                        "re-tag a country and replace it only where tags actually moved."),
                     html.Div(className="stack", children=[
                         _keywords("Rio · mitigation keywords", RK.MITIGATION_KEYWORDS),
                         _keywords("Rio · adaptation keywords", RK.ADAPTATION_KEYWORDS),
                         _keywords("Rio · explicit climate terms", RK.EXPLICIT_CLIMATE_TERMS),
                         _keywords("Exclusion terms (Rio and EU)", RK.EXCLUSION_TERMS),
                         *[_keywords("EU · %s" % C.EU_LABELS[k], EUK.EU_OBJECTIVES_KEYWORDS[k]) for k in C.EU_OBJECTIVES],
                         _keywords("EU · brown screen", EUK.BROWN_KEYWORDS),
                     ]),
                     _p("Sizes: %d mitigation, %d adaptation, %d explicit climate terms, %d exclusion terms." % (n_mit, n_ada, n_exp, n_exc),
                        cls="doc-note"),
                     ),

                _sec("indicators", "Indicators",
                     _p("The dashboard and the results view use these definitions (the same formulas run in the warehouse for the dashboard and in "
                        "the app for a fresh result):"),
                     _table(["Indicator", "Definition"], [
                         ["Rio-weighted share", "Σ amount × weight(marker) ÷ Σ amount, with weights 100% / 40% / 0% for markers 2 / 1 / 0."],
                         ["Principal share · Significant share", "Σ amount of lines with marker 2 (resp. 1) ÷ Σ amount."],
                         ["EU green share", "Σ amount of lines tagged GREEN ÷ Σ amount."],
                         ["EU brown share", "Σ amount of lines tagged BROWN ÷ Σ amount."],
                         ["Objective share", "Σ amount of lines flagged for one objective ÷ Σ amount. Objectives overlap, so these do not add up to the green share."],
                     ]),
                     _ul([
                         "If a country-year has no usable amounts, the same ratios are computed on the number of lines and marked as such.",
                         "Shares are computed within a currency and year, so they compare across countries; absolute amounts do not (different "
                         "currencies and units) and are never summed across countries.",
                         "Negative amounts (reversals) are included as they are. Check files that mix budget and outturn, or commitments and "
                         "payments: the tool tags what it is given.",
                     ]),
                     ),

                _sec("storage", "Storage and versioning",
                     _p("Results are saved to two global Unity Catalog Delta tables with one row per budget line. All countries share the tables; "
                        "the ", html.Code("country_iso3"), " and ", html.Code("year"), " columns are the metadata that identify a slice."),
                     _table(["Table", "Content"], [
                         [html.Code(C.TABLE_NAMES[C.KIND_RIO]), "Rio markers (rio_marker, rio_mitigation, rio_adaptation) plus tag, confidence, rule, rationale."],
                         [html.Code(C.TABLE_NAMES[C.KIND_EU]), "Six boolean objective flags, primary objective, tag, confidence, rule, rationale."],
                     ]),
                     _p("Every row also holds the budget line as uploaded, the metadata you entered (currency, data source, notes), and an audit "
                        "trail (file, row number, who and when, methodology version, content fingerprint)."),
                     _h3("The validity column"),
                     _p(html.Code("validity"), " is TRUE for the current version of a country-year and FALSE for anything else. Nothing is ever "
                        "physically deleted by the app: replacing or deleting data flips the flag and records who did it, when and why, so every "
                        "earlier version can be inspected or restored."),
                     html.Div(className="three-col", children=[
                         _callout("ok", "New country or year", "No valid rows exist for that country-year: the new rows are appended with validity = TRUE."),
                         _callout("info", "Same values", "A valid version with identical content already exists: nothing is written and you are told so."),
                         _callout("warn", "Same country-year, different values",
                                  "You are asked to confirm. The old rows are set to FALSE and the new rows to TRUE in one atomic update."),
                     ]),
                     _p("The switch is safe by construction: the new rows are first written with validity = FALSE and counted; only then does a "
                        "single Delta UPDATE flip old → FALSE and new → TRUE together. If the upload fails halfway, the previous data stays valid. "
                        "Two people saving the same country-year at once end with exactly one valid version."),
                     _p("Always filter on validity when you query the tables:"),
                     _code("SELECT country_iso3, year, SUM(amount * CASE rio_marker WHEN 2 THEN 1.0 WHEN 1 THEN 0.4 ELSE 0 END) / SUM(amount) AS rio_weighted_share\n"
                           "FROM <catalog>.<schema>.%s\nWHERE validity = TRUE\nGROUP BY country_iso3, year;" % C.TABLE_NAMES[C.KIND_RIO]),
                     ),

                _sec("limits", "Limitations and good practice",
                     _ul([
                         [html.Strong("Wording drives everything. "), "A line with a vague title ('Programme 14') scores 0 even if the project is climate-relevant; "
                          "a line mentioning 'water management' scores adaptation even if it is routine. Spot-check large lines."],
                         [html.Strong("English only, no stemming. "), "Translate or add local-language terms to the dictionaries before tagging a "
                          "non-English budget, and add plural forms where they matter."],
                         [html.Strong("Whole lines, not shares of lines. "), "A line is tagged in full; the only apportionment is the 40% weight "
                          "for 'significant'."],
                         [html.Strong("Exclusion terms win. "), "'Solar power plant salary top-up' is neutral because of 'salary'. This is conservative by design."],
                         [html.Strong("Rio has no brown. "), "Use the EU Taxonomy brown screen to see fossil and polluting spending."],
                         [html.Strong("COFOG shortcuts. "), "Division 05 is all green and divisions 01, 02, 03, 07, 09, 10 are all neutral (see above)."],
                         [html.Strong("Check by rule, not only by total. "), "The results page counts lines per deciding rule. A large share decided by "
                          "RIO_PREDETERMINED or RIO_EXCLUSION means the headline depends on those shortcuts."],
                     ]),
                     _p("Recommended routine: tag, review the largest tagged and untagged lines and the rule counts, adjust the "
                        "dictionaries if needed (raising the methodology version), re-tag, and save. Lines the keywords miss are tagged neutral, so "
                        "scan the largest neutral lines for wording the dictionaries do not yet cover."),
                     ),

                _sec("glossary", "Glossary and sources",
                     _table(["Term", "Meaning"], [
                         ["Budget line", "One row of the uploaded file: a combination of classifications with an amount for a year."],
                         ["COFOG", "Classification of the Functions of Government (UN): ten divisions such as 05 Environmental protection."],
                         ["Rio Markers", "OECD DAC policy markers scoring whether an activity targets climate objectives: 0 not targeted, 1 significant, 2 principal."],
                         ["EU Taxonomy", "EU classification of environmentally sustainable activities across six objectives (Regulation (EU) 2020/852)."],
                         ["Validity", "TRUE for the current version of a country-year, FALSE for replaced, deleted or staged rows."],
                         ["Batch", "All rows written by one save operation; identified by batch_id."],
                         ["Content hash", "Fingerprint of the stored values of a country-year, used to recognise an identical re-upload."],
                     ]),
                     _p("Sources: OECD DAC Rio Markers for climate change; Regulation (EU) 2020/852 (Taxonomy Regulation); UN Classification of the "
                        "Functions of Government (COFOG); the tagging rules in this repository (", html.Code("rio_marker_tagger.py"), ", ",
                        html.Code("eu_taxonomy_tagger.py"), ", ", html.Code("shared/"), ").", cls="doc-note"),
                     ),
            ]),
        ]),
    ])
