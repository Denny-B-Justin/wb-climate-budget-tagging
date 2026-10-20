"""Shared pytest fixtures: every test runs against a throw-away SQLite store."""
import os
import sys
import uuid
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src import constants as C          # noqa: E402
from src import queries as q            # noqa: E402


@pytest.fixture()
def store(tmp_path, monkeypatch):
    """Fresh SQLite database + clean module state for each test."""
    from tests import sqlite_backend
    for var in (C.ENV_SERVER_HOSTNAME, C.ENV_HOST, C.ENV_HTTP_PATH, C.ENV_CLIENT_ID, C.ENV_CLIENT_SECRET,
                C.ENV_CATALOG, C.ENV_SCHEMA):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv(C.ENV_CACHE_TTL, "0")
    sqlite_backend.install(tmp_path / "t.sqlite")
    q.ensure_tables()
    yield q
    q.reset()


def make_frame(kind, iso3="ZMB", years=(2022,), n=6, batch_id=None, seed=1, tweak=0.0,
               file="budget.xlsx"):
    """A schema-complete, staged (validity=False) frame like the pipeline would produce."""
    rng = np.random.default_rng(seed)
    batch_id = batch_id or "b" + uuid.uuid4().hex[:10]
    rows = []
    row_no = 2
    for y in years:
        for i in range(n):
            rows.append({"year": y, "i": i, "row": row_no})
            row_no += 1
    df = pd.DataFrame(rows)
    out = pd.DataFrame(index=df.index)
    out["record_id"] = [batch_id + ":" + str(r) for r in df["row"]]
    out["country"] = C.COUNTRY_BY_ISO3[iso3]
    out["country_iso3"] = iso3
    out["year"] = df["year"].astype(int)
    out["validity"] = False
    out["batch_id"] = batch_id
    for f in C.LINE_TEXT_FIELDS:
        out[f] = None
    out["prog1"] = ["Programme %d" % i for i in df["i"]]
    out["admin1"] = "Ministry of Energy"
    out["func1"] = "04"
    out["amount"] = np.round(rng.uniform(10, 1000, len(df)) + tweak, 2)
    out["climate_tag"] = np.where(df["i"] % 2 == 0, "GREEN", "NEUTRAL")
    out["confidence"] = "HIGH"
    out["tag_method"] = "RIO_MARKER" if kind == C.KIND_RIO else "EU_TAXONOMY"
    out["environmental_objective"] = "mitigation"
    out["subcategory"] = "principal"
    out["rationale"] = "test"
    if kind == C.KIND_RIO:
        out["rio_marker"] = (df["i"] % 3).astype(int)
        out["rio_mitigation"] = out["rio_marker"]
        out["rio_adaptation"] = 0
    else:
        for k in C.EU_OBJECTIVES:
            out[C.EU_FLAG_COLUMNS[k]] = (df["i"] % 2 == 0) if k == "mitigation" else False
        out["eu_primary_objective"] = "mitigation"
    out["currency"] = "ZMW"
    out["data_source"] = "unit test"
    out["notes"] = "n"
    out["source_file"] = file
    out["source_row"] = df["row"].astype(int)
    out["methodology_version"] = C.METHODOLOGY_VERSION
    out["uploaded_at"] = pd.Timestamp.now(tz="UTC")
    out["deactivated_at"] = pd.NaT
    out["deactivation_reason"] = None
    out["replaced_by"] = None
    # per-year fingerprint, order independent (the real implementation lives in src.utils)
    h = {}
    for y, g in out.groupby("year"):
        h[y] = "h%d_%s" % (len(g), round(float(g["amount"].sum()), 2))
    out["content_hash"] = out["year"].map(h)
    return out
