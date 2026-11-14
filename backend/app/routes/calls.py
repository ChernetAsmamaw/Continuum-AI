"""Call Room API: start, watch and end voice calls."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from .. import calls
from ..db import get_db
from ..deps import require_auth

router = APIRouter(prefix="/calls", tags=["calls"], dependencies=[Depends(require_auth)])


class StartCall(BaseModel):
    patient_id: str
    mode: str = "simulated"
    phone: str | None = None


class Connected(BaseModel):
    conversation_id: str | None = None


class Line(BaseModel):
    role: str
    text: str


@router.get("/modes")
def modes() -> dict:
    return calls.available_modes()


@router.get("/brief/{patient_id}")
def brief(patient_id: str) -> dict:
    return calls.build_brief(get_db(), patient_id)


@router.post("")
def start(payload: StartCall, user: dict = Depends(require_auth)) -> dict:
    try:
        return {"session": calls.start(get_db(), patient_id=payload.patient_id, mode=payload.mode, phone=payload.phone,
                                       actor=user.get("name", "Coordinator"))}
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/patient/{patient_id}")
def for_patient(patient_id: str) -> dict:
    db = get_db()
    return {"active": calls.active_session(db, patient_id), "recent": calls.list_sessions(db, patient_id)}


@router.get("/{session_id}")
def get(session_id: str) -> dict:
    session = calls.get_session(get_db(), session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Call not found")
    return {"session": session}


@router.post("/{session_id}/connected")
def mark_connected(session_id: str, payload: Connected) -> dict:
    session = calls.connected(get_db(), session_id, conversation_id=payload.conversation_id, detail="Browser call connected")
    if not session:
        raise HTTPException(status_code=404, detail="Call not found")
    return {"session": session}


@router.post("/{session_id}/transcript")
def add_line(session_id: str, payload: Line) -> dict:
    """Browser calls stream their transcript here so it's saved and broadcast."""
    if not calls.get_session(get_db(), session_id):
        raise HTTPException(status_code=404, detail="Call not found")
    return {"event": calls.transcript(get_db(), session_id, role=payload.role, text=payload.text)}


@router.post("/{session_id}/end")
def end(session_id: str) -> dict:
    session = calls.end(get_db(), session_id, reason="Ended from the Call Room")
    if not session:
        raise HTTPException(status_code=404, detail="Call not found")
    return {"session": session}
