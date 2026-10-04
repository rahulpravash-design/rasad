"""Gate contexts: the gate's memory of what it has already accepted.

MemoryContext is for batch work (building the database, evaluating the gate) and is advanced with
commit(): its world is everything committed so far. DbContext answers the same questions from
SQLite for the API; it never writes, and its world is the reports filed before `cutoff_ts`, so a
report is judged against the world as it was when it arrived, not against reports that came later.
"""

from __future__ import annotations

import sqlite3
from collections import defaultdict, deque
from datetime import datetime
from typing import Any

from gate.rules import Previous

HISTORY_LENGTH = 28


class MemoryContext:
    def __init__(
        self,
        public_keys: dict[str, str],
        deliveries: dict[tuple[str, str, str], int],
        now: datetime | None = None,
        posts: dict[str, tuple[str, int]] | None = None,
    ) -> None:
        """`posts` maps post id -> (formation, troops); needed only for peer_rates."""
        self.now = now
        self._keys = public_keys
        self._deliveries = deliveries
        self._posts = posts or {}
        self._peers: dict[str, list[str]] = defaultdict(list)
        for pid, (formation, _) in self._posts.items():
            self._peers[formation].append(pid)
        self._rate: dict[tuple[str, str], float] = {}
        self._ids: set[str] = set()
        self._nonces: set[str] = set()
        self._previous: dict[tuple[str, str], Previous] = {}
        self._history: dict[tuple[str, str], deque[tuple[str, int]]] = defaultdict(
            lambda: deque(maxlen=HISTORY_LENGTH)
        )

    def public_key(self, post_id: str) -> str | None:
        return self._keys.get(post_id)

    def seen(self, report_id: str, nonce: str) -> bool:
        return report_id in self._ids or nonce in self._nonces

    def previous(self, post_id: str, supply_class: str) -> Previous | None:
        return self._previous.get((post_id, supply_class))

    def delivered(self, post_id: str, supply_class: str, date: str) -> int:
        return self._deliveries.get((post_id, supply_class, date), 0)

    def history(self, post_id: str, supply_class: str, n: int) -> list[tuple[str, int]]:
        return list(self._history[(post_id, supply_class)])[-n:]

    def peer_rates(self, post_id: str, supply_class: str) -> list[float]:
        info = self._posts.get(post_id)
        if info is None:
            return []
        return [
            self._rate[(p, supply_class)]
            for p in self._peers[info[0]]
            if p != post_id and (p, supply_class) in self._rate
        ]

    def commit(self, report: dict[str, Any], verdict: str) -> None:
        """Record an accepted report. Rejected ones leave no trace: they never happened."""
        if verdict == "REJECTED":
            return
        self._ids.add(report["report_id"])
        self._nonces.add(report["nonce"])
        key = (report["post_id"], report["class"])
        self._previous[key] = Previous(
            ts=report["ts"],
            date=report["ts"][:10],
            closing=report["closing"],
            verified=verdict == "VERIFIED",
        )
        self._history[key].append((report["ts"], report["consumed"]))
        info = self._posts.get(report["post_id"])
        if info is not None:
            self._rate[key] = report["consumed"] / info[1]


class DbContext:
    """Read-only view over the `reports`, `gate_verdicts`, `deliveries` and `post_keys` tables."""

    def __init__(
        self,
        conn: sqlite3.Connection,
        cutoff_ts: str,
        now: datetime | None = None,
        exclude: str | None = None,
    ) -> None:
        self._conn = conn
        self._cutoff = cutoff_ts  # the world is reports with ts < this
        self.now = now
        # A stored report being re-checked must not count as its own replay.
        self._exclude = exclude or ""

    def public_key(self, post_id: str) -> str | None:
        row = self._conn.execute(
            "SELECT public_key FROM post_keys WHERE post_id = ?", (post_id,)
        ).fetchone()
        return row["public_key"] if row else None

    def seen(self, report_id: str, nonce: str) -> bool:
        row = self._conn.execute(
            """
            SELECT 1 FROM reports r JOIN gate_verdicts v USING (report_id)
            WHERE (r.report_id = ? OR r.nonce = ?) AND v.verdict != 'REJECTED'
              AND r.report_id != ? LIMIT 1
            """,
            (report_id, nonce, self._exclude),
        ).fetchone()
        return row is not None

    def previous(self, post_id: str, supply_class: str) -> Previous | None:
        row = self._conn.execute(
            """
            SELECT r.ts, r.report_date, r.closing, v.verdict
            FROM reports r JOIN gate_verdicts v USING (report_id)
            WHERE r.post_id = ? AND r.supply_class = ? AND r.ts < ? AND r.origin = 'field'
              AND v.verdict != 'REJECTED'
            ORDER BY r.ts DESC LIMIT 1
            """,
            (post_id, supply_class, self._cutoff),
        ).fetchone()
        if row is None:
            return None
        return Previous(row["ts"], row["report_date"], row["closing"], row["verdict"] == "VERIFIED")

    def delivered(self, post_id: str, supply_class: str, date: str) -> int:
        row = self._conn.execute(
            "SELECT COALESCE(SUM(qty), 0) AS q FROM deliveries "
            "WHERE post_id = ? AND supply_class = ? AND date = ?",
            (post_id, supply_class, date),
        ).fetchone()
        return int(row["q"])

    def history(self, post_id: str, supply_class: str, n: int) -> list[tuple[str, int]]:
        rows = self._conn.execute(
            """
            SELECT r.ts, r.consumed FROM reports r JOIN gate_verdicts v USING (report_id)
            WHERE r.post_id = ? AND r.supply_class = ? AND r.ts < ? AND r.origin = 'field'
              AND v.verdict != 'REJECTED'
            ORDER BY r.ts DESC LIMIT ?
            """,
            (post_id, supply_class, self._cutoff, n),
        ).fetchall()
        return [(r["ts"], r["consumed"]) for r in reversed(rows)]

    def peer_rates(self, post_id: str, supply_class: str) -> list[float]:
        rows = self._conn.execute(
            """
            SELECT 1.0 * (
                SELECT r.consumed FROM reports r JOIN gate_verdicts v USING (report_id)
                WHERE r.post_id = p.id AND r.supply_class = ? AND r.ts < ? AND r.origin = 'field'
                  AND v.verdict != 'REJECTED'
                ORDER BY r.ts DESC LIMIT 1
            ) / p.troops AS rate
            FROM posts p
            WHERE p.formation = (SELECT formation FROM posts WHERE id = ?) AND p.id != ?
            """,
            (supply_class, self._cutoff, post_id, post_id),
        ).fetchall()
        return [r["rate"] for r in rows if r["rate"] is not None]
