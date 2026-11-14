"""Optional shared-secret / signature checks for voice webhooks."""

from __future__ import annotations

import hashlib
import hmac
import logging
import time

from fastapi import HTTPException, Request

from .config import settings

log = logging.getLogger("continuum.security")


def _valid_elevenlabs_signature(request: Request, raw_body: bytes) -> bool | None:
    """True/False when a signature can be checked, None when none is present.

    ElevenLabs signs workspace webhooks with ``ElevenLabs-Signature:
    t=<unix>,v0=<hex hmac-sha256 of "<t>.<body>">``.
    """
    secret = settings.elevenlabs_webhook_secret
    header = request.headers.get("elevenlabs-signature")
    if not (secret and header):
        return None
    try:
        parts = dict(part.strip().split("=", 1) for part in header.split(","))
        timestamp = parts.get("t", "")
        signature = parts.get("v0") or parts.get("v1") or ""
        if abs(time.time() - int(timestamp)) > 30 * 60:
            return False
        expected = hmac.new(secret.encode(), f"{timestamp}.".encode() + raw_body, hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, signature)
    except Exception:
        return False


def verify_voice_request(request: Request, raw_body: bytes) -> None:
    """Authenticate a voice tool call or webhook.

    Accepted: a valid ElevenLabs HMAC signature (post-call webhooks, which do
    not carry custom headers), or the shared ``x-continuum-token`` (tool
    calls). Both checks are opt-in so a local demo works out of the box.
    """
    signed = _valid_elevenlabs_signature(request, raw_body)
    if signed is True:
        return
    if signed is False:
        log.warning("Rejected voice request with an invalid ElevenLabs signature on %s", request.url.path)
        raise HTTPException(status_code=401, detail="Invalid ElevenLabs signature")

    expected = settings.continuum_voice_secret
    if expected:
        supplied = request.headers.get("x-continuum-token") or request.headers.get(
            "authorization", ""
        ).removeprefix("Bearer ")
        if not hmac.compare_digest(supplied, expected):
            log.warning("Rejected voice request without a valid token on %s", request.url.path)
            raise HTTPException(status_code=401, detail="Invalid voice webhook token")
