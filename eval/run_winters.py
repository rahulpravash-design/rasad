"""100 simulated winters: RASAD vs the fixed-scale baseline.

Each winter
  * weather: one of the four pre-holdout winters, resampled, with every temperature shifted by
    N(0, 1.5) C and all snowfall scaled by exp(N(0, 0.25)) (one draw per winter);
  * demand: the same consumption model as the data (data/generate.py) with a fresh seed;
  * passes close by the stated rule on that weather; RASAD sees only the closure-risk model's
    probability (trained on earlier winters), never the future.

Both policies start each post-class with 20 days of fixed-scale stock, review every 7 days and
share one emergency rule: when stock falls under 3 days of expected use, a helicopter brings it to
7 days the next day (EMERGENCY airlift).
  * Baseline: order up to 30 days of fixed scale (troops x base rate), trucks only, nothing moves
    while the pass is closed.
  * RASAD: order up to the P90 forecast for 30 days plus minimum stock days of P50 (the planner's
    need rule); when P(closure within 14 days) >= 0.4 and the road is open, stock for the rest of
    the season (up to 90 days of P50) by truck; when the pass is closed, resupply by mule where
    possible, else by planned helicopter.
The simulation applies the planner's need rule and mode order; it does not run the MILP itself.

Usage:  python -m eval.run_winters [--n 100] [--seed 42]
"""

from __future__ import annotations

import argparse
import copy
import json
import math
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from api.db import connect
from api.settings import get_settings
from closure.labels import closure_labels
from closure.risk import fit_and_score
from config.loader import ROOT, load_constraints, load_consumption, load_sector, winter_bounds
from data.generate import generate_consumption
from eval.report import write_section
from forecast import model as qm
from forecast.features import FORECAST_TEMP_DAYS, Panel, PostMeta, features_for, load_panel
from forecast.service import weights_for
from planner.baseline import target_units
from planner.solve import heli_payload

VIRTUAL = 2025  # simulated winters reuse the held-out season's calendar
REVIEW, EMERGENCY_BELOW, EMERGENCY_TO = 7, 3, 7
LEAD = {"truck": 2, "mule": 3, "heli": 1}
RESULTS_JSON = ROOT / "eval" / "winters_results.json"


def load_inputs(db: Path) -> dict[str, Any]:
    sector, ccfg = load_sector(), load_consumption()
    conn = connect(db, readonly=True)
    try:
        weather = pd.read_sql(
            "SELECT location_id, date, t_mean_c, t_min_c, snowfall_cm, precip_mm, source FROM weather_daily",
            conn,
        )
        panel = load_panel(conn, sector, ccfg)
    finally:
        conn.close()
    weather["date"] = pd.to_datetime(weather["date"])
    holdout = int(sector["timeline"]["holdout_winter"])
    pass_ids = [p["id"] for p in sector["passes"]]
    wint = weather["date"].dt.year.where(
        weather["date"].dt.month >= 10, weather["date"].dt.year - 1
    )
    train_w = weather[wint < holdout].copy()
    train_pass = train_w[train_w["location_id"].isin(pass_ids)]
    return {
        "sector": sector,
        "ccfg": ccfg,
        "panel": panel,
        "weather": weather,
        "wint": wint,
        "holdout": holdout,
        "pass_ids": pass_ids,
        "train_pass": train_pass,
        "train_labels": closure_labels(train_pass, load_constraints()["closure_rule"]),
        "weights": weights_for(db),
    }


def virtual_weather(inp: dict[str, Any], rng: np.random.Generator) -> tuple[pd.DataFrame, int]:
    base = int(rng.choice(sorted({int(w) for w in inp["wint"].unique() if w < inp["holdout"]})))
    dt, snow = rng.normal(0, 1.5), math.exp(rng.normal(0, 0.25))
    src = inp["weather"][inp["wint"] == base].sort_values(["location_id", "date"])
    start, end = winter_bounds(inp["sector"]["timeline"], VIRTUAL)
    days = pd.date_range(start, end)
    out = []
    for _, g in src.groupby("location_id"):
        g = g.iloc[: len(days)].copy()
        g["date"] = days[: len(g)]
        g["t_mean_c"] += dt
        g["t_min_c"] += dt
        g["snowfall_cm"] *= snow
        out.append(g)
    return pd.concat(out, ignore_index=True), base


def simulate(inp: dict[str, Any], k: int, seed: int) -> dict[str, Any]:
    rng = np.random.default_rng([seed, 77, k])
    sector, ccfg, c = inp["sector"], inp["ccfg"], load_constraints()
    weather, base = virtual_weather(inp, rng)
    vpass = weather[weather["location_id"].isin(inp["pass_ids"])]
    labels = closure_labels(vpass, c["closure_rule"])
    risk, _ = fit_and_score(
        pd.concat([inp["train_pass"], vpass]),
        pd.concat([inp["train_labels"], labels]),
        sector["passes"],
        VIRTUAL,
    )
    risk = risk[risk["winter"] == VIRTUAL].sort_values(["pass_id", "date"])
    closed = {p: g["closed"].to_numpy() for p, g in labels.groupby("pass_id")}
    p14 = {p: g["p14"].to_numpy() for p, g in risk.groupby("pass_id")}

    vsector = copy.deepcopy(sector)
    vsector["timeline"] = {**sector["timeline"], "first_winter": VIRTUAL, "last_winter": VIRTUAL}
    for f in vsector["formations"].values():
        f["history_start"] = f"{VIRTUAL}-10-01"
    demand_df = generate_consumption(vsector, ccfg, weather, seed=seed * 1000 + k)
    n = int(demand_df.groupby(["post_id", "supply_class"]).size().min())
    demand = {
        key: g["demand"].to_numpy()[:n] for key, g in demand_df.groupby(["post_id", "supply_class"])
    }
    temps = {
        p: g.sort_values("date")["t_mean_c"].to_numpy()[:n]
        for p, g in weather.groupby("location_id")
    }

    results = {}
    for policy in ("baseline", "rasad"):
        results[policy] = run_policy(policy, inp, sector, ccfg, c, demand, temps, closed, p14, n)
    results["base_winter"] = base
    return results


def run_policy(policy, inp, sector, ccfg, c, demand, temps, closed, p14, n) -> dict[str, float]:
    classes = list(ccfg["classes"])
    posts = {p["id"]: p for p in sector["posts"]}
    rate = {cl: float(ccfg["classes"][cl]["rate"]) for cl in classes}
    kg = {cl: float(ccfg["classes"][cl]["kg_per_unit"]) for cl in classes}
    min_days = c["min_stock_days"]
    stock = {key: target_units(posts[key[0]]["troops"], rate[key[1]], 20) for key in demand}
    observed = {key: np.full(n, np.nan) for key in demand}
    pipeline: list[tuple[int, tuple[str, str], float, str]] = []  # arrival day, key, units, mode
    t = {"truck": 0.0, "mule": 0.0, "heli_planned": 0.0, "heli_emergency": 0.0}
    sorties = 0.0
    stockout_days = 0
    panel = None
    if policy == "rasad":
        panel = Panel(
            classes,
            rate,
            {p: PostMeta(v["formation"], v["troops"], v["altitude_m"]) for p, v in posts.items()},
            {**sector["timeline"], "first_winter": VIRTUAL, "last_winter": VIRTUAL},
            {
                (p, cl, VIRTUAL): observed[(p, cl)] / (posts[p]["troops"] * rate[cl])
                for p, cl in demand
            },
            {(p, VIRTUAL): temps[p] for p in posts},
            inp["panel"].clim,
        )

    def ship(day, key, units, mode, emergency=False):
        nonlocal sorties
        if units <= 0:
            return
        tonnes = units * kg[key[1]] / 1000
        if mode == "heli":
            t["heli_emergency" if emergency else "heli_planned"] += tonnes
            sorties += tonnes / heli_payload(c, posts[key[0]]["altitude_m"])
        else:
            t[mode] += tonnes
        pipeline.append((day + LEAD[mode], key, units, mode))

    for d in range(n):
        for item in [x for x in pipeline if x[0] == d]:
            stock[item[1]] += item[2]
        pipeline[:] = [x for x in pipeline if x[0] != d]

        if d % REVIEW == 0:
            for key in sorted(demand):
                post = posts[key[0]]
                is_closed = bool(closed[post["via_pass"]][d])
                in_transit = sum(x[2] for x in pipeline if x[1] == key)
                position = stock[key] + in_transit
                if policy == "baseline":
                    if not is_closed and "truck" in post["access"]:
                        ship(d, key, target_units(post["troops"], rate[key[1]]) - position, "truck")
                    continue
                # RASAD: forecast-driven need, closure-aware stocking
                p50, p90 = forecast(
                    panel, inp["weights"], key, d, n, temps[key[0]], inp["panel"].clim[key[0]]
                )
                scale = post["troops"] * rate[key[1]]
                p50, p90 = p50 * scale, p90 * scale
                mean50 = float(p50.mean()) if len(p50) else 0.0
                target = float(p90.sum()) + float(min_days[key[1]]) * mean50
                truck_ok = "truck" in post["access"] and not is_closed
                if truck_ok and p14[post["via_pass"]][d] >= 0.4:
                    target = max(target, mean50 * min(n - d, 90) + float(min_days[key[1]]) * mean50)
                need = target - position
                if need <= 0:
                    continue
                mode = "truck" if truck_ok else ("mule" if "mule" in post["access"] else "heli")
                ship(d, key, need, mode)

        for key in demand:
            want = demand[key][d]
            got = min(want, stock[key])
            stockout_days += got < want
            stock[key] -= got
            observed[key][d] = got
            if panel is not None:
                post = posts[key[0]]
                panel.y[(key[0], key[1], VIRTUAL)][d] = got / (post["troops"] * rate[key[1]])
            # Emergency rule (both policies): under 3 days of expected use -> heli to 7 days.
            expected = max(np.mean(observed[key][max(0, d - 13) : d + 1]), 1e-9)
            inbound = any(x[1] == key for x in pipeline)
            if stock[key] < EMERGENCY_BELOW * expected and not inbound:
                ship(d, key, EMERGENCY_TO * expected - stock[key], "heli", emergency=True)

    m = c["modes"]
    cost = (
        t["truck"] * m["truck"]["cost_per_t"]
        + t["mule"] * m["mule"]["cost_per_t"]
        + sorties * m["heli"]["cost_per_sortie"]
    )
    return {
        "stockout_days": int(stockout_days),
        **{k: round(v, 3) for k, v in t.items()},
        "sorties": round(sorties, 1),
        "cost": round(cost),
    }


def forecast(panel, w, key, d, n, temp, clim):
    post, cls = key
    idx = np.arange(d + 1, min(d + 31, n))
    if len(idx) == 0:
        return np.zeros(0), np.zeros(0)
    if d == 0:  # nothing observed yet this season: assume the base scale (target 1.0)
        return np.ones(len(idx)), np.ones(len(idx))
    h = idx - d
    tvec = np.where(h <= FORECAST_TEMP_DAYS, temp[idx], clim[idx])
    pred = np.maximum(qm.predict(w, features_for(panel, post, cls, VIRTUAL, idx, tvec)), 0.0)
    return pred[:, 1], pred[:, 2]


def summarise(runs: list[dict[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {"n": len(runs)}
    for metric in ("stockout_days", "heli_emergency", "heli_planned", "truck", "mule", "cost"):
        b = np.array([r["baseline"][metric] for r in runs], dtype=float)
        r_ = np.array([r["rasad"][metric] for r in runs], dtype=float)
        diff = r_ - b
        half = 1.96 * diff.std(ddof=1) / math.sqrt(len(diff)) if len(diff) > 1 else 0.0
        out[metric] = {
            "baseline_mean": b.mean(),
            "rasad_mean": r_.mean(),
            "diff_mean": diff.mean(),
            "diff_ci95": [diff.mean() - half, diff.mean() + half],
            "rasad_better_share": float((r_ < b).mean()),
        }
    return out


def markdown(s: dict[str, Any], seed: int) -> str:
    def row(label, key, fmt):
        m = s[key]
        lo, hi = m["diff_ci95"]
        return (
            f"| {label} | {fmt(m['baseline_mean'])} | {fmt(m['rasad_mean'])} | "
            f"{fmt(m['diff_mean'])} ({fmt(lo)} to {fmt(hi)}) | {100 * m['rasad_better_share']:.0f}% |"
        )

    num = lambda v: f"{v:,.1f}"  # noqa: E731
    inr = lambda v: f"{v / 1e5:,.1f} L"  # noqa: E731
    return "\n".join(
        [
            f"## Planning: {s['n']} simulated winters (seed {seed})",
            "",
            "Weather resampled from the four pre-holdout winters and perturbed (temperature shift, snowfall"
            " scaling); demand from the same consumption model with fresh seeds. Mean per winter across"
            " the 42 posts; the difference is RASAD minus baseline with a 95% CI; the last column is the"
            " share of winters where RASAD was lower (better).",
            "",
            "| Metric (per winter) | Fixed-scale baseline | RASAD | Difference (95% CI) | RASAD lower |",
            "|---|---:|---:|---:|---:|",
            row("Stock-out post-class-days", "stockout_days", num),
            row("Emergency airlift (t)", "heli_emergency", num),
            row("Planned helicopter (t)", "heli_planned", num),
            row("Truck (t)", "truck", num),
            row("Mule (t)", "mule", num),
            row("Movement cost (INR)", "cost", inr),
            "",
            "The simulation applies the planner's need rule and mode order rather than solving the MILP"
            " each week; costs use the assumed rates in config/constraints.yaml.",
        ]
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--db", type=Path, default=get_settings().db_path)
    parser.add_argument("--n", type=int, default=100)
    parser.add_argument("--seed", type=int, default=get_settings().seed)
    parser.add_argument("--no-write", action="store_true")
    args = parser.parse_args()
    inp = load_inputs(args.db)
    runs = [simulate(inp, k, args.seed) for k in range(args.n)]
    s = summarise(runs)
    text = markdown(s, args.seed)
    print(text)
    if not args.no_write:
        RESULTS_JSON.write_text(
            json.dumps({"summary": s, "runs": runs}, indent=1, default=float) + "\n"
        )
        write_section("winters", text)


if __name__ == "__main__":
    main()
