"""Application services shared by API routes and the demo runner."""

from __future__ import annotations

import logging
from datetime import timedelta

from pymongo.database import Database

from . import engine, patient_record
from . import repository as repo
from .agent.graph import resume_agent, run_agent
from .agent.memory import retrieve_context
from .db import demo_today, get_db
from .schemas import (
    ApprovalKind,
    ApprovalStatus,
    EvidenceKind,
    MemoryKind,
    ObligationStatus,
    PatientIntakeRequest,
)
from .seed import (
    MICHAEL_ID,
    day10_transcript,
    day12_transcript,
    day3_transcript,
    discharge_text,
    reset_patient_scenario,
    transcript_text,
)
from .util import add_days, new_id, parse_date, truncate, utcnow

log = logging.getLogger("continuum.workflow")


# ── Documents & chat ─────────────────────────────────────────────────────


def ingest_document(
    *,
    patient_id: str,
    title: str,
    text: str,
    source_ref: str | None = None,
    actor: str = "Care coordinator",
) -> dict:
    return run_agent(
        patient_id=patient_id,
        input_type="document",
        text=text,
        actor=actor,
        title=title,
        source_ref=source_ref,
        metadata={"source": source_ref or title, "synthetic": True},
    )


def chat(*, patient_id: str, message: str, actor: str) -> dict:
    return run_agent(
        patient_id=patient_id,
        input_type="chat",
        text=message,
        actor=actor,
        title="Coordinator chat with Continuum",
    )


# ── Patient intake ────────────────────────────────────────────────────────


def _intake_category(text: str) -> str | None:
    from .ingest.builder import _category

    return _category(text)


def _intake_owner(text: str) -> str | None:
    from .ingest.builder import _owner_for

    return _owner_for(_intake_category(text))


def intake_patient(req: PatientIntakeRequest, actor: str, *, start_tcm: bool = True) -> dict:
    """Create a patient, their care episode, and follow-up obligations.

    Structured obligations are recorded directly. When a discharge summary
    text is provided, it is additionally pushed through the agent pipeline,
    which extracts further obligations (LLM, evidence-linked).
    """
    db = get_db()
    now = utcnow()
    today = demo_today()

    name = req.name.strip()
    if not req.confirm_new:
        duplicates = patient_record.find_duplicates(
            db, name=name, birth_date=req.birth_date, phone=req.phone, mrn=req.mrn
        )
        if duplicates:
            raise patient_record.DuplicatePatientError(duplicates)
    patient_id = new_id("pat")
    episode_id = new_id("ep")
    discharge = (parse_date(req.discharge_date) or today).isoformat()
    admission = (parse_date(req.admission_date) or parse_date(discharge)).isoformat()

    first, _, last = name.partition(" ")
    initials = "".join(ch for ch in (first[:1] + (last[:1] if last else "")) if ch.isalpha())
    patient = {
        "id": patient_id,
        "name": name,
        "initials": initials.upper() or name[:2].upper(),
        "age": patient_record.age_from_birth_date(req.birth_date, today) or req.age,
        "birth_date": (parse_date(req.birth_date).isoformat() if parse_date(req.birth_date) else None),
        "email": req.email or "",
        "mrn": req.mrn or new_id("mrn").upper(),
        "condition": req.condition or "Post-acute follow-up",
        "risk": req.risk,
        "phone": req.phone or "",
        "caregiver": patient_record.caregiver_label(
            {"name": req.caregiver, "relationship": req.caregiver_relationship}
        ) if req.caregiver else None,
        "caregiver_contact": {
            "name": req.caregiver,
            "phone": req.caregiver_phone,
            "relationship": req.caregiver_relationship,
            "may_discuss_care": req.caregiver_may_discuss_care,
        } if req.caregiver else None,
        "preferences": {
            "channel": req.channel,
            "best_time": req.best_time or "Any time",
            "language": req.language,
            "interpreter_needed": req.interpreter_needed,
            "timezone": req.timezone,
            "consent_to_automated_calls": req.consent_to_automated_calls,
            "consent_to_recording": req.consent_to_recording,
        },
        "synthetic": False,
        "created_at": now,
    }
    if req.pronouns:
        patient["pronouns"] = req.pronouns
    db.patients.insert_one(dict(patient))

    db.care_episodes.insert_one(
        {
            "id": episode_id,
            "patient_id": patient_id,
            "admission_date": admission,
            "discharge_date": discharge,
            "reason": req.reason or "Hospitalisation",
            "status": "active",
            "discharge_summary": req.discharge_summary or "",
            "created_at": now,
        }
    )

    repo.add_event(
        db,
        {
            "patient_id": patient_id,
            "episode_id": episode_id,
            "type": "intake",
            "title": f"Patient intake — {name}",
            "detail": (
                f"Discharge {discharge}"
                + (f"; reason: {req.reason}" if req.reason else "")
                + (f"; {len(req.obligations)} follow-up(s) recorded" if req.obligations else "")
            ),
            "actor": actor,
            "source_ref": "Patient intake",
        },
    )
    repo.audit(
        db,
        patient_id=patient_id,
        actor=actor,
        action="Created patient",
        detail=f"{name} (episode {episode_id}, discharge {discharge})",
        after={"patient_id": patient_id, "episode_id": episode_id},
    )
    repo.add_memory(
        db,
        patient_id=patient_id,
        kind=MemoryKind.patient_context,
        summary=f"Patient intake: {name}, {req.condition or 'post-acute follow-up'}, discharged {discharge}.",
        content=req.discharge_summary or req.reason or f"Discharged {discharge}.",
        tags=["intake", "patient"],
        importance=0.8,
    )

    created: list[dict] = []
    for item in req.obligations:
        # A follow-up with no due date could never become overdue, so the sweep
        # would never chase it. Default to a week after discharge.
        due = parse_date(item.due_date) or (parse_date(discharge) + timedelta(days=7))
        created.append(
            repo.create_obligation(
                db,
                {
                    "patient_id": patient_id,
                    "episode_id": episode_id,
                    "description": item.description.strip(),
                    # Same categories as extraction, so a typed "Cardiology
                    # referral" gets referral rules and matches the summary's
                    # cardiology follow-up instead of duplicating it.
                    "category": _intake_category(item.description) or "follow_up",
                    "owner": item.owner or _intake_owner(item.description) or "Care team",
                    "due_date": due.isoformat(),
                    "status": ObligationStatus.pending.value,
                    "keywords": [w.lower() for w in item.description.split() if len(w) > 3][:8],
                    "source": {
                        "event_id": None,
                        "label": "Patient intake",
                        "quote": item.description.strip(),
                    },
                    "latest_evidence": "",
                },
                actor=actor,
                reason=f"Recorded at intake by {actor}",
            )
        )

    ingestion = None
    if req.discharge_summary and req.discharge_summary.strip():
        ingestion = ingest_document(
            patient_id=patient_id,
            title="Discharge summary (intake)",
            text=req.discharge_summary.strip(),
            source_ref="Patient intake",
            actor=actor,
        )

    if start_tcm:  # EHR ingestion starts it after its structured follow-ups exist
        from . import tcm

        tcm.start_episode(db, patient_id, actor=actor, today=today)

    obligations = repo.list_obligations(db, patient_id, today=today)
    return {
        "patient": repo.get_patient(db, patient_id),
        "episode_id": episode_id,
        "created_obligations": [o["id"] for o in created],
        "obligations": obligations,
        "ingestion": ingestion,
    }


# ── Voice ────────────────────────────────────────────────────────────────


def voice_context(patient_id: str) -> dict:
    db = get_db()
    patient = repo.get_patient(db, patient_id)
    if not patient:
        return {}
    bundle = retrieve_context(db, patient_id, "open follow-ups and preferences", k=6, today=demo_today())
    open_items = bundle["open_obligations"]
    lines = [
        f"Patient: {patient['name']}, age {patient['age']}.",
        f"Preferred channel: {patient.get('preferences', {}).get('channel', 'voice')}.",
        "Open obligations:",
    ]
    for o in open_items:
        due = f", due {o['due_date']}" if o.get("due_date") else ""
        state = o["status"].replace("_", " ")
        lines.append(f"- {o['description']}{due}; status {state}.")
    if not open_items:
        lines.append("- none recorded.")
    recalled = [m for m in bundle.get("memories", []) if "response" not in (m.get("tags") or [])][:4]
    if recalled:
        lines.append("What we remember from earlier contact:")
        lines.extend(f"- {m['summary']}" for m in recalled)
    lines.append(
        "Rules for this call: disclose that you are an AI assistant, confirm consent to record, "
        "verify identity before discussing care details, do not give clinical or medication advice, "
        "and transfer or escalate to a clinician for clinical questions."
    )
    return {
        "patient_id": patient_id,
        "patient_name": patient["name"],
        "first_name": patient["name"].split()[0],
        "preferred_language": patient.get("preferences", {}).get("language", "English"),
        "preferences": patient.get("preferences", {}),
        "open_obligations": open_items,
        "summary": bundle.get("summary", ""),
        "context": "\n".join(lines),
        "disclosure": (
            "Hi, this is Continuum, an AI care assistant calling on behalf of your care team. "
            "This call is recorded and transcribed. Is now a good time?"
        ),
        "escalation_script": (
            "I can't give clinical advice. I'm going to connect you with a clinician or have someone "
            "call you back. If this is an emergency, please call 911."
        ),
    }


def voice_identify(
    *,
    given_name: str,
    family_name: str,
    phone: str | None = None,
    birth_date: str | None = None,
) -> dict:
    """Resolve a caller to patient records for identity verification.

    Identity is two identifiers: name plus date of birth (Joint Commission
    NPSG.01.01.01). A phone number alone proves little on a call that comes
    from that phone, so it only narrows the search. When the record has a
    date of birth, the agent must collect it and call again before discussing
    care; a wrong date of birth is treated as no match. The date of birth
    itself is never returned.
    """
    db = get_db()
    matches = repo.find_patients_by_identity(db, given_name, family_name, phone=phone)
    stated = parse_date(birth_date) if birth_date else None
    if birth_date and stated is None:
        return {
            "matches": [],
            "count": 0,
            "guidance": "That date of birth could not be understood. Ask for it again as day, month and year.",
        }
    if stated:
        matches = [m for m in matches if not m.get("birth_date") or m.get("birth_date") == stated.isoformat()]

    candidates = []
    for patient in matches:
        phone_digits = "".join(ch for ch in (patient.get("phone") or "") if ch.isdigit())
        prefs = patient.get("preferences") or {}
        on_file = bool(patient.get("birth_date"))
        candidates.append(
            {
                "patient_id": patient["id"],
                "name": patient["name"],
                "age": patient.get("age"),
                "phone_last4": phone_digits[-4:] if phone_digits else None,
                "birth_date_on_file": on_file,
                "birth_date_verified": bool(on_file and stated and patient.get("birth_date") == stated.isoformat()),
                "consent_to_automated_calls": prefs.get("consent_to_automated_calls") is True,
                "consent_to_recording": prefs.get("consent_to_recording") is True,
                "interpreter_needed": prefs.get("interpreter_needed") is True,
                "language": prefs.get("language") or "English",
            }
        )
    if not candidates:
        guidance = (
            "No patient matches those details. Do not retry with guessed names or dates. "
            "Apologise, offer to have a member of the care team call back, and end the call."
        )
    elif len(candidates) == 1:
        only = candidates[0]
        if only["birth_date_verified"]:
            guidance = "Identity verified by name and date of birth. You may continue."
        elif only["birth_date_on_file"]:
            guidance = (
                "One possible match. Ask the caller for their date of birth and call identify_patient "
                "again with birth_date. Do not discuss anything about their care until it matches."
            )
        else:
            guidance = (
                "Exactly one match, with no date of birth on file. Confirm identity out loud "
                "(full name and the phone number ending in the last four digits) before discussing anything."
            )
    else:
        guidance = (
            "Multiple patients match. Ask for the date of birth (or the phone number) and call "
            "identify_patient again to narrow it down. Never disclose which names are in the system."
        )
    return {"matches": candidates, "count": len(candidates), "guidance": guidance}


def voice_report(
    *,
    patient_id: str,
    report: str,
    obligation_id: str | None = None,
    reported_status: str | None = None,
    reported_date: str | None = None,
    conversation_id: str | None = None,
    direction: str = "inbound",
) -> dict:
    db = get_db()
    text = report
    if obligation_id:
        obligation = repo.get_obligation(db, obligation_id)
        if obligation:
            text = f"Regarding {obligation['description']}: {report}"
    if reported_status:
        text = f"{text} (reported status: {reported_status})"
    if reported_date:
        text = f"{text} (reported date: {reported_date})"

    result = run_agent(
        patient_id=patient_id,
        input_type="voice_report",
        text=text,
        actor="ElevenLabs voice agent",
        title="Inbound voice update" if direction == "inbound" else "Outbound check-in update",
        source_ref=conversation_id,
        metadata={"conversation_id": conversation_id, "direction": direction},
    )
    if conversation_id:
        call = db.voice_calls.find_one({"elevenlabs_conversation_id": conversation_id}, {"_id": 0})
        if call:
            db.voice_calls.update_one(
                {"id": call["id"]},
                {
                    "$set": {
                        "outcome": "completed" if result.get("status") == "completed" else "awaiting_approval",
                        "agent_message": REPORT_ACK,
                    }
                },
            )
    repo.audit(
        db,
        patient_id=patient_id,
        actor="ElevenLabs voice agent",
        action="Voice report submitted",
        detail=truncate(report, 400),
        after={"result": result.get("status"), "proposals": len(result.get("proposals") or [])},
        thread_id=patient_id,
        run_id=result.get("run_id"),
    )
    return {
        **result,
        # The graph's reply is written for the console, not the patient: it can
        # list open obligations or promise actions nobody has taken. The voice
        # agent only gets a fixed, patient-safe acknowledgement.
        "reply": REPORT_ACK,
        "spoken_response": REPORT_ACK,
        "agent_instructions": (
            "Read spoken_response as is. The report is recorded as unverified patient evidence; "
            "do not tell the patient anything is updated, closed, rescheduled or confirmed."
        ),
    }


REPORT_ACK = "Thanks, I've noted that for your care team; the clinic will verify it."


def voice_escalate(
    *,
    patient_id: str,
    reason: str,
    urgency: str = "routine",
    conversation_id: str | None = None,
) -> dict:
    db = get_db()
    patient = repo.get_patient(db, patient_id)
    if not patient:
        return {"ok": False, "detail": "Unknown patient"}
    event = repo.add_event(
        db,
        {
            "patient_id": patient_id,
            "type": "voice_call",
            "title": f"Voice escalation ({urgency})",
            "detail": truncate(reason, 500),
            "actor": "ElevenLabs voice agent",
            "source_ref": conversation_id,
            "metadata": {"urgency": urgency, "conversation_id": conversation_id},
        },
    )
    proposal = {
        "id": new_id("prop"),
        "kind": "escalate",
        "patient_id": patient_id,
        "title": f"Clinical escalation ({urgency})",
        "rationale": reason,
        "evidence_summary": reason,
        "evidence_kind": EvidenceKind.patient_report.value,
        "confidence": 0.95,
        "changes": {"urgency": urgency, "note": reason},
        "requires_approval": True,
        "interrupt": False,
    }
    approval = repo.create_approval(
        db,
        {
            "patient_id": patient_id,
            "run_id": new_id("run"),
            "thread_id": patient_id,
            "kind": ApprovalKind.escalate.value,
            "mode": "queued",
            "obligation_id": None,
            "title": proposal["title"],
            "rationale": reason,
            "proposed_changes": proposal["changes"],
            "evidence_summary": reason,
            "confidence": 0.95,
            "proposal": proposal,
        },
    )
    repo.audit(
        db,
        patient_id=patient_id,
        actor="ElevenLabs voice agent",
        action="Voice escalation requested",
        detail=truncate(reason, 400),
        after={"urgency": urgency, "approval_id": approval["id"]},
        thread_id=patient_id,
        run_id=approval["run_id"],
    )
    urgent = urgency in {"urgent", "emergency"}
    page_status = _page_status(approval)
    paged = page_status in {"sent", "sending"}
    if urgency == "emergency":
        spoken = "I've flagged this for your care team. If this is an emergency, please hang up and call 911 now."
    elif urgent:
        spoken = "I've flagged this for your care team right away. If this is an emergency, please call 911."
    else:
        spoken = "I've flagged this for your care team to review and follow up with you."
    if urgent and not paged:
        instructions = (
            "The on-call clinician could NOT be paged automatically; the escalation is queued for the care team. "
            "Do not tell the patient a clinician has been paged or will call shortly. "
            "If symptoms sound serious, tell them to call 911."
        )
    elif urgent:
        instructions = "The on-call clinician is being paged. Do not promise a call-back time."
    else:
        instructions = "Queued for clinician review. Do not promise a call-back time or give clinical advice."
    return {
        "ok": True,
        "escalation_id": approval["id"],
        "urgency": approval_urgency(approval) or urgency,
        "event_id": event["id"],
        "paged": paged,
        "page_status": page_status,
        "spoken_response": spoken,
        "agent_instructions": instructions,
        "transfer_recommended": urgent,
    }


def approval_urgency(approval: dict) -> str | None:
    return (approval.get("proposed_changes") or {}).get("urgency")


def _page_status(approval: dict) -> str:
    """sent | sending | failed | not_required for an escalation's on-call page."""
    from .notify import email_configured, sms_configured, urgency as page_urgency

    if not page_urgency(approval):
        return "not_required"
    pages = approval.get("pages") or []
    if any(p.get("ok") for p in pages):
        return "sent"
    if pages:
        return "failed"
    # Paging runs on a background thread when a channel is configured.
    return "sending" if (sms_configured() or email_configured()) else "failed"


# ── Voice agent actions (the agent's "hands") ────────────────────────────
#
# Design rule: a patient asking for an action on a voice call is a *request*,
# never a confirmation. The agent may commit the patient to nothing — it
# records the request, and the coordinator (or an immediate, patient-consented
# check-in) executes it. ``spoken_response`` is only ever text that is safe to
# read to the patient verbatim; guidance for the agent goes in
# ``agent_instructions``.


def voice_request_appointment(
    *,
    patient_id: str,
    requested_time: str,
    obligation_id: str | None = None,
    notes: str = "",
    conversation_id: str | None = None,
) -> dict:
    """Record a patient's appointment request and queue a booking approval.

    The coordinator's approval of the resulting card IS the clinic
    confirmation: the obligation moves to `scheduled` with the requested
    time. Until then nothing is booked.
    """
    db = get_db()
    patient = repo.get_patient(db, patient_id)
    if not patient:
        return {"ok": False, "detail": "Unknown patient"}

    obligation = repo.get_obligation(db, obligation_id) if obligation_id else None
    if obligation_id and not obligation:
        return {"ok": False, "detail": "Unknown obligation"}
    label = obligation["description"] if obligation else (notes or "a follow-up visit")
    when = requested_time or "a time the patient prefers"

    repo.add_event(
        db,
        {
            "patient_id": patient_id,
            "type": "voice_call",
            "title": "Appointment requested",
            "detail": truncate(f"{label} — patient asked for {when}.", 500),
            "actor": "ElevenLabs voice agent",
            "source_ref": conversation_id,
            "metadata": {
                "obligation_id": obligation["id"] if obligation else None,
                "requested_time": requested_time,
                "conversation_id": conversation_id,
            },
        },
    )
    approval = repo.create_approval(
        db,
        {
            "patient_id": patient_id,
            "run_id": new_id("run"),
            "thread_id": patient_id,
            "kind": ApprovalKind.schedule.value,
            "mode": "queued",
            "obligation_id": obligation["id"] if obligation else None,
            "title": f"Book appointment: {label} — {when}",
            "rationale": notes or f"Patient requested an appointment for {when} during a voice call.",
            "proposed_changes": {
                "requested_time": requested_time,
                "notes": notes,
                "requested_by": f"{patient['name']} (voice call)",
            },
            "evidence_summary": f"Patient requested {label} for {when}",
            "confidence": 0.9,
            "proposal": {
                "id": new_id("prop"),
                "kind": "schedule",
                "patient_id": patient_id,
                "obligation_id": obligation["id"] if obligation else None,
                "title": f"Confirm appointment: {label} — {when}",
                "rationale": notes or f"Patient requested an appointment for {when} during a voice call.",
                "evidence_summary": f"Patient requested {label} for {when}",
                "evidence_kind": EvidenceKind.patient_report.value,
                "confidence": 0.9,
                "changes": {"requested_time": requested_time, "notes": notes},
                "requires_approval": True,
                "interrupt": False,
            },
        },
    )
    repo.audit(
        db,
        patient_id=patient_id,
        actor="ElevenLabs voice agent",
        action="Voice appointment requested",
        detail=truncate(f"{label} for {when}. {notes}".strip(), 400),
        after={"approval_id": approval["id"], "requested_time": requested_time},
        thread_id=patient_id,
        run_id=approval["run_id"],
    )
    return {
        "ok": True,
        "approval_id": approval["id"],
        "obligation_id": obligation["id"] if obligation else None,
        "status": "pending_confirmation",
        "spoken_response": (
            f"I've sent your request to the clinic for {when}. "
            "They'll confirm the appointment and let you know the final time."
        ),
        "agent_instructions": (
            "The appointment is NOT booked; it is pending clinic confirmation. "
            "Do not tell the patient it is booked or confirmed."
        ),
    }


def voice_request_follow_up(
    *,
    patient_id: str,
    item: str,
    reason: str = "",
    conversation_id: str | None = None,
) -> dict:
    """Queue a 'send X to the patient' task for the coordinator."""
    db = get_db()
    patient = repo.get_patient(db, patient_id)
    if not patient:
        return {"ok": False, "detail": "Unknown patient"}

    repo.add_event(
        db,
        {
            "patient_id": patient_id,
            "type": "voice_call",
            "title": "Follow-up item requested",
            "detail": truncate(f"Patient asked the clinic to send: {item}. {reason}".strip(), 500),
            "actor": "ElevenLabs voice agent",
            "source_ref": conversation_id,
            "metadata": {"item": item, "conversation_id": conversation_id},
        },
    )
    approval = repo.create_approval(
        db,
        {
            "patient_id": patient_id,
            "run_id": new_id("run"),
            "thread_id": patient_id,
            "kind": ApprovalKind.follow_up.value,
            "mode": "queued",
            "obligation_id": None,
            "title": f"Send {item} to {patient['name']}",
            "rationale": reason or f"Patient asked for {item} during a voice call.",
            "proposed_changes": {"item": item, "requested_by": f"{patient['name']} (voice call)"},
            "evidence_summary": f"Patient asked for {item}",
            "confidence": 0.9,
            "proposal": {
                "id": new_id("prop"),
                "kind": "follow_up",
                "patient_id": patient_id,
                "title": f"Send {item} to {patient['name']}",
                "rationale": reason or f"Patient asked for {item} during a voice call.",
                "evidence_summary": f"Patient asked for {item}",
                "evidence_kind": EvidenceKind.patient_report.value,
                "confidence": 0.9,
                "changes": {"item": item, "reason": reason},
                "requires_approval": True,
                "interrupt": False,
            },
        },
    )
    repo.audit(
        db,
        patient_id=patient_id,
        actor="ElevenLabs voice agent",
        action="Voice follow-up requested",
        detail=truncate(f"Send {item}. {reason}".strip(), 400),
        after={"approval_id": approval["id"]},
        thread_id=patient_id,
        run_id=approval["run_id"],
    )
    return {
        "ok": True,
        "approval_id": approval["id"],
        "status": "pending_confirmation",
        "spoken_response": f"I've asked the care team to send you {item}.",
        "agent_instructions": "It has been requested, not sent. Do not say it has already been sent.",
    }


def voice_request_callback(
    *,
    patient_id: str,
    when: str,
    reason: str = "",
    conversation_id: str | None = None,
) -> dict:
    """Schedule an outbound voice check-in the patient just asked for.

    This one executes immediately: the patient requested the call on a
    recorded, consented line, and the check-in is itself an automated call
    gated by the patient's consent flag.
    """
    db = get_db()
    patient = repo.get_patient(db, patient_id)
    if not patient:
        return {"ok": False, "detail": "Unknown patient"}
    prefs = patient.get("preferences") or {}
    if prefs.get("consent_to_automated_calls") is not True:  # opt-in: missing means no
        return {
            "ok": False,
            "detail": "Patient has not consented to automated calls",
            "spoken_response": (
                "I'm not able to schedule an automated call for you, "
                "but a member of the care team will reach out by your preferred method."
            ),
            "agent_instructions": "No call was scheduled. Do not promise a call at a specific time.",
        }

    checkin = repo.schedule_checkin(
        db,
        {
            "patient_id": patient_id,
            "scheduled_for": when,
            "reason": reason or "Patient requested a callback during a voice call",
            "created_by": "ElevenLabs voice agent",
        },
    )
    repo.add_event(
        db,
        {
            "patient_id": patient_id,
            "type": "voice_call",
            "title": "Callback scheduled",
            "detail": truncate(f"Check-in scheduled for {when}. {reason}".strip(), 500),
            "actor": "ElevenLabs voice agent",
            "source_ref": conversation_id,
            "metadata": {"checkin_id": checkin["id"], "conversation_id": conversation_id},
        },
    )
    repo.audit(
        db,
        patient_id=patient_id,
        actor="ElevenLabs voice agent",
        action="Voice callback scheduled",
        detail=truncate(f"Check-in for {when}. {reason}".strip(), 400),
        after={"checkin_id": checkin["id"]},
        thread_id=patient_id,
    )
    resolved = bool(checkin.get("scheduled_for_resolved"))
    if resolved:
        spoken = f"I've set up a call for {_spoken_time(checkin['scheduled_for'])}. The care team will call you then."
        instructions = "The callback time is set. Do not promise anything else about the call."
    else:
        spoken = (
            "I've passed your request for a call back to the care team. "
            "They'll confirm the time with you."
        )
        instructions = (
            "The requested time could not be understood, so it is NOT confirmed; the care team will confirm it. "
            "Do not tell the patient a time is set."
        )
    return {
        "ok": True,
        "checkin_id": checkin["id"],
        "status": "scheduled" if resolved else "needs_time_confirmation",
        "resolved": resolved,
        "scheduled_for": checkin["scheduled_for"] if resolved else None,
        "spoken_response": spoken,
        "agent_instructions": instructions,
    }


def _spoken_time(value: str) -> str:
    """"2026-11-20T14:00:00-05:00" → "Friday, November 20 at 2 PM"; a date → "Friday, November 20"."""
    from datetime import datetime as _dt

    try:
        parsed = _dt.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return value
    day = f"{parsed:%A}, {parsed:%B} {parsed.day}"
    if "T" not in value:
        return day
    from .util import _call_zone

    local = parsed.astimezone(_call_zone()) if parsed.tzinfo else parsed
    hour = local.hour % 12 or 12
    minutes = f":{local.minute:02d}" if local.minute else ""
    return f"{day} at {hour}{minutes} {'AM' if local.hour < 12 else 'PM'}"


def _extract_post_call(payload: dict) -> tuple[dict, list[dict], str | None, str | None]:
    data = payload.get("data") or payload
    transcript = data.get("transcript") or []
    from .util import strip_voice_tags

    for turn in transcript:
        if isinstance(turn, dict) and (turn.get("role") or "") in {"agent", "assistant"} and turn.get("message"):
            turn["message"] = strip_voice_tags(turn["message"])
    conversation_id = data.get("conversation_id") or data.get("conversationId")
    patient_id = (
        data.get("patient_id")
        or (data.get("metadata") or {}).get("patient_id")
        or (payload.get("metadata") or {}).get("patient_id")
        or (data.get("dynamic_variables") or {}).get("patient_id")
        or ((data.get("conversation_initiation_client_data") or {}).get("dynamic_variables") or {}).get("patient_id")
    )
    direction = (
        data.get("direction")
        or (data.get("metadata") or {}).get("direction")
        or ((data.get("metadata") or {}).get("phone_call") or {}).get("direction")
        or "inbound"
    )
    return data, transcript, conversation_id, patient_id or None


def _patient_from_tool_calls(db, transcript: list[dict]) -> str | None:
    """Last patient the agent looked up (identify/context/report tool calls)."""
    import json as _json
    import re as _re

    found: list[str] = []
    for turn in transcript or []:
        blob = _json.dumps([turn.get("tool_calls") or [], turn.get("tool_results") or []])
        found += _re.findall(r'patient_id\\?"\s*:\s*\\?"([A-Za-z0-9_-]+)', blob)
        found += _re.findall(r"/voice/context/([A-Za-z0-9_-]+)", blob)
    for candidate in reversed(found):
        if db.patients.find_one({"id": candidate}, {"_id": 1}):
            return candidate
    return None


def handle_post_call(payload: dict, patient_id: str | None = None) -> dict:
    db = get_db()
    data, transcript, conversation_id, inferred_patient = _extract_post_call(payload)
    resolved_patient = patient_id or inferred_patient

    if not resolved_patient and conversation_id:
        call = db.voice_calls.find_one(
            {"elevenlabs_conversation_id": conversation_id}, {"_id": 0}
        )
        if call:
            resolved_patient = call["patient_id"]

    if not resolved_patient:
        # Browser/test-panel calls carry no patient_id variable, but the agent
        # verified the caller with its tools: take the patient from those.
        resolved_patient = _patient_from_tool_calls(db, transcript)

    if not resolved_patient:
        # Demo-safe fallback: if exactly one synthetic patient exists, use it.
        if db.patients.count_documents({}) == 1:
            resolved_patient = db.patients.find_one({}, {"id": 1})["id"]
        else:
            return {
                "ok": False,
                "detail": "No patient_id on the post-call payload. Configure the dynamic variable or webhook query parameter.",
            }

    text = transcript_text(transcript) if transcript else (data.get("transcript_text") or "")
    from . import tcm

    if not text:
        # A call that rang out still counts toward the documented TCM attempts.
        if conversation_id:
            tcm.record_contact(db, patient_id=resolved_patient, outcome="no_answer", channel="Voice call",
                               actor="Continuum (voice agent)", ref=conversation_id)
        return {"ok": False, "detail": "Post-call payload contained no transcript."}
    patient_spoke = any((turn.get("role") or "") == "user" and (turn.get("message") or "").strip()
                        for turn in (transcript or []) if isinstance(turn, dict))
    tcm.record_contact(
        db,
        patient_id=resolved_patient,
        outcome="reached" if patient_spoke or not transcript else "no_answer",
        channel="Voice call",
        actor="Continuum (voice agent)",
        ref=conversation_id,
        direction=data.get("direction") or (data.get("metadata") or {}).get("direction") or "inbound",
        note="Voice call with transcript",
    )

    # Close out the scheduled check-in this call fulfilled, if any.
    checkin_id = (
        ((data.get("conversation_initiation_client_data") or {}).get("dynamic_variables") or {}).get("checkin_id")
        or (data.get("dynamic_variables") or {}).get("checkin_id")
    )
    if not checkin_id and conversation_id:
        call = db.voice_calls.find_one({"elevenlabs_conversation_id": conversation_id}, {"_id": 0}) or {}
        checkin_id = call.get("checkin_id")
    if checkin_id:
        db.checkins.update_one(
            {"id": checkin_id, "patient_id": resolved_patient},
            {"$set": {"status": "completed", "completed_at": utcnow(), "conversation_id": conversation_id}},
        )

    call = repo.upsert_voice_call(
        db,
        {
            "patient_id": resolved_patient,
            "elevenlabs_conversation_id": conversation_id,
            "direction": data.get("direction") or (data.get("metadata") or {}).get("direction") or "inbound",
            "started_at": utcnow(),
            "duration_secs": (data.get("metadata") or {}).get("call_duration_secs"),
            "outcome": "completed",
            "transcript": transcript,
            "agent_id": data.get("agent_id"),
            "raw_type": payload.get("type"),
        },
    )
    result = run_agent(
        patient_id=resolved_patient,
        input_type="voice_transcript",
        text=text,
        actor="ElevenLabs voice agent",
        title="Voice check-in completed",
        source_ref=conversation_id,
        transcript=transcript,
        metadata={
            "conversation_id": conversation_id,
            "call_id": call["id"],
            "type": payload.get("type"),
        },
    )
    db.voice_calls.update_one(
        {"id": call["id"]},
        {"$set": {"outcome": "processed", "agent_summary": result.get("reply")}},
    )
    if conversation_id:
        from . import calls, ledger

        calls.on_post_call(db, conversation_id=conversation_id, transcript_turns=transcript or [])
        ledger.confirm(db, external_ref=conversation_id, kind="outbound_call",
                       evidence=f"Post-call transcript received ({len(transcript or [])} turns)")
    return {"ok": True, "patient_id": resolved_patient, "call_id": call["id"], **result}


def sync_elevenlabs_call(conversation_id: str) -> dict:
    """Fetch conversation details from ElevenLabs (used by the UI refresh action)."""
    from .config import settings
    import httpx

    if not settings.elevenlabs_api_key:
        return {"ok": False, "detail": "ELEVENLABS_API_KEY is not configured"}
    try:
        response = httpx.get(
            f"https://api.elevenlabs.io/v1/convai/conversations/{conversation_id}",
            headers={"xi-api-key": settings.elevenlabs_api_key},
            timeout=20,
        )
        response.raise_for_status()
        return {"ok": True, "conversation": response.json()}
    except Exception as exc:
        return {"ok": False, "detail": str(exc)}


# ── Approvals ────────────────────────────────────────────────────────────


def resolve_approval(
    approval_id: str,
    *,
    approve: bool,
    actor: str,
    note: str | None = None,
    chosen_date: str | None = None,
) -> dict:
    db = get_db()
    approval = repo.get_approval(db, approval_id)
    if not approval:
        return {"ok": False, "status": "not_found"}
    if chosen_date and approve and approval.get("kind") == "resolve_conflict" and approval.get("status") == "pending":
        approval = _with_chosen_date(db, approval, chosen_date)
    if approval.get("status") != ApprovalStatus.pending.value:
        return {"ok": False, "status": "already_resolved", "approval": approval}

    repo.mark_approval_resolved(db, approval_id, approve, actor, note)
    approval = repo.get_approval(db, approval_id) or approval

    if approval.get("mode") == "interrupt":
        run_approvals = repo.list_approvals(db, approval["patient_id"], status=None)
        siblings = [
            a
            for a in run_approvals
            if a.get("run_id") == approval.get("run_id") and a.get("mode") == "interrupt"
        ]
        still_pending = [a for a in siblings if a.get("status") == ApprovalStatus.pending.value]
        if still_pending:
            return {
                "ok": True,
                "status": "awaiting_more_approvals",
                "approval": approval,
                "remaining": still_pending,
                "patient_id": approval["patient_id"],
            }
        decisions = {
            a["id"]: {
                "approved": a.get("status") == ApprovalStatus.approved.value,
                "actor": a.get("resolved_by") or actor,
                "note": a.get("resolution_note"),
            }
            for a in siblings
        }
        try:
            resumed = resume_agent(approval["patient_id"], decisions)
        except Exception as exc:  # pragma: no cover - checkpoint edge case
            log.exception("Resume failed: %s", exc)
            for a in siblings:
                engine.apply_approval(
                    db, a, approved=a.get("status") == ApprovalStatus.approved.value, actor=actor, note=note
                )
            resumed = {
                "status": "completed",
                "reply": "Approval recorded. The paused run could not be resumed, so changes were applied directly.",
            }
        return {
            "ok": True,
            "status": resumed.get("status", "completed"),
            "approval": repo.get_approval(db, approval_id),
            "patient_id": approval["patient_id"],
            "reply": resumed.get("reply"),
            "applied": resumed.get("applied") or [],
        }

    if approve:
        engine.apply_approval(db, approval, approved=True, actor=actor, note=note)
    else:
        engine.apply_approval(db, approval, approved=False, actor=actor, note=note)
    return {
        "ok": True,
        "status": "approved" if approve else "rejected",
        "approval": repo.get_approval(db, approval_id),
        "patient_id": approval["patient_id"],
    }


def _with_chosen_date(db, approval: dict, chosen: str) -> dict:
    """Resolve a conflict to a third date the coordinator confirmed.

    Both earlier values stay on the obligation as superseded; nothing is
    overwritten silently.
    """
    proposal = dict(approval.get("proposal") or {})
    changes = dict(proposal.get("changes") or approval.get("proposed_changes") or {})
    previous = changes.get("conflict_resolution") or {}
    superseded = [v for v in (previous.get("kept"), previous.get("superseded")) if v and v != chosen]
    changes.update(
        {
            "status": ObligationStatus.scheduled.value,
            "confirmed_date": chosen,
            "confirmed_source": "coordinator-confirmed date",
            "resolution": f"Coordinator confirmed {chosen}; earlier values {', '.join(superseded) or 'none'} preserved as superseded.",
            "conflict_resolution": {**previous, "kept": chosen, "superseded": ", ".join(superseded) or None},
        }
    )
    proposal["changes"] = changes
    db.approvals.update_one(
        {"id": approval["id"]},
        {"$set": {"proposal": proposal, "proposed_changes": changes, "chosen_date": chosen}},
    )
    return repo.get_approval(db, approval["id"]) or approval


# ── Coordinator confirmations ────────────────────────────────────────────


def confirm_appointment(
    *,
    patient_id: str,
    obligation_hint: str,
    confirmed_date: str,
    actor: str,
    source_ref: str | None = None,
    note: str | None = None,
) -> dict:
    db = get_db()
    obligation, score = repo.find_matching_obligation(db, patient_id, obligation_hint)
    if not obligation:
        return {"ok": False, "detail": "No matching obligation"}

    evidence = repo.add_evidence(
        db,
        patient_id=patient_id,
        obligation_id=obligation["id"],
        kind=EvidenceKind.clinic_confirmation.value,
        summary=note
        or f"Clinic confirmed appointment for {confirmed_date}." + (f" Source: {source_ref}" if source_ref else ""),
        source_ref=source_ref,
        confidence=0.95,
        actor=actor,
    )
    updated = repo.set_obligation_status(
        db,
        obligation,
        ObligationStatus.scheduled.value,
        actor,
        f"Appointment confirmed for {confirmed_date} with clinic evidence.",
        due_date=confirmed_date,
        next_action=None,
        thread_id=patient_id,
    )
    db.obligations.update_one(
        {"id": obligation["id"]},
        {"$set": {"confirmed_date": confirmed_date, "confirmed_source": source_ref or "clinic record"}},
    )
    repo.add_event(
        db,
        {
            "patient_id": patient_id,
            "episode_id": obligation.get("episode_id"),
            "type": "coordinator",
            "title": "Appointment confirmed",
            "detail": f"{obligation['description']} confirmed for {confirmed_date}.",
            "actor": actor,
            "source_ref": source_ref,
            "metadata": {"obligation_id": obligation["id"], "evidence_id": evidence["id"]},
        },
    )
    return {"ok": True, "obligation": repo.get_obligation(db, obligation["id"])}


# ── Demo runner ──────────────────────────────────────────────────────────


def _demo_call(
    db: Database,
    *,
    patient_id: str,
    transcript: list[dict],
    direction: str,
    conversation_id: str,
) -> None:
    repo.upsert_voice_call(
        db,
        {
            "patient_id": patient_id,
            "elevenlabs_conversation_id": conversation_id,
            "direction": direction,
            "started_at": utcnow(),
            "duration_secs": transcript[-1].get("time_in_call_secs") if transcript else None,
            "outcome": "completed",
            "transcript": transcript,
        },
    )


def run_demo_day(day: int, actor: str = "Demo runner") -> dict:
    db = get_db()
    today = demo_today()
    patient = repo.get_patient(db, MICHAEL_ID)
    if not patient:
        return {"ok": False, "detail": "Demo patient is not seeded."}

    if day == 0:
        reset_patient_scenario(db, MICHAEL_ID)
        result = ingest_document(
            patient_id=MICHAEL_ID,
            title="Discharge summary — heart failure",
            text=discharge_text(today.isoformat()),
            source_ref="synthetic-discharge-michael",
            actor=actor,
        )
        return {"ok": True, "day": 0, "narration": "Discharge summary ingested; obligations extracted.", **result}

    if day == 3:
        episode = repo.get_active_episode(db, MICHAEL_ID) or {}
        discharge = episode.get("discharge_date") or today.isoformat()
        transcript = day3_transcript(discharge, today)
        conversation_id = f"conv_demo_day3_{today.isoformat()}"
        _demo_call(db, patient_id=MICHAEL_ID, transcript=transcript, direction="outbound", conversation_id=conversation_id)
        result = run_agent(
            patient_id=MICHAEL_ID,
            input_type="voice_transcript",
            text=transcript_text(transcript),
            actor="ElevenLabs voice agent",
            title="Outbound Day 3 check-in completed",
            source_ref=conversation_id,
            transcript=transcript,
            metadata={"conversation_id": conversation_id, "direction": "outbound", "demo_day": 3},
        )
        return {
            "ok": True,
            "day": 3,
            "narration": "Patient reported seeing his family physician. Recorded as patient-reported, awaiting verification.",
            **result,
        }

    if day == 7:
        obligation, _ = repo.find_matching_obligation(db, MICHAEL_ID, "cardiology follow-up")
        if not obligation:
            return {"ok": False, "detail": "Cardiology obligation not found. Run Day 0 first."}
        confirmed = add_days(today.isoformat(), 12)
        result = confirm_appointment(
            patient_id=MICHAEL_ID,
            obligation_hint="cardiology follow-up",
            confirmed_date=confirmed,
            actor=actor,
            source_ref="CS-8841",
            note=f"Cardiology clinic confirmed appointment for {confirmed} (synthetic scheduling record CS-8841).",
        )
        return {
            "ok": True,
            "day": 7,
            "narration": f"Coordinator confirmed the cardiology appointment for {confirmed} with clinic evidence.",
            **result,
        }

    if day == 10:
        obligation, _ = repo.find_matching_obligation(db, MICHAEL_ID, "cardiology follow-up")
        if not obligation:
            return {"ok": False, "detail": "Cardiology obligation not found. Run Day 0 first."}
        confirmed = obligation.get("confirmed_date") or add_days(today.isoformat(), 12)
        reported = add_days(confirmed, 3)
        transcript = day10_transcript(confirmed, reported)
        conversation_id = f"conv_demo_day10_{today.isoformat()}"
        _demo_call(db, patient_id=MICHAEL_ID, transcript=transcript, direction="inbound", conversation_id=conversation_id)
        result = run_agent(
            patient_id=MICHAEL_ID,
            input_type="voice_transcript",
            text=transcript_text(transcript),
            actor="ElevenLabs voice agent",
            title="Inbound call — conflicting cardiology date",
            source_ref=conversation_id,
            transcript=transcript,
            metadata={"conversation_id": conversation_id, "direction": "inbound", "demo_day": 10},
        )
        return {
            "ok": True,
            "day": 10,
            "narration": f"Patient reported {reported}; the clinic record says {confirmed}. Conflict preserved and sent for review.",
            **result,
        }

    if day == 12:
        transcript = day12_transcript()
        conversation_id = f"conv_demo_day12_{today.isoformat()}"
        _demo_call(db, patient_id=MICHAEL_ID, transcript=transcript, direction="inbound", conversation_id=conversation_id)
        result = run_agent(
            patient_id=MICHAEL_ID,
            input_type="voice_transcript",
            text=transcript_text(transcript),
            actor="ElevenLabs voice agent",
            title="Inbound call — medication dosage question",
            source_ref=conversation_id,
            transcript=transcript,
            metadata={"conversation_id": conversation_id, "direction": "inbound", "demo_day": 12},
        )
        return {
            "ok": True,
            "day": 12,
            "narration": "Medication dosage question detected. The voice agent declined clinical advice and escalated to a clinician.",
            **result,
        }

    return {"ok": False, "detail": f"Unknown demo day {day}. Use 0, 3, 7, 10 or 12."}
