from __future__ import annotations

import pytest

from eval.run_winters import load_inputs, markdown, simulate, summarise


@pytest.fixture(scope="module")
def runs(built):
    from forecast.service import models_dir
    from forecast.train import train_all

    if not (models_dir(built["db"]) / "federated.npz").exists():
        train_all(built["db"], seed=42)
    inp = load_inputs(built["db"])
    return [simulate(inp, k, 42) for k in range(2)]


def test_both_policies_report_every_metric(runs):
    for r in runs:
        for policy in ("baseline", "rasad"):
            assert set(r[policy]) == {
                "stockout_days",
                "truck",
                "mule",
                "heli_planned",
                "heli_emergency",
                "sorties",
                "cost",
            }
            assert all(v >= 0 for v in r[policy].values())


def test_baseline_never_uses_mules_or_planned_helicopters(runs):
    for r in runs:
        assert r["baseline"]["mule"] == 0 and r["baseline"]["heli_planned"] == 0


def test_simulation_is_deterministic(built, runs):
    assert simulate(load_inputs(built["db"]), 0, 42) == runs[0]


def test_summary_and_markdown(runs):
    s = summarise(runs)
    assert s["n"] == 2 and "diff_ci95" in s["cost"]
    text = markdown(s, 42)
    assert "Emergency airlift" in text and "Fixed-scale baseline" in text
