from fastapi import APIRouter, Depends, HTTPException, Query

from .. import repository as repo
from ..agent.graph import thread_snapshot
from ..agent.memory import semantic_recall
from ..db import demo_today, get_db
from ..deps import actor_label, require_auth, require_role
from ..patient_record import DuplicatePatientError, find_duplicates, update_patient
from .. import tcm
from .. import episodes
from ..schemas import (
    CloseEpisodeRequest,
    ContactAttemptRequest,
    ReadmissionRequest,
    DocumentIngestRequest,
    PatientIntakeRequest,
    PatientUpdateRequest,
    VisitWindowRequest,
)
from ..util import parse_date
from ..workflow import ingest_document, intake_patient

router = APIRouter(prefix="/patients", tags=["patients"])


@router.post("", dependencies=[Depends(require_auth)])
def create_patient(payload: PatientIntakeRequest, user: dict = Depends(require_auth)) -> dict:
    """Patient intake: create the patient, care episode and follow-up obligations."""
    try:
        return intake_patient(payload, actor=actor_label(user))
    except DuplicatePatientError as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "message": "This looks like a patient who is already registered.",
                "candidates": exc.candidates,
            },
        ) from exc


@router.patch("/{patient_id}")
def edit_patient(patient_id: str, payload: PatientUpdateRequest, user: dict = Depends(require_auth)) -> dict:
    db = get_db()
    changes = payload.model_dump(exclude_unset=True)
    if "birth_date" in changes and changes["birth_date"] and not parse_date(changes["birth_date"]):
        raise HTTPException(status_code=422, detail="birth_date must be YYYY-MM-DD")
    if changes.get("name") or changes.get("birth_date") or changes.get("phone") or changes.get("mrn"):
        current = repo.get_patient(db, patient_id) or {}
        clashes = find_duplicates(
            db,
            name=changes.get("name") or current.get("name", ""),
            birth_date=changes.get("birth_date") or current.get("birth_date"),
            phone=changes.get("phone") or current.get("phone"),
            mrn=changes.get("mrn"),
            exclude_id=patient_id,
        )
        if clashes:
            raise HTTPException(
                status_code=409,
                detail={"message": "These details match another patient. Review identity before saving.", "candidates": clashes},
            )
    patient = update_patient(db, patient_id, changes, actor=actor_label(user), today=demo_today())
    if patient is None:
        raise HTTPException(status_code=404, detail="Patient not found")
    return {"patient": patient}


@router.delete("/{patient_id}", dependencies=[Depends(require_role("coordinator"))])
def delete_patient(patient_id: str, user: dict = Depends(require_role("coordinator"))) -> dict:
    """Delete a patient and all derived records (data governance)."""
    db = get_db()
    if not repo.delete_patient(db, patient_id):
        raise HTTPException(status_code=404, detail="Patient not found")
    return {"ok": True, "deleted": patient_id}


@router.get("")
def list_patients() -> dict:
    db = get_db()
    return {"patients": repo.list_patients(db, demo_today())}


@router.get("/{patient_id}")
def get_patient(patient_id: str) -> dict:
    db = get_db()
    patient = repo.get_patient(db, patient_id)
    if not patient:
        raise HTTPException(status_code=404, detail="Patient not found")
    obligations = repo.list_obligations(db, patient_id, today=demo_today())
    open_items = [o for o in obligations if o["is_open"]]
    return {
        "patient": patient,
        "episode": repo.get_active_episode(db, patient_id),
        "obligations": obligations,
        "counts": {
            "open": len(open_items),
            "overdue": sum(1 for o in open_items if o["status"] == "overdue"),
            "conflicts": sum(1 for o in open_items if o["status"] == "conflict"),
            "awaiting_verification": sum(
                1 for o in open_items if o["status"] == "patient_reported"
            ),
            "escalated": sum(1 for o in open_items if o["status"] == "escalated"),
        },
        "pending_approvals": repo.list_approvals(db, patient_id, status="pending"),
        "thread": thread_snapshot(patient_id),
    }


@router.get("/{patient_id}/journey")
def get_journey(patient_id: str) -> dict:
    db = get_db()
    today = demo_today()
    data = repo.journey(db, patient_id, today=today)
    if not data:
        raise HTTPException(status_code=404, detail="Patient not found")
    data["tcm"] = tcm.clock(db, data["patient"], today, obligations=data["obligations"])
    return data


@router.get("/{patient_id}/tcm")
def get_tcm(patient_id: str) -> dict:
    db = get_db()
    patient = repo.get_patient(db, patient_id)
    if not patient:
        raise HTTPException(status_code=404, detail="Patient not found")
    return {"tcm": tcm.clock(db, patient, demo_today())}


@router.post("/{patient_id}/contacts")
def log_contact(patient_id: str, payload: ContactAttemptRequest, user: dict = Depends(require_auth)) -> dict:
    db = get_db()
    patient = repo.get_patient(db, patient_id)
    if not patient:
        raise HTTPException(status_code=404, detail="Patient not found")
    contact = tcm.record_contact(
        db,
        patient_id=patient_id,
        outcome=payload.outcome,
        channel=payload.channel,
        note=payload.note,
        at=payload.at,
        actor=actor_label(user),
    )
    return {"contact": contact, "tcm": tcm.clock(db, patient, demo_today())}


@router.get("/{patient_id}/episodes")
def list_episodes(patient_id: str) -> dict:
    return {"episodes": episodes.list_episodes(get_db(), patient_id)}


@router.post("/{patient_id}/episodes")
def readmit(patient_id: str, payload: ReadmissionRequest, user: dict = Depends(require_auth)) -> dict:
    db = get_db()
    if not repo.get_patient(db, patient_id):
        raise HTTPException(status_code=404, detail="Patient not found")
    actor = actor_label(user)
    try:
        result = episodes.open_readmission(
            db,
            patient_id,
            admission_date=payload.admission_date,
            discharge_date=payload.discharge_date,
            reason=payload.reason,
            disposition=payload.disposition,
            actor=actor,
            today=demo_today(),
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if payload.discharge_summary and payload.discharge_summary.strip():
        db.care_episodes.update_one({"id": result["episode"]["id"]}, {"$set": {"discharge_summary": payload.discharge_summary}})
        result["ingestion"] = ingest_document(
            patient_id=patient_id,
            title="Discharge summary (readmission)",
            text=payload.discharge_summary.strip(),
            source_ref="Readmission",
            actor=actor,
        )
    return result


@router.post("/{patient_id}/episodes/close")
def close_episode(patient_id: str, payload: CloseEpisodeRequest, user: dict = Depends(require_auth)) -> dict:
    db = get_db()
    try:
        return episodes.close_episode(
            db, patient_id, reason=payload.reason, note=payload.note, close_open=payload.close_open, actor=actor_label(user)
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except episodes.OpenLoopsError as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "message": f"{len(exc.open_items)} follow-up(s) are still open. Close them first, or confirm they end with the episode.",
                "candidates": [{"id": o["id"], "description": o["description"], "status": o["status"]} for o in exc.open_items],
            },
        ) from exc


@router.put("/{patient_id}/tcm/visit-window")
def set_visit_window(patient_id: str, payload: VisitWindowRequest, user: dict = Depends(require_role("clinician"))) -> dict:
    """Clinician-only: the visit window follows medical decision complexity."""
    db = get_db()
    patient = tcm.set_visit_window(db, patient_id, payload.days, actor=actor_label(user))
    if patient is None:
        raise HTTPException(status_code=404, detail="Patient not found")
    return {"patient": patient, "tcm": tcm.clock(db, patient, demo_today())}


@router.get("/{patient_id}/obligations")
def get_obligations(patient_id: str, open_only: bool = False) -> dict:
    db = get_db()
    return {
        "obligations": repo.list_obligations(
            db, patient_id, include_closed=not open_only, today=demo_today()
        )
    }


@router.get("/{patient_id}/memories")
def get_memories(patient_id: str, q: str | None = Query(default=None), limit: int = 40) -> dict:
    db = get_db()
    if q:
        return {"memories": semantic_recall(db, patient_id, q, k=limit)}
    return {"memories": repo.list_memories(db, patient_id, limit=limit)}


@router.post("/{patient_id}/documents")
def upload_document(patient_id: str, payload: DocumentIngestRequest, user: dict = Depends(require_auth)) -> dict:
    db = get_db()
    if not repo.get_patient(db, patient_id):
        raise HTTPException(status_code=404, detail="Patient not found")
    return ingest_document(
        patient_id=patient_id,
        title=payload.title,
        text=payload.text,
        source_ref=payload.source_ref,
        actor=actor_label(user),
    )


@router.get("/{patient_id}/thread")
def get_thread(patient_id: str) -> dict:
    db = get_db()
    if not repo.get_patient(db, patient_id):
        raise HTTPException(status_code=404, detail="Patient not found")
    return thread_snapshot(patient_id)
