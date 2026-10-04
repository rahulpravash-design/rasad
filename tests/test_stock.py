from __future__ import annotations

import sqlite3

import pandas as pd
import pytest

from config.loader import load_constraints, load_sector, supply_classes


@pytest.fixture(scope="module")
def tables(built):
    conn = sqlite3.connect(built["db"])
    out = {
        "reports": pd.read_sql("SELECT * FROM reports", conn),
        "deliveries": pd.read_sql("SELECT * FROM deliveries", conn),
        "posts": pd.read_sql("SELECT * FROM posts", conn),
    }
    conn.close()
    return out


def test_balance_identity_on_every_report(tables):
    r = tables["reports"]
    assert (r["closing"] == r["opening"] + r["received"] - r["consumed"]).all()


def test_no_negative_quantities(tables):
    r = tables["reports"]
    assert (r[["opening", "received", "consumed", "closing"]] >= 0).all().all()


def test_stock_carries_over_day_to_day_within_a_winter(tables):
    r = tables["reports"].sort_values(["post_id", "supply_class", "report_date"])
    year = pd.to_datetime(r["report_date"]).dt.year
    month = pd.to_datetime(r["report_date"]).dt.month
    r["winter"] = year.where(month >= 10, year - 1)
    grp = r.groupby(["post_id", "supply_class", "winter"])
    prev_closing = grp["closing"].shift()
    mask = prev_closing.notna()
    assert (r.loc[mask, "opening"] == prev_closing[mask]).all()


def test_received_matches_deliveries_exactly(tables):
    """The Day 3 rule 'received with no matching delivery' depends on this."""
    r, d = tables["reports"], tables["deliveries"]
    got = r[r["received"] > 0].set_index(["post_id", "supply_class", "report_date"])["received"]
    want = d.set_index(["post_id", "supply_class", "date"])["qty"]
    pd.testing.assert_series_equal(
        got.sort_index(), want.sort_index(), check_names=False, check_index_type=False
    )


def test_one_report_per_post_class_day_with_unique_ids_and_nonces(tables):
    r = tables["reports"]
    assert not r.duplicated(["post_id", "supply_class", "report_date"]).any()
    assert r["report_id"].is_unique
    assert r["nonce"].is_unique
    assert r["nonce"].str.fullmatch(r"[0-9a-f]{16}").all()
    assert r["ts"].str.fullmatch(r"\d{4}-\d{2}-\d{2}T06:\d{2}Z").all()
    assert (r["ts"].str[:10] == r["report_date"]).all()
    assert r["sig"].str.len().eq(88).all()  # base64 of a 64-byte Ed25519 signature


def test_every_post_and_class_reports_every_winter_day(tables):
    r = tables["reports"]
    assert set(r["supply_class"]) == set(supply_classes())
    assert set(r["post_id"]) == {p["id"] for p in load_sector()["posts"]}
    f3 = tables["posts"].set_index("id")["formation"].eq("F3")
    f3_first = r[r["post_id"].map(f3)]["report_date"].min()
    assert f3_first == "2024-10-01"
    assert r[~r["post_id"].map(f3)]["report_date"].min() == "2021-10-01"


def test_delivery_modes_respect_access(tables):
    d = tables["deliveries"].merge(tables["posts"], left_on="post_id", right_on="id")
    import json

    allowed = d["access"].map(json.loads)
    assert all(mode in acc for mode, acc in zip(d["mode"], allowed, strict=True))
    # Posts with no road never get trucks.
    no_road = d[~allowed.map(lambda a: "truck" in a)]
    assert not (no_road["mode"] == "truck").any()


def test_trucks_only_move_while_the_pass_is_open(built):
    conn = sqlite3.connect(built["db"])
    bad = conn.execute(
        """
        SELECT COUNT(*) FROM deliveries d
        JOIN posts p ON p.id = d.post_id
        JOIN pass_status ps ON ps.pass_id = p.via_pass AND ps.as_of = d.date
        WHERE d.mode = 'truck' AND ps.status = 'CLOSED'
        """
    ).fetchone()[0]
    conn.close()
    assert bad == 0


def test_stockouts_are_rare_and_counted(built, tables):
    r = tables["reports"]
    empty = int((r["closing"] == 0).sum())
    assert empty / len(r) < 0.01
    # Every clipped (stock-out) day ends at zero, so the recorded count can never exceed the rows
    # that end at zero; exact exhaustion without clipping makes up any difference.
    stockouts = int(built["summary"]["stockout_days"])
    assert 0 < stockouts <= empty


def test_consumption_magnitudes_are_plausible(tables):
    r = tables["reports"].merge(tables["posts"], left_on="post_id", right_on="id")
    rations = r[r["supply_class"] == "rations"]
    per_soldier = (rations["consumed"] / rations["troops"]).median()
    assert 2.4 < per_soldier < 3.2  # 2.5 kg base plus an altitude/cold uplift


def test_readiness_thresholds_come_from_config():
    assert set(load_constraints()["min_stock_days"]) == set(supply_classes())
