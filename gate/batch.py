"""Batch gate work for the data build: sign the generated history and run it through the gate."""

from __future__ import annotations

import json
from datetime import date
from typing import Any

import pandas as pd

from gate import signing
from gate.anomaly import (
    AnomalyScorer,
    Deferred,
    PostInfo,
    fit_temp_model,
    precompute,
)
from gate.context import MemoryContext
from gate.pipeline import VERIFIED, Gate


def to_wire(row: dict) -> dict:
    """DataFrame row -> the JSON contract (supply_class is called "class" on the wire)."""
    return {
        "report_id": row["report_id"],
        "post_id": row["post_id"],
        "ts": row["ts"],
        "class": row["supply_class"],
        "opening": int(row["opening"]),
        "received": int(row["received"]),
        "consumed": int(row["consumed"]),
        "closing": int(row["closing"]),
        "nonce": row["nonce"],
        "sig": row.get("sig"),
    }


def public_keys(post_ids: list[str], seed: int) -> dict[str, str]:
    return {p: signing.public_key_b64(signing.private_key_from_seed(p, seed)) for p in post_ids}


def sign_reports(reports: pd.DataFrame, seed: int) -> pd.DataFrame:
    """Each report is signed with its own post's simulated key."""
    keys = {p: signing.private_key_from_seed(p, seed) for p in reports["post_id"].unique()}
    sigs = [signing.sign(to_wire(r), keys[r["post_id"]]) for r in reports.to_dict("records")]
    return reports.assign(sig=sigs)


def deliveries_index(deliveries: pd.DataFrame) -> dict[tuple[str, str, str], int]:
    index: dict[tuple[str, str, str], int] = {}
    for r in deliveries.itertuples():
        key = (r.post_id, r.supply_class, r.date.strftime("%Y-%m-%d"))
        index[key] = index.get(key, 0) + int(r.qty)
    return index


def post_infos(sector: dict[str, Any]) -> dict[str, PostInfo]:
    return {
        p["id"]: PostInfo(p["formation"], int(p["troops"]), int(p["altitude_m"]))
        for p in sector["posts"]
    }


def ordered_wire(reports: pd.DataFrame) -> list[dict]:
    ordered = reports.sort_values(["ts", "post_id", "supply_class"], kind="stable")
    return [to_wire(r) for r in ordered.to_dict("records")]


def train_scorer(
    reports: pd.DataFrame,
    deliveries: pd.DataFrame,
    keys: dict[str, str],
    sector: dict[str, Any],
    temps: dict[tuple[str, str], float],
    gate_cfg: dict[str, Any],
    temp_ref_c: float,
    classes: list[str],
    seed: int,
) -> tuple[AnomalyScorer, Deferred, dict[str, int]]:
    """Fit the anomaly scorer on the clean pre-holdout winters and score the whole history.

    One replay of the history computes every report's features (all of it is clean, so each report
    is committed as verified); the forests are fitted on the training winters' rows only, and the
    whole matrix is then scored in a single batch per class.
    """
    posts = post_infos(sector)
    holdout = int(sector["timeline"]["holdout_winter"])
    wire = ordered_wire(reports)
    train_rows = [r for r in wire if winter_of(date.fromisoformat(r["ts"][:10])) < holdout]

    scorer = AnomalyScorer(
        posts=posts,
        temps=temps,
        temp_model=fit_temp_model(posts, temps, train_rows, temp_ref_c),
        history_days=int(gate_cfg["history_days"]),
        min_history=int(gate_cfg["min_history"]),
        temp_ref_c=temp_ref_c,
    )

    ctx = MemoryContext(
        keys,
        deliveries_index(deliveries),
        posts={p: (i.formation, i.troops) for p, i in posts.items()},
    )
    featurised = []
    for r in wire:
        featurised.append((r, scorer.featurize(r, ctx)))
        ctx.commit(r, VERIFIED)

    fit_rows = [
        (r["class"], f)
        for r, f in featurised
        if f is not None and winter_of(date.fromisoformat(r["ts"][:10])) < holdout
    ]
    used = scorer.fit(
        fit_rows,
        classes,
        false_alarm_target=float(gate_cfg["anomaly_false_alarm_target"]),
        n_trees=int(gate_cfg["n_trees"]),
        seed=seed,
    )
    return scorer, Deferred(precompute(scorer, featurised)), used


def run_history(
    reports: pd.DataFrame,
    deliveries: pd.DataFrame,
    keys: dict[str, str],
    gate: Gate,
    posts: dict[str, PostInfo] | None = None,
) -> pd.DataFrame:
    """Gate every report in time order, committing each so later reports see it. Returns one verdict
    row per report."""
    ctx = MemoryContext(
        keys,
        deliveries_index(deliveries),
        posts={p: (i.formation, i.troops) for p, i in (posts or {}).items()},
    )
    ordered = reports.sort_values(["ts", "post_id", "supply_class"], kind="stable")
    rows = []
    for rec in ordered.to_dict("records"):
        wire = to_wire(rec)
        verdict = gate.evaluate(wire, ctx)
        ctx.commit(wire, verdict.verdict)
        rows.append(
            (
                wire["report_id"],
                verdict.verdict,
                json.dumps(verdict.messages),
                json.dumps(verdict.codes),
                verdict.score,
                wire["ts"],
            )
        )
    return pd.DataFrame(
        rows,
        columns=["report_id", "verdict", "reasons", "reason_codes", "score", "scored_at"],
    )


def winter_of(d: date) -> int:
    return d.year if d.month >= 10 else d.year - 1
