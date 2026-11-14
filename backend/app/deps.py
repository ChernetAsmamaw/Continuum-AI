"""Shared FastAPI dependencies."""

from __future__ import annotations

from fastapi import HTTPException, Request

from . import auth as auth_service
from .db import get_db


def _token_from(request: Request) -> str:
    header = request.headers.get("authorization") or request.headers.get("Authorization") or ""
    if header.lower().startswith("bearer "):
        return header[7:].strip()
    return ""


def session_payload(token: str) -> dict:
    """Decode a *session* token; scoped tokens (stream tickets) are refused."""
    payload = auth_service.decode_token(token)
    if payload.get("scope") is not None:
        raise ValueError("scoped token is not a session")
    return payload


def require_auth(request: Request) -> dict:
    """Validate the ``Authorization: Bearer <jwt>`` header and return the user.

    The role always comes from the database record, never from the token, so
    a role change or account removal takes effect on the next request.
    """
    token = _token_from(request)
    if not token:
        raise HTTPException(status_code=401, detail="Authentication required")
    try:
        payload = session_payload(token)
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    user = get_db().users.find_one({"id": payload.get("sub")})
    if not user:
        raise HTTPException(status_code=401, detail="Unknown user")
    return auth_service.public_user(user)


def require_role(*roles: str):
    """Build a dependency that requires the caller to hold one of ``roles``.

    Returns the authenticated user on success; raises 403 when the token is
    valid but the role does not match (401 is left to ``require_auth``).
    """

    def _check(request: Request) -> dict:
        user = require_auth(request)
        if user.get("role") not in roles:
            raise HTTPException(
                status_code=403,
                detail=f"Requires one of the roles: {', '.join(roles)}",
            )
        return user

    return _check


def optional_user(request: Request) -> dict | None:
    """Resolve the user when a valid token is present, otherwise ``None``."""
    token = _token_from(request)
    if not token:
        return None
    try:
        payload = session_payload(token)
    except ValueError:
        return None
    user = get_db().users.find_one({"id": payload.get("sub")})
    return auth_service.public_user(user) if user else None


_ROLE_LABELS = {"coordinator": "care coordinator", "clinician": "clinician"}


def actor_label(user: dict) -> str:
    """The audit name for an authenticated user: ``"Name (role)"``.

    Every write takes its actor from here, never from the request body, so the
    audit trail records who actually acted.
    """
    name = (user.get("name") or user.get("email") or "Unknown user").strip()
    role = _ROLE_LABELS.get(user.get("role") or "", user.get("role") or "user")
    return f"{name} ({role})"
