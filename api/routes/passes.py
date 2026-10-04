from __future__ import annotations

import sqlite3
from typing import Annotated, Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field

from api import metrics
from api.deps import get_as_of, get_db

router = APIRouter(tags=["passes"])


class PassStatus(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    pass_id: str = Field(serialization_alias="pass")
    name: str
    altitude_m: int
    status: Literal["OPEN", "AT_RISK", "CLOSED"]
    # null until the Day 7 closure model; `days` is days closed so far while CLOSED, else null.
    p_close: float | None
    days: int | None
    snow_3d_cm: float | None
    temp_14d_c: float | None
    source: str


class PassesResponse(BaseModel):
    as_of: str
    passes: list[PassStatus]


@router.get("/passes", response_model=PassesResponse, response_model_by_alias=True)
def passes(
    conn: Annotated[sqlite3.Connection, Depends(get_db)],
    as_of: Annotated[str, Depends(get_as_of)],
) -> PassesResponse:
    return PassesResponse(
        as_of=as_of, passes=[PassStatus(**row) for row in metrics.pass_statuses(conn, as_of)]
    )
