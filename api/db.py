"""SQLite helpers: connections, schema creation and a content digest."""

from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path

SCHEMA_PATH = Path(__file__).with_name("schema.sql")


def connect(path: Path | str, *, readonly: bool = False) -> sqlite3.Connection:
    """Open the database.

    `check_same_thread=False` because FastAPI may create a dependency's connection on one worker
    thread and run the endpoint on another. Each connection belongs to exactly one request and is
    never used concurrently, which is what that check exists to protect.
    """
    path = Path(path)
    if readonly:
        conn = sqlite3.connect(
            f"{path.resolve().as_uri()}?mode=ro", uri=True, check_same_thread=False
        )
    else:
        conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))


def digest(conn: sqlite3.Connection) -> str:
    """sha256 over every table's rows in primary-key order. Two builds with the same seed and config
    must produce the same digest; `make data-check` relies on it."""
    h = hashlib.sha256()
    tables = [
        r[0]
        for r in conn.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        )
    ]
    for table in tables:
        info = conn.execute(f"PRAGMA table_info({table})").fetchall()
        pk = [c["name"] for c in sorted((c for c in info if c["pk"]), key=lambda c: c["pk"])]
        order = ", ".join(pk) if pk else "rowid"
        h.update(table.encode())
        for row in conn.execute(f"SELECT * FROM {table} ORDER BY {order}"):
            h.update(repr(tuple(row)).encode())
    return h.hexdigest()
