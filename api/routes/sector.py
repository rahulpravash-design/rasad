from __future__ import annotations

import json
import sqlite3
from typing import Annotated, Any

from fastapi import APIRouter, Depends

from api import metrics
from api.deps import get_as_of, get_db
from config.loader import load_constraints

router = APIRouter(prefix="/sector", tags=["sector"])


def _feature(kind: str, lon: float, lat: float, **props: Any) -> dict[str, Any]:
    return {
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [lon, lat]},
        "properties": {"kind": kind, **props},
    }


@router.get("/geojson")
def geojson(
    conn: Annotated[sqlite3.Connection, Depends(get_db)],
    as_of: Annotated[str, Depends(get_as_of)],
) -> dict[str, Any]:
    """Posts, depots and passes as one GeoJSON FeatureCollection for the dashboard map.

    Posts carry their worst stock-cover ratio (cover / minimum days, over the five classes) so the
    map can colour them without a second request.
    """
    worst: dict[str, float] = {}
    for row in metrics.stock_cover(conn, as_of, load_constraints()["min_stock_days"]):
        if row["days_cover"] is None:
            continue
        ratio = row["days_cover"] / row["min_stock_days"]
        worst[row["post_id"]] = min(worst.get(row["post_id"], ratio), ratio)

    features: list[dict[str, Any]] = []
    for p in conn.execute("SELECT * FROM posts ORDER BY id"):
        ratio = worst.get(p["id"])
        features.append(
            _feature(
                "post",
                p["lon"],
                p["lat"],
                id=p["id"],
                formation=p["formation"],
                altitude_m=p["altitude_m"],
                troops=p["troops"],
                depot=p["depot"],
                via_pass=p["via_pass"],
                access=json.loads(p["access"]),
                cover_ratio=round(ratio, 2) if ratio is not None else None,
                ready=ratio is None or ratio >= 1.0,
            )
        )
    for d in conn.execute("SELECT * FROM depots ORDER BY id"):
        features.append(
            _feature(
                "depot", d["lon"], d["lat"], id=d["id"], name=d["name"], altitude_m=d["altitude_m"]
            )
        )
    status = {s["pass_id"]: s for s in metrics.pass_statuses(conn, as_of)}
    for p in conn.execute("SELECT * FROM passes ORDER BY id"):
        s = status.get(p["id"], {})
        features.append(
            _feature(
                "pass",
                p["lon"],
                p["lat"],
                id=p["id"],
                name=p["name"],
                altitude_m=p["altitude_m"],
                status=s.get("status"),
                days=s.get("days"),
            )
        )
    return {"type": "FeatureCollection", "as_of": as_of, "features": features}
