"""Demand forecasts and the federated-learning summary."""

from __future__ import annotations

import json
import math
import sqlite3
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query

from api.deps import get_as_of, get_db
from api.settings import get_settings
from config.loader import ROOT, load_constraints, load_consumption
from forecast.service import PRIMARY, models_dir, post_forecasts

router = APIRouter(tags=["forecast"])
Conn = Annotated[sqlite3.Connection, Depends(get_db)]
AsOf = Annotated[str, Depends(get_as_of)]


def _forecasts(conn: sqlite3.Connection, post_id: str, as_of: str, model: str) -> dict:
    if not conn.execute("SELECT 1 FROM posts WHERE id = ?", (post_id,)).fetchone():
        raise HTTPException(404, f"no post {post_id}")
    out = post_forecasts(conn, get_settings().db_path, post_id, as_of, model)
    if out is None:
        raise HTTPException(503, "forecast models not trained; run `make train`")
    return out


@router.get("/forecast/{post_id}")
def forecast(
    post_id: str,
    conn: Conn,
    as_of: AsOf,
    supply_class: Annotated[str, Query(alias="class")] = "rations",
    model: Annotated[str, Query(pattern=r"^(federated|central|local_F[123])$")] = PRIMARY,
) -> dict[str, Any]:
    """30 days of {date, p10, p50, p90, actual?} from the day after as_of."""
    data = _forecasts(conn, post_id, as_of, model)
    if supply_class not in data:
        raise HTTPException(404, f"no {supply_class} history for {post_id}")
    unit = load_consumption()["classes"][supply_class]["unit"]
    return {
        "post_id": post_id,
        "class": supply_class,
        "unit": unit,
        "as_of": as_of,
        "model": model,
        "days": data[supply_class],
    }


@router.get("/forecast/items/{post_id}")
def items(post_id: str, conn: Conn, as_of: AsOf) -> dict[str, Any]:
    """Per class: stock on hand, P90 demand over 30 days, and what to send.

    recommended = P90 over 30 days + minimum stock days x mean daily P50 - stock (at least 0).
    """
    data = _forecasts(conn, post_id, as_of, PRIMARY)
    ccfg, min_days = load_consumption(), load_constraints()["min_stock_days"]
    stock = dict(
        conn.execute(
            "SELECT supply_class, closing FROM trusted_reports "
            "WHERE post_id = ? AND report_date = ?",
            (post_id, as_of),
        ).fetchall()
    )
    rows = []
    for cls, days in data.items():
        p90 = sum(d["p90"] for d in days)
        mean_p50 = sum(d["p50"] for d in days) / max(len(days), 1)
        on_hand = int(stock.get(cls, 0))
        need = p90 + float(min_days[cls]) * mean_p50 - on_hand
        rows.append(
            {
                "class": cls,
                "unit": ccfg["classes"][cls]["unit"],
                "stock": on_hand,
                "p50_30d": round(sum(d["p50"] for d in days)),
                "p90_30d": round(p90),
                "days_of_cover": round(on_hand / mean_p50, 1) if mean_p50 > 0 else None,
                "recommended": max(0, math.ceil(need)),
            }
        )
    return {"post_id": post_id, "as_of": as_of, "items": rows}


@router.get("/federated/summary")
def federated_summary() -> dict[str, Any]:
    """Measured per-formation errors (from forecast.eval_forecast) and the training record."""
    results = ROOT / "eval" / "forecast_results.json"
    info = models_dir(get_settings().db_path) / "info.json"
    if not results.exists() or not info.exists():
        raise HTTPException(503, "no federated results yet; run `make train` and `make eval`")
    res, meta = json.loads(results.read_text()), json.loads(info.read_text())
    clim = res["results"]["climatology"]
    formations = []
    for f, row in sorted(clim.items()):
        formations.append(
            {
                "formation": f,
                "training_rows": meta["rows"][f],
                **{f"{m}_wape": row[m]["wape"] for m in ("local", "federated", "central")},
                **{f"{m}_pinball": row[m]["pinball"] for m in ("local", "federated", "central")},
            }
        )
    return {
        "holdout_winter": res["holdout_winter"],
        "rounds": len(meta["federated_rounds"]),
        "raw_bytes_shared": 0,
        "shared": "model weights only",
        "horizon": "days 17-30 (climatology temperature)",
        "formations": formations,
    }
