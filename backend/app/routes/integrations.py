"""Inbound clinic / lab records (FHIR R4)."""

from __future__ import annotations

import hmac

from fastapi import APIRouter, HTTPException, Request

from ..config import settings
from ..deps import require_auth
from ..integrations import ingest_fhir

router = APIRouter(prefix="/integrations", tags=["integrations"])


def _authorize(request: Request) -> str:
    """Accept the integration shared secret, or a signed-in coordinator."""
    token = request.headers.get("x-continuum-integration-token", "")
    if settings.integration_secret and token:
        if hmac.compare_digest(token, settings.integration_secret):
            return "Clinic integration"
        raise HTTPException(status_code=401, detail="Invalid integration token")
    user = require_auth(request)
    if user.get("role") != "coordinator":
        raise HTTPException(status_code=403, detail="Requires the coordinator role or an integration token")
    return f"{user.get('name')} (manual clinic record)"


@router.post("/fhir")
async def fhir(request: Request) -> dict:
    actor = _authorize(request)
    try:
        payload = await request.json()
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Body must be a FHIR resource or Bundle (JSON)") from exc
    if not isinstance(payload, dict) or "resourceType" not in payload:
        raise HTTPException(status_code=422, detail="Missing resourceType")
    return ingest_fhir(payload, actor=actor)
