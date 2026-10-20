"""App wiring: every id a callback talks to must exist in some layout, pages render, helpers behave."""
import re
import shutil
from pathlib import Path

import pandas as pd
import pytest
from dash import dcc
from dash._callback import GLOBAL_CALLBACK_LIST
from dash.development.base_component import Component

from src import constants as C
from src import pipeline as P
from src import queries as q
from src import utils as u

SAMPLE = Path(__file__).resolve().parent.parent / "sample_data" / "sample_input_budget.xlsx"

# Components that are created *inside* a callback (tables built from the data), so no layout contains them.
CREATED_IN_CALLBACKS = {"data-table", "lines-table", "dash-table", "preview-table"}


def _ids(node, found):
    if isinstance(node, Component):
        i = getattr(node, "id", None)
        if isinstance(i, str):
            found.add(i)
        elif isinstance(i, dict):
            found.add(("pattern", i.get("type")))
        _ids(getattr(node, "children", None), found)
    elif isinstance(node, (list, tuple)):
        for x in node:
            _ids(x, found)
    return found


def _callback_ids():
    out = set()
    for cb in GLOBAL_CALLBACK_LIST:
        if "clientside_function" in cb and not cb.get("inputs"):
            continue
        refs = [(d["id"], d["property"]) for d in cb.get("inputs", []) + cb.get("state", [])]
        text = cb["output"]
        for part in re.split(r"\.\.\.", text.strip(".")) if text.startswith("..") else [text]:
            cid, _, prop = part.rpartition(".")
            refs.append((cid, prop))
        for cid, _prop in refs:
            if isinstance(cid, dict):
                out.add(("pattern", cid.get("type")))
            elif isinstance(cid, str) and cid.startswith("{"):
                out.add(("pattern", re.search(r'"type":"([^"]+)"', cid).group(1)))
            else:
                out.add(cid)
    return out


@pytest.fixture()
def app_module(tmp_path, monkeypatch):
    monkeypatch.setenv(C.ENV_WORK_DIR, str(tmp_path / "work"))
    for var in (C.ENV_SERVER_HOSTNAME, C.ENV_HOST, C.ENV_HTTP_PATH, C.ENV_CLIENT_ID, C.ENV_CLIENT_SECRET, C.ENV_TOKEN):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv(C.ENV_BACKEND, "sqlite")
    monkeypatch.setenv(C.ENV_SQLITE_PATH, str(tmp_path / "db.sqlite"))
    monkeypatch.setenv(C.ENV_CACHE_TTL, "0")
    q.reset()
    import app
    yield app
    q.reset()


def test_every_callback_id_exists_in_a_layout(app_module):
    import pages
    jid = P.new_job_id()
    shutil.copy(SAMPLE, P.job_file(jid, "upload.xlsx"))
    info = P.inspect_upload(SAMPLE, SAMPLE.name)
    meta = P.Metadata(country_iso3="ZMB", year_mode="column", source_file=SAMPLE.name)
    P.run_tag_job(jid, "upload.xlsx", ".xlsx", "Raw", 1, info.mapping, meta, P.TaggingSettings())
    upload = P.upload_to_store(info)
    found = set()
    _ids(app_module.app.layout, found)
    shell = u.page_shell("tag", None)
    _ids(shell, found)
    _ids(pages.tag_layout(jid, upload), found)
    _ids(pages.dashboard_layout(), found)
    _ids(pages.data_layout(), found)
    _ids(pages.methodology_layout(), found)
    _ids(pages.results_content(jid, P.read_status(jid, "tag")), found)
    _ids(pages.upload_panel(upload), found)
    missing = {i for i in _callback_ids() if i not in found} - CREATED_IN_CALLBACKS
    assert not missing, "callbacks refer to ids that no layout contains: %s" % sorted(map(str, missing))


def test_all_pages_render(app_module):
    import pages
    for page in (pages.home_layout("map.svg"), pages.tag_layout(None, None), pages.dashboard_layout(), pages.data_layout(),
                 pages.methodology_layout(), pages.not_found_layout()):
        assert page is not None


def test_routing_keys_are_unique_and_known():
    keys = [k for k, _l, _h in C.PAGES]
    assert len(keys) == len(set(keys))
    assert C.DEFAULT_PAGE in keys
    for k, _label, href in C.PAGES:
        assert href == "?page=%s" % k


def test_graph_helper_gives_the_container_the_figure_height():
    import pages
    fig = u.fig_bar_h(["a", "b", "c"], [0.1, 0.2, 0.3], pct=True)
    g = pages.graph(fig)
    assert isinstance(g, dcc.Graph) and g.style["height"] == "%dpx" % fig.layout.height


def test_table_merges_extra_conditional_styles():
    df = pd.DataFrame({"a": [1, 2], "status": ["active", "deleted"]})
    t = u.table(df, "t", style_data_conditional=[{"if": {"column_id": "status"}, "color": "red"}])
    assert t.style_data_conditional[0]["color"] == "red"
    assert any(r["if"].get("state") == "selected" for r in t.style_data_conditional)


def test_trend_figure_skips_overlapping_end_labels():
    rows = []
    for iso, name in (("AAA", "Aland"), ("BBB", "Bland")):          # identical values -> labels would sit on top of each other
        for y in (2023, 2024):
            rows.append({"country_iso3": iso, "year": y, "weighted_share": 0.3})
    df = pd.DataFrame(rows)
    fig = u.fig_share_trend(df, "weighted_share", ["AAA", "BBB"], {"AAA": "Aland", "BBB": "Bland"})
    assert len(fig.layout.annotations) == 1
    assert len(fig.data) == 2
