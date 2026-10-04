"""Read-side queries shared by the dashboard, the map and (later) the forecast/plan endpoints.

Everything is evaluated "as of" a date: rows after it are ignored, which is what lets the app replay
the held-out winter as if it were live.
"""

from __future__ import annotations

import sqlite3
from typing import Any

TRAILING_DAYS = 28
CONVOY_WINDOW_DAYS = 7


def stock_cover(
    conn: sqlite3.Connection, as_of: str, min_stock_days: dict[str, float]
) -> list[dict[str, Any]]:
    """Days of cover per post x class: closing stock on `as_of` over the mean consumption of the
    trailing 28 days (as-of day included)."""
    rows = conn.execute(
        """
        SELECT cur.post_id, cur.supply_class, cur.closing, trail.avg_consumed
        FROM reports AS cur
        JOIN (
            SELECT post_id, supply_class, AVG(consumed) AS avg_consumed
            FROM reports
            WHERE report_date BETWEEN date(:as_of, :window) AND :as_of
            GROUP BY post_id, supply_class
        ) AS trail USING (post_id, supply_class)
        WHERE cur.report_date = :as_of
        ORDER BY cur.post_id, cur.supply_class
        """,
        {"as_of": as_of, "window": f"-{TRAILING_DAYS - 1} days"},
    ).fetchall()
    out = []
    for r in rows:
        avg = r["avg_consumed"]
        cover = (
            r["closing"] / avg if avg and avg > 0 else None
        )  # None = no consumption, never short
        minimum = float(min_stock_days[r["supply_class"]])
        out.append(
            {
                "post_id": r["post_id"],
                "supply_class": r["supply_class"],
                "closing": r["closing"],
                "avg_consumed": avg,
                "days_cover": cover,
                "min_stock_days": minimum,
                "ready": cover is None or cover >= minimum,
            }
        )
    return out


def pass_statuses(conn: sqlite3.Connection, as_of: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT ps.pass_id, p.name, p.altitude_m, ps.status, ps.p_close, ps.days,
               ps.snow_3d_cm, ps.temp_14d_c, ps.source
        FROM pass_status AS ps JOIN passes AS p ON p.id = ps.pass_id
        WHERE ps.as_of = ?
        ORDER BY ps.pass_id
        """,
        (as_of,),
    ).fetchall()
    return [dict(r) for r in rows]


def upcoming_convoys(conn: sqlite3.Connection, as_of: str) -> list[dict[str, Any]]:
    """Scheduled deliveries in the next 7 days, grouped into convoys (depot x day x mode)."""
    rows = conn.execute(
        """
        SELECT p.depot, d.date, d.mode, COUNT(DISTINCT d.post_id) AS posts, COUNT(*) AS lines
        FROM deliveries AS d JOIN posts AS p ON p.id = d.post_id
        WHERE d.date > :as_of AND d.date <= date(:as_of, :window)
        GROUP BY p.depot, d.date, d.mode
        ORDER BY d.date, p.depot, d.mode
        """,
        {"as_of": as_of, "window": f"+{CONVOY_WINDOW_DAYS} days"},
    ).fetchall()
    return [dict(r) for r in rows]
