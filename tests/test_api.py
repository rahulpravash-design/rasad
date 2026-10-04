from __future__ import annotations

import sqlite3

import pytest
from fastapi.testclient import TestClient

from api.main import app
from config.loader import load_constraints


@pytest.fixture
def client(db_path, monkeypatch):
    monkeypatch.setenv("DB_PATH", str(db_path))
    return TestClient(app)


def test_health_reports_provenance(client):
    body = client.get("/health").json()
    assert body["status"] == "ok"
    assert body["offline_mode"] is True
    assert body["data_loaded"] is True
    assert body["data"]["weather_source"] == "synthetic-climatology"
    assert body["data"]["consumption_source"] == "synthetic"
    assert body["data"]["seed"] == "42"


def test_health_works_without_a_database(tmp_path, monkeypatch):
    """Day 1 criterion: /health answers before `make data` has ever run."""
    monkeypatch.setenv("DB_PATH", str(tmp_path / "missing.db"))
    body = TestClient(app).get("/health").json()
    assert body == {"status": "ok", "offline_mode": True, "data_loaded": False}


def test_data_endpoints_say_what_to_do_without_a_database(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "missing.db"))
    resp = TestClient(app).get("/dashboard/kpis")
    assert resp.status_code == 503
    assert "make data" in resp.json()["detail"]


def test_kpis(client):
    k = client.get("/dashboard/kpis").json()
    assert k["as_of"] == load_constraints()["scenario"]["as_of"]
    assert (k["posts"], k["formations"], k["depots"]) == (42, 3, 5)
    assert k["post_classes_total"] == 42 * 5
    assert 0 < k["readiness_pct"] < 100
    assert k["readiness_pct"] == pytest.approx(
        100 * k["post_classes_ready"] / k["post_classes_total"], abs=0.05
    )
    assert k["convoys"] > 0
    assert k["passes_closed"] + k["passes_at_risk"] <= 4


def test_kpis_follow_as_of(client):
    early = client.get("/dashboard/kpis", params={"as_of": "2025-10-15"}).json()
    late = client.get("/dashboard/kpis", params={"as_of": "2026-02-15"}).json()
    assert early["as_of"] == "2025-10-15" and late["as_of"] == "2026-02-15"
    assert early["passes_closed"] == 0  # rule inactive before 1 Nov
    assert late["passes_closed"] >= early["passes_closed"]


def test_readiness_follows_live_constraint_edits(client, monkeypatch):
    """constraints.yaml is re-read per request: raising every minimum drops readiness."""
    base = client.get("/dashboard/kpis").json()["readiness_pct"]
    strict = load_constraints()
    strict["min_stock_days"] = {k: 60 for k in strict["min_stock_days"]}
    monkeypatch.setattr("api.routes.dashboard.load_constraints", lambda: strict)
    assert client.get("/dashboard/kpis").json()["readiness_pct"] < base


def test_passes_contract(client):
    body = client.get("/passes").json()
    assert body["as_of"] == load_constraints()["scenario"]["as_of"]
    rows = {p["pass"]: p for p in body["passes"]}
    assert set(rows) == {"ZOJI_LA", "KHARDUNG_LA", "CHANG_LA", "TANGLANG_LA"}
    for row in rows.values():
        assert {"pass", "status", "p_close", "days"} <= set(row)
        assert row["status"] in {"OPEN", "AT_RISK", "CLOSED"}
        assert row["p_close"] is None  # no model until Day 7
        assert row["source"] == "rule-label"
        assert (row["days"] is not None) == (row["status"] == "CLOSED")


def test_passes_show_a_mix_at_the_scenario_date(client):
    statuses = {p["status"] for p in client.get("/passes").json()["passes"]}
    assert {"OPEN", "CLOSED"} <= statuses


def test_unknown_or_malformed_as_of(client):
    assert client.get("/passes", params={"as_of": "2030-01-01"}).status_code == 404
    assert client.get("/passes", params={"as_of": "yesterday"}).status_code == 422


def test_geojson(client):
    g = client.get("/sector/geojson").json()
    kinds = [f["properties"]["kind"] for f in g["features"]]
    assert kinds.count("post") == 42 and kinds.count("depot") == 5 and kinds.count("pass") == 4
    post = next(f for f in g["features"] if f["properties"]["id"] == "HANLE-03")
    lon, lat = post["geometry"]["coordinates"]
    assert (lon, lat) == (78.98, 32.77)  # GeoJSON order is lon, lat
    assert post["properties"]["access"] == ["mule", "heli"]
    assert isinstance(post["properties"]["ready"], bool)
    zoji = next(f for f in g["features"] if f["properties"]["id"] == "ZOJI_LA")
    assert zoji["properties"]["status"] in {"OPEN", "AT_RISK", "CLOSED"}


def test_database_opens_read_only(client, db_path):
    conn = sqlite3.connect(f"{db_path.resolve().as_uri()}?mode=ro", uri=True)
    with pytest.raises(sqlite3.OperationalError):
        conn.execute("DELETE FROM posts")
    conn.close()


def test_scenario_date_matches_the_story_told_in_constraints_yaml(client):
    """constraints.yaml says Chang La and Khardung La are closed and the other two open on the
    scenario date. If the synthetic weather changes, this fails: update the comment with it."""
    status = {p["pass"]: p["status"] for p in client.get("/passes").json()["passes"]}
    assert status == {
        "CHANG_LA": "CLOSED",
        "KHARDUNG_LA": "CLOSED",
        "TANGLANG_LA": "OPEN",
        "ZOJI_LA": "OPEN",
    }
