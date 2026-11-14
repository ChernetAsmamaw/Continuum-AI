"""Clinic and lab evidence intake (FHIR R4 resources).

This is how loops close on proof instead of on someone typing it in. A clinic
scheduling system, lab system or HIE pushes standard FHIR resources; Continuum
matches each one to an open obligation and applies the product rules:

| Resource                              | Effect                                                         |
| ------------------------------------- | -------------------------------------------------------------- |
| Appointment ``booked``/``pending``    | Clinic-confirmed date → obligation ``scheduled`` (auto)         |
|   …whose date contradicts the record  | Conflict preserved + ``resolve_conflict`` approval              |
| Appointment ``fulfilled``             | Clinic evidence + ``verify_completion`` approval                |
| Encounter ``finished``                | Clinic evidence + ``verify_completion`` approval                |
| DiagnosticReport/Observation ``final``| Lab evidence + ``verify_completion`` approval                   |
| Appointment ``noshow``                | Obligation ``attempted``; the scheduler will chase it           |
| Appointment ``cancelled``             | Back to ``pending`` with a rebook next action                   |
| DocumentReference ``current``         | Consult note filed; a referral with a finished visit can close  |

A specialist referral closes on the visit *and* its consult note (closed-loop
referral): a finished visit alone waits for the note.

Closing still goes through a human approval (a sensitive status), but the
approval now carries clinic-grade evidence, so a coordinator can close it in
one click. Resources are idempotent on ``resourceType/id/versionId``.
"""

from __future__ import annotations

import logging
from typing import Any

from pymongo.database import Database

from . import repository as repo
from .agent.extraction import CATEGORY_RULES, any_term
from .db import demo_today, get_db
from .engine import rank_obligations
from .schemas import ApprovalKind, EvidenceKind, ObligationStatus
from .util import new_id, parse_date, truncate, utcnow

log = logging.getLogger("continuum.integrations")

SUPPORTED = {"Appointment", "Encounter", "DiagnosticReport", "Observation", "DocumentReference"}
REFERRAL_CATEGORIES = {"cardiology", "respiratory", "diabetes", "referral"}


# ── FHIR field helpers ───────────────────────────────────────────────────


def _texts(value: Any) -> list[str]:
    """Collect human-readable strings from CodeableConcept-ish structures."""
    out: list[str] = []
    if isinstance(value, str):
        out.append(value)
    elif isinstance(value, dict):
        for key in ("text", "display"):
            if isinstance(value.get(key), str):
                out.append(value[key])
        for coding in value.get("coding") or []:
            if isinstance(coding, dict) and coding.get("display"):
                out.append(coding["display"])
    elif isinstance(value, list):
        for item in value:
            out.extend(_texts(item))
    return out


def describe(resource: dict) -> str:
    fields = [
        "serviceType", "serviceCategory", "specialty", "appointmentType", "type", "code",
        "category", "reasonCode", "description", "comment", "conclusion",
    ]
    parts: list[str] = []
    for f in fields:
        parts.extend(_texts(resource.get(f)))
    for p in resource.get("participant") or []:
        parts.extend(_texts((p.get("actor") or {}).get("display")))
    for p in resource.get("performer") or []:
        parts.extend(_texts(p.get("display") if isinstance(p, dict) else p))
    return " · ".join(dict.fromkeys(p for p in parts if p))


def patient_ref(resource: dict) -> str | None:
    refs: list[str] = []
    for key in ("subject", "patient"):
        ref = (resource.get(key) or {}).get("reference")
        if ref:
            refs.append(ref)
    for p in resource.get("participant") or []:
        ref = (p.get("actor") or {}).get("reference")
        if ref:
            refs.append(ref)
    for ref in refs:
        if ref.startswith("Patient/"):
            return ref.split("/", 1)[1]
    return None


def resolve_patient(db: Database, resource: dict) -> dict | None:
    ref = patient_ref(resource)
    if not ref:
        return None
    return db.patients.find_one({"$or": [{"id": ref}, {"mrn": ref}, {"ehr.fhir_id": ref}]}, {"_id": 0})


def _normalize_ref(reference: str | None) -> str | None:
    if not reference:
        return None
    parts = [p for p in reference.split("/") if p]
    if "_history" in parts:
        parts = parts[: parts.index("_history")]
    return f"{parts[-2]}/{parts[-1]}" if len(parts) >= 2 else reference


def exact_references(resource: dict) -> list[str]:
    """This resource's own reference plus the orders it fulfils (basedOn)."""
    refs = [f"{resource.get('resourceType')}/{resource.get('id')}"]
    for based in resource.get("basedOn") or []:
        normalized = _normalize_ref(based.get("reference"))
        if normalized:
            refs.append(normalized)
    appointment = _normalize_ref(((resource.get("appointment") or [{}])[0] or {}).get("reference")) if isinstance(resource.get("appointment"), list) else None
    if appointment:
        refs.append(appointment)
    # DocumentReference: the order or encounter the note belongs to.
    for related in (resource.get("context") or {}).get("related") or []:
        normalized = _normalize_ref(related.get("reference"))
        if normalized:
            refs.append(normalized)
    for encounter in (resource.get("context") or {}).get("encounter") or []:
        normalized = _normalize_ref(encounter.get("reference"))
        if normalized:
            refs.append(normalized)
    return refs


def event_date(resource: dict) -> str | None:
    for key in ("start", "effectiveDateTime", "issued", "date"):
        if resource.get(key):
            d = parse_date(resource[key])
            if d:
                return d.isoformat()
    for key in ("period", "effectivePeriod"):
        start = (resource.get(key) or {}).get("start")
        if start and parse_date(start):
            return parse_date(start).isoformat()
    return None


def category_for(text: str) -> str | None:
    low = text.lower()
    for rule in CATEGORY_RULES:
        if any_term(rule["keywords"], low):
            return rule["category"]
    if "lab" in low.split() or "laboratory" in low:
        return "bloodwork"
    return None


def classify(resource: dict) -> str:
    rtype = resource.get("resourceType")
    status = (resource.get("status") or "").lower()
    if rtype == "Appointment":
        return {
            "proposed": "booked", "pending": "booked", "booked": "booked", "arrived": "booked",
            "checked-in": "booked", "fulfilled": "completed", "noshow": "noshow",
            "cancelled": "cancelled", "entered-in-error": "ignore",
        }.get(status, "ignore")
    if rtype == "Encounter":
        return "completed" if status in {"finished", "completed"} else "ignore"
    if rtype in {"DiagnosticReport", "Observation"}:
        return "completed" if status in {"final", "amended", "corrected"} else "ignore"
    if rtype == "DocumentReference":
        return "consult_note" if status in {"current", ""} else "ignore"
    return "ignore"


# ── Intake ───────────────────────────────────────────────────────────────


def _resources(payload: dict) -> list[dict]:
    if payload.get("resourceType") == "Bundle":
        return [e.get("resource") or {} for e in payload.get("entry") or []]
    return [payload]


def ingest_fhir(payload: dict, actor: str = "Clinic integration") -> dict:
    db = get_db()
    results = [_ingest_one(db, r, actor) for r in _resources(payload)]
    return {
        "ok": True,
        "received": len(results),
        "applied": sum(1 for r in results if r["outcome"] not in {"ignored", "duplicate", "unmatched"}),
        "results": results,
    }


def _ingest_one(db: Database, resource: dict, actor: str) -> dict:
    rtype = resource.get("resourceType")
    rid = resource.get("id") or new_id("fhir")
    version = (resource.get("meta") or {}).get("versionId") or "1"
    source_ref = f"{rtype}/{rid}/_history/{version}"
    base = {"resource": f"{rtype}/{rid}", "source_ref": source_ref}

    if rtype not in SUPPORTED:
        return {**base, "outcome": "ignored", "detail": f"Unsupported resource type {rtype}"}
    action = classify(resource)
    if action == "ignore":
        return {**base, "outcome": "ignored", "detail": f"Status {resource.get('status')!r} carries no follow-up meaning"}
    if db.evidence.find_one({"source_ref": source_ref}):
        return {**base, "outcome": "duplicate", "detail": "Already processed"}

    patient = resolve_patient(db, resource)
    if not patient:
        return {**base, "outcome": "unmatched", "detail": "No patient matches the subject reference"}

    text = describe(resource)
    when = event_date(resource)
    category = category_for(text)
    obligations = repo.list_obligations(db, patient["id"], include_closed=False, today=demo_today())
    # Exact match first: an EHR-sourced follow-up knows the order/appointment/
    # report ids behind it, so a result that references them needs no guessing.
    refs = set(exact_references(resource))
    exact = [o for o in obligations if refs & set(o.get("fhir_refs") or [])]
    if exact:
        ranked = [(exact[0], 1.0, ["exact EHR reference"])]
    else:
        ranked = rank_obligations(obligations, text, category, details=text, reported_date=when)
    if not ranked or ranked[0][1] < 0.3:
        repo.add_event(
            db,
            {
                "patient_id": patient["id"],
                "type": "system",
                "title": f"Unmatched clinic record ({rtype})",
                "detail": truncate(f"{text or 'No description'} on {when or 'unknown date'} did not match an open follow-up.", 400),
                "actor": actor,
                "source_ref": source_ref,
            },
        )
        repo.add_memory(
            db,
            patient_id=patient["id"],
            kind="uncertainty",
            summary=f"Clinic record did not match a follow-up: {truncate(text, 120)}",
            content=str(resource)[:1500],
            tags=["integration", "unmatched", rtype.lower()],
            importance=0.6,
        )
        return {**base, "outcome": "unmatched", "patient_id": patient["id"], "detail": "No open follow-up matched"}

    obligation, score, reasons = ranked[0]
    summary = f"{rtype} {resource.get('status')}: {text or obligation['description']}" + (f" on {when}" if when else "")
    evidence = repo.add_evidence(
        db,
        patient_id=patient["id"],
        obligation_id=obligation["id"],
        kind=EvidenceKind.clinic_confirmation.value,
        summary=summary,
        source_ref=source_ref,
        confidence=0.95,
        actor=actor,
    )
    outcome = _apply(db, patient, obligation, action, when, summary, source_ref, actor, evidence["id"])
    repo.add_event(
        db,
        {
            "patient_id": patient["id"],
            "type": "system",
            "title": f"Clinic record received: {obligation['description']}",
            "detail": truncate(f"{summary}. {outcome['detail']}", 500),
            "actor": actor,
            "source_ref": source_ref,
            "metadata": {"obligation_id": obligation["id"], "evidence_id": evidence["id"], "match": reasons},
        },
    )
    try:
        from .agent.memory import refresh_patient_summary

        refresh_patient_summary(db, patient["id"], demo_today())
    except Exception:  # pragma: no cover
        pass
    return {
        **base,
        "patient_id": patient["id"],
        "obligation_id": obligation["id"],
        "match_score": score,
        "match_reasons": reasons,
        "evidence_id": evidence["id"],
        **outcome,
    }


def _verification_approval(db, patient, obligation, summary, source_ref, evidence_id) -> dict:
    run_id = new_id("run")
    proposal = {
        "id": new_id("prop"),
        "kind": "verify_completion",
        "patient_id": patient["id"],
        "obligation_id": obligation["id"],
        "title": f"Verify completion: {obligation['description']}",
        "rationale": f"Clinic record received ({source_ref}). This is verification-grade evidence.",
        "evidence_summary": summary,
        "evidence_kind": EvidenceKind.clinic_confirmation.value,
        "confidence": 0.95,
        "changes": {"status": ObligationStatus.verified.value, "next_action": None},
        "requires_approval": True,
        "interrupt": False,
    }
    approval = repo.create_approval(
        db,
        {
            "patient_id": patient["id"],
            "run_id": run_id,
            "thread_id": patient["id"],
            "kind": ApprovalKind.verify_completion.value,
            "mode": "queued",
            "obligation_id": obligation["id"],
            "title": proposal["title"],
            "rationale": proposal["rationale"],
            "proposed_changes": proposal["changes"],
            "evidence_summary": summary,
            "confidence": 0.95,
            "proposal": proposal,
        },
    )
    # A patient-report approval may already be waiting: upgrade it with the
    # clinic evidence rather than creating a second card.
    db.approvals.update_one(
        {"id": approval["id"]},
        {
            "$set": {
                "evidence_summary": summary,
                "confidence": 0.95,
                "clinic_evidence": {"source_ref": source_ref, "evidence_id": evidence_id},
                "rationale": proposal["rationale"],
                "proposal.evidence_summary": summary,
                "proposal.confidence": 0.95,
                "proposal.rationale": proposal["rationale"],
            }
        },
    )
    return repo.get_approval(db, approval["id"]) or approval


def _apply(db, patient, obligation, action, when, summary, source_ref, actor, evidence_id) -> dict:
    if action == "booked":
        if not when:
            return {"outcome": "evidence_only", "detail": "Booking had no date; evidence attached."}
        reported = parse_date(obligation.get("reported_date"))
        confirmed = parse_date(obligation.get("confirmed_date"))
        if confirmed and confirmed.isoformat() != when:
            # The clinic moved it: record the new authoritative date, keep the old one visible.
            conflicts = list(obligation.get("conflicts") or [])
            conflicts.append(
                {
                    "field": "confirmed_date",
                    "recorded_value": confirmed.isoformat(),
                    "reported_value": when,
                    "reported_by": "clinic",
                    "detected_at": utcnow(),
                    "resolved": True,
                    "superseded": confirmed.isoformat(),
                    "resolution": "Clinic system rescheduled; newer clinic record wins.",
                }
            )
            repo.set_obligation_status(
                db, obligation, ObligationStatus.scheduled.value, actor,
                f"Clinic rescheduled from {confirmed.isoformat()} to {when} ({source_ref}).",
                due_date=when, next_action="Tell the patient about the new date", conflicts=conflicts,
                thread_id=patient["id"],
            )
            db.obligations.update_one({"id": obligation["id"]}, {"$set": {"confirmed_date": when, "confirmed_source": source_ref}})
            return {"outcome": "rescheduled", "detail": f"Clinic moved the date to {when}; patient should be told."}
        repo.set_obligation_status(
            db, obligation, ObligationStatus.scheduled.value, actor,
            f"Clinic confirmed booking for {when} ({source_ref}).",
            due_date=when, next_action=None, thread_id=patient["id"],
        )
        db.obligations.update_one({"id": obligation["id"]}, {"$set": {"confirmed_date": when, "confirmed_source": source_ref}})
        if reported and reported.isoformat() != when:
            return {"outcome": "scheduled", "detail": f"Booked for {when}; differs from what the patient said ({reported})."}
        return {"outcome": "scheduled", "detail": f"Booked for {when}."}

    if action == "consult_note":
        db.obligations.update_one({"id": obligation["id"]}, {"$set": {"consult_note_ref": source_ref}})
        if obligation.get("clinic_completed_on") or obligation.get("category") not in REFERRAL_CATEGORIES:
            approval = _verification_approval(db, patient, obligation, summary, source_ref, evidence_id)
            db.obligations.update_one({"id": obligation["id"]}, {"$set": {"next_action": "Consult note received — approve to close"}})
            return {"outcome": "verification_ready", "approval_id": approval["id"],
                    "detail": "Consult note filed with the visit on record; one-click approval queued to close the referral."}
        db.obligations.update_one({"id": obligation["id"]}, {"$set": {"next_action": "Consult note received; waiting for the visit record"}})
        return {"outcome": "consult_note_filed", "detail": "Consult note filed; the referral closes once the visit is on record."}

    if (
        action == "completed"
        and obligation.get("category") in REFERRAL_CATEGORIES
        and not obligation.get("consult_note_ref")
        and source_ref.split("/")[0] in {"Encounter", "Appointment"}
    ):
        db.obligations.update_one(
            {"id": obligation["id"]},
            {"$set": {"clinic_completed_on": when,
                      "next_action": f"Visit done{' on ' + when if when else ''}; waiting for the consult note to close the referral"}},
        )
        return {"outcome": "awaiting_consult_note",
                "detail": "Visit recorded. A referral closes when the consult note is back with the referring team."}

    if action == "completed":
        approval = _verification_approval(db, patient, obligation, summary, source_ref, evidence_id)
        db.obligations.update_one(
            {"id": obligation["id"]},
            {"$set": {"next_action": "Clinic record received — approve to close", "clinic_completed_on": when}},
        )
        return {
            "outcome": "verification_ready",
            "approval_id": approval["id"],
            "detail": "Clinic proof attached; one-click approval queued to close the loop.",
        }

    if action == "noshow":
        repo.set_obligation_status(
            db, obligation, ObligationStatus.attempted.value, actor,
            f"Clinic recorded a no-show ({source_ref}).",
            next_action="Patient missed the appointment — rebook and check barriers", thread_id=patient["id"],
        )
        return {"outcome": "noshow", "detail": "Marked attempted; follow-through will chase a rebooking."}

    if action == "cancelled":
        repo.set_obligation_status(
            db, obligation, ObligationStatus.pending.value, actor,
            f"Clinic cancelled the appointment ({source_ref}).",
            next_action="Appointment cancelled by clinic — rebook", thread_id=patient["id"],
        )
        db.obligations.update_one({"id": obligation["id"]}, {"$unset": {"confirmed_date": ""}})
        return {"outcome": "cancelled", "detail": "Back to pending with a rebook action."}

    return {"outcome": "evidence_only", "detail": "Evidence attached."}
