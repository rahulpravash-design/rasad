"""FastAPI dependencies: the database connection and the scenario date."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Annotated

from fastapi import Depends, HTTPException, Query

from api.db import connect
from api.settings import get_settings
from config.loader import load_constraints


def get_db() -> Iterator:
    path = get_settings().db_path
    if not path.exists():
        raise HTTPException(
            status_code=503, detail=f"database {path} not found; run `make data` to build it"
        )
    conn = connect(path, readonly=True)
    try:
        yield conn
    finally:
        conn.close()


def get_as_of(
    conn: Annotated[object, Depends(get_db)],
    as_of: Annotated[
        str | None, Query(pattern=r"^\d{4}-\d{2}-\d{2}$", description="ISO date")
    ] = None,
) -> str:
    """The scenario date: ?as_of=YYYY-MM-DD, else scenario.as_of from config/constraints.yaml."""
    chosen = as_of or str(load_constraints()["scenario"]["as_of"])
    first, last = conn.execute("SELECT MIN(report_date), MAX(report_date) FROM reports").fetchone()
    if first is None or not first <= chosen <= last:
        raise HTTPException(
            status_code=404, detail=f"no data for as_of={chosen}; data covers {first} to {last}"
        )
    return chosen
