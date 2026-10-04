"""Two stub roles with JWTs. Only a Logistics Officer may approve a plan.

DEMO ONLY: users and the shared password come from settings, not a directory; there is no lockout,
refresh or revocation. The design (Keycloak, mTLS, full RBAC) is in the docs, not in this code.
"""

from __future__ import annotations

import os
import secrets
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

import jwt
from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel

LOGISTICS_OFFICER, STAFF_OFFICER = "logistics_officer", "staff_officer"
USERS = {"lo": LOGISTICS_OFFICER, "staff": STAFF_OFFICER}
_SECRET = os.environ.get("JWT_SECRET") or secrets.token_hex(32)  # random per run unless set
ALGO = "HS256"

router = APIRouter(prefix="/auth", tags=["auth"])
bearer = HTTPBearer(auto_error=False)


class Login(BaseModel):
    username: str
    password: str


def issue(username: str) -> str:
    claims = {
        "sub": username,
        "role": USERS[username],
        "exp": datetime.now(UTC) + timedelta(hours=8),
    }
    return jwt.encode(claims, _SECRET, algorithm=ALGO)


@router.post("/token")
def token(body: Login) -> dict[str, Any]:
    expected = os.environ.get("DEMO_PASSWORD", "demo")
    if body.username not in USERS or not secrets.compare_digest(body.password, expected):
        raise HTTPException(401, "unknown user or wrong password")
    return {
        "access_token": issue(body.username),
        "token_type": "bearer",
        "role": USERS[body.username],
    }


def current_user(
    creds: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
) -> dict[str, Any] | None:
    if creds is None:
        return None
    try:
        return jwt.decode(creds.credentials, _SECRET, algorithms=[ALGO])
    except jwt.PyJWTError as exc:
        raise HTTPException(401, f"invalid token: {exc}") from exc


def require_role(role: str):
    def check(user: Annotated[dict[str, Any] | None, Depends(current_user)]) -> dict[str, Any]:
        if user is None:
            raise HTTPException(401, "sign in first")
        if user.get("role") != role:
            raise HTTPException(403, f"only a {role.replace('_', ' ')} may do this")
        return user

    return check
