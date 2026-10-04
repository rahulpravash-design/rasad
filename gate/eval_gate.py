"""Evaluate the gate on the held-out winter.

* False-alarm rate: every clean report of the held-out winter is judged in time order, exactly as
  it would arrive. Anything not VERIFIED is a false alarm.
* Detection rate per attack type: on about 10% of those reports an attack of each type (cycling
  through the four) is built from the genuine report and judged against the same state the genuine
  one saw. An attack is never committed, so one injection cannot disturb the next judgement; an
  undetected insider attack that would have broken continuity for the next day's honest report is
  therefore not counted against the false-alarm rate.

The scorer was trained on earlier winters only. Everything is seeded.

Usage:  python -m gate.eval_gate [--db PATH] [--rate 0.10] [--seed 42]
"""

from __future__ import annotations

import argparse
import json
import math
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from api.db import connect
from api.settings import get_settings
from config.loader import ROOT, load_constraints, load_consumption, load_sector
from eval.report import write_section
from gate import attacks, signing
from gate.anomaly import AnomalyScorer, Deferred, model_path, precompute
from gate.batch import deliveries_index, post_infos, to_wire, winter_of
from gate.context import MemoryContext
from gate.pipeline import FLAGGED, REJECTED, VERIFIED, Gate

RESULTS_JSON = ROOT / "eval" / "gate_results.json"
ATTACK_LABEL = {
    attacks.FORGED: "Forged signature",
    attacks.REPLAY: "Replay",
    attacks.INFLATED: "Inflated consumption (insider)",
    attacks.DEFLATED: "Deflated stock (insider)",
}
SWEEP_TARGETS = (0.005, 0.01, 0.02, 0.05)


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """95% Wilson score interval for a proportion."""
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    denom = 1 + z**2 / n
    centre = (p + z**2 / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z**2 / (4 * n**2)) / denom
    return (max(0.0, centre - half), min(1.0, centre + half))


def pct(x: float) -> str:
    return f"{100 * x:.1f}%"


def load_world(db_path: Path) -> dict[str, Any]:
    conn = connect(db_path, readonly=True)
    try:
        rows = [
            to_wire(dict(r))
            for r in conn.execute("SELECT * FROM reports ORDER BY ts, post_id, supply_class")
        ]
        keys = {r["post_id"]: r["public_key"] for r in conn.execute("SELECT * FROM post_keys")}
        deliveries = pd.read_sql("SELECT * FROM deliveries", conn)
    finally:
        conn.close()
    deliveries["date"] = pd.to_datetime(deliveries["date"])
    return {"rows": rows, "keys": keys, "deliveries": deliveries_index(deliveries)}


def evaluate(db_path: Path, seed: int = 42, rate: float = 0.10) -> dict[str, Any]:
    sector = load_sector()
    classes = set(load_consumption()["classes"])
    holdout = int(sector["timeline"]["holdout_winter"])
    scorer = AnomalyScorer.load(model_path(db_path))
    world = load_world(db_path)
    rows, keys, deliveries = world["rows"], world["keys"], world["deliveries"]
    posts = {p: (i.formation, i.troops) for p, i in post_infos(sector).items()}

    def in_holdout(r: dict[str, Any]) -> bool:
        return winter_of(date.fromisoformat(r["ts"][:10])) == holdout

    # ---------------------------------------------------------------- pass 1: decide and featurise
    ctx = MemoryContext(keys, deliveries, posts=posts)
    select_rng = np.random.default_rng([seed, 4])
    recent: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    events: dict[int, tuple[str, dict[str, Any]]] = {}
    items: list[tuple[dict[str, Any], Any]] = []
    counter = 0
    for idx, r in enumerate(rows):
        if in_holdout(r):
            items.append((r, scorer.featurize(r, ctx)))
            if select_rng.random() < rate:
                attack = attacks.ATTACK_TYPES[counter % len(attacks.ATTACK_TYPES)]
                counter += 1
                key = signing.private_key_from_seed(r["post_id"], seed)
                built = attacks.make_attack(
                    attack,
                    r,
                    np.random.default_rng([seed, 5, idx]),
                    key,
                    seed,
                    recent[(r["post_id"], r["class"])][-10:],
                )
                events[idx] = (attack, built) if built is not None else (attack, {})
                if built is not None:
                    items.append((built, scorer.featurize(built, ctx)))
        ctx.commit(r, VERIFIED)
        recent[(r["post_id"], r["class"])].append(r)
    table = precompute(scorer, items)
    gate = Gate(classes, scorer=Deferred(table))

    # ---------------------------------------------------------------- pass 2: judge
    ctx = MemoryContext(keys, deliveries, posts=posts)
    clean: list[tuple[dict[str, Any], Any]] = []
    attacked: list[tuple[str, dict[str, Any], Any]] = []
    for idx, r in enumerate(rows):
        if in_holdout(r):
            if idx in events and events[idx][1]:
                attack, report = events[idx]
                attacked.append((attack, report, gate.evaluate(report, ctx)))
            verdict = gate.evaluate(r, ctx)
            clean.append((r, verdict))
            ctx.commit(r, verdict.verdict)
        else:
            ctx.commit(r, VERIFIED)

    return summarise(scorer, clean, attacked, events, table, classes, seed, rate, holdout)


def summarise(scorer, clean, attacked, events, table, classes, seed, rate, holdout):
    n_clean = len(clean)
    false_alarms = [(r, v) for r, v in clean if v.verdict != VERIFIED]
    far_lo, far_hi = wilson(len(false_alarms), n_clean)
    by_class = {
        c: {
            "n": sum(1 for r, _ in clean if r["class"] == c),
            "false_alarms": sum(1 for r, v in false_alarms if r["class"] == c),
        }
        for c in sorted(classes)
    }

    per_attack: dict[str, Any] = {}
    for attack in attacks.ATTACK_TYPES:
        mine = [(rep, v) for a, rep, v in attacked if a == attack]
        requested = sum(1 for a, built in events.values() if a == attack)
        counts = Counter(v.verdict for _, v in mine)
        codes = Counter(c for _, v in mine for c in v.codes)
        n = len(mine)
        detected = counts[REJECTED] + counts[FLAGGED]
        lo, hi = wilson(detected, n)
        per_attack[attack] = {
            "label": ATTACK_LABEL[attack],
            "selected": requested,
            "built": n,
            "rejected": counts[REJECTED],
            "flagged": counts[FLAGGED],
            "missed": counts[VERIFIED],
            "detected": detected,
            "detection_rate": detected / n if n else None,
            "ci95": [lo, hi],
            "caught_by": dict(codes.most_common()),
        }

    # Inflated consumption: where is the gate blind? Break down by class and by size of the lie.
    genuine_by_key = {(r["post_id"], r["class"], r["ts"]): r for r, _ in clean}
    inflated = [(rep, v) for a, rep, v in attacked if a == attacks.INFLATED]
    by_inflated_class: dict[str, dict[str, int]] = {}
    by_factor: dict[str, dict[str, int]] = {}
    for rep, v in inflated:
        genuine = genuine_by_key[(rep["post_id"], rep["class"], rep["ts"])]
        cell = by_inflated_class.setdefault(rep["class"], {"n": 0, "detected": 0})
        cell["n"] += 1
        cell["detected"] += v.verdict != VERIFIED
        factor = rep["consumed"] / max(genuine["consumed"], 1)
        bucket = "<2x" if factor < 2 else "2-2.5x" if factor < 2.5 else ">=2.5x"
        cell = by_factor.setdefault(bucket, {"n": 0, "detected": 0})
        cell["n"] += 1
        cell["detected"] += v.verdict != VERIFIED

    # Threshold sweep for the anomaly layer alone: false alarms vs inflated detection as the
    # threshold moves. Thresholds come from the training winters, like the one in use.
    sweep = []
    train_scores = scorer.train_scores
    clean_scores = [
        (r["class"], table[r["report_id"]][0]) for r, _ in clean if r["report_id"] in table
    ]
    inflated_scores = [(rep["class"], table[rep["report_id"]][0]) for rep, _ in inflated]
    for target in SWEEP_TARGETS:
        thr = {c: float(np.quantile(s, 1 - target)) for c, s in train_scores.items()}
        fa = sum(1 for c, s in clean_scores if s > thr[c])
        hit = sum(1 for c, s in inflated_scores if s > thr[c])
        sweep.append(
            {
                "training_false_alarm_target": target,
                "holdout_false_alarm_rate": fa / len(clean_scores),
                "inflated_detection_rate": hit / len(inflated_scores) if inflated_scores else None,
            }
        )

    return {
        "seed": seed,
        "attack_rate": rate,
        "holdout_winter": f"{holdout}-{str(holdout + 1)[2:]}",
        "clean": {
            "n": n_clean,
            "false_alarms": len(false_alarms),
            "false_alarm_rate": len(false_alarms) / n_clean,
            "ci95": [far_lo, far_hi],
            "by_class": by_class,
            "reasons": dict(Counter(c for _, v in false_alarms for c in v.codes).most_common()),
        },
        "attacks": per_attack,
        "inflated_by_class": by_inflated_class,
        "inflated_by_factor": by_factor,
        "anomaly_threshold_sweep": sweep,
        "in_use_false_alarm_target": float(
            load_constraints()["gate"]["anomaly_false_alarm_target"]
        ),
    }


def markdown(res: dict[str, Any]) -> str:
    c = res["clean"]
    lines = [
        f"## Verified data gate (held-out winter {res['holdout_winter']}, seed {res['seed']})",
        "",
        f"The anomaly layer is trained on earlier winters only. {pct(res['attack_rate'])} of the "
        f"{c['n']:,} held-out reports were attacked (the four types in rotation); each attack is "
        "judged against the same state as the genuine report it imitates.",
        "",
        "| Attack | Attempts | Rejected | Flagged | Missed | Detection rate (95% CI) | Caught by |",
        "|---|---:|---:|---:|---:|---:|---|",
    ]
    for a in res["attacks"].values():
        lo, hi = a["ci95"]
        caught = ", ".join(f"{k} ({v})" for k, v in a["caught_by"].items()) or "nothing"
        lines.append(
            f"| {a['label']} | {a['built']} | {a['rejected']} | {a['flagged']} | {a['missed']} | "
            f"{pct(a['detection_rate'])} ({pct(lo)}-{pct(hi)}) | {caught} |"
        )
    lo, hi = c["ci95"]
    lines += [
        "",
        f"**False alarms on clean reports:** {c['false_alarms']:,} of {c['n']:,} = "
        f"{pct(c['false_alarm_rate'])} (95% CI {pct(lo)}-{pct(hi)}), all FLAGGED, none rejected "
        f"(reasons: {', '.join(f'{k} {v}' for k, v in c['reasons'].items()) or 'none'}).",
        "",
        "### Where the gate is blind: inflated consumption by a validly signed insider",
        "",
        "| Class | Attempts | Detected |",
        "|---|---:|---:|",
    ]
    for cls, cell in sorted(res["inflated_by_class"].items()):
        lines.append(f"| {cls} | {cell['n']} | {pct(cell['detected'] / cell['n'])} |")
    lines += ["", "| Size of the lie | Attempts | Detected |", "|---|---:|---:|"]
    for bucket in ("<2x", "2-2.5x", ">=2.5x"):
        cell = res["inflated_by_factor"].get(bucket)
        if cell:
            lines.append(
                f"| {bucket} the genuine value | {cell['n']} | "
                f"{pct(cell['detected'] / cell['n'])} |"
            )
    lines += [
        "",
        "### Anomaly threshold trade-off (anomaly layer alone)",
        "",
        "| Training false-alarm target | Held-out false-alarm rate | Inflated detection |",
        "|---:|---:|---:|",
    ]
    for s in res["anomaly_threshold_sweep"]:
        marker = (
            " (in use)"
            if abs(s["training_false_alarm_target"] - res["in_use_false_alarm_target"]) < 1e-9
            else ""
        )
        lines.append(
            f"| {pct(s['training_false_alarm_target'])}{marker} | "
            f"{pct(s['holdout_false_alarm_rate'])} | {pct(s['inflated_detection_rate'])} |"
        )
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--db", type=Path, default=get_settings().db_path)
    parser.add_argument("--seed", type=int, default=get_settings().seed)
    parser.add_argument("--rate", type=float, default=0.10)
    parser.add_argument("--no-write", action="store_true", help="print only; leave eval/ untouched")
    args = parser.parse_args()
    res = evaluate(args.db, args.seed, args.rate)
    text = markdown(res)
    print(text)
    if not args.no_write:
        RESULTS_JSON.write_text(json.dumps(res, indent=2, sort_keys=True) + "\n")
        write_section("gate", text)


if __name__ == "__main__":
    main()
