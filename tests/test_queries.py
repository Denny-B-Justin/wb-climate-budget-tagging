import pandas as pd
import pytest

from src import constants as C
from src import queries as q
from tests.conftest import make_frame

RIO, EU = C.KIND_RIO, C.KIND_EU


def active(kind, iso="ZMB"):
    v = q.list_versions(kind, iso)
    return v[v["status"] == "active"]


def test_tables_have_all_columns(store):
    for kind in C.KINDS:
        with store._get_backend().session() as s:
            cols = store._get_backend().existing_columns(s, kind)
        assert cols == C.column_names(kind)


def test_new_country_is_appended_and_active(store):
    df = make_frame(RIO, "ZMB", (2021, 2022))
    plan = q.plan_save(RIO, df)
    assert set(plan["action"]) == {q.ACTION_NEW}
    rep = q.save(RIO, df, "ana")
    assert rep["rows_written"] == len(df)
    assert sorted(rep["years_added"]) == [2021, 2022]
    v = active(RIO)
    assert sorted(v["year"]) == [2021, 2022] and v["n_lines"].sum() == len(df)
    # a second country does not touch the first
    q.save(RIO, make_frame(RIO, "KEN", (2022,)), "ana")
    assert len(active(RIO, "ZMB")) == 2 and len(active(RIO, "KEN")) == 1


def test_identical_upload_is_skipped(store):
    df = make_frame(RIO, "ZMB", (2022,), seed=5)
    q.save(RIO, df, "ana")
    again = make_frame(RIO, "ZMB", (2022,), seed=5)          # same numbers, new batch id
    plan = q.plan_save(RIO, again)
    assert list(plan["action"]) == [q.ACTION_SAME]
    rep = q.save(RIO, again, "ana")
    assert rep["rows_written"] == 0 and rep["years_unchanged"] == [2022]
    assert q.list_versions(RIO, "ZMB")["batch_id"].nunique() == 1


def test_changed_values_need_confirmation_then_soft_delete(store):
    first = make_frame(RIO, "ZMB", (2022,), seed=5)
    q.save(RIO, first, "ana")
    second = make_frame(RIO, "ZMB", (2022,), seed=5, tweak=7.0)
    plan = q.plan_save(RIO, second)
    assert list(plan["action"]) == [q.ACTION_CHANGED]
    assert plan.loc[0, "old_batch_id"] == first["batch_id"].iloc[0]
    with pytest.raises(q.NeedsConfirmation):
        q.save(RIO, second, "bob")
    # nothing changed yet
    assert active(RIO)["batch_id"].tolist() == [first["batch_id"].iloc[0]]
    rep = q.save(RIO, second, "bob", replace=True)
    assert rep["years_replaced"] == [2022] and rep["replaced_batches"] == [first["batch_id"].iloc[0]]
    v = q.list_versions(RIO, "ZMB")
    act = v[v["status"] == "active"]
    old = v[v["batch_id"] == first["batch_id"].iloc[0]].iloc[0]
    assert act["batch_id"].tolist() == [second["batch_id"].iloc[0]]
    assert old["status"] == "replaced" and old["deactivated_by"] == "bob"
    # rows are still there: validity False, never physically deleted
    lines = q.fetch_lines(RIO, "ZMB", valid_only=None)
    assert len(lines) == len(first) + len(second)
    assert lines["validity"].sum() == len(second)
    assert (lines[~lines["validity"]]["replaced_by"] == second["batch_id"].iloc[0]).all()
    assert q.integrity_issues().empty


def test_only_matching_years_are_replaced(store):
    q.save(RIO, make_frame(RIO, "ZMB", (2020, 2021, 2022), seed=2), "ana")
    new = make_frame(RIO, "ZMB", (2021,), seed=2, tweak=3.0)
    q.save(RIO, new, "bob", replace=True)
    act = active(RIO).sort_values("year")
    assert act["year"].tolist() == [2020, 2021, 2022]
    assert act.loc[act["year"] == 2021, "batch_id"].iloc[0] == new["batch_id"].iloc[0]
    assert act.loc[act["year"] == 2020, "batch_id"].iloc[0] != new["batch_id"].iloc[0]
    assert q.integrity_issues().empty


def test_mixed_plan_new_same_changed(store):
    base = make_frame(RIO, "ZMB", (2020, 2021), seed=9)
    q.save(RIO, base, "ana")
    # 2020 identical, 2021 different, 2022 new  -> build one frame
    a = make_frame(RIO, "ZMB", (2020,), seed=9)
    b = make_frame(RIO, "ZMB", (2021,), seed=9, tweak=1.0)
    c = make_frame(RIO, "ZMB", (2022,), seed=9)
    bid = "mixed1"
    new = pd.concat([a, b, c], ignore_index=True)
    new["batch_id"] = bid
    new["record_id"] = [bid + ":" + str(i) for i in range(len(new))]
    plan = q.plan_save(RIO, new).set_index("year")["action"].to_dict()
    assert plan == {2020: q.ACTION_SAME, 2021: q.ACTION_CHANGED, 2022: q.ACTION_NEW}
    rep = q.save(RIO, new, "bob", replace=True)
    assert rep["years_unchanged"] == [2020] and rep["years_replaced"] == [2021] and rep["years_added"] == [2022]
    act = active(RIO).set_index("year")
    assert act.loc[2020, "batch_id"] == base["batch_id"].iloc[0]       # untouched
    assert act.loc[2021, "batch_id"] == bid and act.loc[2022, "batch_id"] == bid


def test_soft_delete_and_restore(store):
    first = make_frame(EU, "ZMB", (2022,), seed=3)
    q.save(EU, first, "ana")
    bid = first["batch_id"].iloc[0]
    assert q.soft_delete(EU, "ZMB", [2022], bid, "ana") == len(first)
    assert active(EU).empty
    v = q.list_versions(EU, "ZMB")
    assert v.loc[0, "status"] == "deleted"
    n = q.restore(EU, "ZMB", bid, "ana")
    assert n >= len(first)
    assert active(EU)["batch_id"].tolist() == [bid]


def test_restore_older_batch_replaces_current(store):
    a = make_frame(RIO, "ZMB", (2022,), seed=1)
    b = make_frame(RIO, "ZMB", (2022,), seed=1, tweak=5)
    q.save(RIO, a, "ana")
    q.save(RIO, b, "ana", replace=True)
    q.restore(RIO, "ZMB", a["batch_id"].iloc[0], "carl")
    assert active(RIO)["batch_id"].tolist() == [a["batch_id"].iloc[0]]
    assert q.integrity_issues().empty


def test_soft_delete_refuses_when_data_changed_meanwhile(store):
    a = make_frame(RIO, "ZMB", (2022,), seed=1)
    b = make_frame(RIO, "ZMB", (2022,), seed=1, tweak=5)
    q.save(RIO, a, "ana")
    q.save(RIO, b, "ana", replace=True)
    with pytest.raises(q.StoreError):
        q.soft_delete(RIO, "ZMB", [2022], a["batch_id"].iloc[0], "ana")


def test_row_count_guard_discards_partial_batch(store, monkeypatch):
    df = make_frame(RIO, "ZMB", (2022,), seed=1)
    orig = store.to_records
    monkeypatch.setattr(store, "to_records", lambda kind, d: orig(kind, d)[:-1])      # lose one row on the way
    with pytest.raises(q.StoreError, match="Row count check failed"):
        q.save(RIO, df, "ana")
    assert active(RIO).empty
    assert q.list_versions(RIO, "ZMB")["status"].tolist() == ["discarded"]


def test_aggregates_use_only_valid_rows_and_weights(store):
    a = make_frame(RIO, "ZMB", (2022,), seed=1)
    q.save(RIO, a, "ana")
    agg = q.aggregates(RIO)
    r = agg.iloc[0]
    amount = a["amount"]
    w = a["rio_marker"].map(C.RIO_WEIGHTS)
    assert r["n_lines"] == len(a)
    assert r["total_amount"] == pytest.approx(amount.sum())
    assert r["weighted_amount"] == pytest.approx((amount * w).sum())
    assert r["principal_amount"] == pytest.approx(amount[a["rio_marker"] == 2].sum())
    b = make_frame(RIO, "ZMB", (2022,), seed=1, tweak=100)
    q.save(RIO, b, "ana", replace=True)
    assert q.aggregates(RIO).iloc[0]["total_amount"] == pytest.approx(b["amount"].sum())
    assert len(q.aggregates(RIO, valid_only=False)) == 2


def test_eu_aggregates(store):
    a = make_frame(EU, "ZMB", (2022,), seed=4)
    q.save(EU, a, "ana")
    r = q.aggregates(EU).iloc[0]
    assert r["green_amount"] == pytest.approx(a.loc[a["climate_tag"] == "GREEN", "amount"].sum())
    assert r["mitigation_amount"] == pytest.approx(a.loc[a["eu_mitigation"], "amount"].sum())


def test_null_amounts_and_unicode_round_trip(store):
    a = make_frame(RIO, "CIV", (2022,), seed=1)
    a.loc[a.index[0], "amount"] = float("nan")
    a.loc[a.index[1], "prog1"] = "Reboisement d'urgence – défense \"côtière\" \\ backslash"
    a.loc[a.index[2], "notes"] = None
    q.save(RIO, a, "ana")
    lines = q.fetch_lines(RIO, "CIV")
    assert lines["amount"].isna().sum() == 1
    assert lines.loc[1, "prog1"] == a.loc[a.index[1], "prog1"]
    r = q.aggregates(RIO).iloc[0]
    assert r["n_with_amount"] == len(a) - 1


def test_bad_frames_are_rejected(store):
    df = make_frame(RIO, "ZMB", (2022,))
    with pytest.raises(q.StoreError):
        q.save(RIO, df.iloc[0:0], "ana")
    bad = df.copy()
    bad["validity"] = True
    with pytest.raises(q.StoreError):
        q.save(RIO, bad, "ana")
    two = pd.concat([df, make_frame(RIO, "KEN", (2022,))], ignore_index=True)
    with pytest.raises(q.StoreError):
        q.save(RIO, two, "ana")


def test_json_chunking_respects_limits():
    recs = [{"a": "x" * 100, "n": i} for i in range(1000)]
    chunks = list(q._json_chunks(recs, max_rows=300, max_bytes=20_000))
    import json
    parsed = [json.loads(c) for c in chunks]
    assert sum(len(p) for p in parsed) == 1000
    assert all(len(p) <= 300 for p in parsed)
    assert all(len(c) <= 21_000 for c in chunks)


def test_config_validation(monkeypatch):
    for var in (C.ENV_SERVER_HOSTNAME, C.ENV_HTTP_PATH, C.ENV_CLIENT_ID, C.ENV_CLIENT_SECRET, C.ENV_TOKEN,
                C.ENV_CATALOG, C.ENV_SCHEMA, C.ENV_BACKEND):
        monkeypatch.delenv(var, raising=False)
    q.reset()
    assert q.get_config().backend == "sqlite" and q.is_demo_storage()
    q.reset()
    monkeypatch.setenv(C.ENV_SERVER_HOSTNAME, "adb-1.azuredatabricks.net")
    with pytest.raises(q.ConfigError) as e:
        q.get_config()
    assert C.ENV_HTTP_PATH in str(e.value) and C.ENV_CATALOG in str(e.value)
    q.reset()
    monkeypatch.setenv(C.ENV_HTTP_PATH, "/sql/1.0/warehouses/abc")
    monkeypatch.setenv(C.ENV_TOKEN, "dapi-x")
    monkeypatch.setenv(C.ENV_CATALOG, "prd; DROP TABLE x")
    monkeypatch.setenv(C.ENV_SCHEMA, "sch")
    with pytest.raises(q.ConfigError, match="letters, digits"):
        q.get_config()
    q.reset()
    monkeypatch.setenv(C.ENV_CATALOG, "prd_mega")
    cfg = q.get_config()
    assert cfg.backend == "databricks" and q.table_fqn(RIO) == "`prd_mega`.`sch`.`cbt_global_rio_markers`"
    q.reset()


def test_databricks_sql_text(monkeypatch):
    """The Databricks-only statements can't run here, but their text must be well-formed."""
    for var in (C.ENV_CLIENT_ID, C.ENV_CLIENT_SECRET):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv(C.ENV_BACKEND, "databricks")
    monkeypatch.setenv(C.ENV_SERVER_HOSTNAME, "https://adb-1.azuredatabricks.net/")
    monkeypatch.setenv(C.ENV_HTTP_PATH, "/sql/1.0/warehouses/abc")
    monkeypatch.setenv(C.ENV_TOKEN, "dapi-x")
    monkeypatch.setenv(C.ENV_CATALOG, "prd_mega")
    monkeypatch.setenv(C.ENV_SCHEMA, "sgpbpi163")
    q.reset()
    be = q._get_backend()
    assert be.cfg.server_hostname == "adb-1.azuredatabricks.net"
    ddl = be.create_table_sql(RIO)
    assert ddl.startswith("CREATE TABLE IF NOT EXISTS `prd_mega`.`sgpbpi163`.`cbt_global_rio_markers`")
    assert "`validity` BOOLEAN" in ddl and "USING DELTA" in ddl
    assert ddl.count("'") % 2 == 0                        # every quote is balanced (comments are escaped)
    assert "\\'" in ddl                                   # docs containing apostrophes were escaped
    calls = []

    class FakeCursor:
        description = None

        def execute(self, sql, parameters=None):
            calls.append((sql, parameters))

        def fetchall(self):
            return []

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    class FakeConn:
        def cursor(self):
            return FakeCursor()

    s = q._DatabricksSession(be, FakeConn())
    recs = q.to_records(RIO, make_frame(RIO, "ZMB", (2022,), n=3))
    s.insert(RIO, recs)
    sql, params = calls[-1]
    assert "inline(from_json(:payload, 'array<struct<record_id:string" in sql
    assert "CAST(`uploaded_at` AS TIMESTAMP)" in sql
    assert list(params) == ["payload"] and len(__import__("json").loads(params["payload"])) == 3
    be.cfg.insert_mode = "values"
    calls.clear()
    s.insert(RIO, recs)
    assert all(len(p) <= 255 for _, p in calls)
    assert "CAST(:p0_uploaded_at AS TIMESTAMP)" in calls[0][0]
    q.reset()


def test_restore_single_year_of_a_multi_year_batch(store):
    a = make_frame(RIO, "ZMB", (2020, 2021), seed=1)
    q.save(RIO, a, "ana")
    b = make_frame(RIO, "ZMB", (2020, 2021), seed=1, tweak=9)
    q.save(RIO, b, "ana", replace=True)
    n = q.restore(RIO, "ZMB", a["batch_id"].iloc[0], "ana", years=[2021])
    assert n > 0
    act = active(RIO).set_index("year")["batch_id"].to_dict()
    assert act == {2020: b["batch_id"].iloc[0], 2021: a["batch_id"].iloc[0]}
    assert q.integrity_issues().empty
