"""FastAPI dependencies: database connections and the scenario date."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from typing import Annotated

from fastapi import Depends, HTTPException, Query

from api.db import connect
from api.settings import get_settings
from config.loader import load_constraints

AsOfQuery = Annotated[
    str | None,
    Query(pattern=r"^\d{4}-\d{2}-\d{2}$", description="ISO date; default scenario.as_of"),
]


def _path_or_503():
    path = get_settings().db_path
    if not path.exists():
        raise HTTPException(
            status_code=503, detail=f"database {path} not found; run `make data` to build it"
        )
    return path


def get_db() -> Iterator[sqlite3.Connection]:
    """Read-only connection for endpoints that only look."""
    conn = connect(_path_or_503(), readonly=True)
    try:
        yield conn
    finally:
        conn.close()


def get_rw_db() -> Iterator[sqlite3.Connection]:
    """Writable connection for endpoints that record something (reports, plans, audit). Commits when
    the request succeeds, rolls back when it raises."""
    conn = connect(_path_or_503())
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def resolve_as_of(conn: sqlite3.Connection, as_of: str | None) -> str:
    """The scenario date: ?as_of=YYYY-MM-DD, else scenario.as_of from config/constraints.yaml."""
    chosen = as_of or str(load_constraints()["scenario"]["as_of"])
    # Two scalar subqueries so SQLite answers each from the index instead of scanning.
    first, last = conn.execute(
        "SELECT (SELECT MIN(report_date) FROM reports), (SELECT MAX(report_date) FROM reports)"
    ).fetchone()
    if first is None or not first <= chosen <= last:
        raise HTTPException(
            status_code=404, detail=f"no data for as_of={chosen}; data covers {first} to {last}"
        )
    return chosen


def get_as_of(conn: Annotated[sqlite3.Connection, Depends(get_db)], as_of: AsOfQuery = None) -> str:
    return resolve_as_of(conn, as_of)


def get_as_of_rw(
    conn: Annotated[sqlite3.Connection, Depends(get_rw_db)], as_of: AsOfQuery = None
) -> str:
    return resolve_as_of(conn, as_of)
