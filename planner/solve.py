"""Closure-aware movement planner (linear MILP with OR-Tools).

For every post and class the need over the horizon is
    need = P90 demand over the horizon + min_stock_days x mean daily P50 - stock on hand  (>= 0)
converted to tonnes. Decision variables are tonnes per post, class and mode, plus integer
helicopter sorties per post and an explicit shortfall.

Constraints
    * every need is met by trucks + mules + helicopters + shortfall
    * trucks only to posts with road access whose gating pass is not closed
    * mules only to posts with mule access, up to animals x payload x trips per post
    * helicopter tonnes per post <= sorties x payload derated for the post's altitude;
      total sorties <= sector daily sorties x horizon
Objective: truck and mule cost per tonne + helicopter cost per sortie + truck tonnes x closure
probability x risk penalty + shortfall penalty. Not a routing model: it decides how much moves by
which mode, not convoy order.
"""

from __future__ import annotations

import math
import sqlite3
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from ortools.linear_solver import pywraplp

from config.loader import load_constraints, load_consumption, load_sector
from forecast.service import post_forecasts
from planner.explain import reason


def heli_payload(constraints: dict[str, Any], altitude_m: float) -> float:
    h = constraints["modes"]["heli"]
    d = h["altitude_derating"]
    factor = 1.0 - float(d["per_1000m"]) * max(0.0, altitude_m - float(d["start_m"])) / 1000.0
    return round(float(h["payload_t"]) * max(0.1, factor), 4)


def haversine_km(a: tuple[float, float], b: tuple[float, float]) -> float:
    lat1, lon1, lat2, lon2 = map(math.radians, (*a, *b))
    h = (
        math.sin((lat2 - lat1) / 2) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    )
    return 6371.0 * 2 * math.asin(math.sqrt(h))


def needs(
    conn: sqlite3.Connection, db_path: Path, as_of: str, horizon: int
) -> list[dict[str, Any]]:
    """Per post and class: stock, P50/P90 over the horizon and need in units and tonnes."""
    ccfg = load_consumption()
    min_days = load_constraints()["min_stock_days"]
    stock = {
        (r["post_id"], r["supply_class"]): r["closing"]
        for r in conn.execute(
            "SELECT post_id, supply_class, closing FROM trusted_reports WHERE report_date = ?",
            (as_of,),
        )
    }
    out = []
    for post in sorted(p["id"] for p in load_sector()["posts"]):
        fc = post_forecasts(conn, db_path, post, as_of) or {}
        for cls, days in fc.items():
            days = days[:horizon]
            p90 = sum(d["p90"] for d in days)
            p50_mean = sum(d["p50"] for d in days) / max(len(days), 1)
            on_hand = float(stock.get((post, cls), 0))
            units = max(0.0, p90 + float(min_days[cls]) * p50_mean - on_hand)
            kg = float(ccfg["classes"][cls]["kg_per_unit"])
            out.append(
                {
                    "post_id": post,
                    "class": cls,
                    "unit": ccfg["classes"][cls]["unit"],
                    "stock": on_hand,
                    "p90": p90,
                    "need_units": units,
                    "need_t": units * kg / 1000.0,
                }
            )
    return out


def solve(
    conn: sqlite3.Connection,
    db_path: Path,
    as_of: str,
    horizon: int = 30,
    need_rows: list | None = None,
) -> dict[str, Any]:
    c = load_constraints()
    sector = load_sector()
    posts = {p["id"]: p for p in sector["posts"]}
    depots = {d["id"]: d for d in sector["depots"]}
    passes = {p["id"]: p["name"] for p in sector["passes"]}
    status = {
        r["pass_id"]: dict(r)
        for r in conn.execute("SELECT * FROM pass_status WHERE as_of = ?", (as_of,))
    }
    rows = need_rows if need_rows is not None else needs(conn, db_path, as_of, horizon)
    modes = c["modes"]
    lead = int(c["truck_lead_days"])

    solver = pywraplp.Solver.CreateSolver("SCIP")
    x: dict[tuple[str, str, str], Any] = {}
    short: dict[tuple[str, str], Any] = {}
    sorties: dict[str, Any] = {}
    mule_cap_t = (
        float(modes["mule"]["animals_per_post"])
        * float(modes["mule"]["payload_t"])
        * max(1, horizon // int(modes["mule"]["round_trip_days"]))
    )
    objective = solver.Objective()

    def truck_ok(post: dict[str, Any]) -> bool:
        st = status.get(post["via_pass"], {})
        days = st.get("days")
        if st.get("status") == "CLOSED":
            return False
        return days is None or days >= lead

    for r in rows:
        post = posts[r["post_id"]]
        key = (r["post_id"], r["class"])
        allowed = [m for m in post["access"] if m != "truck" or truck_ok(post)]
        for m in allowed:
            v = solver.NumVar(0, solver.infinity(), f"x_{key[0]}_{key[1]}_{m}")
            x[(*key, m)] = v
            cost = float(modes[m].get("cost_per_t", 0.0))  # helicopters are costed per sortie
            if m == "truck":
                cost += float(c["risk_penalty_per_unit"]) * float(
                    status.get(post["via_pass"], {}).get("p_close") or 0
                )
            objective.SetCoefficient(v, cost)
        s = solver.NumVar(0, solver.infinity(), f"short_{key[0]}_{key[1]}")
        short[key] = s
        objective.SetCoefficient(s, float(c["shortfall_penalty_per_t"]))
        ct = solver.Constraint(r["need_t"], solver.infinity())
        for m in allowed:
            ct.SetCoefficient(x[(*key, m)], 1)
        ct.SetCoefficient(s, 1)
    objective.SetMinimization()

    for pid, post in posts.items():
        mule_vars = [v for (p, _, m), v in x.items() if p == pid and m == "mule"]
        if mule_vars:
            ct = solver.Constraint(0, mule_cap_t)
            for v in mule_vars:
                ct.SetCoefficient(v, 1)
        heli_vars = [v for (p, _, m), v in x.items() if p == pid and m == "heli"]
        if heli_vars:
            n = solver.IntVar(0, solver.infinity(), f"sorties_{pid}")
            sorties[pid] = n
            objective.SetCoefficient(n, float(modes["heli"]["cost_per_sortie"]))
            ct = solver.Constraint(-solver.infinity(), 0)
            for v in heli_vars:
                ct.SetCoefficient(v, 1)
            ct.SetCoefficient(n, -heli_payload(c, post["altitude_m"]))
    h = modes["heli"]
    sortie_budget = float(h["daily_sorties"]) * horizon
    ct = solver.Constraint(0, sortie_budget)
    for n in sorties.values():
        ct.SetCoefficient(n, 1)

    solver.SetTimeLimit(20_000)
    code = solver.Solve()
    if code not in (pywraplp.Solver.OPTIMAL, pywraplp.Solver.FEASIBLE):
        raise RuntimeError(f"planner failed with status {code}")

    by_key = {(r["post_id"], r["class"]): r for r in rows}
    items = []
    start = date.fromisoformat(as_of) + timedelta(days=1)
    for (pid, cls, m), v in sorted(x.items()):
        q = v.solution_value()
        if q < 1e-4:
            continue
        items.append(
            _item(pid, cls, m, q, by_key, posts, depots, passes, status, c, sorties, start, horizon)
        )
    for (pid, cls), v in sorted(short.items()):
        if v.solution_value() > 1e-4:
            items.append(
                _item(
                    pid,
                    cls,
                    "shortfall",
                    v.solution_value(),
                    by_key,
                    posts,
                    depots,
                    passes,
                    status,
                    c,
                    sorties,
                    start,
                    horizon,
                )
            )

    totals: dict[str, float] = {}
    for it in items:
        totals[it["mode"]] = totals.get(it["mode"], 0.0) + it["qty_t"]
    return {
        "as_of": as_of,
        "horizon_days": horizon,
        "status": "optimal" if code == pywraplp.Solver.OPTIMAL else "feasible",
        "cost": round(sum(it["cost"] for it in items if it["mode"] != "shortfall")),
        "tonnes_by_mode": {k: round(v, 2) for k, v in sorted(totals.items())},
        "sorties_used": int(round(sum(n.solution_value() for n in sorties.values()))),
        "sortie_budget": int(sortie_budget),
        "items": items,
    }


def _cost(mode: str, tonnes: float, c: dict[str, Any], post: dict[str, Any]) -> int:
    if mode == "heli":
        return round(
            tonnes
            / heli_payload(c, post["altitude_m"])
            * float(c["modes"]["heli"]["cost_per_sortie"])
        )
    if mode in c["modes"]:
        return round(tonnes * float(c["modes"][mode]["cost_per_t"]))
    return 0


def _item(pid, cls, m, q, by_key, posts, depots, passes, status, c, sorties, start, horizon):
    post, need = posts[pid], by_key[(pid, cls)]
    depot = depots[post["depot"]]
    st = status.get(post["via_pass"], {})
    straight = haversine_km((depot["lat"], depot["lon"]), (post["lat"], post["lon"]))
    km = straight if m == "heli" else straight * float(c["road_factor"])
    speed = float(c["modes"][m]["speed_kmph"]) if m in c["modes"] else 1.0
    hours = km / speed
    deadline = None
    if m == "truck" and st.get("days") is not None:
        deadline = (start + timedelta(days=int(st["days"]) - int(c["truck_lead_days"]))).isoformat()
    item = {
        "post_id": pid,
        "class": cls,
        "mode": m,
        "qty_t": round(q, 3),
        "qty_units": round(q * 1000 / float(load_consumption()["classes"][cls]["kg_per_unit"])),
        "unit": need["unit"],
        "depart_date": start.isoformat(),
        "cost": _cost(m, q, c, post),
        "time_days": round(hours / 10.0, 2),  # ~10 moving hours a day in the mountains
        "risk": round(float(st.get("p_close") or 0), 3) if m == "truck" else 0.0,
    }
    item["reason"] = reason(
        item,
        {
            "pass_name": passes[post["via_pass"]],
            "pass_status": st.get("status", "OPEN"),
            "p14": float(st.get("p_close") or 0),
            "deadline": deadline,
            "p90": need["p90"],
            "unit": need["unit"],
            "horizon": horizon,
            "has_truck": "truck" in post["access"],
            "heli_payload": heli_payload(c, post["altitude_m"]),
            "altitude": post["altitude_m"],
            "sorties": int(round(sorties[pid].solution_value())) if pid in sorties else 0,
        },
    )
    return item
