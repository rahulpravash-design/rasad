"""Field reports through the verified data gate."""

from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, timedelta
from typing import Annotated, Any, Literal

import numpy as np
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, StrictInt

from api.deps import get_as_of_rw, get_rw_db
from api.gate_service import get_gate
from api.settings import get_settings
from audit import chain
from gate import attacks, signing
from gate.batch import to_wire
from gate.context import DbContext
from gate.pipeline import Verdict

router = APIRouter(prefix="/reports", tags=["reports"])

VerdictName = Literal["VERIFIED", "FLAGGED", "REJECTED"]


class SignedReport(BaseModel):
    """The report JSON contract. `class` is a Python keyword, so it is `class_` in code."""

    model_config = ConfigDict(populate_by_name=True)

    report_id: str
    post_id: str
    ts: str
    class_: str = Field(alias="class")
    opening: StrictInt
    received: StrictInt
    consumed: StrictInt
    closing: StrictInt
    nonce: str
    sig: str

    def wire(self) -> dict[str, Any]:
        return self.model_dump(by_alias=True)


class VerdictOut(BaseModel):
    report_id: str
    verdict: VerdictName
    reasons: list[str]
    reason_codes: list[str]
    score: float | None = None


class StoredReport(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    report_id: str
    post_id: str
    ts: str
    class_: str = Field(alias="class", serialization_alias="class")
    opening: int
    received: int
    consumed: int
    closing: int
    nonce: str
    sig: str | None
    origin: Literal["field", "injected"]
    verdict: VerdictName
    reasons: list[str]
    reason_codes: list[str]
    score: float | None = None


class ReportList(BaseModel):
    as_of: str
    total: int
    items: list[StoredReport]


class InjectRequest(BaseModel):
    attack_type: Literal["forged_signature", "replay", "inflated_consumption", "deflated_stock"]
    post_id: str | None = None
    supply_class: str | None = None


class InjectResult(BaseModel):
    attack_type: str
    target: str
    detected: bool
    verdict: VerdictOut
    report: StoredReport


def _now(as_of: str) -> datetime:
    """The scenario clock: the end of the as-of day."""
    return datetime.strptime(f"{as_of}T23:59Z", "%Y-%m-%dT%H:%MZ")


def _context(conn: sqlite3.Connection, as_of: str, day: str | None = None, **kw: Any) -> DbContext:
    """The gate's world for judging a report filed on `day` (default: the as-of day): everything
    accepted before that day began."""
    return DbContext(conn, cutoff_ts=f"{day or as_of}T00:00Z", now=_now(as_of), **kw)


def _persist(
    conn: sqlite3.Connection, report: dict[str, Any], verdict: Verdict, origin: str
) -> str:
    """Store a report and its verdict, returning the id it was stored under.

    A replay arrives with an id that is already in the table. The attempt is still worth showing, so
    it is stored under a suffixed id ("<id>~dup1") rather than overwriting or dropping it.
    """
    stored_id = report["report_id"]
    n = 0
    while conn.execute("SELECT 1 FROM reports WHERE report_id = ?", (stored_id,)).fetchone():
        n += 1
        stored_id = f"{report['report_id']}~dup{n}"
    conn.execute(
        "INSERT INTO reports (report_id, post_id, ts, report_date, supply_class, opening, "
        "received, consumed, closing, nonce, sig, origin) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            stored_id,
            report["post_id"],
            report["ts"],
            report["ts"][:10],
            report["class"],
            report["opening"],
            report["received"],
            report["consumed"],
            report["closing"],
            report["nonce"],
            report["sig"],
            origin,
        ),
    )
    conn.execute(
        "INSERT INTO gate_verdicts VALUES (?, ?, ?, ?, ?, ?)",
        (
            stored_id,
            verdict.verdict,
            json.dumps(verdict.messages),
            json.dumps(verdict.codes),
            verdict.score,
            report["ts"],
        ),
    )
    return stored_id


def _verdict_out(report_id: str, v: Verdict) -> VerdictOut:
    return VerdictOut(
        report_id=report_id,
        verdict=v.verdict,
        reasons=v.messages,
        reason_codes=v.codes,
        score=v.score,
    )


def _stored(conn: sqlite3.Connection, report_id: str) -> StoredReport:
    row = conn.execute(
        "SELECT r.*, v.verdict, v.reasons, v.reason_codes, v.score FROM reports r "
        "JOIN gate_verdicts v USING (report_id) WHERE r.report_id = ?",
        (report_id,),
    ).fetchone()
    return _row_to_stored(row)


def _row_to_stored(row: sqlite3.Row) -> StoredReport:
    return StoredReport(
        report_id=row["report_id"],
        post_id=row["post_id"],
        ts=row["ts"],
        class_=row["supply_class"],
        opening=row["opening"],
        received=row["received"],
        consumed=row["consumed"],
        closing=row["closing"],
        nonce=row["nonce"],
        sig=row["sig"],
        origin=row["origin"],
        verdict=row["verdict"],
        reasons=json.loads(row["reasons"]),
        reason_codes=json.loads(row["reason_codes"]),
        score=row["score"],
    )


Conn = Annotated[sqlite3.Connection, Depends(get_rw_db)]
AsOf = Annotated[str, Depends(get_as_of_rw)]


@router.post("", response_model=VerdictOut)
def submit_report(report: SignedReport, conn: Conn, as_of: AsOf) -> VerdictOut:
    """Verify a signed report and record it with its verdict. Rejected reports are recorded too;
    an accepted one for a slot that already has an accepted report is refused with 409."""
    wire = report.wire()
    verdict = get_gate().evaluate(wire, _context(conn, as_of))
    if (
        verdict.verdict != "REJECTED"
        and conn.execute(
            "SELECT 1 FROM trusted_reports "
            "WHERE post_id = ? AND supply_class = ? AND report_date = ?",
            (wire["post_id"], wire["class"], wire["ts"][:10]),
        ).fetchone()
    ):
        # Not a gate matter: two accepted reports for one slot would be counted twice downstream.
        raise HTTPException(
            409,
            f"{wire['post_id']}/{wire['class']} already has an accepted report "
            f"for {wire['ts'][:10]}",
        )
    stored_id = _persist(conn, wire, verdict, "field")
    chain.append(
        conn,
        f"report_{verdict.verdict.lower()}",
        {"report_id": stored_id, "reasons": verdict.codes},
    )
    return _verdict_out(stored_id, verdict)


@router.post("/inject", response_model=InjectResult)
def inject(body: InjectRequest, conn: Conn, as_of: AsOf) -> InjectResult:
    """Generate a tampered report for the as-of day, run it through the gate and record the verdict.

    The report is stored with origin 'injected' so it shows up in the list but never in analytics.
    A tampered report that the gate fails to catch is stored as VERIFIED on purpose: that is the
    honest picture for the insider attacks.
    """
    settings = get_settings()
    n_injected = conn.execute("SELECT COUNT(*) FROM reports WHERE origin = 'injected'").fetchone()[
        0
    ]
    rng = np.random.default_rng([settings.seed, 9, n_injected])

    if body.post_id:
        post_id = body.post_id
    else:
        posts = [r["id"] for r in conn.execute("SELECT id FROM posts ORDER BY id")]
        post_id = posts[int(rng.integers(0, len(posts)))]
    if body.supply_class:
        supply_class = body.supply_class
    else:
        classes = sorted(
            {r["supply_class"] for r in conn.execute("SELECT DISTINCT supply_class FROM reports")}
        )
        supply_class = classes[int(rng.integers(0, len(classes)))]

    genuine_row = conn.execute(
        "SELECT * FROM trusted_reports WHERE post_id = ? AND supply_class = ? AND report_date = ?",
        (post_id, supply_class, as_of),
    ).fetchone()
    if genuine_row is None:
        raise HTTPException(404, f"no genuine report for {post_id}/{supply_class} on {as_of}")
    genuine = to_wire(dict(genuine_row))
    earlier = [
        to_wire(dict(r))
        for r in conn.execute(
            "SELECT * FROM trusted_reports "
            "WHERE post_id = ? AND supply_class = ? AND report_date < ? "
            "ORDER BY report_date DESC LIMIT 10",
            (post_id, supply_class, as_of),
        )
    ]

    report = attacks.make_attack(
        body.attack_type,
        genuine,
        rng,
        signing.private_key_from_seed(post_id, settings.seed),
        settings.seed,
        earlier,
    )
    if report is None:
        raise HTTPException(
            409, f"cannot build a {body.attack_type} report for {post_id}/{supply_class}"
        )

    # Judged as the real report would have been: against the world before that day began.
    verdict = get_gate().evaluate(report, _context(conn, as_of))
    stored_id = _persist(conn, report, verdict, "injected")
    chain.append(
        conn,
        f"report_{verdict.verdict.lower()}",
        {
            "report_id": stored_id,
            "injected": body.attack_type,
            "reasons": verdict.codes,
        },
    )
    return InjectResult(
        attack_type=body.attack_type,
        target=f"{post_id}/{supply_class} on {as_of}",
        detected=verdict.verdict != "VERIFIED",
        verdict=_verdict_out(stored_id, verdict),
        report=_stored(conn, stored_id),
    )


def _next_day(as_of: str) -> str:
    """`ts < next day` selects reports filed up to the end of `as_of` and, unlike a report_date
    filter, lets one index on ts serve both the range and the newest-first order."""
    return (date.fromisoformat(as_of) + timedelta(days=1)).isoformat()


@router.get("", response_model=ReportList)
def list_reports(
    conn: Conn,
    as_of: AsOf,
    status: Annotated[VerdictName | None, Query()] = None,
    post_id: str | None = None,
    origin: Literal["field", "injected"] | None = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> ReportList:
    """Reports filed up to the as-of day, newest first, with their verdicts."""
    where = ["r.ts < ?"]
    args: list[Any] = [_next_day(as_of)]
    for clause, value in (("r.post_id = ?", post_id), ("r.origin = ?", origin)):
        if value is not None:
            where.append(clause)
            args.append(value)
    base = " AND ".join(where)
    cols = "r.*, v.verdict, v.reasons, v.reason_codes, v.score"

    def count_with(verdict: str) -> int:
        return conn.execute(
            f"SELECT COUNT(*) FROM gate_verdicts v CROSS JOIN reports r "
            f"ON r.report_id = v.report_id WHERE v.verdict = ? AND {base}",
            [verdict, *args],
        ).fetchone()[0]

    if status in ("FLAGGED", "REJECTED"):
        # Rare verdicts: start from the verdict index (CROSS JOIN fixes the loop order).
        total = count_with(status)
        rows = conn.execute(
            f"SELECT {cols} FROM gate_verdicts v CROSS JOIN reports r "
            f"ON r.report_id = v.report_id WHERE v.verdict = ? AND {base} "
            "ORDER BY r.ts DESC, r.rowid DESC LIMIT ? OFFSET ?",
            [status, *args, limit, offset],
        ).fetchall()
    else:
        # Everything, or VERIFIED (almost everything): walk the ts index newest first.
        total = conn.execute(f"SELECT COUNT(*) FROM reports r WHERE {base}", args).fetchone()[0]
        verdict_clause = ""
        if status == "VERIFIED":
            total -= count_with("FLAGGED") + count_with("REJECTED")
            # Unary + stops SQLite using the verdict index for the 99% case; the ts index is right.
            verdict_clause = " AND +v.verdict = 'VERIFIED'"
        rows = conn.execute(
            f"SELECT {cols} FROM reports r JOIN gate_verdicts v USING (report_id) "
            f"WHERE {base}{verdict_clause} ORDER BY r.ts DESC, r.rowid DESC LIMIT ? OFFSET ?",
            [*args, limit, offset],
        ).fetchall()
    return ReportList(as_of=as_of, total=total, items=[_row_to_stored(r) for r in rows])


@router.get("/summary")
def summary(conn: Conn, as_of: AsOf) -> dict[str, Any]:
    """Verdict counts up to the as-of day, split by field and injected reports. VERIFIED is the
    total minus the (few) others, which keeps this off a full join."""
    nxt = _next_day(as_of)
    out: dict[str, dict[str, int]] = {
        "field": {"VERIFIED": 0, "FLAGGED": 0, "REJECTED": 0},
        "injected": {"VERIFIED": 0, "FLAGGED": 0, "REJECTED": 0},
    }
    for r in conn.execute(
        "SELECT origin, COUNT(*) AS n FROM reports WHERE ts < ? GROUP BY origin", (nxt,)
    ):
        out[r["origin"]]["VERIFIED"] = r["n"]
    for r in conn.execute(
        "SELECT r.origin, v.verdict, COUNT(*) AS n FROM gate_verdicts v CROSS JOIN reports r "
        "ON r.report_id = v.report_id WHERE v.verdict IN ('FLAGGED', 'REJECTED') AND r.ts < ? "
        "GROUP BY r.origin, v.verdict",
        (nxt,),
    ):
        out[r["origin"]][r["verdict"]] = r["n"]
        out[r["origin"]]["VERIFIED"] -= r["n"]
    return {"as_of": as_of, **out}


@router.post("/{report_id}/verify", response_model=VerdictOut)
def reverify(report_id: str, conn: Conn, as_of: AsOf) -> VerdictOut:
    """Run a stored report through the gate again, against the world before it was filed. Records
    nothing; the stored verdict is unchanged."""
    row = conn.execute("SELECT * FROM reports WHERE report_id = ?", (report_id,)).fetchone()
    if row is None:
        raise HTTPException(404, f"no report {report_id}")
    wire = to_wire(dict(row))
    wire["report_id"] = report_id.split("~dup")[0]  # a stored duplicate is judged as the original
    verdict = get_gate().evaluate(
        wire, _context(conn, as_of, day=row["report_date"], exclude=report_id)
    )
    return _verdict_out(report_id, verdict)
