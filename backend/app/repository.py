"""MongoDB repository primitives.

The repository is intentionally thin: it owns persistence and append-only
audit rules, while domain decisions live in ``engine.py`` and ``agent/``.
"""

from __future__ import annotations

import logging

import hashlib
from datetime import date
from typing import Any, Iterable

from pymongo.database import Database
from pymongo.errors import BulkWriteError

from .schemas import (
    OPEN_STATUSES,
    ApprovalKind,
    ApprovalStatus,
    MemoryKind,
    ObligationStatus,
)
from .util import new_id, parse_date, similarity, tokens, truncate, utcnow

PRIORITY = {
    ObligationStatus.overdue: 0,
    ObligationStatus.conflict: 1,
    ObligationStatus.escalated: 2,
    ObligationStatus.patient_reported: 3,
    ObligationStatus.pending: 4,
    ObligationStatus.attempted: 5,
    ObligationStatus.unknown: 6,
    ObligationStatus.scheduled: 7,
    ObligationStatus.verified: 8,
    ObligationStatus.cancelled: 9,
}


# ── Presentation helpers ─────────────────────────────────────────────────



log = logging.getLogger("continuum.repository")

def present_obligation(doc: dict, today: date | None = None) -> dict:
    today = today or date.today()
    out = dict(doc)
    raw = dot_get(doc, "status", ObligationStatus.pending.value)
    due = parse_date(doc.get("due_date"))
    derived = False
    if (
        due
        and due < today
        and raw
        in {
            ObligationStatus.pending.value,
            ObligationStatus.scheduled.value,
            ObligationStatus.attempted.value,
            ObligationStatus.unknown.value,
        }
    ):
        out["status"] = ObligationStatus.overdue.value
        out["raw_status"] = raw
        derived = True
    out["status_derived"] = derived
    out["days_until_due"] = (due - today).days if due else None
    out["days_overdue"] = max((today - due).days, 0) if due else 0
    out["is_open"] = ObligationStatus(out["status"]) in OPEN_STATUSES
    out["is_overdue"] = bool(due and due < today and out["is_open"])
    out["priority_rank"] = PRIORITY.get(ObligationStatus(out["status"]), 9)
    return out


def dot_get(doc: dict, path: str, default: Any = None) -> Any:
    cur: Any = doc
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return default
        cur = cur[part]
    return cur


def sort_obligations(items: Iterable[dict]) -> list[dict]:
    return sorted(
        items,
        key=lambda o: (
            o.get("priority_rank", 9),
            o.get("due_date") or "9999-99-99",
            o.get("description", ""),
        ),
    )


# ── Patients & episodes ──────────────────────────────────────────────────


def get_patient(db: Database, patient_id: str) -> dict | None:
    return db.patients.find_one({"id": patient_id}, {"_id": 0})


def delete_patient(db: Database, patient_id: str) -> bool:
    """Delete a patient and every record derived from them.

    Data-governance operation (subject access/deletion requests, test-data
    cleanup). Removes the patient, their episodes, obligations, evidence,
    events, memories, audit entries, approvals, voice calls, check-ins and
    LangGraph checkpoints. Returns False when the patient did not exist.
    """
    patient = db.patients.find_one({"id": patient_id}, {"_id": 0})
    if not patient:
        return False
    for coll in (
        "obligations",
        "events",
        "evidence",
        "memories",
        "agent_audit",
        "approvals",
        "voice_calls",
        "checkins",
    ):
        db[coll].delete_many({"patient_id": patient_id})
    for coll in ("checkpoints", "checkpoint_writes"):
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
    audit(
        db,
        patient_id="system",
        actor="Continuum",
        action="Deleted patient",
        detail=f"{patient.get('name')} ({patient_id}) and all derived records",
        after={"patient_id": patient_id},
    )
    db.patients.delete_one({"id": patient_id})
    return True


def find_patients_by_identity(
    db: Database,
    given_name: str,
    family_name: str,
    phone: str | None = None,
) -> list[dict]:
    """Best-effort patient lookup for voice identity verification.

    Matches on normalized name; when a phone number is supplied, digits are
    compared on the trailing four so callers can state either the full number
    or "the last four". Returns candidate documents (never raises on no match).
    """

    def normalize(value: str | None) -> str:
        return " ".join((value or "").lower().replace(".", " ").split())

    def digits(value: str | None) -> str:
        return "".join(ch for ch in (value or "") if ch.isdigit())

    given = normalize(given_name)
    family = normalize(family_name)
    query_phone = digits(phone)[-4:] if phone else ""

    out: list[dict] = []
    for patient in db.patients.find({}, {"_id": 0}):
        name = normalize(patient.get("name"))
        parts = name.split()
        name_ok = (
            (given and family and given in parts and family in parts)
            or (given and not family and given in parts)
        )
        if not name_ok:
            continue
        patient_phone = digits(patient.get("phone"))
        if query_phone and patient_phone:
            if not patient_phone.endswith(query_phone):
                continue
        out.append(patient)
    return out[:5]


def _patient_rows(patients: list[dict], obligations: list[dict]) -> list[dict]:
    """Patient list rows from already-presented obligations (no queries)."""
    by_patient: dict[str, list[dict]] = {}
    for o in obligations:
        by_patient.setdefault(o.get("patient_id"), []).append(o)
    out = []
    for patient in patients:
        open_items = [o for o in by_patient.get(patient["id"], []) if o["is_open"]]
        out.append(
            {
                **patient,
                "open_count": len(open_items),
                "overdue_count": sum(
                    1 for o in open_items if o["status"] == ObligationStatus.overdue.value
                ),
                "conflict_count": sum(
                    1 for o in open_items if o["status"] == ObligationStatus.conflict.value
                ),
                "escalation_count": sum(
                    1 for o in open_items if o["status"] == ObligationStatus.escalated.value
                ),
                "pending_verification": sum(
                    1
                    for o in open_items
                    if o["status"] == ObligationStatus.patient_reported.value
                ),
                "next_due": min(
                    (o["due_date"] for o in open_items if o.get("due_date")), default=None
                ),
                "priority": sort_obligations(open_items)[:3],
            }
        )
    out.sort(key=lambda p: (-p["overdue_count"], -p["conflict_count"], p["name"]))
    return out


def _presented_obligations(db: Database, patient_ids: list[str] | None, today: date | None) -> list[dict]:
    query: dict[str, Any] = {} if patient_ids is None else {"patient_id": {"$in": patient_ids}}
    return [present_obligation(doc, today) for doc in db.obligations.find(query, {"_id": 0})]


def list_patients(db: Database, today: date | None = None) -> list[dict]:
    # Two queries in all (was one per patient).
    patients = list(db.patients.find({}, {"_id": 0}))
    return _patient_rows(patients, _presented_obligations(db, [p["id"] for p in patients], today))


def get_active_episode(db: Database, patient_id: str) -> dict | None:
    return db.care_episodes.find_one(
        {"patient_id": patient_id, "status": "active"},
        {"_id": 0},
        sort=[("discharge_date", -1)],
    )


# ── Obligations ──────────────────────────────────────────────────────────


def get_obligation(db: Database, obligation_id: str) -> dict | None:
    return db.obligations.find_one({"id": obligation_id}, {"_id": 0})


def list_obligations(
    db: Database,
    patient_id: str | None = None,
    include_closed: bool = True,
    today: date | None = None,
) -> list[dict]:
    query: dict[str, Any] = {}
    if patient_id:
        query["patient_id"] = patient_id
    docs = [present_obligation(d, today) for d in db.obligations.find(query, {"_id": 0})]
    if not include_closed:
        docs = [d for d in docs if d["is_open"]]
    return sort_obligations(docs)


def open_obligations(db: Database, patient_id: str, today: date | None = None) -> list[dict]:
    return list_obligations(db, patient_id, include_closed=False, today=today)


def _obligation_doc(doc: dict, actor: str, reason: str, *, now: str | None = None, obligation_id: str | None = None) -> dict:
    now = now or utcnow()
    obligation = {
        "id": obligation_id or new_id("obl"),
        "status": ObligationStatus.pending.value,
        "owner": "Care team",
        "verification_required": True,
        "confidence": 0.7,
        "evidence_ids": [],
        "conflicts": [],
        "history": [],
        "version": 1,
        "created_at": now,
        "updated_at": now,
        **doc,
    }
    obligation["history"].append(
        {
            "at": now,
            "status": obligation["status"],
            "actor": actor,
            "reason": reason,
        }
    )
    return obligation


def _obligation_side_docs(obligation: dict, actor: str) -> tuple[dict, dict]:
    """(audit kwargs, memory kwargs) recorded when an obligation is created."""
    audit_kwargs = dict(
        patient_id=obligation["patient_id"],
        actor=actor,
        action="Created obligation",
        detail=obligation["description"],
        after={"status": obligation["status"], "due_date": obligation.get("due_date")},
        thread_id=obligation.get("thread_id"),
    )
    memory_kwargs = dict(
        patient_id=obligation["patient_id"],
        kind=MemoryKind.care_obligation,
        summary=f"Obligation created: {obligation['description']} (due {obligation.get('due_date') or 'unspecified'})",
        content=obligation.get("source", {}).get("quote") or obligation["description"],
        tags=["obligation", "created"],
        importance=0.6,
        source_event_id=obligation.get("source", {}).get("event_id"),
    )
    return audit_kwargs, memory_kwargs


def memory_embedding_text(summary: str, content: str) -> str:
    """The text a memory's vector is computed from."""
    return f"{summary}\n{content}"


def create_obligation(db: Database, doc: dict, actor: str, reason: str) -> dict:
    obligation = _obligation_doc(doc, actor, reason)
    db.obligations.insert_one(dict(obligation))
    audit_kwargs, memory_kwargs = _obligation_side_docs(obligation, actor)
    audit(db, **audit_kwargs)
    add_memory(db, **memory_kwargs)
    return obligation


def stable_id(prefix: str, *parts: str) -> str:
    """Deterministic id (same shape as ``new_id``) so a retried write is a no-op."""
    digest = hashlib.sha1("\x1f".join(parts).encode("utf-8")).hexdigest()[:12]
    return f"{prefix}_{digest}"


def _insert_idempotent(collection, docs: list[dict]) -> None:
    """insert_many that treats already-present ids as done (re-run after a crash)."""
    if not docs:
        return
    try:
        collection.insert_many([dict(d) for d in docs], ordered=False)
    except BulkWriteError as exc:
        others = [e for e in exc.details.get("writeErrors", []) if e.get("code") != 11000]
        if others:
            raise


def create_obligations_bulk(db: Database, items: list[dict], actor: str) -> list[dict]:
    """Create many obligations with their evidence, audit and memory entries.

    Same documents as ``create_obligation`` + ``add_evidence`` per item, in a
    handful of round trips instead of ~7 per obligation, with one batched
    embedding call. Each item: ``{"key", "doc", "reason", "evidence": [...]}``
    where ``key`` makes every id deterministic (patient + key), so a step
    re-run after a crash completes the missing writes instead of duplicating.
    The obligation itself is written last: it is what marks the item done.
    (Obligations are never deleted on their own — retention keeps them and
    ``delete_patient`` removes the patient too — so an id can't be reused for
    different content.)
    """
    from .agent.llm import embed_texts

    obligations, audits, memories, evidence = [], [], [], []
    for item in items:
        doc = item["doc"]
        base = (doc["patient_id"], item["key"])
        now = utcnow()
        obligation = _obligation_doc(doc, actor, item["reason"], now=now, obligation_id=stable_id("obl", *base))
        audit_kwargs, memory_kwargs = _obligation_side_docs(obligation, actor)
        audits.append(_audit_doc(**audit_kwargs, audit_id=stable_id("aud", *base)))
        memories.append(_memory_doc(**memory_kwargs, memory_id=stable_id("mem", *base)))
        memories[-1]["_text"] = memory_embedding_text(memory_kwargs["summary"], memory_kwargs["content"])
        for i, ev in enumerate(item.get("evidence") or []):
            ev_doc = _evidence_doc(
                patient_id=doc["patient_id"], obligation_id=obligation["id"], actor=actor,
                evidence_id=stable_id("evd", *base, str(i)), **ev,
            )
            evidence.append(ev_doc)
            # What add_evidence's follow-up update would have set.
            obligation["evidence_ids"].append(ev_doc["id"])
            obligation["latest_evidence"] = ev_doc["summary"]
            obligation["updated_at"] = ev_doc["recorded_at"]
        obligations.append(obligation)

    vectors = embed_texts([m.pop("_text") for m in memories]) if memories else []
    for memory, vector in zip(memories, vectors):
        if vector:
            memory["embedding"] = vector
    _insert_idempotent(db.memories, memories)
    _insert_idempotent(db.agent_audit, audits)
    _insert_idempotent(db.evidence, evidence)
    _insert_idempotent(db.obligations, obligations)
    return obligations


def set_obligation_status(
    db: Database,
    obligation: dict,
    status: str,
    actor: str,
    reason: str,
    *,
    due_date: str | None = None,
    next_action: str | None = None,
    conflicts: list[dict] | None = None,
    thread_id: str | None = None,
) -> dict:
    now = utcnow()
    before = {
        "status": obligation.get("status"),
        "due_date": obligation.get("due_date"),
    }
    update: dict[str, Any] = {
        "status": status,
        "updated_at": now,
        "version": obligation.get("version", 1) + 1,
    }
    if due_date:
        update["due_date"] = due_date
    if next_action is not None:
        update["next_action"] = next_action
    if conflicts is not None:
        update["conflicts"] = conflicts
    history_entry = {
        "at": now,
        "status": status,
        "actor": actor,
        "reason": reason,
    }
    db.obligations.update_one(
        {"id": obligation["id"]},
        {"$set": update, "$push": {"history": history_entry}},
    )
    audit(
        db,
        patient_id=obligation["patient_id"],
        actor=actor,
        action="Updated obligation",
        detail=f"{obligation['description']} → {status}",
        before=before,
        after={"status": status, "due_date": update.get("due_date", obligation.get("due_date"))},
        thread_id=thread_id,
    )
    add_memory(
        db,
        patient_id=obligation["patient_id"],
        kind=MemoryKind.change,
        summary=f"{obligation['description']} → {status}",
        content=reason,
        tags=["status", status, "obligation"],
        importance=0.7,
        source_event_id=None,
    )
    return get_obligation(db, obligation["id"]) or obligation


def find_matching_obligation(
    db: Database,
    patient_id: str,
    text: str,
    *,
    include_closed: bool = False,
) -> tuple[dict | None, float]:
    """Match free text to an existing obligation using token overlap."""
    candidates = list(
        db.obligations.find(
            {"patient_id": patient_id},
            {"_id": 0},
        )
    )
    best, best_score = None, 0.0
    q = tokens(text)
    for candidate in candidates:
        haystack = " ".join(
            [
                candidate.get("description", ""),
                candidate.get("category", ""),
                candidate.get("owner", ""),
                " ".join(candidate.get("aliases", [])),
            ]
        )
        score = similarity(text, haystack)
        # Boost obvious keyword matches.
        for keyword in candidate.get("keywords", []):
            if keyword.lower() in text.lower():
                score += 0.25
        if candidate.get("status") not in {
            ObligationStatus.cancelled.value,
            ObligationStatus.verified.value,
        }:
            score += 0.02
        if score > best_score:
            best, best_score = candidate, score
    if best is None:
        return None, 0.0
    if not include_closed and best.get("status") in {
        ObligationStatus.cancelled.value,
        ObligationStatus.verified.value,
    }:
        return None, best_score
    return best, min(round(best_score, 3), 1.0)


# ── Events ───────────────────────────────────────────────────────────────


def add_event(db: Database, event: dict) -> dict:
    doc = {
        "id": new_id("evt"),
        "occurred_at": utcnow(),
        "created_at": utcnow(),
        **event,
    }
    db.events.insert_one(dict(doc))
    return doc


def list_events(db: Database, patient_id: str, limit: int = 100) -> list[dict]:
    return list(
        db.events.find({"patient_id": patient_id}, {"_id": 0})
        .sort("occurred_at", -1)
        .limit(limit)
    )


# ── Evidence ─────────────────────────────────────────────────────────────


def _evidence_doc(
    *,
    patient_id: str,
    obligation_id: str | None,
    kind: str,
    summary: str,
    source_ref: str | None = None,
    quote: str | None = None,
    confidence: float = 0.7,
    actor: str = "Continuum",
    evidence_id: str | None = None,
) -> dict:
    return {
        "id": evidence_id or new_id("evd"),
        "patient_id": patient_id,
        "obligation_id": obligation_id,
        "kind": kind,
        "summary": truncate(summary, 600),
        "source_ref": source_ref,
        "quote": truncate(quote or "", 1500) or None,
        "confidence": confidence,
        "recorded_at": utcnow(),
        "recorded_by": actor,
    }


def add_evidence(
    db: Database,
    *,
    patient_id: str,
    obligation_id: str | None,
    kind: str,
    summary: str,
    source_ref: str | None = None,
    quote: str | None = None,
    confidence: float = 0.7,
    actor: str = "Continuum",
) -> dict:
    doc = _evidence_doc(
        patient_id=patient_id, obligation_id=obligation_id, kind=kind, summary=summary,
        source_ref=source_ref, quote=quote, confidence=confidence, actor=actor,
    )
    db.evidence.insert_one(dict(doc))
    if obligation_id:
        db.obligations.update_one(
            {"id": obligation_id},
            {
                "$push": {"evidence_ids": doc["id"]},
                "$set": {"latest_evidence": doc["summary"], "updated_at": doc["recorded_at"]},
            },
        )
    return doc


def get_evidence(db: Database, obligation_id: str) -> list[dict]:
    return list(
        db.evidence.find({"obligation_id": obligation_id}, {"_id": 0}).sort("recorded_at", -1)
    )


def get_evidence_many(db: Database, obligation_ids: list[str]) -> dict[str, list[dict]]:
    """``{id: get_evidence(db, id)}`` for many obligations in one query."""
    out: dict[str, list[dict]] = {oid: [] for oid in obligation_ids}
    if not obligation_ids:
        return out
    for doc in db.evidence.find({"obligation_id": {"$in": obligation_ids}}, {"_id": 0}).sort(
        [("recorded_at", -1), ("_id", 1)]  # ties: insertion order, as the per-id index scan
    ):
        out[doc["obligation_id"]].append(doc)
    return out


# ── Audit & memory ───────────────────────────────────────────────────────


def _audit_doc(
    *,
    patient_id: str,
    actor: str,
    action: str,
    detail: str,
    before: dict | None = None,
    after: dict | None = None,
    evidence_id: str | None = None,
    thread_id: str | None = None,
    run_id: str | None = None,
    audit_id: str | None = None,
) -> dict:
    return {
        "id": audit_id or new_id("aud"),
        "patient_id": patient_id,
        "thread_id": thread_id,
        "run_id": run_id,
        "actor": actor,
        "action": action,
        "detail": truncate(detail, 1000),
        "before": before,
        "after": after,
        "evidence_id": evidence_id,
        "created_at": utcnow(),
    }


def audit(
    db: Database,
    *,
    patient_id: str,
    actor: str,
    action: str,
    detail: str,
    before: dict | None = None,
    after: dict | None = None,
    evidence_id: str | None = None,
    thread_id: str | None = None,
    run_id: str | None = None,
) -> dict:
    doc = _audit_doc(
        patient_id=patient_id, actor=actor, action=action, detail=detail, before=before, after=after,
        evidence_id=evidence_id, thread_id=thread_id, run_id=run_id,
    )
    db.agent_audit.insert_one(dict(doc))
    return doc


def _memory_doc(
    *,
    patient_id: str,
    kind: str,
    summary: str,
    content: str,
    tags: list[str] | None = None,
    importance: float = 0.5,
    source_event_id: str | None = None,
    memory_id: str | None = None,
) -> dict:
    return {
        "id": memory_id or new_id("mem"),
        "patient_id": patient_id,
        "kind": kind,
        "summary": truncate(summary, 400),
        "content": truncate(content, 2000),
        "tags": tags or [],
        "importance": importance,
        "source_event_id": source_event_id,
        "created_at": utcnow(),
    }


def add_memory(
    db: Database,
    *,
    patient_id: str,
    kind: str,
    summary: str,
    content: str,
    tags: list[str] | None = None,
    importance: float = 0.5,
    source_event_id: str | None = None,
    embedding: list[float] | None = None,
) -> dict:
    doc = _memory_doc(
        patient_id=patient_id, kind=kind, summary=summary, content=content, tags=tags,
        importance=importance, source_event_id=source_event_id,
    )
    if embedding is None:
        # Lazy import avoids a repository -> agent -> repository cycle. With no
        # embedding key this returns None and recall falls back to lexical.
        from .agent.llm import embed_text

        embedding = embed_text(memory_embedding_text(summary, content))
    if embedding:
        doc["embedding"] = embedding
    db.memories.insert_one(dict(doc))
    doc.pop("embedding", None)
    return doc


def list_memories(db: Database, patient_id: str, limit: int = 50, kind: str | None = None) -> list[dict]:
    query: dict[str, Any] = {"patient_id": patient_id}
    if kind:
        query["kind"] = kind
    return list(
        db.memories.find(query, {"_id": 0, "embedding": 0})
        .sort("created_at", -1)
        .limit(limit)
    )


def list_audit(db: Database, patient_id: str | None = None, limit: int = 100) -> list[dict]:
    query = {"patient_id": patient_id} if patient_id else {}
    return list(
        db.agent_audit.find(query, {"_id": 0}).sort("created_at", -1).limit(limit)
    )


# ── Approvals ────────────────────────────────────────────────────────────


def approval_audience(kind: str) -> str:
    """Which role's queue an approval kind belongs to.

    Clinical questions (escalations) route to the clinician queue; every other
    sensitive decision — closures, conflicts, cancellations, overrides — stays
    with the care coordinator, who owns the operational record.
    """
    return "clinician" if kind == ApprovalKind.escalate.value else "coordinator"


def approval_doc_audience(doc: dict) -> str:
    """Audience of an approval document, tolerating legacy docs without the field."""
    return doc.get("audience") or approval_audience(doc.get("kind", ""))


def create_approval(db: Database, doc: dict) -> dict:
    audience = approval_audience(doc["kind"])
    if doc.get("obligation_id"):
        target = db.obligations.find_one({"id": doc["obligation_id"]}, {"_id": 0, "category": 1}) or {}
        if target.get("category") == "medication_reconciliation":
            audience = "clinician"  # only a clinician attests a reconciliation
    doc = {**doc, "audience": audience}
    existing = db.approvals.find_one(
        {
            "patient_id": doc["patient_id"],
            "kind": doc["kind"],
            "obligation_id": doc.get("obligation_id"),
            "status": ApprovalStatus.pending.value,
        },
        {"_id": 0},
    )
    if existing:
        # A coordinator explicitly asking for the same sensitive action should
        # upgrade a queued recommendation into a paused, human-in-the-loop run
        # rather than creating a duplicate card.
        if doc.get("mode") == "interrupt" and existing.get("mode") != "interrupt":
            db.approvals.update_one(
                {"id": existing["id"]},
                {
                    "$set": {
                        "mode": "interrupt",
                        "run_id": doc.get("run_id"),
                        "thread_id": doc.get("thread_id"),
                        "proposal": doc.get("proposal") or existing.get("proposal"),
                        "rationale": doc.get("rationale") or existing.get("rationale"),
                        "evidence_summary": doc.get("evidence_summary") or existing.get("evidence_summary"),
                        "proposed_changes": doc.get("proposed_changes") or existing.get("proposed_changes"),
                    }
                },
            )
            return get_approval(db, existing["id"]) or existing
        if doc["kind"] == ApprovalKind.escalate.value:
            return _merge_escalation(db, existing, doc)
        return existing

    approval = {
        "id": new_id("apr"),
        "status": ApprovalStatus.pending.value,
        "mode": "queued",  # queued | interrupt
        "confidence": 0.7,
        "created_at": utcnow(),
        "resolved_at": None,
        "resolved_by": None,
        "resolution_note": None,
        "applied_at": None,
        **doc,
    }
    db.approvals.insert_one(dict(approval))
    audit(
        db,
        patient_id=approval["patient_id"],
        actor="Continuum",
        action="Prepared approval request",
        detail=approval.get("title") or approval["kind"],
        after={"approval_id": approval["id"], "mode": approval["mode"]},
        thread_id=approval.get("thread_id"),
        run_id=approval.get("run_id"),
    )
    if approval["kind"] == ApprovalKind.escalate.value:
        from .notify import page_in_background

        try:
            page_in_background(db, approval)
        except Exception:  # paging must never block recording the escalation
            log.exception("Paging failed for %s", approval["id"])
        approval = get_approval(db, approval["id"]) or approval
    return approval


_URGENCY_RANK = {"routine": 0, "urgent": 1, "emergency": 2}


def _merge_escalation(db: Database, existing: dict, doc: dict) -> dict:
    """Fold a further escalation into the open one without losing its reason.

    One card per patient keeps the clinician queue (and its paging) coherent,
    but every reason is kept, and the card takes the highest urgency raised.
    A rise to urgent/emergency pages the on-call clinician.
    """
    new_reason = (doc.get("rationale") or "").strip()
    old_changes = dict(existing.get("proposed_changes") or {})
    new_changes = doc.get("proposed_changes") or {}
    reasons = list(existing.get("reasons") or [
        {"reason": existing.get("rationale"), "urgency": old_changes.get("urgency", "routine"), "at": existing.get("created_at")}
    ])
    if not new_reason or any((r.get("reason") or "").strip() == new_reason for r in reasons):
        return existing  # the same escalation sent twice (a retried tool call)
    old_level = old_changes.get("urgency", "routine")
    new_level = new_changes.get("urgency", "routine")
    level = new_level if _URGENCY_RANK.get(new_level, 0) > _URGENCY_RANK.get(old_level, 0) else old_level
    reasons.append({"reason": new_reason, "urgency": new_level, "at": utcnow()})
    combined = "\n".join(f"[{r.get('urgency', 'routine')}] {r.get('reason')}" for r in reasons)
    changes = {**old_changes, "urgency": level, "note": combined}
    update = {
        "reasons": reasons,
        "rationale": combined,
        "evidence_summary": combined,
        "proposed_changes": changes,
        "title": f"Clinical escalation ({level})" if (existing.get("title") or "").startswith("Clinical escalation") else existing.get("title"),
    }
    proposal = existing.get("proposal")
    if proposal:
        update["proposal"] = {**proposal, "rationale": combined, "evidence_summary": combined,
                              "changes": {**(proposal.get("changes") or {}), "urgency": level, "note": combined},
                              "title": update["title"]}
    db.approvals.update_one({"id": existing["id"]}, {"$set": update})
    audit(
        db,
        patient_id=existing["patient_id"],
        actor="Continuum",
        action="Added reason to open escalation",
        detail=truncate(new_reason, 300),
        after={"approval_id": existing["id"], "urgency": level},
        thread_id=existing.get("thread_id"),
        run_id=doc.get("run_id"),
    )
    merged = get_approval(db, existing["id"]) or existing
    if level != old_level and level in {"urgent", "emergency"}:
        from .notify import page_in_background

        try:
            page_in_background(db, merged)
        except Exception:
            log.exception("Paging failed for %s", existing["id"])
        merged = get_approval(db, existing["id"]) or merged
    return merged


def get_approval(db: Database, approval_id: str) -> dict | None:
    return db.approvals.find_one({"id": approval_id}, {"_id": 0})


def list_approvals(
    db: Database,
    patient_id: str | None = None,
    status: str | None = ApprovalStatus.pending.value,
    audience: str | None = None,
) -> list[dict]:
    query: dict[str, Any] = {}
    if patient_id:
        query["patient_id"] = patient_id
    if status:
        query["status"] = status
    docs = list(db.approvals.find(query, {"_id": 0}).sort("created_at", -1))
    if audience:
        docs = [
            doc
            for doc in docs
            if approval_doc_audience(doc) == audience
        ]
    return docs


def mark_approval_resolved(
    db: Database, approval_id: str, approved: bool, actor: str, note: str | None
) -> dict:
    now = utcnow()
    status = ApprovalStatus.approved.value if approved else ApprovalStatus.rejected.value
    db.approvals.update_one(
        {"id": approval_id, "status": ApprovalStatus.pending.value},
        {
            "$set": {
                "status": status,
                "resolved_at": now,
                "resolved_by": actor,
                "resolution_note": note,
            }
        },
    )
    approval = get_approval(db, approval_id) or {}
    audit(
        db,
        patient_id=approval.get("patient_id", "unknown"),
        actor=actor,
        action=f"Approval {status}",
        detail=f"{approval.get('title', approval_id)}" + (f" — {note}" if note else ""),
        after={"approval_id": approval_id, "status": status},
        thread_id=approval.get("thread_id"),
        run_id=approval.get("run_id"),
    )
    from . import ledger  # late import: ledger imports repository

    ledger.on_approval_resolved(db, approval)
    return approval


def mark_approval_applied(db: Database, approval_id: str) -> None:
    db.approvals.update_one({"id": approval_id}, {"$set": {"applied_at": utcnow()}})


# ── Voice calls & check-ins ──────────────────────────────────────────────


def upsert_voice_call(db: Database, doc: dict) -> dict:
    existing = None
    if doc.get("elevenlabs_conversation_id"):
        existing = db.voice_calls.find_one(
            {"elevenlabs_conversation_id": doc["elevenlabs_conversation_id"]}, {"_id": 0}
        )
    if existing:
        db.voice_calls.update_one({"id": existing["id"]}, {"$set": doc})
        return {**existing, **doc}
    call = {
        "id": new_id("call"),
        "created_at": utcnow(),
        "started_at": utcnow(),
        "outcome": "in_progress",
        **doc,
    }
    db.voice_calls.insert_one(dict(call))
    return call


def list_voice_calls(db: Database, patient_id: str) -> list[dict]:
    return list(
        db.voice_calls.find({"patient_id": patient_id}, {"_id": 0})
        .sort("created_at", -1)
        .limit(50)
    )


def schedule_checkin(db: Database, doc: dict) -> dict:
    """Store a check-in, resolving loose times ("tomorrow afternoon") once,
    relative to the day it was requested, so the sweep has a fixed target."""
    from .db import demo_today
    from .util import resolve_when

    doc = dict(doc)
    raw = doc.get("scheduled_for")
    if raw:
        dt, d = resolve_when(raw, demo_today())
        resolved = dt.isoformat() if dt else d.isoformat() if d else None
        if resolved and resolved != raw:
            doc["scheduled_for"] = resolved
            doc["scheduled_for_raw"] = raw
        doc["scheduled_for_resolved"] = bool(resolved)
    checkin = {"id": new_id("chk"), "status": "scheduled", "created_at": utcnow(), **doc}
    db.checkins.insert_one(dict(checkin))
    return checkin


def list_checkins(db: Database, patient_id: str) -> list[dict]:
    return list(
        db.checkins.find({"patient_id": patient_id}, {"_id": 0}).sort("scheduled_for", 1)
    )


# ── Dashboard aggregates ─────────────────────────────────────────────────


def dashboard(db: Database, today: date | None = None, audience: str | None = None) -> dict:
    # Every obligation is read once and shared by the patient rows and the
    # stats; pending approvals are read once for the count and the list.
    patient_docs = list(db.patients.find({}, {"_id": 0}))
    obligations = _presented_obligations(db, None, today)
    known = {p["id"] for p in patient_docs}
    patients = _patient_rows(patient_docs, [o for o in obligations if o.get("patient_id") in known])
    open_items = [o for o in obligations if o["is_open"]]
    pending = list_approvals(db, status=ApprovalStatus.pending.value, audience=audience)
    return {
        "patients": patients,
        "stats": {
            "patients": len(patients),
            "open_obligations": len(open_items),
            "overdue": sum(1 for o in open_items if o["status"] == "overdue"),
            "conflicts": sum(1 for o in open_items if o["status"] == "conflict"),
            "awaiting_verification": sum(
                1 for o in open_items if o["status"] == "patient_reported"
            ),
            "escalated": sum(1 for o in open_items if o["status"] == "escalated"),
            "pending_approvals": len(pending),
            "verified_this_episode": sum(
                1 for o in obligations if o["status"] == "verified"
            ),
        },
        "priority_obligations": sort_obligations(open_items)[:12],
        "pending_approvals": pending,
        "recent_events": list(db.events.find({}, {"_id": 0}).sort("occurred_at", -1).limit(12)),
        "recent_audit": list_audit(db, limit=12),
    }


def journey(db: Database, patient_id: str, today: date | None = None) -> dict:
    patient = get_patient(db, patient_id)
    if not patient:
        return {}
    obligations = list_obligations(db, patient_id, today=today)
    evidence = get_evidence_many(db, [o["id"] for o in obligations])
    return {
        "patient": patient,
        "episode": get_active_episode(db, patient_id),
        "obligations": obligations,
        "evidence": evidence,
        "events": list_events(db, patient_id, limit=200),
        "memories": list_memories(db, patient_id, limit=60),
        "approvals": list_approvals(db, patient_id, status=None),
        "audit": list_audit(db, patient_id, limit=200),
        "voice_calls": list_voice_calls(db, patient_id),
        "checkins": list_checkins(db, patient_id),
    }
