"""One-sentence reason for each planned movement, from whatever constraint decided it."""

from __future__ import annotations

from typing import Any


def _fmt(q: float, unit: str) -> str:
    return f"{q:,.0f} {unit}"


def reason(item: dict[str, Any], ctx: dict[str, Any]) -> str:
    pass_name, status = ctx["pass_name"], ctx["pass_status"]
    demand = f"P90 demand {_fmt(ctx['p90'], ctx['unit'])} over {ctx['horizon']} days"
    if item["mode"] == "truck":
        when = f"before {ctx['deadline']}" if ctx.get("deadline") else "now"
        return (
            f"Truck {when}: {pass_name} closure risk {ctx['p14']:.2f} within 14 days; {demand}; "
            f"cheapest mode while the road is open."
        )
    if item["mode"] == "heli":
        why = (
            f"{pass_name} is closed"
            if status == "CLOSED"
            else "no road access"
            if not ctx["has_truck"]
            else "mule capacity used up"
        )
        return (
            f"Helicopter: {why}; payload derated to {ctx['heli_payload']:.2f} t at "
            f"{ctx['altitude']:,} m ({ctx['sorties']} sorties); {demand}."
        )
    if item["mode"] == "mule":
        why = f"{pass_name} is closed" if status == "CLOSED" else "no road access"
        return f"Mule: {why}; cheaper than helicopter at this load; {demand}."
    return (
        f"SHORTFALL {item['qty_t']:.2f} t: helicopter and mule capacity exhausted "
        f"({pass_name} {status.lower()}); {demand}."
    )
