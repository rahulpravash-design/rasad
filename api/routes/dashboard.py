from __future__ import annotations

import sqlite3
from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from api import metrics
from api.deps import get_as_of, get_db
from config.loader import load_constraints

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


class Kpis(BaseModel):
    as_of: str
    posts: int
    formations: int
    depots: int
    convoys: int
    convoy_window_days: int
    passes_at_risk: int
    passes_closed: int
    readiness_pct: float
    post_classes_total: int
    post_classes_ready: int


@router.get("/kpis", response_model=Kpis)
def kpis(
    conn: Annotated[sqlite3.Connection, Depends(get_db)],
    as_of: Annotated[str, Depends(get_as_of)],
) -> Kpis:
    cover = metrics.stock_cover(conn, as_of, load_constraints()["min_stock_days"])
    statuses = metrics.pass_statuses(conn, as_of)
    ready = sum(1 for row in cover if row["ready"])
    return Kpis(
        as_of=as_of,
        posts=conn.execute("SELECT COUNT(*) FROM posts").fetchone()[0],
        formations=conn.execute("SELECT COUNT(DISTINCT formation) FROM posts").fetchone()[0],
        depots=conn.execute("SELECT COUNT(*) FROM depots").fetchone()[0],
        convoys=len(metrics.upcoming_convoys(conn, as_of)),
        convoy_window_days=metrics.CONVOY_WINDOW_DAYS,
        passes_at_risk=sum(1 for s in statuses if s["status"] == "AT_RISK"),
        passes_closed=sum(1 for s in statuses if s["status"] == "CLOSED"),
        readiness_pct=round(100.0 * ready / len(cover), 1) if cover else 0.0,
        post_classes_total=len(cover),
        post_classes_ready=ready,
    )
