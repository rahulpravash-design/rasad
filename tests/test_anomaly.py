from __future__ import annotations

import sqlite3

import pytest

from config.loader import load_constraints, load_sector
from eval.report import write_section
from gate import attacks, signing
from gate.anomaly import AnomalyScorer, Deferred, model_path, precompute
from gate.batch import deliveries_index, post_infos, to_wire
from gate.context import MemoryContext
from gate.eval_gate import evaluate, markdown, wilson
from gate.pipeline import FLAGGED, VERIFIED, Gate


@pytest.fixture(scope="module")
def scorer(built):
    return AnomalyScorer.load(model_path(built["db"]))


@pytest.fixture(scope="module")
def stream(built):
    import pandas as pd

    conn = sqlite3.connect(built["db"])
    conn.row_factory = sqlite3.Row
    rows = [
        to_wire(dict(r))
        for r in conn.execute("SELECT * FROM reports ORDER BY ts, post_id, supply_class")
    ]
    keys = {r["post_id"]: r["public_key"] for r in conn.execute("SELECT * FROM post_keys")}
    deliveries = pd.read_sql("SELECT * FROM deliveries", conn)
    deliveries["date"] = pd.to_datetime(deliveries["date"])
    conn.close()
    return rows, keys, deliveries_index(deliveries)


def context_before(stream, idx):
    rows, keys, deliveries = stream
    posts = {p: (i.formation, i.troops) for p, i in post_infos(load_sector()).items()}
    ctx = MemoryContext(keys, deliveries, posts=posts)
    for r in rows[:idx]:
        ctx.commit(r, VERIFIED)
    return ctx


def locate(stream, post, cls, date):
    rows, _, _ = stream
    return next(
        i
        for i, r in enumerate(rows)
        if (r["post_id"], r["class"], r["ts"][:10]) == (post, cls, date)
    )


def test_one_forest_and_threshold_per_class(scorer):
    assert (
        set(scorer.models)
        == set(scorer.thresholds)
        == {"rations", "fuel", "medical", "ammunition", "spares"}
    )
    assert all(0 < t < 1 for t in scorer.thresholds.values())


def test_threshold_flags_the_target_share_of_training_reports(scorer):
    target = load_constraints()["gate"]["anomaly_false_alarm_target"]
    for cls, scores in scorer.train_scores.items():
        share = float((scores > scorer.thresholds[cls]).mean())
        assert share == pytest.approx(target, abs=0.002), cls


def test_trained_on_pre_holdout_winters_only(scorer):
    """F3 has one pre-holdout winter; the others have four. Training rows reflect that."""
    sector = load_sector()
    n_posts = {f: sum(p["formation"] == f for p in sector["posts"]) for f in sector["formations"]}
    expected = (4 * (n_posts["F1"] + n_posts["F2"]) + n_posts["F3"]) * 182  # ~days per winter
    assert abs(len(scorer.train_scores["rations"]) - expected) < 0.02 * expected


def test_features_of_an_ordinary_report_are_unremarkable(scorer, stream):
    idx = locate(stream, "DRASS-01", "rations", "2025-12-10")
    f = scorer.featurize(stream[0][idx], context_before(stream, idx))
    assert abs(f.z28) < 3
    assert 0.3 < f.ratio_formation < 3
    assert f.window_mean is not None and f.window_mean > 0


def test_z_score_tracks_a_report_that_is_far_above_the_posts_own_history(scorer, stream):
    idx = locate(stream, "DRASS-01", "rations", "2025-12-10")
    ctx = context_before(stream, idx)
    genuine = stream[0][idx]
    big = {**genuine, "consumed": genuine["consumed"] * 3}
    assert scorer.featurize(big, ctx).z28 > scorer.featurize(genuine, ctx).z28 + 5


def test_no_history_means_no_z_score_rather_than_a_wrong_one(scorer, stream):
    rows, keys, deliveries = stream
    empty = MemoryContext(
        keys, deliveries, posts={p: (i.formation, i.troops) for p, i in scorer.posts.items()}
    )
    f = scorer.featurize(rows[5000], empty)
    assert f.z28 == 0.0 and f.window_mean is None


def test_a_large_spike_scores_above_an_ordinary_day(scorer, stream):
    idx = locate(stream, "DRASS-01", "fuel", "2025-12-10")
    ctx = context_before(stream, idx)
    genuine = stream[0][idx]
    spike = {**genuine, "consumed": genuine["consumed"] * 4}
    normal_score, normal_reasons = scorer.score(genuine, ctx)
    spike_score, spike_reasons = scorer.score(spike, ctx)
    assert spike_score > normal_score
    assert spike_reasons and spike_reasons[0].code == "anomalous_consumption"
    assert "x this post's 28-day average" in spike_reasons[0].message
    assert not normal_reasons


def test_batch_scoring_matches_single_scoring(scorer, stream):
    rows, _, _ = stream
    idxs = [locate(stream, "LEH-01", c, "2025-12-12") for c in ("rations", "fuel", "medical")]
    items, singles = [], {}
    for i in idxs:
        ctx = context_before(stream, i)
        items.append((rows[i], scorer.featurize(rows[i], ctx)))
        singles[rows[i]["report_id"]] = scorer.score(rows[i], ctx)
    table = precompute(scorer, items)
    for rid, (score, reasons) in singles.items():
        assert table[rid][0] == pytest.approx(score, abs=1e-4)
        assert [r.code for r in table[rid][1]] == [r.code for r in reasons]


def test_deferred_scorer_returns_the_precomputed_answer_or_nothing(scorer, stream):
    d = Deferred({"abc": (0.9, [])})
    assert d.score({"report_id": "abc"}, None) == (0.9, [])
    assert d.score({"report_id": "zzz"}, None) == (0.0, [])


def test_the_gate_flags_a_spike_but_never_rejects_it(scorer, stream):
    idx = locate(stream, "DRASS-01", "fuel", "2025-12-10")
    ctx = context_before(stream, idx)
    genuine = stream[0][idx]
    spike = {**genuine, "consumed": genuine["consumed"] * 4}
    spike["closing"] = spike["opening"] + spike["received"] - spike["consumed"]
    if spike["closing"] < 0:
        pytest.skip("spike would overdraw the stock")
    spike["sig"] = signing.sign(spike, signing.private_key_from_seed("DRASS-01", 42))
    verdict = Gate({"fuel", "rations", "medical", "ammunition", "spares"}, scorer=scorer).evaluate(
        spike, ctx
    )
    assert verdict.verdict == FLAGGED
    assert verdict.codes == ["anomalous_consumption"]
    assert verdict.score is not None


# ------------------------------------------------------------------ the evaluation harness


@pytest.fixture(scope="module")
def result(built):
    return evaluate(built["db"], seed=42, rate=0.05)


def test_eval_forged_and_replayed_reports_are_caught_every_time(result):
    for attack in (attacks.FORGED, attacks.REPLAY):
        a = result["attacks"][attack]
        assert a["built"] > 400 and a["missed"] == 0 and a["detection_rate"] == 1.0
        assert a["rejected"] == a["built"]


def test_eval_deflated_stock_is_caught_almost_always(result):
    assert result["attacks"][attacks.DEFLATED]["detection_rate"] > 0.95


def test_eval_inflated_consumption_is_the_hard_case_and_the_numbers_say_so(result):
    inflated = result["attacks"][attacks.INFLATED]["detection_rate"]
    assert 0.5 < inflated < 0.97  # caught often, nowhere near always
    by_class = result["inflated_by_class"]
    assert (
        by_class["ammunition"]["detected"] / by_class["ammunition"]["n"] < 0.6
    )  # surges look alike


def test_eval_false_alarms_are_flags_near_the_target_and_never_rejections(result):
    clean = result["clean"]
    assert clean["n"] > 30_000
    assert 0.003 < clean["false_alarm_rate"] < 0.03
    assert set(clean["reasons"]) == {"anomalous_consumption"}


def test_eval_threshold_sweep_trades_false_alarms_for_detection(result):
    sweep = result["anomaly_threshold_sweep"]
    far = [s["holdout_false_alarm_rate"] for s in sweep]
    det = [s["inflated_detection_rate"] for s in sweep]
    assert far == sorted(far) and det == sorted(det)


def test_eval_attack_counts_are_balanced_across_types(result):
    built = [a["built"] for a in result["attacks"].values()]
    assert max(built) - min(built) < 0.1 * max(built)


def test_eval_is_deterministic(built, result):
    assert evaluate(built["db"], seed=42, rate=0.05) == result


def test_eval_markdown_names_every_attack_and_the_blind_spot(result):
    text = markdown(result)
    for label in ("Forged signature", "Replay", "Inflated consumption", "Deflated stock"):
        assert label in text
    assert "Where the gate is blind" in text and "False alarms on clean reports" in text


def test_wilson_interval_is_sane():
    lo, hi = wilson(50, 100)
    assert lo < 0.5 < hi and 0.39 < lo and hi < 0.61
    assert wilson(0, 0) == (0.0, 0.0)
    assert wilson(100, 100)[1] == pytest.approx(1.0)


def test_write_section_replaces_only_its_own_section(tmp_path):
    path = tmp_path / "results.md"
    write_section("gate", "first", path)
    write_section("other", "keep me", path)
    write_section("gate", "second", path)
    text = path.read_text()
    assert "second" in text and "first" not in text and "keep me" in text
    assert text.count("<!-- gate:start -->") == 1
    assert text.startswith("# Results")
