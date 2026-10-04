"""Hash-chained audit log.

Each entry stores prev_hash and hash = sha256(prev_hash + canonical(entry)), where canonical(entry)
is the sorted-key JSON of {ts, event, actor, payload}. Changing, deleting or reordering any entry
breaks every hash after it; verify() reports the first entry that no longer checks out.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import UTC, datetime
from typing import Any

GENESIS = "0" * 64


def canonical(entry: dict[str, Any]) -> str:
    return json.dumps(entry, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def entry_hash(prev_hash: str, ts: str, event: str, actor: str | None, payload: str) -> str:
    body = canonical({"ts": ts, "event": event, "actor": actor, "payload": json.loads(payload)})
    return hashlib.sha256((prev_hash + body).encode()).hexdigest()


def append(
    conn: sqlite3.Connection,
    event: str,
    payload: dict[str, Any],
    actor: str | None = None,
    ts: str | None = None,
) -> str:
    """Add one entry; returns its hash. Call inside the caller's transaction."""
    row = conn.execute("SELECT hash FROM audit ORDER BY seq DESC LIMIT 1").fetchone()
    prev = row[0] if row else GENESIS
    ts = ts or datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    body = canonical(payload)
    h = entry_hash(prev, ts, event, actor, body)
    conn.execute(
        "INSERT INTO audit (ts, event, actor, payload, prev_hash, hash) VALUES (?, ?, ?, ?, ?, ?)",
        (ts, event, actor, body, prev, h),
    )
    return h


def verify(conn: sqlite3.Connection) -> dict[str, Any]:
    prev = GENESIS
    n = 0
    for r in conn.execute(
        "SELECT seq, ts, event, actor, payload, prev_hash, hash FROM audit ORDER BY seq"
    ):
        n += 1
        if r[5] != prev or entry_hash(prev, r[1], r[2], r[3], r[4]) != r[6]:
            return {"valid": False, "broken_at": r[0], "entries": n}
        prev = r[6]
    return {"valid": True, "broken_at": None, "entries": n}
