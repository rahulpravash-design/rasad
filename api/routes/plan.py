"""Movement plans: generate, read, approve. Every step is written to the audit chain."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from api.auth import LOGISTICS_OFFICER, current_user, require_role
from api.deps import get_as_of_rw, get_rw_db
from api.settings import get_settings
from audit import chain
from planner.solve import solve

router = APIRouter(tags=["plan"])
Conn = Annotated[sqlite3.Connection, Depends(get_rw_db)]
AsOf = Annotated[str, Depends(get_as_of_rw)]


class PlanRequest(BaseModel):
    horizon_days: int = Field(30, ge=7, le=30)


def _read(conn: sqlite3.Connection, plan_id: int) -> dict[str, Any]:
    plan = conn.execute("SELECT * FROM plans WHERE id = ?", (plan_id,)).fetchone()
    if plan is None:
        raise HTTPException(404, f"no plan {plan_id}")
    items = [
        dict(r)
        for r in conn.execute("SELECT * FROM plan_items WHERE plan_id = ? ORDER BY id", (plan_id,))
    ]
    for it in items:
        it["class"] = it.pop("supply_class")
    by_mode: dict[str, float] = {}
    for it in items:
        by_mode[it["mode"]] = round(by_mode.get(it["mode"], 0) + it["qty_t"], 2)
    return {
        **dict(plan),
        "cost": round(sum(i["cost"] for i in items)),
        "tonnes_by_mode": by_mode,
        "items": items,
    }


@router.post("/plan")
def create_plan(
    body: PlanRequest,
    conn: Conn,
    as_of: AsOf,
    user: Annotated[dict[str, Any] | None, Depends(current_user)],
) -> dict[str, Any]:
    """Solve a plan for the as-of day and store it as a DRAFT."""
    result = solve(conn, get_settings().db_path, as_of, body.horizon_days)
    now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    actor = user["sub"] if user else None
    cur = conn.execute(
        "INSERT INTO plans (created_at, as_of, horizon_days, status, created_by) VALUES (?, ?, ?, 'DRAFT', ?)",
        (now, as_of, body.horizon_days, actor),
    )
    plan_id = cur.lastrowid
    for it in result["items"]:
        mode = "heli" if it["mode"] == "shortfall" else it["mode"]  # schema has no shortfall mode
        conn.execute(
            "INSERT INTO plan_items (plan_id, post_id, supply_class, mode, qty_t, depart_date, cost, "
            "time_days, risk, reason, status) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                plan_id,
                it["post_id"],
                it["class"],
                mode,
                it["qty_t"],
                it["depart_date"],
                it["cost"],
                it["time_days"],
                it["risk"],
                it["reason"],
                "SHORTFALL" if it["mode"] == "shortfall" else "PROPOSED",
            ),
        )
    chain.append(
        conn,
        "plan_generated",
        {
            "plan_id": plan_id,
            "as_of": as_of,
            "cost": result["cost"],
            "tonnes_by_mode": result["tonnes_by_mode"],
            "sorties": result["sorties_used"],
        },
        actor,
    )
    out = _read(conn, plan_id)
    out.update(
        {
            "sorties_used": result["sorties_used"],
            "sortie_budget": result["sortie_budget"],
            "solver_status": result["status"],
        }
    )
    return out


@router.get("/plan/{plan_id}")
def get_plan(plan_id: int, conn: Conn) -> dict[str, Any]:
    return _read(conn, plan_id)


@router.get("/plans")
def list_plans(conn: Conn) -> list[dict[str, Any]]:
    return [dict(r) for r in conn.execute("SELECT * FROM plans ORDER BY id DESC LIMIT 20")]


@router.post("/plan/{plan_id}/approve")
def approve(
    plan_id: int,
    conn: Conn,
    user: Annotated[dict[str, Any], Depends(require_role(LOGISTICS_OFFICER))],
) -> dict[str, Any]:
    plan = _read(conn, plan_id)
    if plan["status"] != "DRAFT":
        raise HTTPException(409, f"plan {plan_id} is already {plan['status']}")
    conn.execute("UPDATE plans SET status = 'APPROVED' WHERE id = ?", (plan_id,))
    conn.execute(
        "UPDATE plan_items SET status = 'APPROVED' WHERE plan_id = ? AND status = 'PROPOSED'",
        (plan_id,),
    )
    chain.append(conn, "plan_approved", {"plan_id": plan_id, "cost": plan["cost"]}, user["sub"])
    return _read(conn, plan_id)


@router.get("/audit")
def audit_log(conn: Conn, limit: int = 100) -> list[dict[str, Any]]:
    return [
        dict(r) for r in conn.execute("SELECT * FROM audit ORDER BY seq DESC LIMIT ?", (limit,))
    ]


@router.get("/audit/verify")
def audit_verify(conn: Conn) -> dict[str, Any]:
    return chain.verify(conn)
