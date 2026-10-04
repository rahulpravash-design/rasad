from __future__ import annotations

import shutil
import sqlite3

import pytest
from fastapi.testclient import TestClient

from api.main import app
from config.loader import load_constraints
from gate.anomaly import model_path

AS_OF = load_constraints()["scenario"]["as_of"]


@pytest.fixture
def db(db_path, tmp_path, monkeypatch):
    """A private copy of the database: these tests write."""
    copy = tmp_path / "copy.db"
    shutil.copy(db_path, copy)
    shutil.copy(model_path(db_path), model_path(copy))
    monkeypatch.setenv("DB_PATH", str(copy))
    return copy


@pytest.fixture
def client(db):
    return TestClient(app)


def genuine(db, post="DRASS-01", cls="rations", date=AS_OF):
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    row = dict(
        conn.execute(
            "SELECT * FROM reports WHERE post_id=? AND supply_class=? AND report_date=?",
            (post, cls, date),
        ).fetchone()
    )
    conn.close()
    return {
        "report_id": row["report_id"],
        "post_id": row["post_id"],
        "ts": row["ts"],
        "class": row["supply_class"],
        "opening": row["opening"],
        "received": row["received"],
        "consumed": row["consumed"],
        "closing": row["closing"],
        "nonce": row["nonce"],
        "sig": row["sig"],
    }


def remove_slot(db, post="DRASS-01", cls="rations", date=AS_OF):
    conn = sqlite3.connect(db)
    rid = conn.execute(
        "SELECT report_id FROM reports WHERE post_id=? AND supply_class=? AND report_date=?",
        (post, cls, date),
    ).fetchone()[0]
    conn.execute("DELETE FROM gate_verdicts WHERE report_id=?", (rid,))
    conn.execute("DELETE FROM reports WHERE report_id=?", (rid,))
    conn.commit()
    conn.close()


# ------------------------------------------------------------------ submitting


def test_a_genuine_report_that_arrives_verifies(client, db):
    report = genuine(db)
    remove_slot(db)  # as if it had not been filed yet
    body = client.post("/reports", json=report).json()
    assert body["verdict"] == "VERIFIED"
    assert body["reasons"] == []


def test_the_same_report_sent_again_is_a_rejected_replay(client, db):
    report = genuine(db)
    remove_slot(db)
    assert client.post("/reports", json=report).json()["verdict"] == "VERIFIED"
    again = client.post("/reports", json=report).json()
    assert again["verdict"] == "REJECTED"
    assert "replay" in again["reason_codes"]
    assert again["report_id"].endswith("~dup1")  # the attempt is kept, not overwritten


def test_a_tampered_field_is_rejected_with_a_reason(client, db):
    report = genuine(db)
    remove_slot(db)
    report["consumed"] += 5
    report["closing"] -= 5  # arithmetic still balances, signature does not
    body = client.post("/reports", json=report).json()
    assert body["verdict"] == "REJECTED"
    assert body["reason_codes"] == ["bad_signature"]
    assert "signature" in body["reasons"][0]


def test_an_accepted_report_for_an_already_reported_slot_is_refused(client, db):
    report = genuine(db)
    other = {**report, "report_id": "other-id", "nonce": "00000000000000bb"}
    # Re-sign so only the duplicate slot is wrong.
    from gate import signing

    other["sig"] = signing.sign(other, signing.private_key_from_seed(report["post_id"], 42))
    resp = client.post("/reports", json=other)
    assert resp.status_code == 409
    assert "already has an accepted report" in resp.json()["detail"]


def test_malformed_json_is_a_422_not_a_crash(client):
    assert client.post("/reports", json={"post_id": "DRASS-01"}).status_code == 422
    bad = {
        "report_id": "x",
        "post_id": "p",
        "ts": "t",
        "class": "rations",
        "opening": "5",
        "received": 0,
        "consumed": 0,
        "closing": 0,
        "nonce": "n",
        "sig": "s",
    }
    assert client.post("/reports", json=bad).status_code == 422


# ------------------------------------------------------------------ injecting


@pytest.mark.parametrize(
    ("attack", "verdict", "code"),
    [
        ("forged_signature", "REJECTED", "bad_signature"),
        ("replay", "REJECTED", "replay"),
        ("deflated_stock", "FLAGGED", "stock_discontinuity"),
    ],
)
def test_inject_is_caught_with_the_expected_reason(client, attack, verdict, code):
    body = client.post(
        "/reports/inject",
        json={"attack_type": attack, "post_id": "DRASS-01", "supply_class": "rations"},
    ).json()
    assert body["verdict"]["verdict"] == verdict
    assert code in body["verdict"]["reason_codes"]
    assert body["detected"] is True
    assert body["report"]["origin"] == "injected"
    assert body["verdict"]["reasons"]  # something a person can read


def test_injected_reports_never_reach_analytics(client):
    before = client.get("/dashboard/kpis").json()
    for attack in ("forged_signature", "replay", "inflated_consumption", "deflated_stock"):
        resp = client.post("/reports/inject", json={"attack_type": attack})
        assert resp.status_code == 200, resp.text
    assert client.get("/dashboard/kpis").json() == before


def test_injected_reports_show_up_in_the_list_and_summary(client):
    client.post(
        "/reports/inject",
        json={"attack_type": "forged_signature", "post_id": "DRASS-01", "supply_class": "rations"},
    )
    listing = client.get("/reports", params={"origin": "injected"}).json()
    assert listing["total"] == 1
    assert listing["items"][0]["verdict"] == "REJECTED"
    summary = client.get("/reports/summary").json()
    assert summary["injected"]["REJECTED"] == 1
    assert summary["field"]["VERIFIED"] > 100_000


def test_inject_picks_a_target_when_none_is_given(client):
    body = client.post("/reports/inject", json={"attack_type": "forged_signature"}).json()
    assert body["target"].endswith(f"on {AS_OF}")


def test_inject_rejects_an_unknown_attack_type(client):
    assert client.post("/reports/inject", json={"attack_type": "sabotage"}).status_code == 422


def test_inject_for_a_post_with_no_report_is_a_404(client):
    resp = client.post("/reports/inject", json={"attack_type": "replay", "post_id": "NOWHERE-01"})
    assert resp.status_code == 404


# ------------------------------------------------------------------ listing and re-verifying


def test_list_filters_by_status_and_respects_as_of(client):
    everything = client.get("/reports", params={"limit": 5}).json()
    assert everything["as_of"] == AS_OF
    assert len(everything["items"]) == 5
    assert all(i["ts"][:10] <= AS_OF for i in everything["items"])
    assert everything["items"][0]["ts"] >= everything["items"][-1]["ts"]  # newest first
    assert client.get("/reports", params={"status": "REJECTED"}).json()["total"] == 0
    early = client.get("/reports", params={"as_of": "2024-12-01", "limit": 1}).json()
    assert early["items"][0]["ts"].startswith("2024-12-01")


def test_list_items_use_the_wire_field_name_class(client):
    item = client.get("/reports", params={"limit": 1}).json()["items"][0]
    assert "class" in item and "class_" not in item
    assert item["sig"]


def test_reverify_a_clean_report_verifies_and_changes_nothing(client, db):
    report = genuine(db)
    before = client.get("/reports/summary").json()
    body = client.post(f"/reports/{report['report_id']}/verify").json()
    assert body["verdict"] == "VERIFIED"
    assert client.get("/reports/summary").json() == before


def test_reverify_unknown_report_is_a_404(client):
    assert client.post("/reports/nope/verify").status_code == 404


def test_writable_connection_survives_a_thread_hop(db):
    """Regression: FastAPI may open a connection on one thread and use it on another."""
    import threading

    from api.db import connect

    conn = connect(db)
    errors = []

    def use():
        try:
            conn.execute("SELECT COUNT(*) FROM posts").fetchone()
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    t = threading.Thread(target=use)
    t.start()
    t.join()
    conn.close()
    assert errors == []
