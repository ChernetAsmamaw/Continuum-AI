import json
import logging
import re

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from .. import repository as repo
from ..config import settings
from ..db import get_db
from ..deps import require_auth
from ..security import verify_voice_request as _verify_shared_secret
from ..workflow import (
    handle_post_call,
    sync_elevenlabs_call,
    voice_context,
    voice_escalate,
    voice_identify,
    voice_report,
    voice_request_appointment,
    voice_request_callback,
    voice_request_follow_up,
)

router = APIRouter(prefix="/voice", tags=["voice"])
log = logging.getLogger("continuum.voice")

# ElevenLabs conversation ids are opaque [A-Za-z0-9_-] tokens. Anything else
# (``?``, ``#``, ``/`` decoded from %-escapes) must never reach the upstream URL.
_CONVERSATION_ID = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


def verify_voice_request(request: Request, raw_body: bytes, *, signed: bool = False) -> None:
    """Authenticate a voice webhook, failing closed outside development.

    - Without ``CONTINUUM_VOICE_SECRET`` the voice API (patient lookup by name,
      patient context, report/escalate) is open to anyone who can reach it.
      That is allowed only in development; elsewhere it is a 503.
    - ``signed`` endpoints (the post-call webhook, which ElevenLabs signs)
      require the ``ElevenLabs-Signature`` header whenever
      ``ELEVENLABS_WEBHOOK_SECRET`` is set: a missing header is a 401, not a
      silent skip of the check.
    """
    if not settings.continuum_voice_secret and not settings.is_development:
        raise HTTPException(status_code=503, detail="Voice API is disabled: CONTINUUM_VOICE_SECRET is not set")
    if signed and settings.elevenlabs_webhook_secret and not request.headers.get("elevenlabs-signature"):
        raise HTTPException(status_code=401, detail="Missing ElevenLabs signature")
    _verify_shared_secret(request, raw_body)


@router.post("/identify")
async def identify(request: Request) -> dict:
    raw = await request.body()
    verify_voice_request(request, raw)
    try:
        payload = json.loads(raw or b"{}")
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail="Invalid JSON") from exc
    if not payload.get("given_name"):
        raise HTTPException(status_code=422, detail="given_name is required")
    return voice_identify(
        given_name=payload["given_name"],
        family_name=payload.get("family_name") or "",
        phone=payload.get("phone"),
        birth_date=payload.get("birth_date"),
    )


@router.get("/context/{patient_id}")
def context(request: Request, patient_id: str) -> dict:
    verify_voice_request(request, b"")
    data = voice_context(patient_id)
    if not data:
        raise HTTPException(status_code=404, detail="Patient not found")
    return data


# The voice LLM sometimes repeats a tool call with identical arguments while
# waiting on a slow reply. Within one conversation, an identical repeat gets
# the first call's result instead of writing a second report or request.
_RECENT_CALLS: dict[str, tuple[float, dict]] = {}
_RECENT_LOCK = __import__("threading").Lock()
_REPEAT_WINDOW_SECS = 180


def _repeat_key(patient_id: str, tool: str, payload: dict) -> str | None:
    conversation = payload.get("conversation_id") or payload.get("conversationId")
    if not conversation:
        return None
    args = {k: v for k, v in payload.items() if k not in {"call_session_id", "conversation_id", "conversationId"}}
    return f"{conversation}|{patient_id}|{tool}|{json.dumps(args, sort_keys=True, default=str)}"


def _call_event(patient_id: str, payload: dict, tool: str, summary: str, run) -> dict:
    """Run a voice tool and show it (with what it changed) on the patient's live call."""
    import time

    from .. import calls
    from ..db import get_db

    key = _repeat_key(patient_id, tool, payload)
    now = time.monotonic()
    if key:
        with _RECENT_LOCK:
            for k in [k for k, (at, _) in _RECENT_CALLS.items() if now - at > _REPEAT_WINDOW_SECS]:
                _RECENT_CALLS.pop(k, None)
            hit = _RECENT_CALLS.get(key)
        if hit:
            return {**hit[1], "repeat": True}

    db = get_db()
    before = calls.snapshot(db, patient_id)
    result = run()
    if key and isinstance(result, dict):
        with _RECENT_LOCK:
            _RECENT_CALLS[key] = (now, result)
    try:
        calls.tool_event(db, patient_id=patient_id, tool=tool, summary=summary, before=before,
                         call_session_id=payload.get("call_session_id"),
                         detail={k: v for k, v in payload.items() if k not in {"patient_id", "call_session_id"}})
    except Exception:  # never fail the tool call because the console view failed
        pass
    return result


@router.post("/report")
async def report(request: Request, patient_id: str | None = Query(default=None)) -> dict:
    raw = await request.body()
    verify_voice_request(request, raw)
    try:
        payload = json.loads(raw or b"{}")
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail="Invalid JSON") from exc
    resolved = payload.get("patient_id") or patient_id
    if not resolved:
        raise HTTPException(status_code=422, detail="patient_id is required")
    if not payload.get("report"):
        raise HTTPException(status_code=422, detail="report is required")
    return _call_event(resolved, payload, "report", "Recorded patient report (unverified until the clinic confirms)", lambda: voice_report(
        patient_id=resolved,
        report=payload["report"],
        obligation_id=payload.get("obligation_id"),
        reported_status=payload.get("reported_status"),
        reported_date=payload.get("reported_date"),
        conversation_id=payload.get("conversation_id") or payload.get("conversationId"),
        direction=payload.get("direction", "inbound"),
    ))


@router.post("/escalate")
async def escalate(request: Request, patient_id: str | None = Query(default=None)) -> dict:
    raw = await request.body()
    verify_voice_request(request, raw)
    try:
        payload = json.loads(raw or b"{}")
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail="Invalid JSON") from exc
    resolved = payload.get("patient_id") or patient_id
    if not resolved:
        raise HTTPException(status_code=422, detail="patient_id is required")
    if not payload.get("reason"):
        raise HTTPException(status_code=422, detail="reason is required")
    return _call_event(resolved, payload, "escalate", "Escalated to a clinician (no advice given)", lambda: voice_escalate(
        patient_id=resolved,
        reason=payload["reason"],
        urgency=payload.get("urgency", "routine"),
        conversation_id=payload.get("conversation_id") or payload.get("conversationId"),
    ))


@router.post("/appointment")
async def appointment(request: Request, patient_id: str | None = Query(default=None)) -> dict:
    raw = await request.body()
    verify_voice_request(request, raw)
    try:
        payload = json.loads(raw or b"{}")
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail="Invalid JSON") from exc
    resolved = payload.get("patient_id") or patient_id
    if not resolved:
        raise HTTPException(status_code=422, detail="patient_id is required")
    if not payload.get("requested_time"):
        raise HTTPException(status_code=422, detail="requested_time is required")
    return _call_event(resolved, payload, "appointment", "Booking requested; waiting on the care team to confirm", lambda: voice_request_appointment(
        patient_id=resolved,
        requested_time=payload["requested_time"],
        obligation_id=payload.get("obligation_id"),
        notes=payload.get("notes") or "",
        conversation_id=payload.get("conversation_id") or payload.get("conversationId"),
    ))


@router.post("/follow-up")
async def follow_up(request: Request, patient_id: str | None = Query(default=None)) -> dict:
    raw = await request.body()
    verify_voice_request(request, raw)
    try:
        payload = json.loads(raw or b"{}")
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail="Invalid JSON") from exc
    resolved = payload.get("patient_id") or patient_id
    if not resolved:
        raise HTTPException(status_code=422, detail="patient_id is required")
    if not payload.get("item"):
        raise HTTPException(status_code=422, detail="item is required")
    return _call_event(resolved, payload, "follow_up", "Follow-up task queued for the care team", lambda: voice_request_follow_up(
        patient_id=resolved,
        item=payload["item"],
        reason=payload.get("reason") or "",
        conversation_id=payload.get("conversation_id") or payload.get("conversationId"),
    ))


@router.post("/checkin")
async def checkin(request: Request, patient_id: str | None = Query(default=None)) -> dict:
    raw = await request.body()
    verify_voice_request(request, raw)
    try:
        payload = json.loads(raw or b"{}")
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail="Invalid JSON") from exc
    resolved = payload.get("patient_id") or patient_id
    if not resolved:
        raise HTTPException(status_code=422, detail="patient_id is required")
    if not payload.get("when"):
        raise HTTPException(status_code=422, detail="when is required")
    return _call_event(resolved, payload, "callback", "Follow-up call scheduled", lambda: voice_request_callback(
        patient_id=resolved,
        when=payload["when"],
        reason=payload.get("reason") or "",
        conversation_id=payload.get("conversation_id") or payload.get("conversationId"),
    ))


@router.post("/post-call")
async def post_call(request: Request, patient_id: str | None = Query(default=None)) -> dict:
    raw = await request.body()
    verify_voice_request(request, raw, signed=True)
    try:
        payload = json.loads(raw or b"{}")
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail="Invalid JSON") from exc
    log.info("Post-call webhook received: type=%s", payload.get("type"))
    result = handle_post_call(payload, patient_id=patient_id or payload.get("patient_id"))
    if not result.get("ok"):
        # ElevenLabs retries non-2xx responses; return 200 with the diagnostic so
        # the demo does not spin on a misconfigured patient id.
        return result
    return result


@router.get("/calls/{patient_id}")
def get_calls(request: Request, patient_id: str) -> dict:
    verify_voice_request(request, b"")
    db = get_db()
    return {"calls": repo.list_voice_calls(db, patient_id)}


@router.post("/calls/{conversation_id}/sync")
def sync_call(conversation_id: str, user: dict = Depends(require_auth)) -> dict:
    """Fetch a call from ElevenLabs (signed-in staff only: the response is a transcript)."""
    if not _CONVERSATION_ID.match(conversation_id):
        raise HTTPException(status_code=422, detail="Invalid conversation id")
    return sync_elevenlabs_call(conversation_id)
