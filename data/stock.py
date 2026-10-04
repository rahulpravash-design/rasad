"""Stock simulation: demand -> deliveries -> closing stock -> daily clean reports.

This is the *historical* operation that produced the reports the gate, forecaster and planner
consume. It is a simple fixed-schedule restock, not the RASAD planner and not the Day 9 baseline:

* Each post-class gets its own resupply cycle (10-24 days) and buffer (6-20 days of cover above
  the cycle), so some posts run thin and others fat. That spread is what makes readiness and the
  planner's decisions interesting.
* At each resupply the post is topped up to (cycle + buffer) days of trailing-mean demand.
* Delivery mode follows the post's `access` list: trucks while the gating pass is open, otherwise
  helicopter (else mule). Posts without a road always get mule (else heli).
* Consumption is clipped to what is on hand. A clipped day is a stock-out and is counted, because
  clipping censors the demand signal.

Balance identity on every report: closing = opening + received - consumed.
"""

from __future__ import annotations

import uuid
import zlib
from typing import Any

import numpy as np
import pandas as pd

# Fixed namespace so report and delivery ids are reproducible from (post, class, date).
ID_NAMESPACE = uuid.UUID("5a3a1d52-5e8a-4b6e-9a53-7d8c0a1b2c3d")


def _mode(access: list[str], pass_closed: bool) -> str:
    if "truck" in access:
        if not pass_closed:
            return "truck"
        return "heli" if "heli" in access else "mule"
    return "mule" if "mule" in access else "heli"


def simulate_stock(
    sector: dict[str, Any],
    ccfg: dict[str, Any],
    consumption: pd.DataFrame,
    closure: pd.DataFrame,
    seed: int = 42,
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, int]]:
    """Return (reports, deliveries, stats).

    `closure` is closure.labels.closure_labels output (pass_id, date, closed).
    """
    rs = ccfg["resupply"]
    posts = {p["id"]: p for p in sector["posts"]}
    closed_on = {
        (r.pass_id, r.date): bool(r.closed)
        for r in closure[["pass_id", "date", "closed"]].itertuples()
    }

    report_rows: list[tuple] = []
    delivery_rows: list[tuple] = []
    stockout_days = 0

    ordered = consumption.sort_values(["post_id", "supply_class", "date"], kind="stable")
    for (post_id, cls), grp in ordered.groupby(["post_id", "supply_class"], sort=True):
        post = posts[post_id]
        winter_of = grp["date"].dt.year.where(grp["date"].dt.month >= 10, grp["date"].dt.year - 1)
        for winter, wg in grp.groupby(winter_of, sort=True):
            demand = wg["demand"].to_numpy()
            dates = wg["date"].to_numpy()
            n = len(demand)

            rng = np.random.default_rng(
                [seed, 2, zlib.crc32(post_id.encode()), zlib.crc32(cls.encode()), int(winter)]
            )
            cycle = int(rng.integers(rs["cycle_days"][0], rs["cycle_days"][1] + 1))
            phase = int(rng.integers(0, cycle))
            buffer_d = int(rng.integers(rs["buffer_days"][0], rs["buffer_days"][1] + 1))
            target_days = cycle + buffer_d
            window = int(rs["trailing_days"])
            first_mean = max(1.0, float(demand[:window].mean()))

            stock = int(round(target_days * first_mean))  # opening stock on day 0
            for d in range(n):
                day = pd.Timestamp(dates[d])
                received = 0
                if d > 0 and (d - phase) % cycle == 0:
                    trailing = max(1.0, float(demand[max(0, d - window) : d].mean()))
                    received = max(0, int(round(target_days * trailing)) - stock)
                    if received:
                        mode = _mode(post["access"], closed_on.get((post["via_pass"], day), False))
                        delivery_rows.append((post_id, cls, day, received, mode))
                opening = stock
                available = opening + received
                consumed = min(int(demand[d]), available)
                stockout_days += consumed < demand[d]
                stock = available - consumed
                report_rows.append((post_id, cls, day, opening, received, consumed, stock))

    reports = pd.DataFrame(
        report_rows,
        columns=["post_id", "supply_class", "date", "opening", "received", "consumed", "closing"],
    )
    deliveries = pd.DataFrame(
        delivery_rows, columns=["post_id", "supply_class", "date", "qty", "mode"]
    )

    # Ids and nonces derive only from the seed and the row key, so they are reproducible.
    reports["report_id"] = [
        str(uuid.uuid5(ID_NAMESPACE, f"report|{p}|{c}|{d:%Y-%m-%d}"))
        for p, c, d in zip(
            reports["post_id"], reports["supply_class"], reports["date"], strict=True
        )
    ]
    id_rng = np.random.default_rng([seed, 3])
    minutes = id_rng.integers(0, 60, len(reports))
    nonces = id_rng.integers(0, 2**63 - 1, len(reports), dtype=np.int64)
    # Daily SITREP at about 06:00Z; the report is attributed to the day it is filed on.
    reports["ts"] = [
        f"{d:%Y-%m-%d}T06:{m:02d}Z" for d, m in zip(reports["date"], minutes, strict=True)
    ]
    reports["nonce"] = [f"{n:016x}" for n in nonces]
    reports["sig"] = None  # signing arrives with gate/signing.py on Day 3
    deliveries["delivery_id"] = [
        str(uuid.uuid5(ID_NAMESPACE, f"delivery|{p}|{c}|{d:%Y-%m-%d}"))
        for p, c, d in zip(
            deliveries["post_id"], deliveries["supply_class"], deliveries["date"], strict=True
        )
    ]

    stats = {
        "report_rows": len(reports),
        "delivery_rows": len(deliveries),
        "stockout_days": int(stockout_days),
    }
    reports = reports[
        [
            "report_id",
            "post_id",
            "ts",
            "date",
            "supply_class",
            "opening",
            "received",
            "consumed",
            "closing",
            "nonce",
            "sig",
        ]
    ]
    deliveries = deliveries[["delivery_id", "post_id", "supply_class", "date", "qty", "mode"]]
    return reports, deliveries, stats
