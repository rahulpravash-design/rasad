"""Hard and soft checks on a single report, given what the gate already knows.

Hard failures (severity `reject`) make a report REJECTED: it cannot be true. Soft ones (`flag`) make
it FLAGGED: it may be true but needs a person to look. Each check returns Reason objects; none of
them reads or writes state, they only ask the context.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Protocol

from gate import signing

REJECT, FLAG = "reject", "flag"
TS_FORMAT = "%Y-%m-%dT%H:%MZ"
TS_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}Z$")
QUANTITY_FIELDS = ("opening", "received", "consumed", "closing")
FUTURE_TOLERANCE = timedelta(hours=1)


@dataclass(frozen=True)
class Reason:
    code: str
    severity: str
    message: str


@dataclass(frozen=True)
class Previous:
    """The last report the gate accepted (not rejected) for a post and class before some time."""

    ts: str
    date: str
    closing: int
    verified: bool  # False if it was only FLAGGED


class GateContext(Protocol):
    """What the gate needs to know about the world; see gate/context.py for implementations.

    "The world" is every report the gate has accepted up to the moment the candidate arrives:
    `previous` is the latest accepted report for a post and class, `history` the (ts, consumed) of
    the latest few, `peer_rates` the latest consumption per soldier at the other posts of the same
    formation. Each implementation fixes where that moment is.
    """

    now: datetime | None

    def public_key(self, post_id: str) -> str | None: ...
    def seen(self, report_id: str, nonce: str) -> bool: ...
    def previous(self, post_id: str, supply_class: str) -> Previous | None: ...
    def delivered(self, post_id: str, supply_class: str, date: str) -> int: ...
    def history(self, post_id: str, supply_class: str, n: int) -> list[tuple[str, int]]: ...
    def peer_rates(self, post_id: str, supply_class: str) -> list[float]: ...


def check_schema(report: Mapping[str, Any], classes: set[str]) -> list[Reason]:
    missing = [k for k in (*signing.SIGNED_FIELDS, "sig") if k not in report]
    if missing:
        return [Reason("malformed", REJECT, f"missing fields: {', '.join(missing)}")]
    bad = [
        k for k in QUANTITY_FIELDS if not isinstance(report[k], int) or isinstance(report[k], bool)
    ]
    if bad:
        return [Reason("malformed", REJECT, f"quantities must be integers: {', '.join(bad)}")]
    if not isinstance(report["ts"], str) or not TS_PATTERN.match(report["ts"]):
        return [Reason("malformed", REJECT, "ts must look like 2026-10-10T06:20Z")]
    if report["class"] not in classes:
        return [Reason("malformed", REJECT, f"unknown supply class {report['class']!r}")]
    return []


def check_signature(report: Mapping[str, Any], public_key: str | None) -> list[Reason]:
    if public_key is None:
        return [Reason("unknown_post", REJECT, f"no registered key for post {report['post_id']}")]
    if not signing.verify(report, public_key):
        return [Reason("bad_signature", REJECT, "signature does not verify for this post's key")]
    return []


def check_balance(report: Mapping[str, Any]) -> list[Reason]:
    out: list[Reason] = []
    if any(report[k] < 0 for k in QUANTITY_FIELDS):
        out.append(Reason("negative_quantity", REJECT, "a quantity is negative"))
    expected = report["opening"] + report["received"] - report["consumed"]
    if report["closing"] != expected:
        out.append(
            Reason(
                "balance_mismatch",
                REJECT,
                f"closing {report['closing']} != opening + received - consumed = {expected}",
            )
        )
    return out


def check_timestamp(
    report: Mapping[str, Any], previous: Previous | None, now: datetime | None
) -> list[Reason]:
    out: list[Reason] = []
    try:
        ts = datetime.strptime(report["ts"], TS_FORMAT)
    except ValueError:
        return [Reason("malformed", REJECT, "ts is not a real date and time")]
    if now is not None and ts > now + FUTURE_TOLERANCE:
        out.append(Reason("timestamp_in_future", REJECT, f"ts {report['ts']} is in the future"))
    if previous is not None and report["ts"] <= previous.ts:
        out.append(
            Reason(
                "timestamp_not_after_previous",
                REJECT,
                f"ts {report['ts']} is not after the last accepted report ({previous.ts})",
            )
        )
    return out


def check_replay(report: Mapping[str, Any], ctx: GateContext) -> list[Reason]:
    if ctx.seen(report["report_id"], report["nonce"]):
        return [Reason("replay", REJECT, "report id or nonce has been seen before")]
    return []


def check_continuity(report: Mapping[str, Any], previous: Previous | None) -> list[Reason]:
    """Opening stock must equal the previous day's closing stock.

    Only assessed when the previous accepted report is from the day before and was itself verified:
    after a gap, or after a flagged report, there is nothing trustworthy to compare with, and
    flagging the next honest report would turn one alert into a chain of them.
    """
    if previous is None or not previous.verified:
        return []
    day_before = (datetime.strptime(report["ts"][:10], "%Y-%m-%d") - timedelta(days=1)).strftime(
        "%Y-%m-%d"
    )
    if previous.date != day_before or report["opening"] == previous.closing:
        return []
    return [
        Reason(
            "stock_discontinuity",
            FLAG,
            f"opening {report['opening']} does not match yesterday's closing {previous.closing}",
        )
    ]


def check_receipt(report: Mapping[str, Any], ctx: GateContext) -> list[Reason]:
    """Stock received must match what logistics records as delivered that day."""
    delivered = ctx.delivered(report["post_id"], report["class"], report["ts"][:10])
    if report["received"] == delivered:
        return []
    if delivered == 0:
        message = f"reports {report['received']} received but no delivery is recorded"
    else:
        message = f"reports {report['received']} received but {delivered} was delivered"
    return [Reason("receipt_mismatch", FLAG, message)]
