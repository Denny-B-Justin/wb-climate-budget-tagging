import json
import time
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from src import constants as C
from src import pipeline as P
from src import queries as q

SAMPLE = C.SAMPLE_DIR / "sample_input_budget.xlsx"


# ------------------------------------------------------------------------------------------- reading
def test_inspect_picks_raw_sheet_and_detects_columns():
    info = P.inspect_upload(SAMPLE, SAMPLE.name)
    assert info.sheet == "Raw" and "Notes" in info.sheets
    for k in ("admin1", "func1", "func2", "econ1", "econ2", "prog1", "prog2", "amount", "year"):
        assert k in info.mapping and info.mapping[k] == k
    assert len(info.preview) == C.PREVIEW_ROWS


def test_unsupported_file_type_and_bad_excel(tmp_path):
    bad = tmp_path / "x.xlsx"
    bad.write_text("not an excel file")
    with pytest.raises(P.PipelineError, match="valid Excel"):
        P.inspect_upload(bad, "x.xlsx")
    with pytest.raises(P.PipelineError, match="Unsupported"):
        P.inspect_upload(bad, "x.pdf")


def test_csv_with_semicolons_and_latin1(tmp_path):
    p = tmp_path / "b.csv"
    p.write_bytes("year;prog1;amount;func1\n2020;Reboisement côtière;1.234,50;0500\n2020;Salaires;(100);01\n".encode("cp1252"))
    info = P.inspect_upload(p, "b.csv")
    assert info.mapping["prog1"] == "prog1"
    raw = P.read_sheet(p, ".csv")
    meta = P.Metadata(country_iso3="CIV", year_mode="column")
    prep = P.prepare_lines(raw, info.mapping, meta)
    assert prep.lines["amount"].tolist() == [1234.5, -100.0]
    assert prep.lines["func1"].tolist() == ["0500", "01"]          # leading zeros survive CSV
    assert "Reboisement côtière" in prep.lines["prog1"].tolist()


@pytest.mark.parametrize("text,expected", [
    ("1,234.50", 1234.5), ("1.234,50", 1234.5), ("(1 000)", -1000.0), ("ZMW 5,000", 5000.0), ("12,5", 12.5),
    ("1,234,567", 1234567.0), ("-", np.nan), ("n/a", np.nan), ("abc", np.nan), ("1e3", 1000.0), ("-42", -42.0),
    ("1.234.567", 1234567.0), ("€ 99.9", 99.9), ("100-", -100.0),
])
def test_parse_amount(text, expected):
    got = P.parse_amount(pd.Series([text], dtype=object)).iloc[0]
    assert (np.isnan(got) and np.isnan(expected)) or got == pytest.approx(expected)


@pytest.mark.parametrize("value,expected", [
    (2019, 2019), (2019.0, 2019), ("2019", 2019), ("FY2019/20", 2019), ("2019-20", 2019), ("N/A", None),
    (1850, None), (2019.5, None), (pd.Timestamp("2021-03-01"), 2021), (None, None),
])
def test_parse_year(value, expected):
    got = P.parse_year(pd.Series([value], dtype=object)).iloc[0]
    assert (pd.isna(got) and expected is None) or got == expected


def test_prepare_lines_year_modes_and_errors():
    raw = pd.DataFrame({"yr": [2020, 2021, None], "title": ["Solar farm", "Salaries", None], "amt": ["10", "20", None]})
    mp = {"year": "yr", "prog1": "title", "amount": "amt"}
    prep = P.prepare_lines(raw, mp, P.Metadata(country_iso3="KEN", year_mode="column"))
    assert prep.years == [2020, 2021] and prep.n_dropped_empty == 1
    # single year overrides the column
    prep = P.prepare_lines(raw, {"prog1": "title", "amount": "amt"},
                           P.Metadata(country_iso3="KEN", year_mode="single", single_year=2019))
    assert prep.years == [2019]
    # a row with data but no year is an error, not silently dropped
    raw2 = raw.copy()
    raw2.loc[1, "yr"] = None
    with pytest.raises(P.PipelineError, match="no readable year"):
        P.prepare_lines(raw2, mp, P.Metadata(country_iso3="KEN", year_mode="column"))
    with pytest.raises(P.PipelineError, match="Map at least one"):
        P.prepare_lines(raw, {"amount": "amt"}, P.Metadata(country_iso3="KEN", year_mode="single", single_year=2020))
    with pytest.raises(P.PipelineError, match="more than one field"):
        P.prepare_lines(raw, {"prog1": "title", "prog2": "title"},
                        P.Metadata(country_iso3="KEN", year_mode="single", single_year=2020))
    with pytest.raises(P.PipelineError, match="Year"):
        P.prepare_lines(raw, {"prog1": "title"}, P.Metadata(country_iso3="KEN", year_mode="column"))


def test_metadata_validation():
    m = P.Metadata()
    errs = m.errors()
    assert any("country" in e for e in errs)
    assert P.Metadata(country_iso3="ZMB", year_mode="single", single_year=1800).errors()
    assert not P.Metadata(country_iso3="ZMB", year_mode="column").errors()


# ------------------------------------------------------------------------------------------- tagging
def _prepared():
    info = P.inspect_upload(SAMPLE, SAMPLE.name)
    raw = P.read_sheet(SAMPLE, ".xlsx", info.sheet)
    return P.prepare_lines(raw, info.mapping, P.Metadata(country_iso3="ZMB", year_mode="column"))


def test_dedup_is_exactly_equivalent_to_row_by_row_tagging():
    from src.taxonomy_method.rio_marker_tagger import RioMarkerTagger
    from src.taxonomy_method.eu_taxonomy_tagger import EUTaxonomyTagger
    prep = _prepared()
    tagged = P.tag_lines(prep.lines, P.TaggingSettings())
    lines = prep.lines
    colmap = {f: f for f in C.LINE_TEXT_FIELDS if lines[f].notna().any()}
    rio = RioMarkerTagger(mapping_path=str(C.COFOG_MAPPING_PATH))
    eu = EUTaxonomyTagger(mapping_path=str(C.COFOG_MAPPING_PATH))
    for i in range(len(lines)):
        row = lines[list(colmap)].iloc[i]
        r = rio.tag_row(row, colmap)
        e = eu.tag_row(row, colmap)
        t = tagged.rio.iloc[i]
        assert (t["rio_marker"], t["rio_mitigation"], t["rio_adaptation"]) == (r.rio_marker, r.rio_mitigation, r.rio_adaptation)
        assert t["tag_method"] == r.tagging_result.method and t["rationale"] == r.tagging_result.rationale
        u = tagged.eu.iloc[i]
        for k in C.EU_OBJECTIVES:
            assert bool(u[C.EU_FLAG_COLUMNS[k]]) == bool(e.eu_objectives[k])
        assert u["climate_tag"] == e.tagging_result.climate_tag.name
    assert tagged.stats["unique_classifications"] == len(lines)       # the sample has 25 distinct classifications


def test_dedup_copies_results_to_repeated_lines():
    prep = _prepared()
    twice = pd.concat([prep.lines, prep.lines.assign(year=2025, amount=1.0)], ignore_index=True)
    out = P.tag_lines(twice, P.TaggingSettings())
    assert out.stats["lines"] == 50 and out.stats["unique_classifications"] == 25
    cols = ["climate_tag", "tag_method", "rationale", "rio_marker", "rio_mitigation", "rio_adaptation"]
    pd.testing.assert_frame_equal(out.rio.iloc[:25][cols].reset_index(drop=True), out.rio.iloc[25:][cols].reset_index(drop=True))
    assert out.rio.iloc[25:]["year"].eq(2025).all() and out.rio.iloc[25:]["amount"].eq(1.0).all()


def test_cofog_mapping_is_applied():
    """If the COFOG mapping path were not passed, 'RIO_PREDETERMINED' could never appear."""
    prep = _prepared()
    tagged = P.tag_lines(prep.lines, P.TaggingSettings(methods=C.METHOD_RIO))
    assert tagged.eu is None
    assert (tagged.rio["tag_method"] == "RIO_PREDETERMINED").any()


def test_extra_user_columns_cannot_change_results():
    prep = _prepared()
    base = P.tag_lines(prep.lines, P.TaggingSettings(methods=C.METHOD_RIO)).rio
    poisoned = prep.lines.copy()
    poisoned["sector"] = "solar renewable climate change"      # would be read by the tagger if it were passed in
    again = P.tag_lines(poisoned, P.TaggingSettings(methods=C.METHOD_RIO)).rio
    pd.testing.assert_frame_equal(base, again)


def test_one_failing_line_does_not_stop_the_batch(monkeypatch):
    prep = _prepared()
    from src.taxonomy_method.rio_marker_tagger import RioMarkerTagger
    orig = RioMarkerTagger.tag_row
    calls = {"n": 0}

    def flaky(self, row, mapping):
        calls["n"] += 1
        if calls["n"] == 3:
            raise ValueError("boom")
        return orig(self, row, mapping)

    monkeypatch.setattr(RioMarkerTagger, "tag_row", flaky)
    out = P.tag_lines(prep.lines, P.TaggingSettings(methods=C.METHOD_RIO))
    assert out.stats["errors"] == 1 and (out.rio["tag_method"] == "RIO_ERROR").any()
    assert len(out.rio) == len(prep.lines)


def test_there_is_no_llm_layer(monkeypatch):
    """Keyword rules only: even with provider keys in the environment nothing is sent anywhere, and no table column mentions an LLM."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    rio, eu = P.make_taggers(P.TaggingSettings())
    assert rio.use_llm is False and rio.llm_tagger is None and eu.use_llm is False and eu.llm_tagger is None
    out = P.tag_lines(_prepared().lines, P.TaggingSettings())
    assert not any("llm" in k.lower() for k in out.stats)
    for kind in (C.KIND_RIO, C.KIND_EU):
        assert not [c for c in C.column_names(kind) if "llm" in c.lower() or c == "tagger_mode"]
    assert not any(str(m).endswith("_LLM") for df in (out.rio, out.eu) for m in df["tag_method"])


# ------------------------------------------------------------------------------------------- hashing / frames
def test_content_hash_ignores_order_and_notes_but_sees_values():
    prep = _prepared()
    lines = prep.lines.copy()
    lines.loc[:11, "year"] = 2023                                    # two years in one file
    t = P.tag_lines(lines, P.TaggingSettings()).rio
    meta = P.Metadata(country_iso3="ZMB", currency="ZMW", notes="one")
    a = P.finalize_for_save(C.KIND_RIO, t, meta, {}, "b1")
    shuffled = t.sample(frac=1.0, random_state=3).reset_index(drop=True)
    meta2 = P.Metadata(country_iso3="ZMB", currency="ZMW", notes="other note", source_file="other.xlsx")
    b = P.finalize_for_save(C.KIND_RIO, shuffled, meta2, {}, "b2")
    assert set(a["content_hash"]) == set(b["content_hash"]) or a.groupby("year")["content_hash"].first().to_dict() == \
        b.groupby("year")["content_hash"].first().to_dict()
    changed = t.copy()
    changed.loc[0, "amount"] += 1
    c = P.finalize_for_save(C.KIND_RIO, changed, meta, {}, "b3")
    y = int(changed.loc[0, "year"])
    assert a[a["year"] == y]["content_hash"].iloc[0] != c[c["year"] == y]["content_hash"].iloc[0]
    other_year = [yy for yy in a["year"].unique() if yy != y][0]
    assert a[a["year"] == other_year]["content_hash"].iloc[0] == c[c["year"] == other_year]["content_hash"].iloc[0]
    d = P.finalize_for_save(C.KIND_RIO, t, P.Metadata(country_iso3="ZMB", currency="USD"),
                            {}, "b4")
    assert a["content_hash"].iloc[0] != d["content_hash"].iloc[0]       # currency is part of the meaning


def test_finalized_frame_matches_table_schema():
    prep = _prepared()
    tg = P.tag_lines(prep.lines, P.TaggingSettings())
    meta = P.Metadata(country_iso3="ZMB")
    for kind, df in ((C.KIND_RIO, tg.rio), (C.KIND_EU, tg.eu)):
        out = P.finalize_for_save(kind, df, meta, tg.stats, "b1")
        assert list(out.columns) == C.column_names(kind)
        assert out["record_id"].is_unique and not out["validity"].any()
        assert (out["country"] == "Zambia").all()
        recs = q.to_records(kind, out)
        json.dumps(recs)                                  # everything is JSON-serialisable (Databricks insert path)


def test_summaries_match_the_original_streamlit_weights():
    prep = _prepared()
    tg = P.tag_lines(prep.lines, P.TaggingSettings())
    s = P.summarize(C.KIND_RIO, tg.rio)
    amt, mk = tg.rio["amount"], tg.rio["rio_marker"]
    expected = (amt[mk == 2].sum() * 1.0 + amt[mk == 1].sum() * 0.4) / amt.sum()
    assert s["weighted_share"] == pytest.approx(expected)
    assert s["principal_share"] + s["significant_share"] <= 1.0


# ------------------------------------------------------------------------------------------- jobs
def test_end_to_end_jobs(tmp_path, monkeypatch):
    monkeypatch.setenv(C.ENV_WORK_DIR, str(tmp_path / "work"))
    for var in (C.ENV_SERVER_HOSTNAME, C.ENV_HTTP_PATH, C.ENV_CLIENT_ID, C.ENV_CLIENT_SECRET):
        monkeypatch.delenv(var, raising=False)
    from tests import sqlite_backend
    monkeypatch.setenv(C.ENV_CACHE_TTL, "0")
    sqlite_backend.install(tmp_path / "db.sqlite")
    jid = P.new_job_id()
    import shutil
    shutil.copy(SAMPLE, P.job_file(jid, "upload.xlsx"))
    info = P.inspect_upload(SAMPLE, SAMPLE.name)
    meta = P.Metadata(country_iso3="ZMB", year_mode="column", currency="ZMW", source_file=SAMPLE.name)
    P.run_tag_job(jid, "upload.xlsx", ".xlsx", "Raw", 1, info.mapping, meta, P.TaggingSettings())
    st = P.read_status(jid, "tag")
    assert st["state"] == "done", st
    assert st["stats"]["lines"] == 25 and set(st["summaries"]) == {"rio", "eu"}
    plans = P.build_save_plan(jid, q)
    assert all(set(p["action"]) == {"new"} for p in plans.values())
    P.run_save_job(jid, False, q)
    sv = P.read_status(jid, "save")
    assert sv["state"] == "done", sv
    assert q.aggregates(C.KIND_RIO)["n_lines"].sum() == 25 and q.aggregates(C.KIND_EU)["n_lines"].sum() == 25
    # same file again -> identical, nothing written
    P.run_save_job(jid, False, q)
    sv = P.read_status(jid, "save")
    assert sv["state"] == "done" and sv["report"]["reports"][0]["rows_written"] == 0
    # change the result -> needs confirmation, then replace
    res = P.get_object(jid, "result.pkl")
    res["rio"].loc[0, "amount"] += 5
    P.put_object(jid, "result.pkl", res)
    P.run_save_job(jid, False, q)
    assert P.read_status(jid, "save")["state"] == "needs_confirmation"
    P.run_save_job(jid, True, q)
    assert P.read_status(jid, "save")["state"] == "done"
    v = q.list_versions(C.KIND_RIO, "ZMB")
    assert set(v["status"]) == {"active", "replaced"} and q.integrity_issues().empty
    q.reset()


def test_job_ids_cannot_escape_the_work_dir(tmp_path, monkeypatch):
    monkeypatch.setenv(C.ENV_WORK_DIR, str(tmp_path / "w"))
    for bad in ("../../etc", "a" * 31, "G" * 32, "", None):
        with pytest.raises(P.PipelineError):
            P.job_file(bad, "x.json")
    with pytest.raises(P.PipelineError):
        P.job_file(P.new_job_id(), "../x")


def test_error_states_are_reported_not_raised(tmp_path, monkeypatch):
    monkeypatch.setenv(C.ENV_WORK_DIR, str(tmp_path / "w"))
    jid = P.new_job_id()
    (P.job_file(jid, "upload.csv")).write_text("a;b\n1;2\n")
    meta = P.Metadata(country_iso3="ZMB", year_mode="single", single_year=2020)
    P.run_tag_job(jid, "upload.csv", ".csv", None, 1, {"amount": "b"}, meta, P.TaggingSettings())
    st = P.read_status(jid, "tag")
    assert st["state"] == "error" and "Map at least one" in st["error"]


def test_stalled_detection():
    assert P.effective_state({"state": "running", "heartbeat": time.time()}) == "running"
    assert P.effective_state({"state": "running", "heartbeat": time.time() - 999}) == "stalled"
    assert P.effective_state({"state": "done", "heartbeat": 0}) == "done"
