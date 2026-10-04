"""The gate and the four attacks, against a real built database (see conftest.built)."""

from __future__ import annotations

import sqlite3
from collections import defaultdict
from datetime import datetime

import numpy as np
import pytest

from config.loader import load_consumption
from gate import attacks, signing
from gate.batch import deliveries_index, to_wire
from gate.context import MemoryContext
from gate.pipeline import FLAGGED, REJECTED, VERIFIED, Gate

SEED = 42


@pytest.fixture(scope="module")
def gate():
    return Gate(set(load_consumption()["classes"]))


@pytest.fixture(scope="module")
def stream(built):
    """The whole clean history as wire reports in time order, plus the delivery index and keys."""
    conn = sqlite3.connect(built["db"])
    conn.row_factory = sqlite3.Row
    rows = [
        to_wire(dict(r))
        for r in conn.execute("SELECT * FROM reports ORDER BY ts, post_id, supply_class")
    ]
    keys = {r["post_id"]: r["public_key"] for r in conn.execute("SELECT * FROM post_keys")}
    import pandas as pd

    deliveries = pd.read_sql("SELECT * FROM deliveries", conn)
    deliveries["date"] = pd.to_datetime(deliveries["date"])
    conn.close()
    return rows, keys, deliveries_index(deliveries)


def fresh_ctx(stream, upto: int, now: datetime | None = None) -> MemoryContext:
    """A gate memory that has accepted the first `upto` reports of the clean history."""
    rows, keys, deliveries = stream
    ctx = MemoryContext(keys, deliveries, now=now)
    for r in rows[:upto]:
        ctx.commit(r, VERIFIED)
    return ctx


def pick(stream, post="DRASS-01", cls="rations", date="2025-12-10"):
    rows, _, _ = stream
    idx = next(
        i
        for i, r in enumerate(rows)
        if (r["post_id"], r["class"], r["ts"][:10]) == (post, cls, date)
    )
    return idx, rows[idx]


# ------------------------------------------------------------------ the clean history


def test_no_clean_report_in_the_build_is_rejected_and_few_are_flagged(built):
    """Hard rules never fire on clean data; the anomaly layer flags about its 1% target."""
    conn = sqlite3.connect(built["db"])
    counts = dict(conn.execute("SELECT verdict, COUNT(*) FROM gate_verdicts GROUP BY verdict"))
    conn.close()
    assert set(counts) <= {"VERIFIED", "FLAGGED"}
    assert sum(counts.values()) == built["summary"]["report_rows"]
    assert 0.005 < counts["FLAGGED"] / sum(counts.values()) < 0.02


def test_every_clean_report_carries_a_valid_signature(stream):
    rows, keys, _ = stream
    assert all(signing.verify(r, keys[r["post_id"]]) for r in rows[::50])  # every 50th: speed


# ------------------------------------------------------------------ individual rules


def test_a_genuine_report_verifies_against_the_state_before_it(stream, gate):
    idx, report = pick(stream)
    assert gate.evaluate(report, fresh_ctx(stream, idx)).verdict == VERIFIED


@pytest.mark.parametrize(
    ("mutate", "code"),
    [
        (lambda r: {**r, "closing": r["closing"] + 1}, "balance_mismatch"),
        (
            lambda r: {**r, "opening": -5, "closing": -5 + r["received"] - r["consumed"]},
            "negative_quantity",
        ),
        (lambda r: {**r, "consumed": r["consumed"] + 1}, "bad_signature"),
        (lambda r: {**r, "post_id": "NOWHERE-01"}, "unknown_post"),
        (lambda r: {k: v for k, v in r.items() if k != "nonce"}, "malformed"),
        (lambda r: {**r, "ts": "yesterday"}, "malformed"),
        (lambda r: {**r, "class": "tanks"}, "malformed"),
        (lambda r: {**r, "opening": "420"}, "malformed"),
    ],
)
def test_hard_rules_reject(stream, gate, mutate, code):
    idx, report = pick(stream)
    verdict = gate.evaluate(mutate(report), fresh_ctx(stream, idx))
    assert verdict.verdict == REJECTED
    assert code in verdict.codes


def test_timestamp_in_the_future_is_rejected(stream, gate):
    idx, report = pick(stream)
    ctx = fresh_ctx(stream, idx, now=datetime(2025, 12, 9, 12, 0))
    assert "timestamp_in_future" in gate.evaluate(report, ctx).codes


def test_timestamp_must_be_after_the_previous_report(stream, gate):
    """A report older than one the gate has already accepted is stale, however well it is signed."""
    idx, report = pick(stream)
    stale = {
        **report,
        "report_id": "stale-1",
        "nonce": "00000000000000aa",
        "ts": "2025-12-08T06:00Z",
    }
    stale["sig"] = signing.sign(stale, signing.private_key_from_seed(report["post_id"], SEED))
    verdict = gate.evaluate(stale, fresh_ctx(stream, idx))  # yesterday (12-09) is already accepted
    assert verdict.verdict == REJECTED
    assert verdict.codes == ["timestamp_not_after_previous"]


def test_replay_is_rejected_once_the_original_is_accepted(stream, gate):
    idx, report = pick(stream)
    ctx = fresh_ctx(stream, idx)
    assert gate.evaluate(report, ctx).verdict == VERIFIED
    ctx.commit(report, VERIFIED)
    again = gate.evaluate(report, ctx)
    assert again.verdict == REJECTED
    assert {"replay", "timestamp_not_after_previous"} <= set(again.codes)


def test_a_rejected_report_leaves_no_trace_so_it_cannot_block_the_real_one(stream, gate):
    idx, report = pick(stream)
    ctx = fresh_ctx(stream, idx)
    forged = {**report, "sig": "A" * 86 + "=="}
    assert gate.evaluate(forged, ctx).verdict == REJECTED
    ctx.commit(forged, REJECTED)
    assert gate.evaluate(report, ctx).verdict == VERIFIED


def test_stock_discontinuity_is_flagged(stream, gate):
    idx, report = pick(stream)
    key = signing.private_key_from_seed(report["post_id"], SEED)
    skewed = {**report, "opening": report["opening"] - 10, "closing": report["closing"] - 10}
    skewed["sig"] = signing.sign(skewed, key)
    verdict = gate.evaluate(skewed, fresh_ctx(stream, idx))
    assert verdict.verdict == FLAGGED
    assert verdict.codes == ["stock_discontinuity"]


def test_discontinuity_is_not_assessed_after_a_flagged_report(stream, gate):
    """One alert must not turn the next honest report into another."""
    idx, report = pick(stream)
    rows, keys, deliveries = stream
    ctx = MemoryContext(keys, deliveries)
    for r in rows[:idx]:
        ctx.commit(r, VERIFIED)
    yesterday = next(
        r
        for r in reversed(rows[:idx])
        if (r["post_id"], r["class"]) == (report["post_id"], report["class"])
    )
    ctx.commit(yesterday, FLAGGED)  # yesterday's report is now only flagged
    skewed = {**report, "opening": report["opening"] - 10, "closing": report["closing"] - 10}
    skewed["sig"] = signing.sign(skewed, signing.private_key_from_seed(report["post_id"], SEED))
    assert "stock_discontinuity" not in gate.evaluate(skewed, ctx).codes


def test_receipt_without_a_matching_delivery_is_flagged(stream, gate):
    idx, report = pick(stream)
    assert report["received"] == 0
    padded = {**report, "received": 50, "closing": report["closing"] + 50}
    padded["sig"] = signing.sign(padded, signing.private_key_from_seed(report["post_id"], SEED))
    verdict = gate.evaluate(padded, fresh_ctx(stream, idx))
    assert verdict.verdict == FLAGGED
    assert verdict.codes == ["receipt_mismatch"]


# ------------------------------------------------------------------ the four attacks


def _positions(stream, n, seed):
    rows, _, _ = stream
    rng = np.random.default_rng(seed)
    # Skip the first day of each winter (no previous report) and the very start of the stream.
    candidates = [i for i, r in enumerate(rows) if i > 2000 and not r["ts"][5:10] == "10-01"]
    return sorted(candidates[int(i)] for i in rng.choice(len(candidates), n, replace=False))


def walk(stream, positions):
    """Yield (index, context) for sorted positions, advancing ONE context through the clean history
    rather than rebuilding it each time. Evaluating never changes a context, so it stays valid."""
    rows, keys, deliveries = stream
    ctx = MemoryContext(keys, deliveries)
    done = 0
    recent: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for idx in positions:
        while done < idx:
            r = rows[done]
            ctx.commit(r, VERIFIED)
            recent[(r["post_id"], r["class"])].append(r)
            done += 1
        yield idx, ctx, recent


def run_attack(stream, gate, attack, positions, seed=7):
    """Evaluate `attack` against the genuine report at each position; returns a list of verdicts
    (positions where the attack cannot be built are skipped)."""
    rows, _, _ = stream
    rng = np.random.default_rng(seed)
    out = []
    for idx, ctx, recent in walk(stream, positions):
        genuine = rows[idx]
        earlier = recent[(genuine["post_id"], genuine["class"])][-10:]
        key = signing.private_key_from_seed(genuine["post_id"], SEED)
        report = attacks.make_attack(attack, genuine, rng, key, SEED, earlier)
        if report is not None:
            out.append(gate.evaluate(report, ctx))
    return out


@pytest.mark.parametrize("attack", [attacks.FORGED, attacks.REPLAY])
def test_forged_and_replayed_reports_are_rejected_100_percent(stream, gate, attack):
    """The Day 3 done-criterion."""
    verdicts = run_attack(stream, gate, attack, _positions(stream, 300, seed=11))
    assert len(verdicts) >= 280
    assert {v.verdict for v in verdicts} == {REJECTED}


def test_forged_reports_are_caught_only_by_the_signature(stream, gate):
    verdicts = run_attack(stream, gate, attacks.FORGED, _positions(stream, 50, seed=5))
    assert len(verdicts) == 50
    assert all(v.codes == ["bad_signature"] for v in verdicts)


def test_replays_are_caught_by_replay_and_timestamp_rules(stream, gate):
    verdicts = run_attack(stream, gate, attacks.REPLAY, _positions(stream, 80, seed=5))
    assert len(verdicts) >= 70
    assert all({"replay", "timestamp_not_after_previous"} <= set(v.codes) for v in verdicts)


def test_insider_attacks_are_validly_signed_and_balanced(stream):
    """What makes them hard: the cheap checks have nothing to object to."""
    rows, keys, _ = stream
    rng = np.random.default_rng(1)
    for attack in (attacks.INFLATED, attacks.DEFLATED):
        built = 0
        for idx in _positions(stream, 40, seed=2):
            genuine = rows[idx]
            key = signing.private_key_from_seed(genuine["post_id"], SEED)
            report = attacks.make_attack(attack, genuine, rng, key, SEED)
            if report is None:
                continue
            built += 1
            assert signing.verify(report, keys[report["post_id"]])
            assert report["closing"] == report["opening"] + report["received"] - report["consumed"]
            assert min(report["opening"], report["closing"], report["consumed"]) >= 0
            assert report["report_id"] != genuine["report_id"]
            assert report["nonce"] != genuine["nonce"]
        assert built >= 30


def test_inflated_consumption_raises_consumed_and_deflated_stock_lowers_stock(stream):
    rows, _, _ = stream
    rng = np.random.default_rng(1)
    idx = next(
        i
        for i in _positions(stream, 60, seed=2)
        if rows[i]["opening"] > 20 and rows[i]["closing"] > 20
    )
    g = rows[idx]
    key = signing.private_key_from_seed(g["post_id"], SEED)
    inflated = attacks.make_attack(attacks.INFLATED, g, rng, key, SEED)
    deflated = attacks.make_attack(attacks.DEFLATED, g, rng, key, SEED)
    assert inflated["consumed"] > g["consumed"] and inflated["opening"] == g["opening"]
    assert deflated["opening"] < g["opening"] and deflated["consumed"] == g["consumed"]


def test_deflated_stock_is_flagged_by_continuity(stream, gate):
    verdicts = run_attack(stream, gate, attacks.DEFLATED, _positions(stream, 80, seed=9))
    assert len(verdicts) >= 60
    assert all(v.verdict == FLAGGED for v in verdicts)
    assert all(v.codes == ["stock_discontinuity"] for v in verdicts)


def test_unknown_attack_type_is_an_error(stream):
    with pytest.raises(ValueError, match="unknown attack"):
        attacks.make_attack("sabotage", stream[0][5000], np.random.default_rng(0), None, SEED)
