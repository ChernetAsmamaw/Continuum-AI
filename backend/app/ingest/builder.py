"""Turn a ``DischargePacket`` into a tracked patient, episode and follow-ups.

Order of operations:

1. **Identity.** Exact EHR id / MRN → reuse. A single name + birth-date match
   → reuse. Several candidates → never merge: create a new record flagged for
   coordinator review (a wrong-patient merge is the worst failure here).
2. **Structured follow-ups** from orders, booked appointments, pending results
   and new medications, each carrying ``fhir_refs`` back to the EHR so results
   can be matched exactly later.
3. **Narrative extraction** of the discharge summary through the existing
   agent graph. The engine already skips any category that is tracked, so the
   AI only adds what the structured data missed.

Re-ingesting the same encounter adds only orders that are new since the last
pull; nothing is duplicated and the narrative is not re-extracted.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta

from pymongo.database import Database

from .. import repository as repo
from ..agent.extraction import CATEGORY_RULES, any_term
from ..db import demo_today
from ..schemas import EvidenceKind, MemoryKind, ObligationStatus, PatientIntakeRequest
from ..util import new_id, parse_date, truncate, utcnow
from .packet import DischargePacket

log = logging.getLogger("continuum.ingest.builder")

ACTOR = "EHR ingestion"


# ── planning (pure) ──────────────────────────────────────────────────────


def _category(text: str) -> str | None:
    low = text.lower()
    for rule in CATEGORY_RULES:
        if any_term(rule["keywords"], low):
            return rule["category"]
    return None


def _owner_for(category: str | None) -> str | None:
    return next((r["owner"] for r in CATEGORY_RULES if r["category"] == category), None)


def _default_days(category: str | None, fallback: int) -> int:
    return next((r["default_days"] for r in CATEGORY_RULES if r["category"] == category), fallback)


def _plus(day: str | None, days: int) -> str:
    base = parse_date(day) or demo_today()
    return (base + timedelta(days=days)).isoformat()


def recognized(order) -> bool:
    """Do the built-in rules understand this order on their own?"""
    return order.kind != "other" or _category(f"{order.display} {order.performer or ''}") is not None


def plan_followups(packet: DischargePacket, classify=None) -> list[dict]:
    """Structured follow-ups for a packet.

    ``classify(order) -> {kind, category, method, ...} | None`` is consulted
    only for orders the rules don't recognize (e.g. a hospital switched to
    local codes). Without it, or when it finds nothing, the order still
    becomes a follow-up, flagged for a human to confirm what it is.
    """
    discharge = packet.encounter.discharge_date
    plans: list[dict] = []
    appt_for_order = {b: a for a in packet.appointments for b in a.based_on}
    used_appointments: set[str] = set()

    for order in packet.orders:
        label = f"{order.display} {order.performer or ''}"
        classification = None
        if not recognized(order):
            classification = classify(order) if classify else None
            if classification:
                order = order.model_copy(update={"kind": classification["kind"], "performer": order.performer or classification.get("performer")})
                label = f"{order.display} {order.performer or ''}"
        if classification and order.kind not in ("lab", "imaging"):
            category = classification["category"]
            description = f"{order.performer} follow-up" if order.performer else order.display
            owner = (f"{order.performer} clinic" if order.performer else None) or _owner_for(category) or "Care team"
        elif order.kind == "lab":
            category = "bloodwork" if _category(label) in (None, "bloodwork") else _category(label)
            description = order.display
            owner = "Laboratory"
        elif order.kind == "imaging":
            category = "diagnostic_imaging"
            description = order.display
            owner = "Medical imaging"
        else:
            category = _category(label) or ("referral" if order.kind == "referral" else "follow_up")
            description = f"{order.performer} follow-up" if order.performer else order.display
            owner = (f"{order.performer} clinic" if order.performer else None) or _owner_for(category) or "Care team"
        plan = {
            "key": order.ref,
            "kind": f"order:{order.kind}",
            "description": description,
            "category": category,
            "owner": owner,
            "due_date": order.due_date or _plus(discharge, _default_days(category, 14)),
            "status": ObligationStatus.pending.value,
            "fhir_refs": [order.ref],
            "evidence_ref": order.version_ref,
            "evidence_summary": f"Ordered at discharge: {order.display}"
            + (f" ({order.performer})" if order.performer else "")
            + (f", due {order.due_date}" if order.due_date else ""),
            "quote": order.note or order.display,
        }
        if classification and classification.get("confirmed"):
            plan["classified_by"] = classification  # kept for provenance; nothing left to confirm
        elif classification:
            plan["classified_by"] = classification
            plan["next_action"] = f"Order type inferred from a similar past order ({classification['method']}) — confirm"
        elif not recognized(order):
            plan["classified_by"] = {"method": "unrecognized"}
            plan["next_action"] = "Unrecognized order type — confirm what this follow-up is"
        appt = appt_for_order.get(order.ref)
        if appt and appt.start:
            used_appointments.add(appt.ref)
            plan.update(
                status=ObligationStatus.scheduled.value,
                due_date=appt.start,
                confirmed_date=appt.start,
                confirmed_source=appt.version_ref,
                appointment_ref=appt.ref,
                appointment_version_ref=appt.version_ref,
            )
            plan["fhir_refs"].append(appt.ref)
            plan["evidence_summary"] += f"; booked {appt.start}" + (f" with {appt.practitioner}" if appt.practitioner else "")
        plans.append(plan)

    for appt in packet.appointments:
        if appt.ref in used_appointments or not appt.start:
            continue
        category = _category(appt.service) or "follow_up"
        plans.append(
            {
                "key": appt.ref,
                "kind": "appointment",
                "description": appt.service,
                "category": category,
                "owner": appt.practitioner or _owner_for(category) or "Care team",
                "due_date": appt.start,
                "status": ObligationStatus.scheduled.value,
                "confirmed_date": appt.start,
                "confirmed_source": appt.version_ref,
                "fhir_refs": [appt.ref],
                "evidence_ref": appt.version_ref,
                "evidence_summary": f"Appointment booked in the EHR: {appt.service} on {appt.start}",
                "quote": appt.service,
            }
        )

    for result in packet.pending_results:
        plans.append(
            {
                "key": result.ref,
                "kind": "pending_result",
                "description": f"Review pending result: {result.display}",
                # Imaging shares the extractor's category so a narrative line like
                # "echocardiogram report pending" isn't tracked a second time.
                "category": "diagnostic_imaging" if result.kind == "imaging" else "pending_result",
                "owner": packet.encounter.attending or "Discharging clinician",
                "due_date": _plus(discharge, 3),
                "status": ObligationStatus.pending.value,
                "fhir_refs": [result.ref, *result.based_on],
                "evidence_ref": result.version_ref,
                "evidence_summary": f"Result not final at discharge ({result.status}): {result.display}",
                "quote": result.display,
                "next_action": "Result pending at discharge — review when final",
            }
        )

    if packet.medications:
        names = ", ".join(m.display for m in packet.medications)
        home = ", ".join(m.display for m in packet.home_medications)
        plans.append(
            {
                "key": packet.medications[0].ref,
                "kind": "medication_reconciliation",
                "description": "Medication reconciliation after discharge changes",
                "category": "medication_reconciliation",
                "owner": "Clinician",
                # Re-dated to the TCM visit deadline once the episode exists.
                "due_date": _plus(discharge, 7),
                "status": ObligationStatus.pending.value,
                "fhir_refs": [m.ref for m in packet.medications] + [m.ref for m in packet.home_medications],
                "evidence_ref": packet.medications[0].version_ref,
                "evidence_summary": (
                    f"New or changed at discharge: {names}"
                    + (f". Home list on file: {home}" if home else ". No home medication list on file: ask the patient.")
                ),
                "quote": names,
            }
        )
    return plans


# ── identity ─────────────────────────────────────────────────────────────


def _norm(text: str | None) -> str:
    return " ".join((text or "").lower().replace(".", " ").split())


def match_patient(db: Database, packet: DischargePacket) -> tuple[dict | None, str, list[dict]]:
    """Return (patient, how, other_candidates)."""
    p = packet.patient
    if p.fhir_id:
        found = db.patients.find_one({"ehr.fhir_id": p.fhir_id, "ehr.fhir_base": packet.fhir_base}, {"_id": 0})
        if found:
            return found, "ehr_id", []
    values = [i.value for i in p.identifiers if (i.type or "").upper() not in {"SS", "SB"}]  # never key on national ids
    if values:
        # One query for every identifier; the first identifier (in EHR order) that matches wins, as before.
        hits = list(db.patients.find({"$or": [{"mrn": {"$in": values}}, {"ehr.identifiers.value": {"$in": values}}]}, {"_id": 0}))
        for value in values:
            found = next(
                (h for h in hits if h.get("mrn") == value or value in [i.get("value") for i in (h.get("ehr") or {}).get("identifiers") or []]),
                None,
            )
            if found:
                return found, "identifier", []
    # Scan only what the name/birth-date comparison needs; fetch the full
    # record for the one candidate that is returned as a match.
    candidates = [
        c
        for c in db.patients.find({}, {"_id": 0, "id": 1, "name": 1, "birth_date": 1})
        if _norm(c.get("name")) == _norm(p.name)
        and (not p.birth_date or not c.get("birth_date") or c.get("birth_date") == p.birth_date)
    ]
    if len(candidates) == 1 and p.birth_date and candidates[0].get("birth_date") == p.birth_date:
        found = db.patients.find_one({"id": candidates[0]["id"]}, {"_id": 0})
        if found:
            return found, "name_birth_date", []
    return None, "new", candidates


def _age(birth_date: str | None, today: date) -> int | None:
    born = parse_date(birth_date)
    if not born:
        return None
    return today.year - born.year - ((today.month, today.day) < (born.month, born.day))


# ── ingest ───────────────────────────────────────────────────────────────


def _has_followup_content(packet: DischargePacket) -> bool:
    # Only what belongs to this stay counts. Active prescriptions and upcoming
    # appointments are found by patient and date, not by stay, so any guessed
    # encounter "has" them.
    return bool(packet.orders or packet.pending_results or packet.discharge_summary_text)


def _replaces_active(db: Database, patient_id: str, packet: DischargePacket) -> bool:
    """Should this stay become the patient's active episode?

    Not when it carries nothing to follow up (e.g. a pull without an encounter
    id guessed a stay with no discharge orders) and not when it was discharged
    before the episode already being worked.
    """
    active = list(db.care_episodes.find({"patient_id": patient_id, "status": "active"}, {"_id": 0, "discharge_date": 1}))
    if not active:
        return True
    if not _has_followup_content(packet):
        return False
    new_end = parse_date(packet.encounter.discharge_date)
    latest = max((d for d in (parse_date(e.get("discharge_date")) for e in active) if d), default=None)
    return not (new_end and latest and new_end < latest)


def _create_episode(db: Database, patient_id: str, packet: DischargePacket, actor: str) -> str:
    episode_id = new_id("ep")
    status = "active"
    if _replaces_active(db, patient_id, packet):
        db.care_episodes.update_many({"patient_id": patient_id, "status": "active"}, {"$set": {"status": "superseded"}})
    elif db.care_episodes.count_documents({"patient_id": patient_id, "status": "active"}, limit=1):
        # An older stay, or one with nothing to follow up: record it, but keep
        # the patient's current episode as the one being worked.
        status = "historical"
        log.info("Stay %s for patient %s does not replace the active episode; filed as historical.", packet.encounter.ref, patient_id)
    db.care_episodes.insert_one(
        {
            "id": episode_id,
            "patient_id": patient_id,
            "admission_date": packet.encounter.admit_date or packet.encounter.discharge_date,
            "discharge_date": packet.encounter.discharge_date,
            "reason": packet.encounter.reason or "Hospitalisation",
            "status": status,
            "discharge_summary": packet.discharge_summary_text or "",
            "created_at": utcnow(),
        }
    )
    repo.add_event(
        db,
        {
            "patient_id": patient_id,
            "episode_id": episode_id,
            "type": "intake",
            "title": "New hospital discharge received from the EHR",
            "detail": f"Discharged {packet.encounter.discharge_date}; reason: {packet.encounter.reason or 'unspecified'}.",
            "actor": actor,
            "source_ref": packet.encounter.version_ref,
        },
    )
    return episode_id


def step_identify(db: Database, packet: DischargePacket, actor: str = ACTOR) -> dict:
    """Find or create the patient and this stay's episode. Safe to re-run."""
    from ..workflow import intake_patient  # late import: workflow pulls in the agent graph

    today = demo_today()
    existing_episode = db.care_episodes.find_one(
        {"ehr.encounter_ref": packet.encounter.ref, "ehr.fhir_base": packet.fhir_base}, {"_id": 0}
    )
    review_candidates: list[dict] = []
    if existing_episode:
        patient = repo.get_patient(db, existing_episode["patient_id"])
        episode_id = existing_episode["id"]
        how, outcome = "existing_episode", "updated"
    else:
        patient, how, review_candidates = match_patient(db, packet)
        if patient:
            episode_id = _create_episode(db, patient["id"], packet, actor)
            outcome = "new_episode"
        else:
            req = PatientIntakeRequest(
                name=packet.patient.name,
                age=_age(packet.patient.birth_date, today),
                mrn=packet.patient.mrn,
                condition=packet.encounter.reason,
                risk="High" if "heart failure" in (packet.encounter.reason or "").lower() else "Medium",
                phone=packet.patient.phone,
                admission_date=packet.encounter.admit_date,
                discharge_date=packet.encounter.discharge_date,
                reason=packet.encounter.reason,
                language=packet.patient.language or "English",
                birth_date=packet.patient.birth_date,
                # Identity was already decided above (EHR id, MRN, name + DOB review).
                confirm_new=True,
            )
            created = intake_patient(req, actor=actor, start_tcm=False)
            patient = created["patient"]
            episode_id = created["episode_id"]
            outcome = "new_patient"
        db.care_episodes.update_one(
            {"id": episode_id},
            {
                "$set": {
                    "ehr": {
                        "fhir_base": packet.fhir_base,
                        "encounter_ref": packet.encounter.ref,
                        "encounter_version_ref": packet.encounter.version_ref,
                        "facility": packet.encounter.facility,
                        "attending": packet.encounter.attending,
                        "disposition": packet.encounter.disposition,
                    },
                    "discharge_summary": packet.discharge_summary_text or "",
                }
            },
        )

    patient_id = patient["id"]
    patient_update: dict = {
        "ehr.fhir_base": packet.fhir_base,
        "ehr.fhir_id": packet.patient.fhir_id,
        "ehr.identifiers": [i.model_dump() for i in packet.patient.identifiers if (i.type or "").upper() not in {"SS", "SB"}],
        "birth_date": packet.patient.birth_date,
    }
    if review_candidates:
        patient_update["identity_review"] = {
            "status": "needed",
            "candidates": [c["id"] for c in review_candidates],
            "reason": "Same name as existing patient(s) without a matching EHR identifier or birth date.",
        }
    db.patients.update_one({"id": patient_id}, {"$set": patient_update})
    if review_candidates:
        repo.add_event(
            db,
            {
                "patient_id": patient_id,
                "type": "system",
                "title": "Identity review needed",
                "detail": f"Possible duplicate of {', '.join(c['name'] + ' [' + c['id'] + ']' for c in review_candidates)}; records were not merged.",
                "actor": actor,
                "source_ref": packet.source_ref,
            },
        )
    return {
        "patient_id": patient_id,
        "episode_id": episode_id,
        "outcome": outcome,
        "matched_by": how,
        "identity_review": bool(review_candidates),
    }


def step_structured(db: Database, packet: DischargePacket, state: dict, actor: str = ACTOR) -> dict:
    """Create follow-ups from structured EHR records. Idempotent per EHR reference."""
    from .orders import classifier_for, remember_orders

    patient_id, episode_id = state["patient_id"], state["episode_id"]
    created_ids: list[str] = []
    skipped: list[str] = []
    plans = plan_followups(packet, classify=classifier_for(db, packet.fhir_base))
    # One query for "already tracked?" instead of one per plan. Refs claimed by
    # a plan created earlier in this run count too, as they would sequentially.
    keys = [plan["key"] for plan in plans]
    tracked = {
        ref
        for o in db.obligations.find({"patient_id": patient_id, "fhir_refs": {"$in": keys}}, {"_id": 0, "fhir_refs": 1})
        for ref in o.get("fhir_refs") or []
    } if keys else set()
    items: list[dict] = []
    for plan in plans:
        if plan["key"] in tracked:
            skipped.append(plan["key"])
            continue
        tracked.update(plan["fhir_refs"])
        doc = {
            "patient_id": patient_id,
            "episode_id": episode_id,
            "description": plan["description"],
            "category": plan["category"],
            "occurrence": 1,
            "owner": plan["owner"],
            "due_date": plan["due_date"],
            "status": plan["status"],
            "keywords": [w.lower() for w in plan["description"].split() if len(w) > 3][:8],
            "source": {"event_id": None, "label": f"EHR · {plan['key']}", "quote": plan["quote"]},
            "origin": "ehr_structured",
            "ehr_kind": plan["kind"],
            "fhir_refs": plan["fhir_refs"],
            "confidence": 0.75 if plan.get("classified_by") and not plan["classified_by"].get("confirmed") else 0.95,
            "latest_evidence": "",
        }
        for key in ("confirmed_date", "confirmed_source", "next_action", "classified_by"):
            if plan.get(key):
                doc[key] = plan[key]
        evidence = [
            {
                "kind": EvidenceKind.document.value,
                "summary": plan["evidence_summary"],
                "source_ref": plan["evidence_ref"],
                "quote": plan["quote"],
                "confidence": 0.95,
            }
        ]
        if plan.get("appointment_version_ref"):
            evidence.append(
                {
                    "kind": EvidenceKind.clinic_confirmation.value,
                    "summary": f"Clinic booking in the EHR for {plan['due_date']}",
                    "source_ref": plan["appointment_version_ref"],
                    "confidence": 0.95,
                }
            )
        items.append({"key": plan["key"], "doc": doc, "reason": f"Created from EHR record {plan['key']}", "evidence": evidence})
    created_ids = [o["id"] for o in repo.create_obligations_bulk(db, items, actor=actor)]
    remember_orders(db, packet, plans)
    return {"created": created_ids, "skipped": skipped, "planned": [p["key"] for p in plans]}


def step_narrative(db: Database, packet: DischargePacket, state: dict, actor: str = ACTOR) -> dict:
    """Extract follow-ups the orders missed from the discharge narrative (once per stay)."""
    from ..workflow import ingest_document

    patient_id, episode_id = state["patient_id"], state["episode_id"]
    episode = db.care_episodes.find_one({"id": episode_id}, {"_id": 0, "narrative_extracted_at": 1}) or {}
    if state.get("outcome") == "updated" or not packet.discharge_summary_text or episode.get("narrative_extracted_at"):
        return {"ai_created": [], "extraction_status": "skipped"}
    before = {o["id"] for o in db.obligations.find({"patient_id": patient_id}, {"id": 1})}
    extraction = ingest_document(
        patient_id=patient_id,
        title="Discharge summary (EHR)",
        text=packet.discharge_summary_text,
        source_ref=packet.discharge_summary_ref,
        actor=actor,
    )
    ai_created = [o["id"] for o in db.obligations.find({"patient_id": patient_id}, {"id": 1}) if o["id"] not in before]
    if ai_created:
        db.obligations.update_many({"id": {"$in": ai_created}}, {"$set": {"origin": "ai_extracted", "episode_id": episode_id}})
    restated = retire_restated_orders(db, episode_id, ai_created, actor=actor)
    ai_created = [i for i in ai_created if i not in {r["ai"] for r in restated}]
    linked = link_possible_duplicates(db, episode_id, ai_created, actor=actor)
    db.care_episodes.update_one({"id": episode_id}, {"$set": {"narrative_extracted_at": utcnow()}})
    return {"ai_created": ai_created, "extraction_status": (extraction or {}).get("status"), "linked": linked,
            "restated": restated}


# Categories the structured builder files orders under, beyond CATEGORY_RULES.
STRUCTURED_ONLY_CATEGORIES = {"pending_result", "medication_reconciliation"}
# Several distinct follow-ups of these kinds can coexist (two tests, two scans).
MULTI_INSTANCE = {"pending_result", "bloodwork", "diagnostic_imaging"}


def canonical_category(category: str | None, text: str) -> str | None:
    """Map a narrative follow-up's category onto the vocabulary the orders use.

    An LLM extractor does not always use the rule categories ("primary_care",
    "lab_review"), and the engine de-duplicates by exact category, so a
    narrative item can restate an EHR order under another name.
    """
    known = {r["category"] for r in CATEGORY_RULES} | STRUCTURED_ONLY_CATEGORIES
    if category in known:
        return category
    low = f"{(category or '').replace('_', ' ')} {text}".lower()
    if "pending" in low and "result" in low:
        return "pending_result"
    if "reconcil" in low:
        return "medication_reconciliation"
    return _category(low) or category


def _order_text(o: dict) -> str:
    return f"{o.get('description', '')} {(o.get('source') or {}).get('quote') or ''}"


def retire_restated_orders(db: Database, episode_id: str, ai_ids: list[str], *, actor: str = ACTOR) -> list[dict]:
    """Cancel narrative follow-ups that restate an EHR order already tracked.

    Only settled orders count (recognized, or confirmed by a coordinator); a
    narrative item that may match an unsettled order is left to
    ``link_possible_duplicates`` and the coordinator. Kept narrative items get
    the canonical category so later documents de-duplicate against them.
    Nothing is deleted: a restated item is cancelled with a reason and
    ``duplicate_of``, so the audit trail still shows what the narrative said.
    """
    if not ai_ids:
        return []
    from ..util import similarity

    structured = [
        o for o in db.obligations.find({"episode_id": episode_id, "origin": "ehr_structured"}, {"_id": 0})
        if not (o.get("classified_by") and not o["classified_by"].get("confirmed"))
    ]
    retired: list[dict] = []
    for ai in db.obligations.find({"id": {"$in": ai_ids}}, {"_id": 0}):
        text = _order_text(ai)
        category = canonical_category(ai.get("category"), text)
        candidates = [o for o in structured if o.get("category") == category]
        match = None
        if len(candidates) == 1 and (category not in MULTI_INSTANCE or similarity(text, _order_text(candidates[0])) >= 0.15):
            match = candidates[0]
        elif len(candidates) > 1:
            score, best = max(((similarity(text, _order_text(o)), o) for o in candidates), key=lambda x: x[0])
            match = best if score >= 0.25 else None
        if category != ai.get("category"):
            db.obligations.update_one(
                {"id": ai["id"]}, {"$set": {"category": category, "extracted_category": ai.get("category")}}
            )
            ai = {**ai, "category": category}
        if not match:
            continue
        label = match.get("description", "EHR order")
        db.obligations.update_one({"id": ai["id"]}, {"$set": {"duplicate_of": match["id"]}})
        repo.set_obligation_status(
            db,
            ai,
            ObligationStatus.cancelled.value,
            actor,
            f"The discharge narrative restates EHR order “{label}” [{match['id']}], already tracked "
            "from structured data.",
            next_action=f"Tracked as “{label}”",
        )
        retired.append({"ai": ai["id"], "order": match["id"], "category": category})
    return retired


def _days_apart(a: str | None, b: str | None) -> int | None:
    da, db_ = parse_date(a), parse_date(b)
    return abs((da - db_).days) if da and db_ else None


def link_possible_duplicates(db: Database, episode_id: str, ai_ids: list[str], *, actor: str = ACTOR) -> list[dict]:
    """Pair narrative follow-ups with EHR orders whose type is still unsettled.

    The extractor de-duplicates by category, so an EHR order the rules didn't
    recognize (filed as a generic follow-up) is tracked a second time when the
    narrative names it ("Heart function clinic consult" vs "Cardiology
    follow-up within 2 weeks"). Nothing is dropped on a guess: the AI item is
    marked as a possible duplicate, the order gets the narrative's category as
    a suggestion, and confirming the order type resolves both.
    """
    if not ai_ids:
        return []
    from ..util import similarity

    structured = list(db.obligations.find({"episode_id": episode_id, "origin": "ehr_structured"}, {"_id": 0}))
    unsettled = [
        o for o in structured
        if o.get("classified_by") and not o["classified_by"].get("confirmed") and not o.get("suggested_category")
    ]
    if not unsettled:
        return []
    settled_categories = {o.get("category") for o in structured if o not in unsettled}
    links: list[dict] = []
    for ai in db.obligations.find({"id": {"$in": ai_ids}}, {"_id": 0}):
        if ai.get("category") in settled_categories:
            continue  # an EHR order of that type already exists; not ours to pair
        best, best_score = None, 0.0
        for order in unsettled:
            score = similarity(
                f"{ai.get('description', '')} {(ai.get('source') or {}).get('quote', '')}",
                f"{order.get('description', '')} {(order.get('source') or {}).get('quote', '')} {order.get('owner', '')}",
            )
            apart = _days_apart(ai.get("due_date"), order.get("due_date"))
            if apart is not None and apart <= 3:
                score += 0.5
            if score > best_score:
                best, best_score = order, score
        if not best or best_score < 0.3:
            continue
        unsettled.remove(best)
        label = best.get("description", "EHR order")
        db.obligations.update_one(
            {"id": ai["id"]},
            {"$set": {"possible_duplicate_of": best["id"],
                      "next_action": f"May be the same as EHR order \u201c{label}\u201d — confirm that order's type"}},
        )
        db.obligations.update_one(
            {"id": best["id"]},
            {"$set": {"suggested_category": ai.get("category"), "suggested_by": ai["id"]}},
        )
        repo.audit(
            db,
            actor=actor,
            action="Linked possible duplicate",
            patient_id=ai.get("patient_id"),
            detail=(f"Narrative follow-up \u201c{ai.get('description')}\u201d may be EHR order \u201c{label}\u201d "
                    f"({best['id']}), whose type is unconfirmed; suggested {ai.get('category')}."),
            after={"obligation_id": ai["id"], "possible_duplicate_of": best["id"], "suggested_category": ai.get("category")},
        )
        links.append({"ai": ai["id"], "order": best["id"], "category": ai.get("category")})
    return links


class VerificationError(RuntimeError):
    pass


def step_verify(
    db: Database, packet: DischargePacket, state: dict, actor: str = ACTOR, *, job_id: str | None = None
) -> dict:
    """Read back what was written. Only a follow-up that is actually in the
    database counts; an intended write that didn't land fails the job."""
    patient_id = state["patient_id"]
    planned = state.get("planned", [])
    found = {
        ref
        for o in db.obligations.find({"patient_id": patient_id, "fhir_refs": {"$in": planned}}, {"_id": 0, "fhir_refs": 1})
        for ref in o.get("fhir_refs") or []
    } if planned else set()
    missing = [k for k in planned if k not in found]
    if missing:
        raise VerificationError(f"{len(missing)} planned follow-up(s) not found after writing: {', '.join(missing[:5])}")
    created, ai_created, skipped = state.get("created", []), state.get("ai_created", []), state.get("skipped", [])
    summary = (
        f"{len(created)} structured follow-up(s)"
        + (f", {len(ai_created)} from the discharge narrative" if ai_created else "")
        + (f", {len(skipped)} already tracked" if skipped else "")
    )
    already = db.ingest_runs.find_one({"job_id": job_id}, {"_id": 0}) if job_id else None
    if already:
        run = already
    else:
        if created or ai_created:
            repo.add_event(
                db,
                {
                    "patient_id": patient_id,
                    "episode_id": state["episode_id"],
                    "type": "system",
                    "title": "Follow-ups built from the EHR",
                    "detail": summary + ".",
                    "actor": actor,
                    "source_ref": packet.source_ref,
                },
            )
            repo.add_memory(
                db,
                patient_id=patient_id,
                kind=MemoryKind.care_event,
                summary=f"Discharge {packet.encounter.discharge_date} received from the EHR: {summary}.",
                content=truncate(packet.discharge_summary_text or packet.encounter.reason or "", 1500),
                tags=["ehr", "discharge"],
                importance=0.8,
            )
        run = {
            "id": new_id("ing"),
            "job_id": job_id,
            "at": utcnow(),
            "source": packet.source,
            "source_ref": packet.source_ref,
            "fhir_base": packet.fhir_base,
            "patient_id": patient_id,
            "episode_id": state["episode_id"],
            "encounter_ref": packet.encounter.ref,
            "outcome": state["outcome"],
            "matched_by": state["matched_by"],
            "created": created,
            "ai_created": ai_created,
            "skipped": skipped,
            "warnings": packet.warnings,
            "verified": True,
        }
        db.ingest_runs.insert_one(dict(run))
        log.info("Ingested %s for %s: %s", packet.encounter.ref, patient_id, summary)
    run = {k: v for k, v in run.items() if k != "_id"}
    from .. import tcm

    tcm.start_episode(db, patient_id, actor=actor, today=demo_today())
    return {
        **run,
        "summary": summary,
        "identity_review": state.get("identity_review", False),
        "obligations": repo.list_obligations(db, patient_id, today=demo_today()),
        "extraction_status": state.get("extraction_status"),
    }


def ingest_packet(db: Database, packet: DischargePacket, actor: str = ACTOR) -> dict:
    """All steps in one go (no job record). The job runner calls them one at a time."""
    state = step_identify(db, packet, actor)
    state.update(step_structured(db, packet, state, actor))
    state.update(step_narrative(db, packet, state, actor))
    return step_verify(db, packet, state, actor)
