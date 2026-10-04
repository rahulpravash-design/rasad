"""RASAD API.  Run:  uvicorn api.main:app --reload"""

from __future__ import annotations

import sqlite3
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api import auth
from api.db import connect
from api.routes import dashboard, forecast, passes, plan, reports, sector
from api.settings import get_settings

app = FastAPI(
    title="RASAD",
    version="0.1.0",
    description="Verified reports, federated forecasting and closure-aware movement planning.",
)

# The Vite dev server runs on another origin; production serves the UI and proxies /api.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

app.include_router(dashboard.router)
app.include_router(passes.router)
app.include_router(sector.router)
app.include_router(reports.router)
app.include_router(forecast.router)
app.include_router(plan.router)
app.include_router(auth.router)


@app.get("/health", tags=["health"])
def health() -> dict[str, Any]:
    """Liveness plus data provenance. Works with no database and no network."""
    settings = get_settings()
    out: dict[str, Any] = {
        "status": "ok",
        "offline_mode": settings.offline_mode,
        "data_loaded": False,
    }
    if settings.db_path.exists():
        conn = connect(settings.db_path, readonly=True)
        try:
            out["data"] = dict(conn.execute("SELECT key, value FROM meta").fetchall())
            out["data_loaded"] = True
        except sqlite3.DatabaseError:
            out["data_loaded"] = False
        finally:
            conn.close()
    return out
