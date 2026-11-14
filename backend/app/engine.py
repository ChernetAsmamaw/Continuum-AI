"""Deterministic reasoning/validation and proposal application.

The Unclosed Loop Engine lives here. LLM extraction proposes *candidates*;
this module decides what they mean against current records and enforces the
core product rules:

* a patient report is never verified completion;
* requesting/booking an appointment does not close a loop;
* sensitive closures, cancellations and conflict resolutions need approval;
* nothing is overwritten — conflicts and history are preserved.
"""

from __future__ import annotations

import logging
from datetime import date

from pymongo.database import Database

from . import repository as repo
from .schemas import ApprovalKind, EvidenceKind, ObligationStatus
from .util import add_days, new_id, parse_date, similarity, truncate, utcnow

log = logging.getLogger("continuum.engine")

OPEN = {
    ObligationStatus.pending.value,
    ObligationStatus.scheduled.value,
    ObligationStatus.attempted.value,
    ObligationStatus.patient_reported.value,
    ObligationStatus.overdue.value,
    ObligationStatus.conflict.value,
    ObligationStatus.unknown.value,
}

SAFE_KINDS = {
    "create_obligation", "record_patient_report", "record_patient_update", "flag_conflict", "schedule_checkin", "note",
}


def _proposal(
    kind: str,
    *,
    title: str,
    rationale: str,
    obligation_id: str | None = None,
    evidence_summary: str = "",
    evidence_kind: str = EvidenceKind.agent_note.value,
    confidence: float = 0.7,
    changes: dict | None = None,
    approval_kind: str | None = None,
    requires_approval: bool = False,
    interrupt: bool = False,
) -> dict:
    return {
        "id": new_id("prop"),
        "kind": kind,
        "title": title,
        "rationale": rationale,
        "obligation_id": obligation_id,
        "evidence_summary": truncate(evidence_summary, 600),
        "evidence_kind": evidence_kind,
        "confidence": round(confidence, 3),
        "changes": changes or {},
        "approval_kind": approval_kind,
        "requires_approval": requires_approval,
        "interrupt": interrupt,
    }


def _category_match(
    obligations: list[dict], category: str | None, occurrence: int | None = None
) -> dict | None:
    if not category:
        return None
    for o in obligations:
        if o.get("category") != category:
            continue
        if occurrence is not None:
            # Matching a follow-up requirement: the same kind *and* the same
            # instance (first bloodwork vs repeat bloodwork), any status.
            if int(o.get("occurrence") or 1) == occurrence:
                return o
            continue
        if o.get("status") not in {ObligationStatus.cancelled.value, ObligationStatus.verified.value}:
            return o
    return None


def rank_obligations(
    obligations: list[dict],
    hint: str,
    category: str | None = None,
    *,
    details: str = "",
    reported_date: str | None = None,
) -> list[tuple[dict, float, list[str]]]:
    """Score every open obligation against a report and explain each score.

    Signals: same care category, whole-word keyword hits, wording overlap with
    the description and owner, how close a reported date is to the recorded
    due/confirmed date, and, for repeats, a mild preference for the earliest
    still-open instance. Returns ``[(obligation, score, reasons)]`` best first.
    """
    from .agent.extraction import has_term

    text = f"{hint} {details}".lower()
    reported = parse_date(reported_date)
    ranked: list[tuple[dict, float, list[str]]] = []
    for o in obligations:
        if o.get("status") in {ObligationStatus.cancelled.value, ObligationStatus.verified.value}:
            continue
        score, reasons = 0.0, []
        if category and o.get("category") == category:
            score += 0.6
            reasons.append("same follow-up type")
        hits = [k for k in o.get("keywords", []) if has_term(k.lower(), text)]
        if hits:
            score += min(0.2 * len(hits), 0.4)
            reasons.append(f"mentions {', '.join(hits[:3])}")
        overlap = similarity(text, " ".join([o.get("description", ""), o.get("owner", ""), o.get("category", "")]))
        if overlap:
            score += 0.5 * overlap
        owner = (o.get("owner") or "").lower()
        if owner and owner not in {"care team"} and has_term(owner, text):
            score += 0.15
            reasons.append(f"names {o.get('owner')}")
        # A matching date alone says nothing about *which* follow-up the
        # patient means ("I did my bloodwork yesterday" must not land on a
        # home-care visit that happened to be due the day before). Only count
        # date closeness once the report is about this follow-up at all.
        if score <= 0 and overlap < 0.15:
            continue
        recorded = parse_date(o.get("confirmed_date") or o.get("due_date"))
        if reported and recorded:
            gap = abs((reported - recorded).days)
            closeness = max(0.0, 1 - gap / 30)
            if closeness:
                score += 0.3 * closeness
                reasons.append(f"date within {gap} day(s) of record")
        occurrence = int(o.get("occurrence") or 1)
        score -= 0.04 * (occurrence - 1)
        if score > 0:
            ranked.append((o, round(score, 3), reasons))
    ranked.sort(key=lambda r: r[1], reverse=True)
    return ranked


def _match(
    obligations: list[dict],
    hint: str,
    category: str | None = None,
    *,
    details: str = "",
    reported_date: str | None = None,
) -> tuple[dict | None, float]:
    ranked = rank_obligations(obligations, hint, category, details=details, reported_date=reported_date)
    if not ranked or ranked[0][1] < 0.18:
        return None, ranked[0][1] if ranked else 0.0
    return ranked[0][0], min(ranked[0][1], 1.0)


def _ambiguity(ranked: list[tuple[dict, float, list[str]]]) -> dict | None:
    """Second-best candidate when it is too close to call."""
    if len(ranked) >= 2 and ranked[0][1] - ranked[1][1] < 0.03:
        return ranked[1][0]
    return None


# ── Validation ───────────────────────────────────────────────────────────


def validate(
    db: Database,
    *,
    extraction: dict,
    existing: list[dict],
    input_type: str,
    event_id: str | None,
    anchor: str,
    run_id: str,
    thread_id: str,
) -> tuple[list[dict], list[str]]:
    proposals: list[dict] = []
    notes: list[str] = []
    today = date.today()

    # 1. New follow-up requirements from documents or explicit instructions.
    for item in extraction.get("follow_ups", []):
        category = item.get("category")
        description = item.get("description") or "Follow-up"
        occurrence = int(item.get("occurrence") or 1)
        match = _category_match(existing, category, occurrence)
        if match:
            new_due = item.get("due_date") or (
                add_days(anchor, item["due_in_days"]) if item.get("due_in_days") is not None else None
            )
            if new_due and match.get("due_date") and new_due != match["due_date"]:
                notes.append(
                    f"Existing {description} due date {match['due_date']} differs from document ({new_due}); left unchanged for review."
                )
            else:
                notes.append(f"{description} already tracked as [{match['id']}].")
            continue

        due_date = item.get("due_date") or (
            add_days(anchor, item["due_in_days"]) if item.get("due_in_days") is not None else None
        )
        proposals.append(
            _proposal(
                "create_obligation",
                title=f"Create obligation: {description}",
                rationale=(
                    f"Explicit follow-up requirement in the {input_type.replace('_', ' ')}. "
                    f"Proposed owner: {item.get('owner_hint') or 'care team'}."
                ),
                evidence_summary=item.get("quote") or description,
                evidence_kind=EvidenceKind.document.value if input_type == "document" else EvidenceKind.agent_note.value,
                confidence=float(item.get("confidence") or 0.7),
                changes={
                    "description": description,
                    "category": category or "follow_up",
                    "owner": item.get("owner_hint") or "Care team",
                    "due_date": due_date,
                    "source_label": "Discharge summary" if input_type == "document" else "Care interaction",
                    "quote": item.get("quote"),
                    "event_id": event_id,
                    "keywords": _keywords_for(category, description),
                    "occurrence": occurrence,
                    "status": ObligationStatus.pending.value,
                    "verification_required": True,
                },
            )
        )

    # 2. Patient reports never verify completion.
    for report in extraction.get("patient_reports", []):
        hint = report.get("obligation_hint") or ""
        ranked = rank_obligations(
            existing,
            hint,
            report.get("category"),
            details=report.get("details") or "",
            reported_date=report.get("reported_date"),
        )
        match, score = (ranked[0][0], ranked[0][1]) if ranked and ranked[0][1] >= 0.18 else (None, 0.0)
        runner_up = _ambiguity(ranked) if match else None
        if runner_up:
            notes.append(
                f"Report could refer to {match['description']} or {runner_up['description']}; "
                f"recorded against {match['description']} — coordinator should confirm."
            )
            proposals.append(
                _proposal(
                    "note",
                    title="Ambiguous patient report",
                    rationale=(
                        f"The report matched {match['description']} and {runner_up['description']} almost equally. "
                        "Recorded against the first; please confirm which follow-up the patient meant."
                    ),
                    evidence_summary=report.get("details") or hint,
                    confidence=0.5,
                    changes={
                        "kind": "uncertainty",
                        "summary": f"Ambiguous report: {match['description']} vs {runner_up['description']}",
                    },
                )
            )
        if not match:
            notes.append(f"Patient report could not be matched to an obligation: “{truncate(hint, 90)}”.")
            proposals.append(
                _proposal(
                    "note",
                    title="Unmatched patient report",
                    rationale="Patient reported an update that did not match an open obligation. Recorded for coordinator review.",
                    evidence_summary=report.get("details") or hint,
                    confidence=0.5,
                    changes={"kind": "uncertainty", "summary": f"Unmatched patient report: {truncate(hint, 120)}"},
                )
            )
            continue

        reported_date = parse_date(report.get("reported_date"))
        confirmed = parse_date(match.get("confirmed_date"))
        conflict = False
        if reported_date and confirmed and reported_date != confirmed:
            conflict = True
            already = any(
                c.get("reported_value") == reported_date.isoformat() for c in match.get("conflicts", [])
            )
            if not already:
                proposals.append(
                    _proposal(
                        "flag_conflict",
                        title=f"Conflict: {match['description']} date",
                        rationale=(
                            f"Patient reported {reported_date.isoformat()} but the record shows "
                            f"{confirmed.isoformat()} (confirmed {match.get('confirmed_source', 'earlier record')}). "
                            "Both values are preserved; coordinator review required."
                        ),
                        obligation_id=match["id"],
                        evidence_summary=report.get("details") or f"Patient reported {reported_date.isoformat()}",
                        evidence_kind=EvidenceKind.patient_report.value,
                        confidence=0.9,
                        changes={
                            "conflict": {
                                "field": "confirmed_date",
                                "recorded_value": confirmed.isoformat(),
                                "reported_value": reported_date.isoformat(),
                                "reported_by": "patient",
                                "detected_at": utcnow(),
                                "resolved": False,
                            }
                        },
                    )
                )
            proposals.append(
                _proposal(
                    "resolve_conflict",
                    title=f"Resolve conflict: {match['description']}",
                    rationale=(
                        f"Choose the clinic-confirmed date {confirmed.isoformat()} and record the patient's "
                        f"{reported_date.isoformat()} as a superseded report, or reject and keep the patient report."
                    ),
                    obligation_id=match["id"],
                    evidence_summary=(
                        f"Clinic record: {confirmed.isoformat()}. Patient report: {reported_date.isoformat()}."
                    ),
                    evidence_kind=EvidenceKind.clinic_confirmation.value,
                    confidence=0.9,
                    changes={
                        "status": ObligationStatus.scheduled.value,
                        "confirmed_date": confirmed.isoformat(),
                        "resolution": "Keep clinic-confirmed date; patient report preserved as superseded.",
                        "conflict_resolution": {
                            "kept": confirmed.isoformat(),
                            "superseded": reported_date.isoformat(),
                            "resolved_at": utcnow(),
                        },
                    },
                    approval_kind=ApprovalKind.resolve_conflict.value,
                    requires_approval=True,
                )
            )
            notes.append(f"Conflict detected for {match['description']}: {confirmed} vs {reported_date}.")

        status_note = report.get("reported_status", "unknown")
        evidence_summary = report.get("details") or f"Patient reported {status_note} for {match['description']}."
        if (
            confirmed
            and reported_date == confirmed
            and status_note in {"scheduled", "rescheduled", "unknown"}
            and match.get("status") == ObligationStatus.scheduled.value
        ):
            # The patient repeats the clinic-confirmed date: corroboration, not a
            # new claim. Keep the clinic-backed status and log the agreement.
            proposals.append(
                _proposal(
                    "record_patient_update",
                    title=f"Patient confirms {match['description']} on {confirmed.isoformat()}",
                    rationale="Patient's date matches the clinic-confirmed date. Status stays scheduled.",
                    obligation_id=match["id"],
                    evidence_summary=evidence_summary,
                    evidence_kind=EvidenceKind.patient_report.value,
                    confidence=0.85,
                    changes={
                        "memory_summary": f"{match['description']}: patient confirms the clinic date {confirmed.isoformat()}",
                        "memory_tags": ["patient_report", "corroborated"],
                    },
                )
            )
            continue
        if status_note == "not_completed" and not conflict:
            # "Not yet" is information, not progress: keep the status, keep the evidence.
            proposals.append(
                _proposal(
                    "record_patient_update",
                    title=f"Note: {match['description']} not done yet",
                    rationale="Patient says this has not happened yet. Status unchanged; the loop stays open.",
                    obligation_id=match["id"],
                    evidence_summary=evidence_summary,
                    evidence_kind=EvidenceKind.patient_report.value,
                    confidence=0.8,
                    changes={
                        "reported_status": status_note,
                        "next_action": f"Patient has not completed {match['description'].lower()} yet; confirm it gets booked",
                    },
                )
            )
            continue
        proposals.append(
            _proposal(
                "record_patient_report",
                title=f"Record patient report: {match['description']}",
                rationale=(
                    f"Patient reported '{status_note}'. This is recorded as patient-reported evidence only; "
                    "the obligation remains open until verified."
                    + (f" Matched to {match['description']}: {'; '.join(ranked[0][2])}." if ranked and ranked[0][2] else "")
                ),
                obligation_id=match["id"],
                evidence_summary=evidence_summary,
                evidence_kind=EvidenceKind.patient_report.value,
                confidence=float(report.get("confidence") or 0.75),
                changes={
                    "status": ObligationStatus.conflict.value if conflict else ObligationStatus.patient_reported.value,
                    "reported_status": status_note,
                    "reported_date": reported_date.isoformat() if reported_date else None,
                    "next_action": (
                        "Resolve conflicting appointment date" if conflict else "Verify reported completion with the responsible provider"
                    ),
                },
            )
        )

        if not conflict and status_note in {"completed", "attended"}:
            proposals.append(
                _proposal(
                    "verify_completion",
                    title=f"Verify completion: {match['description']}",
                    rationale=(
                        "Patient reports completion. Closing the loop requires clinic confirmation or an "
                        "authorized coordinator override — a patient report alone is not verification."
                    ),
                    obligation_id=match["id"],
                    evidence_summary=evidence_summary,
                    evidence_kind=EvidenceKind.clinic_confirmation.value,
                    confidence=0.6,
                    changes={
                        "status": ObligationStatus.verified.value,
                        "next_action": "Clinic confirmation or coordinator override required",
                    },
                    approval_kind=ApprovalKind.verify_completion.value,
                    requires_approval=True,
                )
            )

    # 3. Explicit coordinator requests for sensitive actions.
    for action in extraction.get("requested_actions", []):
        kind = action.get("action", "none")
        if kind == "none":
            continue
        match, _ = _match(existing, action.get("obligation_hint", ""))
        if kind == "schedule_checkin":
            proposals.append(
                _proposal(
                    "schedule_checkin",
                    title="Schedule voice check-in",
                    rationale=action.get("rationale") or "Coordinator requested a follow-up call.",
                    confidence=0.8,
                    changes={"scheduled_for": add_days(today.isoformat(), 2), "reason": "Coordinator-requested check-in"},
                )
            )
            continue
        if not match:
            notes.append(f"Could not match a sensitive action to an obligation: {action.get('obligation_hint')}.")
            continue

        if kind == "verify_completion":
            proposals.append(
                _proposal(
                    "verify_completion",
                    title=f"Verify completion: {match['description']}",
                    rationale=action.get("rationale") or "Coordinator requested closure.",
                    obligation_id=match["id"],
                    evidence_summary=action.get("rationale") or "Coordinator override",
                    evidence_kind=EvidenceKind.coordinator_override.value,
                    confidence=0.8,
                    changes={"status": ObligationStatus.verified.value},
                    approval_kind=ApprovalKind.verify_completion.value,
                    requires_approval=True,
                    interrupt=input_type in {"chat", "coordinator"},
                )
            )
        elif kind == "cancel":
            proposals.append(
                _proposal(
                    "cancel_obligation",
                    title=f"Cancel obligation: {match['description']}",
                    rationale=action.get("rationale") or "Coordinator requested cancellation.",
                    obligation_id=match["id"],
                    evidence_summary=action.get("rationale") or "Coordinator override",
                    evidence_kind=EvidenceKind.coordinator_override.value,
                    confidence=0.8,
                    changes={"status": ObligationStatus.cancelled.value},
                    approval_kind=ApprovalKind.cancel.value,
                    requires_approval=True,
                    interrupt=input_type in {"chat", "coordinator"},
                )
            )
        elif kind == "escalate":
            proposals.append(
                _proposal(
                    "escalate",
                    title=f"Escalate: {match['description']}",
                    rationale=action.get("rationale") or "Coordinator requested escalation.",
                    obligation_id=match["id"],
                    evidence_summary=action.get("rationale") or "Escalation requested",
                    confidence=0.8,
                    changes={"status": ObligationStatus.escalated.value},
                    approval_kind=ApprovalKind.escalate.value,
                    requires_approval=True,
                    interrupt=input_type in {"chat", "coordinator"},
                )
            )

    # 4. Safety guardrails. Red-flag instructions inside a document are not
    # evidence that the patient has symptoms.
    safety = extraction.get("safety") or {}
    if input_type == "document":
        safety = {}
    if safety.get("urgent_symptoms") or safety.get("emergency"):
        proposals.append(
            _proposal(
                "escalate",
                title="Urgent clinical escalation",
                rationale=(
                    "Potentially urgent symptoms were described. Continuum does not assess symptoms; "
                    "emergency guidance was provided and a clinician review is required."
                ),
                evidence_summary=safety.get("summary") or "Urgent symptom language detected.",
                evidence_kind=EvidenceKind.patient_report.value,
                confidence=0.95,
                changes={
                    "status": ObligationStatus.escalated.value,
                    "urgency": "emergency" if safety.get("emergency") else "urgent",
                    "note": "Urgent symptom escalation",
                },
                approval_kind=ApprovalKind.escalate.value,
                requires_approval=True,
            )
        )
    elif safety.get("medication_question") or safety.get("clinical_question"):
        proposals.append(
            _proposal(
                "escalate",
                title="Clinical question — escalate to clinician",
                rationale=(
                    "The interaction contains a clinical or medication question. Continuum must not advise on "
                    "diagnosis, dosage or treatment; route to an authorized clinician."
                ),
                evidence_summary=safety.get("summary") or "Clinical question detected.",
                evidence_kind=EvidenceKind.patient_report.value,
                confidence=0.9,
                changes={
                    "status": ObligationStatus.escalated.value,
                    "urgency": "routine",
                    "note": "Clinical question requiring clinician response",
                },
                approval_kind=ApprovalKind.escalate.value,
                requires_approval=True,
            )
        )

    # 5. Uncertainty / extraction gaps.
    for note in extraction.get("uncertainty", []):
        proposals.append(
            _proposal(
                "note",
                title="Extraction uncertainty",
                rationale=note,
                confidence=0.4,
                changes={"kind": "uncertainty", "summary": note},
            )
        )

    return proposals, notes


def _keywords_for(category: str | None, description: str) -> list[str]:
    from .agent.extraction import CATEGORY_RULES

    for rule in CATEGORY_RULES:
        if rule["category"] == category:
            return rule["keywords"]
    return [w for w in description.lower().split() if len(w) > 4]


# ── Application ──────────────────────────────────────────────────────────


def apply_proposal(
    db: Database,
    proposal: dict,
    *,
    actor: str,
    approval_id: str | None = None,
    thread_id: str | None = None,
    run_id: str | None = None,
    note: str | None = None,
) -> dict:
    kind = proposal["kind"]
    changes = proposal.get("changes") or {}
    patient_id = proposal.get("patient_id")
    obligations = list(db.obligations.find({"patient_id": patient_id}, {"_id": 0})) if patient_id else []

    if kind == "create_obligation":
        obligation = repo.create_obligation(
            db,
            doc={
                "patient_id": patient_id,
                "episode_id": proposal.get("episode_id"),
                "description": changes["description"],
                "category": changes.get("category"),
                "owner": changes.get("owner", "Care team"),
                "due_date": changes.get("due_date"),
                "confidence": proposal.get("confidence", 0.7),
                "verification_required": changes.get("verification_required", True),
                "keywords": changes.get("keywords", []),
                "occurrence": changes.get("occurrence", 1),
                "source": {
                    "event_id": changes.get("event_id"),
                    "label": changes.get("source_label", "Care interaction"),
                    "quote": changes.get("quote"),
                },
                "thread_id": thread_id,
            },
            actor="Continuum" if not approval_id else actor,
            reason="Extracted follow-up requirement",
        )
        return obligation

    target = None
    if proposal.get("obligation_id"):
        target = repo.get_obligation(db, proposal["obligation_id"])
    if target is None and changes.get("description"):
        target, _ = repo.find_matching_obligation(db, patient_id, changes["description"])

    if kind == "note":
        repo.add_memory(
            db,
            patient_id=patient_id,
            kind=changes.get("kind", "uncertainty"),
            summary=changes.get("summary", proposal["title"]),
            content=proposal.get("rationale", ""),
            tags=["uncertainty", "agent"],
            importance=0.5,
        )
        return {"id": None}

    if kind == "schedule_checkin":
        checkin = repo.schedule_checkin(
            db,
            {
                "patient_id": patient_id,
                "scheduled_for": changes.get("scheduled_for"),
                "reason": changes.get("reason"),
                "created_by": actor,
            },
        )
        repo.audit(
            db,
            patient_id=patient_id,
            actor=actor,
            action="Scheduled voice check-in",
            detail=f"Check-in scheduled for {changes.get('scheduled_for')}",
            after=checkin,
            thread_id=thread_id,
            run_id=run_id,
        )
        repo.add_memory(
            db,
            patient_id=patient_id,
            kind="interaction",
            summary=f"Voice check-in scheduled for {changes.get('scheduled_for')}",
            content=changes.get("reason", ""),
            tags=["voice", "checkin", "scheduled"],
            importance=0.6,
        )
        return checkin

    if target is None and kind == "follow_up":
        item = changes.get("item", "requested item")
        repo.add_memory(
            db,
            patient_id=patient_id,
            kind="change",
            summary=f"Follow-up completed: sent {item}",
            content=proposal.get("rationale", ""),
            tags=["follow_up", "voice", "completed"],
            importance=0.6,
        )
        repo.audit(
            db,
            patient_id=patient_id,
            actor=actor,
            action="Follow-up task completed",
            detail=f"Sent {item} to patient (approved by coordinator).",
            after={"approval_id": approval_id},
            thread_id=thread_id,
            run_id=run_id,
        )
        return {"id": approval_id}

    if target is None and kind == "escalate":
        urgency = changes.get("urgency", "routine")
        repo.add_memory(
            db,
            patient_id=patient_id,
            kind="uncertainty",
            summary=f"Escalated to clinician ({urgency}): {truncate(proposal.get('evidence_summary', ''), 160)}",
            content=proposal.get("rationale", ""),
            tags=["escalation", urgency, "clinical review"],
            importance=0.95,
        )
        repo.audit(
            db,
            patient_id=patient_id,
            actor=actor,
            action="Escalated to clinician",
            detail=proposal.get("evidence_summary") or proposal.get("rationale", ""),
            after={"urgency": urgency, "approval_id": approval_id},
            thread_id=thread_id,
            run_id=run_id,
        )
        return {"id": approval_id}

    if target is None:
        repo.audit(
            db,
            patient_id=patient_id,
            actor=actor,
            action="Proposal not applied",
            detail=f"{proposal['title']}: no matching obligation",
            thread_id=thread_id,
            run_id=run_id,
        )
        return {"id": None}

    evidence = repo.add_evidence(
        db,
        patient_id=patient_id,
        obligation_id=target["id"],
        kind=proposal.get("evidence_kind", EvidenceKind.agent_note.value),
        summary=proposal.get("evidence_summary") or proposal.get("rationale", ""),
        source_ref=approval_id,
        quote=changes.get("quote"),
        confidence=proposal.get("confidence", 0.7),
        actor=actor,
    )

    if kind == "record_patient_update":
        update = {"last_patient_update": proposal.get("evidence_summary"), "updated_at": utcnow()}
        if changes.get("next_action"):
            update["next_action"] = changes["next_action"]
        db.obligations.update_one({"id": target["id"]}, {"$set": update})
        repo.add_memory(
            db,
            patient_id=patient_id,
            kind="interaction",
            summary=changes.get("memory_summary") or f"{target['description']}: patient says not done yet",
            content=proposal.get("evidence_summary", ""),
            tags=[*(changes.get("memory_tags") or ["patient_report", "not_completed"]), target.get("category") or ""],
            importance=0.6,
        )
        return repo.get_obligation(db, target["id"]) or target

    if kind == "record_patient_report":
        status = changes.get("status", ObligationStatus.patient_reported.value)
        updated = repo.set_obligation_status(
            db,
            target,
            status,
            actor,
            "Patient report recorded; verification pending" + (f" — {note}" if note else ""),
            next_action=changes.get("next_action"),
            thread_id=thread_id,
        )
        if changes.get("confirmed_date"):
            db.obligations.update_one(
                {"id": target["id"]},
                {"$set": {"confirmed_date": changes["confirmed_date"]}},
            )
        if changes.get("reported_date") or changes.get("reported_status"):
            db.obligations.update_one(
                {"id": target["id"]},
                {"$set": {"reported_date": changes.get("reported_date"), "reported_status": changes.get("reported_status")}},
            )
        return updated

    if kind == "flag_conflict":
        conflict = changes.get("conflict") or {}
        conflicts = list(target.get("conflicts") or [])
        if not any(c.get("reported_value") == conflict.get("reported_value") for c in conflicts):
            conflicts.append(conflict)
        updated = repo.set_obligation_status(
            db,
            target,
            ObligationStatus.conflict.value,
            "Continuum",
            f"Conflicting information flagged: {conflict.get('recorded_value')} vs {conflict.get('reported_value')}",
            conflicts=conflicts,
            next_action="Coordinator review of conflicting date",
            thread_id=thread_id,
        )
        repo.add_memory(
            db,
            patient_id=patient_id,
            kind="uncertainty",
            summary=(
                f"Conflict on {target['description']}: record {conflict.get('recorded_value')} "
                f"vs report {conflict.get('reported_value')}"
            ),
            content=proposal.get("rationale", ""),
            tags=["conflict", "unverified"],
            importance=0.9,
        )
        return updated

    if kind == "resolve_conflict":
        conflict = changes.get("conflict_resolution") or {}
        conflicts = list(target.get("conflicts") or [])
        for c in conflicts:
            if not c.get("resolved"):
                c["resolved"] = True
                c["resolved_at"] = conflict.get("resolved_at") or utcnow()
                c["kept"] = conflict.get("kept")
                c["superseded"] = conflict.get("superseded")
                c["resolved_by"] = actor
        updated = repo.set_obligation_status(
            db,
            target,
            changes.get("status", ObligationStatus.scheduled.value),
            actor,
            changes.get("resolution") or "Conflict resolved by coordinator",
            due_date=changes.get("confirmed_date"),
            conflicts=conflicts,
            next_action=None,
            thread_id=thread_id,
        )
        db.obligations.update_one(
            {"id": target["id"]},
            {
                "$set": {
                    "confirmed_date": changes.get("confirmed_date") or target.get("confirmed_date"),
                    "confirmed_source": changes.get("confirmed_source") or "clinic record",
                }
            },
        )
        repo.add_memory(
            db,
            patient_id=patient_id,
            kind="change",
            summary=f"Resolved conflict on {target['description']}: kept {conflict.get('kept')}",
            content=proposal.get("rationale", ""),
            tags=["conflict", "resolved"],
            importance=0.8,
        )
        return updated

    if kind == "verify_completion":
        updated = repo.set_obligation_status(
            db,
            target,
            ObligationStatus.verified.value,
            actor,
            f"Verified with evidence: {proposal.get('evidence_summary')}",
            next_action=None,
            thread_id=thread_id,
        )
        repo.add_memory(
            db,
            patient_id=patient_id,
            kind="care_obligation",
            summary=f"Verified completion: {target['description']}",
            content=proposal.get("evidence_summary", ""),
            tags=["verified", "evidence"],
            importance=0.8,
        )
        return updated

    if kind == "schedule":
        requested_time = changes.get("requested_time") or ""
        parsed = parse_date(requested_time)
        confirmed_date = parsed.isoformat() if parsed else None
        updated = repo.set_obligation_status(
            db,
            target,
            ObligationStatus.scheduled.value,
            actor,
            f"Appointment booked for {requested_time or 'a pending time'} — clinic confirmed via coordinator.",
            due_date=confirmed_date or target.get("due_date"),
            next_action=None,
            thread_id=thread_id,
        )
        db.obligations.update_one(
            {"id": target["id"]},
            {
                "$set": {
                    "confirmed_date": confirmed_date or target.get("confirmed_date"),
                    "confirmed_source": "clinic confirmed (voice request approved)",
                }
            },
        )
        repo.add_memory(
            db,
            patient_id=patient_id,
            kind="change",
            summary=f"Booked appointment: {target['description']} for {requested_time or 'pending time'}",
            content=changes.get("notes", ""),
            tags=["appointment", "scheduled", "voice"],
            importance=0.8,
        )
        return updated

    if kind == "cancel_obligation":
        return repo.set_obligation_status(
            db,
            target,
            ObligationStatus.cancelled.value,
            actor,
            f"Cancelled by authorized override: {proposal.get('evidence_summary')}",
            next_action=None,
            thread_id=thread_id,
        )

    if kind == "escalate":
        urgency = changes.get("urgency", "routine")
        updated = repo.set_obligation_status(
            db,
            target,
            ObligationStatus.escalated.value,
            actor,
            f"Escalated ({urgency}): {proposal.get('evidence_summary')}",
            next_action="Awaiting clinician review",
            thread_id=thread_id,
        )
        repo.add_memory(
            db,
            patient_id=patient_id,
            kind="uncertainty",
            summary=f"Escalated {target['description']} ({urgency})",
            content=proposal.get("rationale", ""),
            tags=["escalation", urgency],
            importance=0.95,
        )
        return updated

    return {"id": None}


def apply_approval(
    db: Database,
    approval: dict,
    *,
    approved: bool,
    actor: str,
    note: str | None,
) -> dict | None:
    if approval.get("applied_at"):
        return None
    proposal = approval.get("proposal") or {}
    proposal["patient_id"] = approval["patient_id"]
    if not approved:
        repo.add_memory(
            db,
            patient_id=approval["patient_id"],
            kind="change",
            summary=f"Coordinator rejected: {approval.get('title')}",
            content=note or "Proposal rejected",
            tags=["approval", "rejected"],
            importance=0.7,
        )
        return None
    result = apply_proposal(
        db,
        proposal,
        actor=actor,
        approval_id=approval["id"],
        thread_id=approval.get("thread_id"),
        run_id=approval.get("run_id"),
        note=note,
    )
    repo.mark_approval_applied(db, approval["id"])
    return result
