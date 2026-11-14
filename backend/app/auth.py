"""Demo authentication: PBKDF2 password hashing + HS256 JWT.

Implemented with the standard library only so the local demo runs without
extra dependencies. Tokens are short-lived, signed, and scoped to a single
synthetic coordinator account. This is demo-grade access control, not a
substitute for a real identity provider.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import logging
import re
import threading
import time
from typing import Any

from .config import DEFAULT_DEMO_PASSWORD, settings

log = logging.getLogger("continuum.auth")

_PBKDF2_ITERS = 200_000
# Bounds on stored iteration counts: a corrupted or hostile hash string must
# not turn one login into a minutes-long PBKDF2 run.
_MIN_ITERS, _MAX_ITERS = 100_000, 2_000_000
# Clock skew tolerated on ``iat`` (tokens "issued in the future").
_IAT_LEEWAY_SECONDS = 60


# ── Password hashing ────────────────────────────────────────────────────────


def hash_password(password: str, *, salt: bytes | None = None) -> str:
    salt = salt or secrets.token_bytes(16)
    derived = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, _PBKDF2_ITERS)
    return f"pbkdf2_sha256${_PBKDF2_ITERS}${salt.hex()}${derived.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, iters, salt_hex, hash_hex = stored.split("$")
        if scheme != "pbkdf2_sha256" or not (_MIN_ITERS <= int(iters) <= _MAX_ITERS):
            return False
        derived = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), bytes.fromhex(salt_hex), int(iters)
        )
        return hmac.compare_digest(derived, bytes.fromhex(hash_hex))
    except (ValueError, AttributeError, TypeError):
        return False


# Verified against when the email is unknown, so "no such user" costs the same
# PBKDF2 work as "wrong password" and response time doesn't reveal which
# accounts exist.
_DUMMY_HASH = hash_password(secrets.token_urlsafe(16))


# ── JWT (HS256) ──────────────────────────────────────────────────────────────


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(data: str) -> bytes:
    padding = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(data + padding)


def _sign(signing_input: bytes) -> bytes:
    return hmac.new(settings.auth_secret.encode("utf-8"), signing_input, hashlib.sha256).digest()


def create_token(
    *, sub: str, email: str, name: str, role: str, ttl_minutes: float | None = None, scope: str | None = None
) -> str:
    """Sign a token. ``scope`` marks a narrow-purpose token (e.g. ``"stream"``
    for the SSE query string) that ``deps.require_auth`` will not accept as a
    session, so leaking one from an access log can't open the API."""
    ttl = ttl_minutes if ttl_minutes is not None else settings.auth_token_ttl_minutes
    now = int(time.time())
    header = {"alg": "HS256", "typ": "JWT"}
    payload: dict[str, Any] = {
        "sub": sub,
        "email": email,
        "name": name,
        "role": role,
        "iat": now,
        "exp": now + int(ttl * 60),
    }
    if scope:
        payload["scope"] = scope
    signing_input = (
        _b64url_encode(json.dumps(header, separators=(",", ":")).encode())
        + "."
        + _b64url_encode(json.dumps(payload, separators=(",", ":")).encode())
    )
    return f"{signing_input}.{_b64url_encode(_sign(signing_input.encode('utf-8')))}"


def decode_token(token: str) -> dict[str, Any]:
    """Return the payload dict, or raise ValueError if the token is invalid.

    Every failure is a ValueError (never a 500): bad shape, bad signature,
    wrong ``alg``, non-object payload, missing/non-numeric/expired ``exp``,
    ``iat`` in the future, or no ``sub``.
    """
    if not isinstance(token, str):
        raise ValueError("malformed token")
    try:
        head, body, signature = token.split(".")
        signing_input = f"{head}.{body}".encode("ascii")
        supplied = _b64url_decode(signature)
    except (ValueError, UnicodeEncodeError) as exc:
        raise ValueError("malformed token") from exc
    if not hmac.compare_digest(_sign(signing_input), supplied):
        raise ValueError("invalid signature")
    try:
        header = json.loads(_b64url_decode(head))
        payload = json.loads(_b64url_decode(body))
    except Exception as exc:
        raise ValueError("invalid payload") from exc
    if not isinstance(header, dict) or header.get("alg") != "HS256":
        raise ValueError("unsupported token algorithm")
    if not isinstance(payload, dict):
        raise ValueError("invalid payload")
    now = time.time()
    exp, iat = payload.get("exp"), payload.get("iat", 0)
    if isinstance(exp, bool) or not isinstance(exp, (int, float)):
        raise ValueError("token has no expiry")
    if exp <= now:
        raise ValueError("token expired")
    if isinstance(iat, bool) or not isinstance(iat, (int, float)) or iat > now + _IAT_LEEWAY_SECONDS:
        raise ValueError("token issued in the future")
    if not isinstance(payload.get("sub"), str) or not payload["sub"]:
        raise ValueError("token has no subject")
    return payload


# ── User repository ──────────────────────────────────────────────────────────


def _public_user(user: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": user["id"],
        "email": user["email"],
        "name": user["name"],
        "role": user.get("role", "coordinator"),
    }


def _may_create_demo_account(password: str) -> bool:
    """Demo accounts with the public default password exist only in demo mode."""
    if settings.demo_mode or password != DEFAULT_DEMO_PASSWORD:
        return True
    log.error("SECURITY: not creating a demo account with the default password while DEMO_MODE is off.")
    return False


def ensure_demo_user(db) -> dict[str, Any] | None:
    """Idempotently create the synthetic coordinator account."""
    users = db.users
    existing = users.find_one({"id": "user_coordinator"})
    if existing:
        return _public_user(existing)
    if not _may_create_demo_account(settings.demo_coordinator_password):
        return None
    users.insert_one(
        {
            "id": "user_coordinator",
            "email": settings.demo_coordinator_email.strip().lower(),
            "name": settings.demo_coordinator_name,
            "role": "coordinator",
            "password_hash": hash_password(settings.demo_coordinator_password),
            "created_at": time.time(),
        }
    )
    return {
        "id": "user_coordinator",
        "email": settings.demo_coordinator_email.strip().lower(),
        "name": settings.demo_coordinator_name,
        "role": "coordinator",
    }


def ensure_clinician_user(db) -> dict[str, Any] | None:
    """Idempotently create the synthetic clinician account."""
    users = db.users
    existing = users.find_one({"id": "user_clinician"})
    if existing:
        return _public_user(existing)
    if not _may_create_demo_account(settings.demo_clinician_password):
        return None
    users.insert_one(
        {
            "id": "user_clinician",
            "email": settings.demo_clinician_email.strip().lower(),
            "name": settings.demo_clinician_name,
            "role": "clinician",
            "password_hash": hash_password(settings.demo_clinician_password),
            "created_at": time.time(),
        }
    )
    return {
        "id": "user_clinician",
        "email": settings.demo_clinician_email.strip().lower(),
        "name": settings.demo_clinician_name,
        "role": "clinician",
    }


def get_user_by_email(db, email: str) -> dict[str, Any] | None:
    if not isinstance(email, str):
        return None
    normalized = email.strip().lower()
    # Exact match first; fall back to a case-insensitive scan for accounts
    # stored before emails were normalised (a mixed-case DEMO_*_EMAIL).
    user = db.users.find_one({"email": normalized})
    if user:
        return user
    return db.users.find_one({"email": {"$regex": f"^{re.escape(normalized)}$", "$options": "i"}})


def get_user_by_id(db, user_id: str) -> dict[str, Any] | None:
    return db.users.find_one({"id": user_id})


def authenticate(db, email: str, password: str) -> dict[str, Any] | None:
    user = get_user_by_email(db, email)
    if not user:
        verify_password(password, _DUMMY_HASH)  # equalise timing with a real miss
        return None
    if not verify_password(password, user.get("password_hash", "")):
        return None
    return user


# ── Login throttle ───────────────────────────────────────────────────────────


class LoginThrottle:
    """In-process sliding-window limit on failed logins per email and per client.

    Demo-grade (one process, resets on restart); a real deployment puts rate
    limiting at the gateway/IdP. Still enough to stop an unattended password
    guesser against the four-account demo.
    """

    def __init__(self, max_failures: int = 5, window_seconds: int = 300):
        self.max_failures = max_failures
        self.window = window_seconds
        self._failures: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def _recent(self, key: str, now: float) -> list[float]:
        hits = [t for t in self._failures.get(key, []) if now - t < self.window]
        if hits:
            self._failures[key] = hits
        else:
            self._failures.pop(key, None)
        return hits

    def retry_after(self, *keys: str) -> int:
        """Seconds until a blocked key frees up, 0 when the attempt may proceed."""
        now = time.time()
        with self._lock:
            waits = [
                int(self.window - (now - hits[0])) + 1
                for hits in (self._recent(k, now) for k in keys)
                if len(hits) >= self.max_failures
            ]
        return max(waits, default=0)

    def failure(self, *keys: str) -> None:
        now = time.time()
        with self._lock:
            for k in keys:
                self._recent(k, now)
                self._failures.setdefault(k, []).append(now)

    def success(self, *keys: str) -> None:
        with self._lock:
            for k in keys:
                self._failures.pop(k, None)

    def reset(self) -> None:
        with self._lock:
            self._failures.clear()


login_throttle = LoginThrottle()


def public_user(user: dict[str, Any]) -> dict[str, Any]:
    return _public_user(user)
