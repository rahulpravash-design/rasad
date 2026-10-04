"""Write the generated tables into a fresh SQLite database."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pandas as pd

from api.db import connect, init_schema


def _iso(series: pd.Series) -> list[str]:
    return series.dt.strftime("%Y-%m-%d").tolist()


def load_database(
    path: Path,
    sector: dict[str, Any],
    weather: pd.DataFrame,
    pass_status: pd.DataFrame,
    reports: pd.DataFrame,
    deliveries: pd.DataFrame,
    verdicts: pd.DataFrame,
    keys: dict[str, str],
    meta: dict[str, str],
) -> None:
    """Build the database in a temp file and move it into place, so a failed build never leaves a
    half-written database behind."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.unlink(missing_ok=True)

    conn = connect(tmp)
    try:
        init_schema(conn)
        conn.executemany(
            "INSERT INTO depots VALUES (?, ?, ?, ?, ?)",
            [(d["id"], d["name"], d["lat"], d["lon"], d["altitude_m"]) for d in sector["depots"]],
        )
        conn.executemany(
            "INSERT INTO passes VALUES (?, ?, ?, ?, ?)",
            [(p["id"], p["name"], p["lat"], p["lon"], p["altitude_m"]) for p in sector["passes"]],
        )
        conn.executemany(
            "INSERT INTO posts VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    p["id"],
                    p["formation"],
                    p["lat"],
                    p["lon"],
                    p["altitude_m"],
                    p["troops"],
                    p["depot"],
                    p["via_pass"],
                    json.dumps(p["access"]),
                )
                for p in sector["posts"]
            ],
        )

        w = weather.sort_values(["location_id", "date"])
        conn.executemany(
            "INSERT INTO weather_daily VALUES (?, ?, ?, ?, ?, ?, ?)",
            zip(
                w["location_id"],
                _iso(w["date"]),
                w["t_mean_c"],
                w["t_min_c"],
                w["snowfall_cm"],
                w["precip_mm"],
                w["source"],
                strict=True,
            ),
        )

        d = deliveries.sort_values(["date", "post_id", "supply_class"])
        conn.executemany(
            "INSERT INTO deliveries VALUES (?, ?, ?, ?, ?, ?)",
            zip(
                d["delivery_id"],
                d["post_id"],
                d["supply_class"],
                _iso(d["date"]),
                d["qty"].astype(int),
                d["mode"],
                strict=True,
            ),
        )

        r = reports.sort_values(["date", "post_id", "supply_class"])
        conn.executemany("INSERT INTO post_keys VALUES (?, ?)", sorted(keys.items()))

        conn.executemany(
            "INSERT INTO reports (report_id, post_id, ts, report_date, supply_class, opening, "
            "received, consumed, closing, nonce, sig) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            zip(
                r["report_id"],
                r["post_id"],
                r["ts"],
                _iso(r["date"]),
                r["supply_class"],
                r["opening"].astype(int),
                r["received"].astype(int),
                r["consumed"].astype(int),
                r["closing"].astype(int),
                r["nonce"],
                r["sig"],
                strict=True,
            ),
        )

        v = verdicts.sort_values("report_id")
        conn.executemany(
            "INSERT INTO gate_verdicts VALUES (?, ?, ?, ?, ?, ?)",
            zip(
                v["report_id"],
                v["verdict"],
                v["reasons"],
                v["reason_codes"],
                [None if pd.isna(s) else float(s) for s in v["score"]],
                v["scored_at"],
                strict=True,
            ),
        )

        # Pass status for every day from the closure-risk model (closure/risk.py).
        status_rows = [
            (
                r.pass_id,
                r.date.strftime("%Y-%m-%d"),
                r.status,
                round(float(r.p14), 3),
                None if r.days is None or pd.isna(r.days) else int(r.days),
                round(float(r.snow3), 2),
                round(float(r.temp14), 2),
                "model",
            )
            for r in pass_status.sort_values(["pass_id", "date"]).itertuples()
        ]
        conn.executemany("INSERT INTO pass_status VALUES (?, ?, ?, ?, ?, ?, ?, ?)", status_rows)

        conn.executemany("INSERT INTO meta VALUES (?, ?)", sorted(meta.items()))
        conn.commit()
    finally:
        conn.close()
    os.replace(tmp, path)
