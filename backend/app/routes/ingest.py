"""EHR ingestion: HL7 discharge trigger, FHIR pull, result poll."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from ..config import settings
from ..db import get_db
from ..ingest.fhir_client import FHIRError, client_from_settings
from ..ingest.poller import poll_ehr
from ..ingest.pull import PullError
from ..ingest.service import IngestUnavailable, handle_hl7, pull_and_ingest
from .integrations import _authorize

router = APIRouter(prefix="/ingest", tags=["ingest"])


class PullRequest(BaseModel):
    patient_fhir_id: str | None = None
    mrn: str | None = None
    encounter_fhir_id: str | None = None


@router.post("/hl7")
async def hl7(request: Request) -> dict:
    """Receive one HL7v2 message (ER7 text body, or JSON {"message": "..."})."""
    actor = _authorize(request)
    body = (await request.body()).decode("utf-8", errors="replace")
    if request.headers.get("content-type", "").startswith("application/json"):
        import json

        try:
            body = json.loads(body).get("message", "")
        except Exception as exc:
            raise HTTPException(status_code=400, detail="JSON body must be {\"message\": \"MSH|...\"}") from exc
    if not body.strip():
        raise HTTPException(status_code=422, detail="Empty HL7 message")
    return handle_hl7(body, actor=f"{actor} (HL7)")


@router.post("/pull")
def pull(payload: PullRequest, request: Request) -> dict:
    """Pull a discharge from the EHR on demand (backfill, or no ADT feed)."""
    actor = _authorize(request)
    if not (payload.patient_fhir_id or payload.mrn):
        raise HTTPException(status_code=422, detail="Give patient_fhir_id or mrn")
    try:
        return pull_and_ingest(
            patient_fhir_id=payload.patient_fhir_id,
            patient_identifiers=[payload.mrn] if payload.mrn else [],
            encounter_fhir_id=payload.encounter_fhir_id,
            actor=f"{actor} (EHR pull)",
        )
    except IngestUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except PullError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except FHIRError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.post("/poll")
def poll(request: Request) -> dict:
    """Check the EHR now for results behind open follow-ups."""
    _authorize(request)
    client = client_from_settings()
    if client is None:
        raise HTTPException(status_code=503, detail="EHR_FHIR_BASE_URL is not configured")
    try:
        return poll_ehr(get_db(), client)
    finally:
        client.close()


@router.get("/status")
def status(request: Request) -> dict:
    _authorize(request)
    db = get_db()
    return {
        "ehr_configured": bool(settings.ehr_fhir_base_url),
        "fhir_base": settings.ehr_fhir_base_url,
        "auth_mode": settings.ehr_auth_mode,
        "poll_enabled": settings.ehr_poll_enabled,
        "mllp_port": settings.hl7_mllp_port or None,
        "recent_runs": list(db.ingest_runs.find({}, {"_id": 0}).sort("at", -1).limit(10)),
        "recent_messages": list(db.hl7_messages.find({}, {"_id": 0}).sort("received_at", -1).limit(10)),
        "last_poll": db.ehr_poll_runs.find_one({}, {"_id": 0}, sort=[("at", -1)]),
    }


@router.get("/jobs")
def jobs(request: Request) -> dict:
    """Ingestion jobs with per-step checkpoint state (newest first)."""
    _authorize(request)
    from ..ingest.jobs import list_jobs

    return {"jobs": list_jobs(get_db())}


@router.get("/jobs/{job_id}")
def job(job_id: str, request: Request) -> dict:
    _authorize(request)
    from ..ingest.jobs import get_job

    found = get_job(get_db(), job_id)
    if not found:
        raise HTTPException(status_code=404, detail="Job not found")
    return {"job": found}


@router.post("/jobs/{job_id}/resume")
def resume(job_id: str, request: Request, force: bool = False) -> dict:
    """Continue a job from its first unfinished step (force=true ignores another worker's lease)."""
    _authorize(request)
    from ..ingest.jobs import run_job

    return run_job(get_db(), job_id, force=force)
