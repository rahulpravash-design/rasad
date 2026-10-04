from __future__ import annotations

import copy
import shutil
import sqlite3
from unittest import mock

import pytest
from fastapi.testclient import TestClient

from api.db import connect
from api.main import app
from audit import chain
from config.loader import load_constraints, load_sector
from forecast.service import models_dir
from forecast.train import train_all
from gate.anomaly import model_path
from planner import solve as S

AS_OF = load_constraints()["scenario"]["as_of"]


@pytest.fixture(scope="module")
def trained(built):
    train_all(built["db"], seed=42)
    return built["db"]


@pytest.fixture(scope="module")
def needs(trained):
    conn = connect(trained, readonly=True)
    try:
        return S.needs(conn, trained, AS_OF, 30)
    finally:
        conn.close()


def run(db, needs, constraints=None):
    conn = connect(db, readonly=True)
    try:
        if constraints is None:
            return S.solve(conn, db, AS_OF, need_rows=needs)
        with mock.patch.object(S, "load_constraints", lambda: constraints):
            return S.solve(conn, db, AS_OF, need_rows=needs)
    finally:
        conn.close()


# ------------------------------------------------------------------ closure model


def test_pass_status_comes_from_the_model_with_probabilities(built):
    conn = sqlite3.connect(built["db"])
    rows = conn.execute("SELECT status, p_close, source FROM pass_status").fetchall()
    conn.close()
    assert {r[2] for r in rows} == {"model"}
    assert all(0 <= r[1] <= 1 for r in rows)
    assert all(r[1] == 1.0 for r in rows if r[0] == "CLOSED")
    assert {r[0] for r in rows} == {"OPEN", "AT_RISK", "CLOSED"}


# ------------------------------------------------------------------ planner


def test_every_need_is_met_and_the_solution_is_optimal(trained, needs):
    res = run(trained, needs)
    assert res["status"] == "optimal"
    moved = {}
    for it in res["items"]:
        moved[(it["post_id"], it["class"])] = (
            moved.get((it["post_id"], it["class"]), 0) + it["qty_t"]
        )
    for n in needs:
        assert moved.get((n["post_id"], n["class"]), 0) >= n["need_t"] - 1e-3


def test_no_truck_crosses_a_closed_pass_and_modes_respect_access(trained, needs):
    posts = {p["id"]: p for p in load_sector()["posts"]}
    conn = sqlite3.connect(trained)
    closed = {
        r[0]
        for r in conn.execute(
            "SELECT pass_id FROM pass_status WHERE as_of=? AND status='CLOSED'", (AS_OF,)
        )
    }
    conn.close()
    assert closed
    for it in run(trained, needs)["items"]:
        post = posts[it["post_id"]]
        if it["mode"] == "shortfall":
            continue
        assert it["mode"] in post["access"]
        if it["mode"] == "truck":
            assert post["via_pass"] not in closed


def test_every_item_has_a_reason(trained, needs):
    for it in run(trained, needs)["items"]:
        assert len(it["reason"]) > 30 and "P90 demand" in it["reason"]


def test_lower_helicopter_payload_means_more_sorties_and_cost(trained, needs):
    base = run(trained, needs)
    c = copy.deepcopy(load_constraints())
    c["modes"]["heli"]["payload_t"] = 0.4
    lighter = run(trained, needs, c)
    assert lighter["sorties_used"] > base["sorties_used"]
    assert lighter["cost"] > base["cost"]


def test_too_few_sorties_shows_up_as_shortfall_not_a_crash(trained, needs):
    c = copy.deepcopy(load_constraints())
    c["modes"]["heli"]["daily_sorties"] = 0
    res = run(trained, needs, c)
    assert res["tonnes_by_mode"].get("shortfall", 0) > 0
    assert any("SHORTFALL" in it["reason"] for it in res["items"] if it["mode"] == "shortfall")


def test_heli_payload_derates_with_altitude():
    c = load_constraints()
    assert S.heli_payload(c, 2500) == c["modes"]["heli"]["payload_t"]
    assert S.heli_payload(c, 4500) < S.heli_payload(c, 3500) < S.heli_payload(c, 2500)


# ------------------------------------------------------------------ audit chain


def test_audit_chain_detects_any_edit(tmp_path):
    from api.db import init_schema

    conn = connect(tmp_path / "a.db")
    init_schema(conn)
    for i in range(5):
        chain.append(conn, "event", {"i": i}, "lo", ts=f"2025-11-08T06:0{i}:00Z")
    assert chain.verify(conn) == {"valid": True, "broken_at": None, "entries": 5}
    conn.execute("UPDATE audit SET payload = '{\"i\":99}' WHERE seq = 3")
    assert chain.verify(conn)["broken_at"] == 3
    conn.close()


# ------------------------------------------------------------------ API: plan, approve, roles


@pytest.fixture
def client(trained, tmp_path, monkeypatch):
    copy_db = tmp_path / "p.db"
    shutil.copy(trained, copy_db)
    shutil.copy(model_path(trained), model_path(copy_db))
    shutil.copytree(models_dir(trained), models_dir(copy_db))
    monkeypatch.setenv("DB_PATH", str(copy_db))
    return TestClient(app)


def token(client, user):
    return client.post("/auth/token", json={"username": user, "password": "demo"}).json()[
        "access_token"
    ]


def test_only_a_logistics_officer_can_approve_and_it_is_audited(client):
    plan = client.post("/plan", json={"horizon_days": 30}).json()
    assert plan["status"] == "DRAFT" and plan["items"]
    pid = plan["id"]
    assert client.post(f"/plan/{pid}/approve").status_code == 401
    staff = {"Authorization": f"Bearer {token(client, 'staff')}"}
    assert client.post(f"/plan/{pid}/approve", headers=staff).status_code == 403
    lo = {"Authorization": f"Bearer {token(client, 'lo')}"}
    assert client.post(f"/plan/{pid}/approve", headers=lo).json()["status"] == "APPROVED"
    assert client.post(f"/plan/{pid}/approve", headers=lo).status_code == 409
    events = [a["event"] for a in client.get("/audit").json()]
    assert events[:2] == ["plan_approved", "plan_generated"]
    assert client.get("/audit/verify").json()["valid"] is True


def test_bad_credentials_and_tokens_are_refused(client):
    assert (
        client.post("/auth/token", json={"username": "lo", "password": "nope"}).status_code == 401
    )
    bad = {"Authorization": "Bearer not-a-token"}
    assert client.post("/plan/1/approve", headers=bad).status_code == 401


def test_forecast_endpoints(client):
    fc = client.get("/forecast/HANLE-03", params={"class": "fuel"}).json()
    assert len(fc["days"]) == 30 and fc["unit"] == "litre"
    assert all(d["p10"] <= d["p50"] <= d["p90"] for d in fc["days"])
    items = client.get("/forecast/items/HANLE-03").json()["items"]
    assert {i["class"] for i in items} == {"rations", "fuel", "medical", "ammunition", "spares"}
    assert client.get("/forecast/NOWHERE").status_code == 404
