from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from .. import auth as auth_service
from ..config import settings
from ..db import get_db
from ..deps import require_auth

router = APIRouter(prefix="/auth", tags=["auth"])

# Bounds keep a hostile body from feeding megabytes into PBKDF2 or the regex.
_MAX_EMAIL, _MAX_PASSWORD = 254, 1024


class LoginRequest(BaseModel):
    email: str
    password: str


def _client_key(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def login(payload: LoginRequest, request: Request) -> dict:
    email = payload.email.strip().lower()
    if not email or not payload.password:
        raise HTTPException(status_code=422, detail="Email and password are required")
    if len(email) > _MAX_EMAIL or len(payload.password) > _MAX_PASSWORD:
        raise HTTPException(status_code=422, detail="Email or password is too long")
    # One counter per (email, client): a guesser is slowed down, but a stranger
    # can't lock a coordinator out from a different machine.
    key = f"{email}|{_client_key(request)}"
    wait = auth_service.login_throttle.retry_after(key)
    if wait:
        raise HTTPException(
            status_code=429,
            detail="Too many failed sign-in attempts. Try again shortly.",
            headers={"Retry-After": str(wait)},
        )
    db = get_db()
    user = auth_service.authenticate(db, email, payload.password)
    if not user:
        auth_service.login_throttle.failure(key)
        raise HTTPException(status_code=401, detail="Incorrect email or password")
    auth_service.login_throttle.success(key)
    token = auth_service.create_token(
        sub=user["id"],
        email=user["email"],
        name=user["name"],
        role=user.get("role", "coordinator"),
    )
    return {
        "token": token,
        "user": auth_service.public_user(user),
        "ttl_minutes": settings.auth_token_ttl_minutes,
    }


def me(user: dict = Depends(require_auth)) -> dict:
    return {"user": user}


def logout() -> dict:
    # Client-side session clear; there is no server state to revoke in the demo.
    return {"ok": True}


def demo_credential() -> dict:
    """Expose the synthetic demo accounts so the sign-in screen can offer one-click sign-in.

    Only in demo mode. With ``DEMO_MODE=false`` this is a 404, exactly as if
    the route did not exist, so a deployment never hands out passwords and the
    login page simply hides its demo buttons (it already ignores a failed call).
    """
    if not settings.demo_mode:
        raise HTTPException(status_code=404, detail="Not Found")
    return {
        "coordinator": {
            "email": settings.demo_coordinator_email.strip().lower(),
            "password": settings.demo_coordinator_password,
            "name": settings.demo_coordinator_name,
            "role": "coordinator",
        },
        "clinician": {
            "email": settings.demo_clinician_email.strip().lower(),
            "password": settings.demo_clinician_password,
            "name": settings.demo_clinician_name,
            "role": "clinician",
        },
    }


router.add_api_route("/login", login, methods=["POST"])
router.add_api_route("/me", me, methods=["GET"])
router.add_api_route("/logout", logout, methods=["POST"])
router.add_api_route("/demo", demo_credential, methods=["GET"], include_in_schema=False)
