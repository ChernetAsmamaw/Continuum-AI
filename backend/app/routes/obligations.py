from fastapi import APIRouter, Depends, HTTPException, Query

from .. import repository as repo
from ..db import demo_today, get_db
from ..deps import actor_label, require_auth
from ..schemas import (
    MED_REC_CATEGORY,
    REFERRAL_CATEGORIES,
    SENSITIVE_STATUSES,
    EvidenceCreateRequest,
    ObligationCreateRequest,
    ObligationStatus,
    ObligationStatusRequest,
)
from ..util import add_days, parse_date
from ..workflow import confirm_appointment

router = APIRouter(prefix="/obligations", tags=["obligations"])


@router.get("")
def list_obligations(
    patient_id: str | None = None,
    status: str | None = Query(default=None),
    open_only: bool = False,
) -> dict:
    db = get_db()
    items = repo.list_obligations(
        db, patient_id, include_closed=not open_only, today=demo_today()
    )
    if status:
        items = [o for o in items if o["status"] == status or o.get("raw_status") == status]
    return {"obligations": items, "count": len(items)}


@router.post("")
def create_obligation(payload: ObligationCreateRequest, user: dict = Depends(require_auth)) -> dict:
    actor = actor_label(user)
    db = get_db()
    if not repo.get_patient(db, payload.patient_id):
        raise HTTPException(status_code=404, detail="Patient not found")
    episode = repo.get_active_episode(db, payload.patient_id) or {}
    due = payload.due_date or add_days(demo_today().isoformat(), 7)
    if not parse_date(due):
        raise HTTPException(status_code=422, detail="due_date must be YYYY-MM-DD")
    obligation = repo.create_obligation(
        db,
        doc={
            "patient_id": payload.patient_id,
            "episode_id": episode.get("id"),
            "description": payload.description,
            "category": "coordinator_entry",
            "owner": payload.owner,
            "due_date": due,
            "source": {"label": payload.source, "quote": None},
            "keywords": [w for w in payload.description.lower().split() if len(w) > 4],
            "verification_required": True,
        },
        actor=actor,
        reason="Created by coordinator",
    )
    return {"obligation": repo.present_obligation(obligation, demo_today())}


@router.get("/{obligation_id}")
def get_obligation(obligation_id: str) -> dict:
    db = get_db()
    obligation = repo.get_obligation(db, obligation_id)
    if not obligation:
        raise HTTPException(status_code=404, detail="Obligation not found")
    return {
        "obligation": repo.present_obligation(obligation, demo_today()),
        "evidence": repo.get_evidence(db, obligation_id),
    }


@router.get("/{obligation_id}/evidence")
def get_evidence(obligation_id: str) -> dict:
    db = get_db()
    return {"evidence": repo.get_evidence(db, obligation_id)}


@router.post("/{obligation_id}/evidence")
def add_evidence(obligation_id: str, payload: EvidenceCreateRequest, user: dict = Depends(require_auth)) -> dict:
    actor = actor_label(user)
    db = get_db()
    obligation = repo.get_obligation(db, obligation_id)
    if not obligation:
        raise HTTPException(status_code=404, detail="Obligation not found")
    evidence = repo.add_evidence(
        db,
        patient_id=obligation["patient_id"],
        obligation_id=obligation_id,
        kind=payload.kind.value,
        summary=payload.summary,
        source_ref=payload.source_ref,
        actor=actor,
    )
    repo.audit(
        db,
        patient_id=obligation["patient_id"],
        actor=actor,
        action="Added evidence",
        detail=payload.summary,
        after={"evidence_id": evidence["id"]},
    )
    return {"evidence": evidence}


@router.post("/{obligation_id}/status")
def change_status(obligation_id: str, payload: ObligationStatusRequest, user: dict = Depends(require_auth)) -> dict:
    actor = actor_label(user)
    db = get_db()
    obligation = repo.get_obligation(db, obligation_id)
    if not obligation:
        raise HTTPException(status_code=404, detail="Obligation not found")

    status = payload.status
    if status in SENSITIVE_STATUSES and not (payload.evidence_summary or payload.override_reason):
        raise HTTPException(
            status_code=422,
            detail=(
                "Verified completion, cancellation and escalation require evidence "
                "or an explicit coordinator override reason."
            ),
        )
    category = obligation.get("category")
    if status == ObligationStatus.verified and category == MED_REC_CATEGORY:
        if user.get("role") != "clinician":
            raise HTTPException(
                status_code=403,
                detail="Medication reconciliation is closed by a clinician's attestation.",
            )
        if not payload.evidence_summary:
            raise HTTPException(
                status_code=422,
                detail="Attest what you reconciled (for example: discharge list compared with home list, 2 changes confirmed with the patient).",
            )
    if (
        status == ObligationStatus.verified
        and category in REFERRAL_CATEGORIES
        and not (payload.consult_note_ref or obligation.get("consult_note_ref") or payload.override_reason)
    ):
        raise HTTPException(
            status_code=422,
            detail=(
                "A referral closes when the consult note is back. Add the consult note reference, "
                "or an override reason if the note will not come."
            ),
        )
    if payload.consult_note_ref:
        db.obligations.update_one({"id": obligation_id}, {"$set": {"consult_note_ref": payload.consult_note_ref}})
    if status == ObligationStatus.overdue:
        raise HTTPException(
            status_code=422,
            detail="Overdue is derived from the due date; update the due date or status instead.",
        )

    evidence = None
    if payload.evidence_summary or payload.override_reason:
        kind = (
            "clinic_confirmation"
            if payload.evidence_summary
            else "coordinator_override"
        )
        evidence = repo.add_evidence(
            db,
            patient_id=obligation["patient_id"],
            obligation_id=obligation_id,
            kind=kind,
            summary=payload.evidence_summary
            or f"Coordinator override: {payload.override_reason}",
            source_ref=payload.source_ref,
            actor=actor,
            confidence=0.9,
        )

    updated = repo.set_obligation_status(
        db,
        obligation,
        status.value,
        actor,
        payload.evidence_summary
        or payload.override_reason
        or f"Status changed to {status.value}",
        thread_id=obligation["patient_id"],
    )

    # A direct, evidence-backed coordinator update supersedes a queued
    # approval request for the same obligation.
    for approval in repo.list_approvals(db, obligation["patient_id"], status="pending"):
        if approval.get("obligation_id") == obligation_id and approval.get("kind") in {
            "verify_completion",
            "cancel",
            "resolve_conflict",
        }:
            repo.mark_approval_resolved(
                db,
                approval["id"],
                True,
                actor,
                "Superseded by a direct coordinator status update with evidence.",
            )

    return {
        "obligation": repo.present_obligation(updated, demo_today()),
        "evidence": evidence,
    }


@router.post("/{obligation_id}/confirm")
def confirm(obligation_id: str, confirmed_date: str, source_ref: str | None = None, user: dict = Depends(require_auth)) -> dict:
    actor = actor_label(user)
    db = get_db()
    obligation = repo.get_obligation(db, obligation_id)
    if not obligation:
        raise HTTPException(status_code=404, detail="Obligation not found")
    if not parse_date(confirmed_date):
        raise HTTPException(status_code=422, detail="confirmed_date must be YYYY-MM-DD")
    result = confirm_appointment(
        patient_id=obligation["patient_id"],
        obligation_hint=obligation["description"],
        confirmed_date=confirmed_date,
        actor=actor,
        source_ref=source_ref,
    )
    if not result.get("ok"):
        raise HTTPException(status_code=404, detail=result.get("detail", "Could not confirm"))
    return result
