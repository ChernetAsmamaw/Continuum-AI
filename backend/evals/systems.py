"""The two systems under test.

``run_continuum`` replays every step of a case through the real Continuum
pipeline (LangGraph agent, Unclosed Loop Engine, MongoDB memory) for a fresh
synthetic patient, then reads the obligation state back from the database.

``run_baseline`` is the same extraction model with no memory and no loop
engine: it sees only the most recent conversation or document (what a
stateless chat assistant has in its context window) and takes what it reads
at face value. With an LLM key configured both systems use the same model;
without one both use the same deterministic extractor, so the comparison
isolates memory and rules rather than model quality.
"""

from __future__ import annotations

import re
from datetime import date, timedelta

from app.agent.extraction import CATEGORY_RULES, any_term, extract
from app.agent.graph import run_agent
from app.db import demo_today, get_db
from app import repository as repo
from app.util import new_id, utcnow
from app.workflow import confirm_appointment

STATUS_CLASS = {
    "pending": "open",
    "overdue": "open",
    "attempted": "open",
    "unknown": "open",
    "scheduled": "scheduled",
    "patient_reported": "patient_reported",
    "verified": "closed",
    "conflict": "conflict",
    "cancelled": "cancelled",
    "escalated": "escalated",
}

_DATE_TOKEN = re.compile(r"\{(d|iso)\+(\d+)\}")


def render(text: str, anchor: date) -> str:
    """Fill {d+N} (spoken date) and {iso+N} (ISO date) relative to the anchor."""

    def sub(m: re.Match) -> str:
        day = anchor + timedelta(days=int(m.group(2)))
        return day.isoformat() if m.group(1) == "iso" else f"{day:%B} {day.day}"

    return _DATE_TOKEN.sub(sub, text)


def canonical_category(category: str | None, description: str | None = "") -> str:
    """Map whatever label an extractor produced onto the shared category keys."""
    key = (category or "").strip().lower()
    if key in {r["category"] for r in CATEGORY_RULES}:
        return key
    text = f"{key.replace('_', ' ')} {description or ''}".lower()
    for rule in CATEGORY_RULES:
        if any_term(rule["keywords"], text) or rule["category"].replace("_", " ") in text:
            return rule["category"]
    return key or "other"


def _keyed(items: list[tuple[str, dict]]) -> dict[str, dict]:
    """Key by category, numbering repeats: bloodwork, bloodwork#2, …"""
    out: dict[str, dict] = {}
    for cat, value in items:
        key, n = cat, 1
        while key in out:
            n += 1
            key = f"{cat}#{n}"
        out[key] = value
    return out


# ── Continuum ────────────────────────────────────────────────────────────


def _new_patient(db, case_id: str, anchor: date) -> str:
    patient_id = new_id("evalpat")
    db.patients.insert_one(
        {
            "id": patient_id,
            "name": f"Eval Patient {case_id}",
            "initials": "EP",
            "age": 70,
            "mrn": new_id("mrn").upper(),
            "condition": "Synthetic evaluation case",
            "risk": "medium",
            "phone": "",
            "preferences": {"channel": "phone", "consent_to_automated_calls": True},
            "synthetic": True,
            "created_at": utcnow(),
        }
    )
    db.care_episodes.insert_one(
        {
            "id": new_id("ep"),
            "patient_id": patient_id,
            "admission_date": (anchor - timedelta(days=5)).isoformat(),
            "discharge_date": anchor.isoformat(),
            "reason": "Synthetic evaluation case",
            "status": "active",
            "created_at": utcnow(),
        }
    )
    return patient_id


def run_continuum(inputs: dict) -> dict:
    db = get_db()
    anchor = demo_today()
    patient_id = _new_patient(db, inputs["case_id"], anchor)
    errors: list[str] = []
    for step in inputs["steps"]:
        kind = step["type"]
        if kind == "clinic_confirm":
            result = confirm_appointment(
                patient_id=patient_id,
                obligation_hint=step["hint"],
                confirmed_date=(anchor + timedelta(days=step["offset"])).isoformat(),
                actor="Eval clinic feed",
                source_ref=step.get("source_ref"),
            )
            if not result.get("ok"):
                errors.append(f"clinic_confirm: {result.get('detail')}")
            continue
        result = run_agent(
            patient_id=patient_id,
            input_type="document" if kind == "document" else "voice_transcript" if kind == "call" else "chat",
            text=render(step["text"], anchor),
            actor="Eval runner",
            title=step.get("title"),
        )
        if result.get("status") == "awaiting_approval" and not result.get("run_id"):
            errors.append(f"{kind}: thread paused, step skipped")

    obligations = repo.list_obligations(db, patient_id, include_closed=True, today=anchor)
    obligations.sort(key=lambda o: (o.get("due_date") or "9999", o.get("created_at") or ""))
    state = _keyed(
        [
            (
                canonical_category(o.get("category"), o.get("description")),
                {
                    "status": STATUS_CLASS.get(o["status"], o["status"]),
                    "raw_status": o["status"],
                    "date": o.get("confirmed_date") or o.get("due_date"),
                    "reported_date": o.get("reported_date"),
                },
            )
            for o in obligations
        ]
    )
    approvals = list(db.approvals.find({"patient_id": patient_id}, {"_id": 0, "kind": 1, "audience": 1}))
    escalated = any(a.get("kind") == "escalate" or a.get("audience") == "clinician" for a in approvals)
    return {
        "system": "continuum",
        "anchor": anchor.isoformat(),
        "obligations": state,
        "escalated": escalated,
        "approvals": [a.get("kind") for a in approvals],
        "errors": errors,
    }


# ── Stateless baseline ───────────────────────────────────────────────────

REPORT_CLASS = {
    "completed": "closed",
    "attended": "closed",
    "scheduled": "scheduled",
    "rescheduled": "scheduled",
    "cancelled": "cancelled",
    "not_completed": "open",
    "unknown": "open",
}


def run_baseline(inputs: dict) -> dict:
    anchor = demo_today()
    last = [s for s in inputs["steps"] if s["type"] in {"document", "call", "chat"}][-1]
    kind = last["type"]
    result = extract(
        render(last["text"], anchor),
        input_type="document" if kind == "document" else "voice_transcript" if kind == "call" else "chat",
        patient={"name": "Eval Patient"},
        existing_obligations=[],
        anchor=anchor.isoformat(),
    )
    items: list[tuple[str, dict]] = []
    for f in result.get("follow_ups", []):
        due = f.get("due_date")
        if not due and f.get("due_in_days") is not None:
            due = (anchor + timedelta(days=int(f["due_in_days"]))).isoformat()
        items.append((canonical_category(f.get("category"), f.get("description")), {"status": "open", "date": due}))
    state = _keyed(items)
    for r in result.get("patient_reports", []):
        cat = canonical_category(r.get("category"), r.get("obligation_hint"))
        state[cat] = {
            "status": REPORT_CLASS.get(r.get("reported_status") or "unknown", "open"),
            "date": r.get("reported_date"),
        }
    safety = result.get("safety") or {}
    escalated = any(
        safety.get(k) for k in ("clinical_question", "medication_question", "urgent_symptoms", "emergency")
    ) or any(a.get("action") == "escalate" for a in result.get("requested_actions", []))
    return {
        "system": "stateless_baseline",
        "anchor": anchor.isoformat(),
        "obligations": state,
        "escalated": bool(escalated),
        "method": result.get("method"),
        "errors": [],
    }
