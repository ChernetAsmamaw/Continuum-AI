"""Synthetic demo data.

All people, phone numbers and clinical details here are fictional. The demo
uses a single 30-day heart-failure scenario plus three additional synthetic
patients so the dashboard has realistic breadth.
"""

from __future__ import annotations

from datetime import date, timedelta

from pymongo.database import Database

from .db import demo_today
from .schemas import ApprovalKind, EvidenceKind, MemoryKind, ObligationStatus
from .util import add_days, new_id, utcnow
from .agent.extraction import CATEGORY_RULES

MICHAEL_ID = "pat_michael"
MICHAEL_EPISODE_ID = "ep_michael_1"


def _keywords(category: str) -> list[str]:
    for rule in CATEGORY_RULES:
        if rule["category"] == category:
            return rule["keywords"]
    return []


def _iso(d: date) -> str:
    return d.isoformat()


def discharge_text(discharge: str) -> str:
    return f"""DISCHARGE SUMMARY — SYNTHETIC DEMO RECORD
Heart failure with reduced ejection fraction

Patient: Michael Chen
Age: 64
MRN: C-10482
Discharge date: {discharge}
Discharging service: Cardiology, Toronto General (synthetic)

Summary:
Mr. Chen was admitted with decompensated heart failure and improved with diuresis.
He is stable for discharge home. He lives alone; his daughter is the preferred caregiver.

Follow-up plan:
- Family physician follow-up within 7 days.
- Bloodwork (creatinine and electrolytes) within 2 weeks.
- Cardiology follow-up within 4 weeks.
- Home-care assessment to be arranged.

Medication changes were made during admission. The patient was advised to weigh himself
daily and follow a low-sodium diet. He should seek urgent care for worsening shortness of
breath, chest pain, or rapid weight gain.

Synthetic note: this document exists only for a hackathon demonstration. It is not a real
medical record.
"""


def day3_transcript(discharge: str, today: date) -> list[dict]:
    fp_visit = (date.fromisoformat(discharge) + timedelta(days=4)).isoformat()
    return [
        {
            "role": "agent",
            "message": "Hi Michael, this is Continuum calling on behalf of your care team. This is an AI assistant and this call is being recorded. Is now still a good time?",
            "time_in_call_secs": 0,
        },
        {
            "role": "user",
            "message": "Yes, it's fine. I'm doing okay.",
            "time_in_call_secs": 8,
        },
        {
            "role": "agent",
            "message": "Good to hear. I'm checking on a few follow-ups from your hospital stay. Did you manage to see your family physician?",
            "time_in_call_secs": 14,
        },
        {
            "role": "user",
            "message": f"Yes, I saw Dr. Shah on {fp_visit}. She said things looked okay.",
            "time_in_call_secs": 23,
        },
        {
            "role": "agent",
            "message": "Thanks, I've noted that as a patient-reported update. I'll ask the clinic to confirm so the follow-up can be closed properly. Have you had your bloodwork done yet?",
            "time_in_call_secs": 31,
        },
        {
            "role": "user",
            "message": "Not yet. I'll go next week.",
            "time_in_call_secs": 41,
        },
        {
            "role": "agent",
            "message": "Okay. Your bloodwork is due soon. I'll leave that open and the care team may follow up. Thanks, Michael. Goodbye.",
            "time_in_call_secs": 47,
        },
    ]


def day10_transcript(confirmed_date: str, reported_date: str) -> list[dict]:
    return [
        {
            "role": "agent",
            "message": "Hi Michael, this is Continuum. This is an AI assistant and this call is recorded. You're calling about your cardiology appointment — is that right?",
            "time_in_call_secs": 0,
        },
        {
            "role": "user",
            "message": f"Yes. I have it written down for {reported_date}. Is that what you have?",
            "time_in_call_secs": 9,
        },
        {
            "role": "agent",
            "message": "Thanks for checking. Our record shows the clinic confirmed a different date. I've noted what you told me and flagged the discrepancy for the care team to review. They'll confirm the correct date with you.",
            "time_in_call_secs": 17,
        },
        {
            "role": "user",
            "message": "Okay. I don't want to miss it.",
            "time_in_call_secs": 29,
        },
        {
            "role": "agent",
            "message": "Understood. The care coordinator will follow up with the clinic and contact you. Goodbye.",
            "time_in_call_secs": 34,
        },
    ]


def day12_transcript() -> list[dict]:
    return [
        {
            "role": "agent",
            "message": "Hi Michael, this is Continuum, an AI assistant. This call is recorded. How can I help today?",
            "time_in_call_secs": 0,
        },
        {
            "role": "user",
            "message": "I want to know if I can double my water pill dose. I'm still a bit swollen.",
            "time_in_call_secs": 8,
        },
        {
            "role": "agent",
            "message": "I can't advise on medication or dosage changes. I've flagged this for a clinician, who will follow up with you. Please don't change your medication before speaking with them. If you have chest pain or trouble breathing, call 911.",
            "time_in_call_secs": 18,
        },
        {
            "role": "user",
            "message": "Okay, I'll wait for the nurse to call.",
            "time_in_call_secs": 30,
        },
    ]


def transcript_text(transcript: list[dict]) -> str:
    # Tool-call turns have no spoken message; leave them out of the text.
    return "\n".join(
        f"{t.get('role', 'user')}: {t['message']}" for t in transcript if (t.get("message") or "").strip()
    )


# ── Seeding ──────────────────────────────────────────────────────────────


def _insert_obligation(
    db: Database,
    *,
    patient_id: str,
    episode_id: str,
    description: str,
    category: str,
    owner: str,
    due_date: str,
    status: str,
    latest_evidence: str = "",
    confirmed_date: str | None = None,
    confirmed_source: str | None = None,
    next_action: str | None = None,
    source_label: str = "Discharge summary",
    quote: str | None = None,
    history: list[dict] | None = None,
    created_at: str | None = None,
) -> dict:
    now = created_at or utcnow()
    doc = {
        "id": new_id("obl"),
        "patient_id": patient_id,
        "episode_id": episode_id,
        "description": description,
        "category": category,
        "owner": owner,
        "due_date": due_date,
        "status": status,
        "verification_required": True,
        "confidence": 0.88,
        "evidence_ids": [],
        "conflicts": [],
        "keywords": _keywords(category),
        "source": {"event_id": None, "label": source_label, "quote": quote or description},
        "latest_evidence": latest_evidence,
        "confirmed_date": confirmed_date,
        "confirmed_source": confirmed_source,
        "next_action": next_action,
        "version": len(history) + 1 if history else 1,
        "created_at": now,
        "updated_at": now,
        "history": history
        or [{"at": now, "status": status, "actor": "Continuum", "reason": "Extracted from discharge summary"}],
    }
    db.obligations.insert_one(dict(doc))
    return doc


def _insert_memory(
    db: Database,
    *,
    patient_id: str,
    kind: str,
    summary: str,
    content: str,
    tags: list[str],
    importance: float,
    created_at: str,
) -> None:
    doc = {
        "id": new_id("mem"),
        "patient_id": patient_id,
        "kind": kind,
        "summary": summary,
        "content": content,
        "tags": tags,
        "importance": importance,
        "source_event_id": None,
        "created_at": created_at,
    }
    from .agent.llm import embed_text

    embedding = embed_text(f"{summary}\n{content}")
    if embedding:
        doc["embedding"] = embedding
    db.memories.insert_one(doc)


def _discharge_obligations(
    db: Database,
    patient_id: str,
    episode_id: str,
    discharge: str,
    *,
    statuses: dict[str, tuple[str, str | None]] | None = None,
) -> None:
    statuses = statuses or {}
    specs = [
        ("family_physician", "Family physician follow-up", "Family physician", 7),
        ("bloodwork", "Bloodwork (creatinine and electrolytes)", "Laboratory", 14),
        ("cardiology", "Cardiology follow-up", "Cardiology clinic", 28),
        ("home_care", "Home-care assessment", "Community care", 8),
    ]
    for category, description, owner, offset in specs:
        status, evidence = statuses.get(category, (ObligationStatus.pending.value, None))
        _insert_obligation(
            db,
            patient_id=patient_id,
            episode_id=episode_id,
            description=description,
            category=category,
            owner=owner,
            due_date=add_days(discharge, offset),
            status=status,
            latest_evidence=evidence or "",
            next_action=(
                "Verify reported completion with the responsible provider"
                if status == ObligationStatus.patient_reported.value
                else None
            ),
        )


def seed_michael(db: Database, today: date | None = None) -> dict:
    """Seed Michael at a Day-7-like state so the dashboard is immediately useful.

    Discharge 10 days ago, day-3 voice report recorded, cardiology confirmed on
    day 7, home-care assessment now overdue.
    """
    today = today or demo_today()
    discharge = _iso(today - timedelta(days=10))
    episode_id = MICHAEL_EPISODE_ID
    created = f"{discharge}T15:20:00+00:00"

    patient = {
        "id": MICHAEL_ID,
        "name": "Michael Chen",
        "initials": "MC",
        "age": 64,
        "birth_date": "1962-03-08",
        "mrn": "C-10482",
        "condition": "Heart failure",
        "risk": "High",
        "pronouns": "he/him",
        "phone": "+1 416 555 0142 (synthetic)",
        "caregiver": "Emily Chen (daughter)",
        "preferences": {
            "channel": "Voice call",
            "best_time": "Mornings, 9–11am",
            "language": "English",
            "consent_to_automated_calls": True,
            "consent_to_recording": True,
            "accessibility": "Prefers slower pace; hearing aid",
        },
        "synthetic": True,
        "created_at": created,
    }
    db.patients.replace_one({"id": MICHAEL_ID}, patient, upsert=True)
    db.care_episodes.replace_one(
        {"id": episode_id},
        {
            "id": episode_id,
            "patient_id": MICHAEL_ID,
            "admission_date": _iso(today - timedelta(days=14)),
            "discharge_date": discharge,
            "reason": "Decompensated heart failure",
            "status": "active",
            "discharge_summary": discharge_text(discharge),
            "created_at": created,
        },
        upsert=True,
    )

    confirmed = _iso(today + timedelta(days=12))
    _discharge_obligations(
        db,
        MICHAEL_ID,
        episode_id,
        discharge,
        statuses={
            "family_physician": (
                ObligationStatus.patient_reported.value,
                "Michael reported attending Dr. Shah on the Day 3 call; clinic confirmation pending.",
            ),
            "cardiology": (
                ObligationStatus.scheduled.value,
                f"Clinic confirmed appointment for {confirmed} (scheduling record CS-8841).",
            ),
            "home_care": (
                ObligationStatus.pending.value,
                "Referral submitted; no home-care appointment confirmed yet.",
            ),
        },
    )
    db.obligations.update_one(
        {"patient_id": MICHAEL_ID, "category": "cardiology"},
        {
            "$set": {
                # A confirmed booking moves the obligation's due date to the
                # appointment, as the engine does when it schedules one.
                "due_date": confirmed,
                "confirmed_date": confirmed,
                "confirmed_source": "clinic record CS-8841",
            }
        },
    )
    db.obligations.update_one(
        {"patient_id": MICHAEL_ID, "category": "family_physician"},
        {"$set": {"confirmed_date": _iso(today - timedelta(days=6))}},
    )

    events = [
        {
            "id": new_id("evt"),
            "patient_id": MICHAEL_ID,
            "episode_id": episode_id,
            "type": "document",
            "title": "Discharge summary processed",
            "detail": "Four follow-up obligations extracted and added to the Unclosed Loop Engine.",
            "actor": "Continuum",
            "occurred_at": f"{discharge}T15:20:00+00:00",
            "created_at": f"{discharge}T15:20:00+00:00",
            "transcript": [],
            "metadata": {"source": "synthetic discharge summary"},
        },
        {
            "id": new_id("evt"),
            "patient_id": MICHAEL_ID,
            "episode_id": episode_id,
            "type": "voice_call",
            "title": "Outbound Day 3 check-in completed",
            "detail": "Michael reported seeing his family physician and said bloodwork is still pending.",
            "actor": "ElevenLabs voice agent",
            "occurred_at": f"{_iso(today - timedelta(days=7))}T09:12:00+00:00",
            "created_at": f"{_iso(today - timedelta(days=7))}T09:12:00+00:00",
            "transcript": day3_transcript(discharge, today),
            "metadata": {"conversation_id": "conv_demo_day3", "direction": "outbound", "duration_secs": 74},
        },
        {
            "id": new_id("evt"),
            "patient_id": MICHAEL_ID,
            "episode_id": episode_id,
            "type": "coordinator",
            "title": "Cardiology appointment confirmed",
            "detail": f"Clinic confirmed {confirmed} for cardiology follow-up; evidence attached.",
            "actor": "Jordan Davis",
            "occurred_at": f"{_iso(today - timedelta(days=3))}T13:45:00+00:00",
            "created_at": f"{_iso(today - timedelta(days=3))}T13:45:00+00:00",
            "transcript": [],
            "metadata": {"source_ref": "CS-8841"},
        },
    ]
    db.events.insert_many(events)

    db.voice_calls.insert_one(
        {
            "id": new_id("call"),
            "patient_id": MICHAEL_ID,
            "episode_id": episode_id,
            "direction": "outbound",
            "elevenlabs_conversation_id": "conv_demo_day3",
            "started_at": f"{_iso(today - timedelta(days=7))}T09:12:00+00:00",
            "duration_secs": 74,
            "outcome": "completed",
            "transcript": day3_transcript(discharge, today),
            "created_at": f"{_iso(today - timedelta(days=7))}T09:12:00+00:00",
        }
    )

    fp = db.obligations.find_one({"patient_id": MICHAEL_ID, "category": "family_physician"})
    cardio = db.obligations.find_one({"patient_id": MICHAEL_ID, "category": "cardiology"})
    home = db.obligations.find_one({"patient_id": MICHAEL_ID, "category": "home_care"})

    db.evidence.insert_many(
        [
            {
                "id": new_id("evd"),
                "patient_id": MICHAEL_ID,
                "obligation_id": fp["id"],
                "kind": EvidenceKind.patient_report.value,
                "summary": "Michael reported attending Dr. Shah during the Day 3 voice check-in.",
                "source_ref": "conv_demo_day3",
                "quote": f"Yes, I saw Dr. Shah on {fp.get('confirmed_date')}.",
                "confidence": 0.8,
                "recorded_at": f"{_iso(today - timedelta(days=7))}T09:12:00+00:00",
                "recorded_by": "Continuum",
            },
            {
                "id": new_id("evd"),
                "patient_id": MICHAEL_ID,
                "obligation_id": cardio["id"],
                "kind": EvidenceKind.clinic_confirmation.value,
                "summary": f"Clinic confirmed cardiology appointment for {confirmed}.",
                "source_ref": "CS-8841",
                "quote": None,
                "confidence": 0.95,
                "recorded_at": f"{_iso(today - timedelta(days=3))}T13:45:00+00:00",
                "recorded_by": "Jordan Davis",
            },
            {
                "id": new_id("evd"),
                "patient_id": MICHAEL_ID,
                "obligation_id": home["id"],
                "kind": EvidenceKind.agent_note.value,
                "summary": "Referral submitted; no appointment confirmation received.",
                "source_ref": "referral-882",
                "quote": None,
                "confidence": 0.6,
                "recorded_at": f"{_iso(today - timedelta(days=8))}T10:00:00+00:00",
                "recorded_by": "Continuum",
            },
        ]
    )

    memories = [
        (
            MemoryKind.patient_context.value,
            "Michael is recovering from decompensated heart failure; lives alone, daughter Emily is caregiver.",
            "Heart failure with reduced ejection fraction. Discharged home after diuresis. Prefers slower-paced calls; uses a hearing aid.",
            ["heart failure", "caregiver", "lives alone"],
            0.9,
        ),
        (
            MemoryKind.preference.value,
            "Prefers voice calls in the morning; consented to automated calls and recording.",
            "Best time 9–11am. English. Consent to automated calls and recording confirmed at discharge.",
            ["preference", "voice", "consent"],
            0.8,
        ),
        (
            MemoryKind.interaction.value,
            "Day 3 outbound check-in: reported seeing family physician; bloodwork still pending.",
            transcript_text(day3_transcript(discharge, today)),
            ["voice", "day3", "family physician"],
            0.75,
        ),
        (
            MemoryKind.change.value,
            f"Cardiology appointment confirmed for {confirmed} by clinic record CS-8841.",
            "Coordinator attached clinic confirmation. No conflict at this point.",
            ["cardiology", "scheduled", "confirmed"],
            0.7,
        ),
        (
            MemoryKind.care_obligation.value,
            "Home-care assessment is overdue; referral submitted but no appointment confirmed.",
            "Due date passed. Coordinator follow-up recommended.",
            ["home care", "overdue"],
            0.8,
        ),
    ]
    for kind, summary, content, tags, importance in memories:
        _insert_memory(
            db,
            patient_id=MICHAEL_ID,
            kind=kind,
            summary=summary,
            content=content,
            tags=tags,
            importance=importance,
            created_at=utcnow(),
        )

    proposal = {
        "id": new_id("prop"),
        "kind": "verify_completion",
        "title": "Verify completion: Family physician follow-up",
        "rationale": (
            "Michael reported seeing Dr. Shah. Closing the loop requires clinic confirmation "
            "or an authorized coordinator override — a patient report alone is not verification."
        ),
        "obligation_id": fp["id"],
        "patient_id": MICHAEL_ID,
        "episode_id": episode_id,
        "evidence_summary": "Michael reported attending Dr. Shah during the Day 3 voice check-in.",
        "evidence_kind": EvidenceKind.clinic_confirmation.value,
        "confidence": 0.6,
        "changes": {"status": ObligationStatus.verified.value},
        "requires_approval": True,
        "interrupt": False,
    }
    db.approvals.insert_one(
        {
            "id": new_id("apr"),
            "patient_id": MICHAEL_ID,
            "run_id": "run_seed_day3",
            "thread_id": MICHAEL_ID,
            "kind": ApprovalKind.verify_completion.value,
            "mode": "queued",
            "obligation_id": fp["id"],
            "title": proposal["title"],
            "rationale": proposal["rationale"],
            "proposed_changes": proposal["changes"],
            "evidence_summary": proposal["evidence_summary"],
            "confidence": 0.6,
            "proposal": proposal,
            "status": "pending",
            "created_at": utcnow(),
            "resolved_at": None,
            "resolved_by": None,
            "resolution_note": None,
            "applied_at": None,
        }
    )

    db.agent_audit.insert_many(
        [
            {
                "id": new_id("aud"),
                "patient_id": MICHAEL_ID,
                "thread_id": MICHAEL_ID,
                "run_id": None,
                "actor": "Continuum",
                "action": "Extracted obligations",
                "detail": "Discharge summary produced 4 follow-up obligations.",
                "before": None,
                "after": {"count": 4},
                "evidence_id": None,
                "created_at": f"{discharge}T15:20:01+00:00",
            },
            {
                "id": new_id("aud"),
                "patient_id": MICHAEL_ID,
                "thread_id": MICHAEL_ID,
                "run_id": "run_seed_day3",
                "actor": "Continuum",
                "action": "Recorded patient report",
                "detail": "Family physician follow-up → patient-reported; verification requested.",
                "before": {"status": "pending"},
                "after": {"status": "patient_reported"},
                "evidence_id": None,
                "created_at": f"{_iso(today - timedelta(days=7))}T09:12:05+00:00",
            },
            {
                "id": new_id("aud"),
                "patient_id": MICHAEL_ID,
                "thread_id": MICHAEL_ID,
                "run_id": None,
                "actor": "Jordan Davis",
                "action": "Updated obligation",
                "detail": "Cardiology follow-up → scheduled; clinic confirmation attached.",
                "before": {"status": "pending"},
                "after": {"status": "scheduled", "confirmed_date": confirmed},
                "evidence_id": None,
                "created_at": f"{_iso(today - timedelta(days=3))}T13:45:00+00:00",
            },
        ]
    )

    return {"patient_id": MICHAEL_ID, "episode_id": episode_id}


def seed_other_patients(db: Database, today: date) -> None:
    others = [
        {
            "id": "pat_sarah",
            "name": "Sarah Okafor",
            "initials": "SO",
            "age": 58,
            "birth_date": "1968-07-22",
            "mrn": "C-10491",
            "condition": "COPD",
            "risk": "Medium",
            "phone": "+1 416 555 0188 (synthetic)",
            "preferences": {"channel": "Text", "best_time": "Afternoons", "language": "English",
                            "consent_to_automated_calls": True, "consent_to_recording": True},
            "discharge": _iso(today - timedelta(days=7)),
            "obligations": [
                ("Respiratory clinic follow-up", "respiratory", "Respiratory clinic", 3, "overdue",
                 "No booking confirmation received."),
                ("Inhaler technique review", "pharmacy", "Community pharmacist", 9, "scheduled",
                 "Booked for tomorrow at 10:30."),
            ],
        },
        {
            "id": "pat_david",
            "name": "David Morin",
            "initials": "DM",
            "age": 71,
            "birth_date": "1955-05-30",
            "mrn": "C-10503",
            "condition": "Type 2 diabetes",
            "risk": "Medium",
            "phone": "+1 416 555 0121 (synthetic)",
            "preferences": {"channel": "Voice call", "best_time": "Afternoons", "language": "French/English",
                            "consent_to_automated_calls": True, "consent_to_recording": True},
            "discharge": _iso(today - timedelta(days=4)),
            "obligations": [
                ("Diabetes education session", "diabetes", "Diabetes education program", 10, "scheduled",
                 "Appointment confirmation #DE-201."),
                ("Bloodwork (A1C)", "bloodwork", "Laboratory", 14, "pending", ""),
            ],
        },
        {
            "id": "pat_amina",
            "name": "Amina Yusuf",
            "initials": "AY",
            "age": 67,
            "birth_date": "1959-09-04",
            "mrn": "C-10474",
            "condition": "Heart failure",
            "risk": "High",
            "phone": "+1 416 555 0177 (synthetic)",
            "preferences": {"channel": "Caregiver", "best_time": "Mornings", "language": "Somali/English",
                            "consent_to_automated_calls": True, "consent_to_recording": True},
            "discharge": _iso(today - timedelta(days=13)),
            "obligations": [
                ("Daily weight monitoring setup", "home_care", "Caregiver", 11, "verified",
                 "Nurse verified scale and log."),
                ("Cardiology follow-up", "cardiology", "Cardiology clinic", 11, "escalated",
                 "Two unsuccessful booking attempts; clinician review requested."),
            ],
        },
    ]
    for p in others:
        episode_id = f"ep_{p['id']}"
        db.patients.replace_one(
            {"id": p["id"]},
            {
                "id": p["id"],
                "name": p["name"],
                "initials": p["initials"],
                "age": p["age"],
                "birth_date": p["birth_date"],
                "mrn": p["mrn"],
                "condition": p["condition"],
                "risk": p["risk"],
                "phone": p["phone"],
                "preferences": p["preferences"],
                "synthetic": True,
                "created_at": utcnow(),
            },
            upsert=True,
        )
        db.care_episodes.replace_one(
            {"id": episode_id},
            {
                "id": episode_id,
                "patient_id": p["id"],
                "admission_date": _iso(today - timedelta(days=10)),
                "discharge_date": p["discharge"],
                "reason": p["condition"],
                "status": "active",
                "created_at": utcnow(),
            },
            upsert=True,
        )
        for description, category, owner, offset, status, evidence in p["obligations"]:
            _insert_obligation(
                db,
                patient_id=p["id"],
                episode_id=episode_id,
                description=description,
                category=category,
                owner=owner,
                due_date=add_days(p["discharge"], offset),
                status=status,
                latest_evidence=evidence,
            )
        db.events.insert_one(
            {
                "id": new_id("evt"),
                "patient_id": p["id"],
                "episode_id": episode_id,
                "type": "document",
                "title": "Discharge summary processed",
                "detail": "Follow-up obligations extracted.",
                "actor": "Continuum",
                "occurred_at": f"{p['discharge']}T15:20:00+00:00",
                "created_at": f"{p['discharge']}T15:20:00+00:00",
                "transcript": [],
                "metadata": {},
            }
        )


def seed_if_empty() -> None:
    db = get_db_safe()
    if db is None:
        return
    if db.patients.count_documents({}) > 0:
        return
    today = demo_today()
    seed_michael(db, today)
    seed_other_patients(db, today)
    from . import repository as repo

    repo.audit(
        db,
        patient_id=MICHAEL_ID,
        actor="System",
        action="Seeded synthetic demo",
        detail="Four synthetic patients seeded. No real patient data.",
    )


def get_db_safe() -> Database | None:
    try:
        from .db import get_db

        return get_db()
    except Exception:
        return None


def reset_patient_scenario(db: Database, patient_id: str = MICHAEL_ID) -> None:
    """Remove one patient's derived records so Day 0 can be replayed."""
    for coll in [
        "obligations",
        "events",
        "evidence",
        "memories",
        "agent_audit",
        "approvals",
        "voice_calls",
        "checkins",
    ]:
        db[coll].delete_many({"patient_id": patient_id})
    for coll in ["checkpoints", "checkpoint_writes"]:
        try:
            db[coll].delete_many({"thread_id": patient_id})
        except Exception:
            pass
    try:
        from .agent.checkpoint import get_checkpointer

        get_checkpointer().delete_thread(patient_id)
    except Exception:
        pass
    db.care_episodes.delete_many({"patient_id": patient_id})
    today = demo_today()
    discharge = _iso(today)
    if patient_id == MICHAEL_ID:
        db.care_episodes.insert_one(
            {
                "id": MICHAEL_EPISODE_ID,
                "patient_id": MICHAEL_ID,
                "admission_date": _iso(today - timedelta(days=4)),
                "discharge_date": discharge,
                "reason": "Decompensated heart failure",
                "status": "active",
                "discharge_summary": discharge_text(discharge),
                "created_at": utcnow(),
            }
        )


def seed_all(reset: bool = False) -> dict:
    db = get_db_safe()
    if db is None:
        return {"seeded": False, "reason": "database unavailable"}
    if reset:
        for coll in [
            "patients", "care_episodes", "events", "obligations", "evidence", "memories",
            "agent_audit", "approvals", "voice_calls", "checkins", "checkpoints", "checkpoint_writes",
            "ingest_runs", "hl7_messages", "ehr_poll_runs", "ingest_jobs", "actions", "order_memory", "call_sessions",
        ]:
            db[coll].delete_many({})
    if db.patients.count_documents({}) == 0:
        today = demo_today()
        seed_michael(db, today)
        seed_other_patients(db, today)
    return {
        "seeded": True,
        "patients": db.patients.count_documents({}),
        "obligations": db.obligations.count_documents({}),
    }


if __name__ == "__main__":
    print(seed_all(reset=True))
