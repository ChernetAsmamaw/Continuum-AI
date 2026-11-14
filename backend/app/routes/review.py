"""Coordinator review queue: things the ingestion layer refused to guess.

* **Order types.** A follow-up created from an EHR order the rules didn't
  recognize (``classified_by.method == "unrecognized"``) or placed by
  similarity to a past order (``known_code`` / ``similar_order`` /
  ``similar_order_lexical``). The coordinator confirms or corrects the
  category; the answer is taught back to ``order_memory`` through
  ``learn_correction`` so the next identical order is recognized exactly.
* **Identity.** A discharge whose patient matched an existing record by name
  only. Continuum never merges on its own; the coordinator decides "same
  person" (merge, fully audited, tombstone kept) or "different people"
  (dismiss).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from .. import repository as repo
from ..agent.extraction import CATEGORY_RULES
from ..db import demo_today, get_db
from ..deps import actor_label, require_role
from ..ingest.orders import learn_correction, order_text
from ..util import new_id, utcnow

router = APIRouter(prefix="/review", tags=["review"])

coordinator = require_role("coordinator")

# Categories a follow-up can be filed under, with the order kind each implies.
EXTRA_CATEGORIES = [
    {"category": "referral", "label": "Referral (other specialty)", "owner": "Care team", "kind": "referral"},
    {"category": "follow_up", "label": "General follow-up", "owner": "Care team", "kind": "other"},
    {"category": "pending_result", "label": "Pending result", "owner": "Care team", "kind": "lab"},
]
KIND_FOR = {"bloodwork": "lab", "diagnostic_imaging": "imaging", "pending_result": "lab", "follow_up": "other"}

# Every collection that carries a patient's records (moved on merge).
PATIENT_COLLECTIONS = (
    "care_episodes",
    "obligations",
    "evidence",
    "events",
    "memories",
    "agent_audit",
    "approvals",
    "voice_calls",
    "checkins",
    "actions",
    "ingest_jobs",
)


def _categories() -> list[dict]:
    rules = [
        {
            "category": r["category"],
            "label": r["category"].replace("_", " ").capitalize(),
            "owner": r.get("owner"),
            "kind": KIND_FOR.get(r["category"], "referral"),
        }
        for r in CATEGORY_RULES
    ]
    seen = {r["category"] for r in rules}
    return rules + [c for c in EXTRA_CATEGORIES if c["category"] not in seen]


def _actor(user: dict) -> str:
    return actor_label(user)


def _needs_order_review(ob: dict) -> bool:
    cb = ob.get("classified_by") or {}
    return bool(cb) and not cb.get("confirmed")


def _source_order(db, ob: dict) -> dict | None:
    """The EHR order this follow-up came from, from the ingestion job's saved packet."""
    for ref in ob.get("fhir_refs") or []:
        if not str(ref).startswith("ServiceRequest/"):
            continue
        job = db.ingest_jobs.find_one({"packet.orders.ref": ref}, {"_id": 0, "packet.orders": 1})
        for order in ((job or {}).get("packet") or {}).get("orders") or []:
            if order.get("ref") == ref:
                return order
    return None


# ── overview ─────────────────────────────────────────────────────────────


@router.get("")
def overview(patient_id: str | None = None) -> dict:
    """Everything waiting for a coordinator: order types to confirm, identities to review."""
    db = get_db()
    names = {p["id"]: p.get("name") for p in db.patients.find({}, {"_id": 0, "id": 1, "name": 1})}
    query: dict = {"classified_by": {"$exists": True}}
    if patient_id:
        query["patient_id"] = patient_id
    orders = []
    for ob in db.obligations.find(query, {"_id": 0}):
        if not _needs_order_review(ob):
            continue
        item = repo.present_obligation(ob, demo_today())
        item["patient_name"] = names.get(ob["patient_id"])
        item["source_order"] = _source_order(db, ob)
        item["possible_duplicates"] = [
            {"id": d["id"], "description": d.get("description"), "category": d.get("category"),
             "quote": (d.get("source") or {}).get("quote"), "due_date": d.get("due_date")}
            for d in db.obligations.find({"possible_duplicate_of": ob["id"], "status": {"$nin": list(CLOSED)}}, {"_id": 0})
        ]
        orders.append(item)
    identities = [_identity_case(db, p) for p in db.patients.find({"identity_review.status": "needed"}, {"_id": 0})]
    if patient_id:
        identities = [c for c in identities if c["patient"]["id"] == patient_id]
    return {
        "orders": orders,
        "identities": identities,
        "categories": _categories(),
        "counts": {"orders": len(orders), "identities": len(identities), "total": len(orders) + len(identities)},
    }


# ── order types ──────────────────────────────────────────────────────────


class OrderReview(BaseModel):
    category: str = Field(min_length=2)
    note: str | None = None


@router.post("/orders/{obligation_id}")
def confirm_order(obligation_id: str, payload: OrderReview, user: dict = Depends(coordinator)) -> dict:
    """Confirm (same category) or correct (new category) an inferred/unrecognized order type."""
    db = get_db()
    ob = repo.get_obligation(db, obligation_id)
    if not ob:
        raise HTTPException(status_code=404, detail="Follow-up not found")
    if not ob.get("classified_by"):
        raise HTTPException(status_code=422, detail="This follow-up's type came from the EHR directly; nothing to confirm.")
    if ob["classified_by"].get("confirmed"):
        raise HTTPException(status_code=409, detail="Order type was already confirmed.")
    cats = {c["category"]: c for c in _categories()}
    if payload.category not in cats:
        raise HTTPException(status_code=422, detail=f"Unknown category '{payload.category}'")

    actor = _actor(user)
    now = utcnow()
    before_cat = ob.get("category")
    corrected = payload.category != before_cat
    target = cats[payload.category]
    order = _source_order(db, ob) or {}
    code = order.get("code")
    performer = order.get("performer")
    text = order_text(order.get("display") or (ob.get("source") or {}).get("quote") or ob["description"], performer)
    kind = target["kind"] if corrected or not order.get("kind") or order.get("kind") == "other" else order["kind"]

    update: dict = {
        "category": payload.category,
        "classified_by.confirmed": True,
        "classified_by.confirmed_by": actor,
        "classified_by.confirmed_at": now,
        "classified_by.outcome": "corrected" if corrected else "confirmed",
        "classified_by.original_category": before_cat,
        "confidence": 0.95,
        "updated_at": now,
    }
    # Owner followed the old category's default: follow the new one.
    old_owner = next((c.get("owner") for c in cats.values() if c["category"] == before_cat), None)
    if corrected and target.get("owner") and ob.get("owner") in (old_owner, "Care team", None):
        update["owner"] = target["owner"]
    reason = (
        f"Order type corrected: {before_cat} → {payload.category}" if corrected else f"Order type confirmed as {payload.category}"
    ) + (f" ({payload.note})" if payload.note else "")
    db.obligations.update_one(
        {"id": obligation_id},
        {
            "$set": update,
            "$unset": {"next_action": "", "suggested_category": "", "suggested_by": ""},
            "$inc": {"version": 1},
            "$push": {"history": {"at": now, "status": ob.get("status"), "actor": actor, "reason": reason}},
        },
    )
    learn_correction(db, code=code, text=text, kind=kind, category=payload.category, performer=performer, actor=actor)
    repo.audit(
        db,
        patient_id=ob["patient_id"],
        actor=actor,
        action="Corrected order type" if corrected else "Confirmed order type",
        detail=f"{ob['description']}: {reason}. Learned for future orders ({'code ' + code if code else 'by text'}).",
        before={"category": before_cat, "classified_by": ob["classified_by"]},
        after={"category": payload.category, "learned_key": f"code:{code}" if code else f"text:{text}"},
    )
    repo.add_event(
        db,
        {
            "patient_id": ob["patient_id"],
            "type": "coordinator",
            "title": "Order type corrected" if corrected else "Order type confirmed",
            "detail": reason,
            "actor": actor,
            "metadata": {"obligation_id": obligation_id},
        },
    )
    duplicates = _resolve_duplicates(db, ob, payload.category, actor)
    return {
        "obligation": repo.present_obligation(repo.get_obligation(db, obligation_id), demo_today()),
        "outcome": "corrected" if corrected else "confirmed",
        "learned": {"code": code, "text": text, "category": payload.category},
        "duplicates": duplicates,
    }


CLOSED = ("verified", "cancelled")


def _resolve_duplicates(db, order: dict, category: str, actor: str) -> list[dict]:
    """Settle narrative follow-ups linked to this order as possible duplicates.

    Same type as the confirmed order: the same follow-up, so the AI copy is
    cancelled (its quote kept as evidence on the order). Different type: they
    were different follow-ups after all, so the link is cleared.
    """
    out: list[dict] = []
    for ai in db.obligations.find({"possible_duplicate_of": order["id"]}, {"_id": 0}):
        label = order.get("description", "EHR order")
        if ai.get("category") == category and ai.get("status") not in CLOSED:
            quote = (ai.get("source") or {}).get("quote")
            if quote:
                repo.add_evidence(
                    db, patient_id=order["patient_id"], obligation_id=order["id"], kind="document",
                    summary=f"Discharge narrative names the same follow-up: {quote}", quote=quote, confidence=0.9, actor=actor,
                )
            repo.set_obligation_status(
                db, ai, "cancelled", actor,
                f"Same follow-up as EHR order \u201c{label}\u201d ({order['id']}), confirmed as {category}.",
                next_action="",
            )
            db.obligations.update_one({"id": ai["id"]}, {"$set": {"duplicate_of": order["id"]}, "$unset": {"possible_duplicate_of": ""}})
            out.append({"id": ai["id"], "resolution": "cancelled_duplicate"})
        else:
            db.obligations.update_one({"id": ai["id"]}, {"$unset": {"possible_duplicate_of": "", "next_action": ""}})
            repo.audit(
                db, patient_id=order["patient_id"], actor=actor, action="Kept separate follow-up",
                detail=(f"\u201c{ai.get('description')}\u201d is not EHR order \u201c{label}\u201d: "
                        f"the order was confirmed as {category}, this follow-up is {ai.get('category')}."),
                after={"obligation_id": ai["id"]},
            )
            out.append({"id": ai["id"], "resolution": "kept"})
    return out


# ── identity ─────────────────────────────────────────────────────────────


def _footprint(db, patient_id: str) -> dict:
    return {
        "episodes": db.care_episodes.count_documents({"patient_id": patient_id}),
        "follow_ups": db.obligations.count_documents({"patient_id": patient_id}),
        "open_follow_ups": sum(
            1 for o in db.obligations.find({"patient_id": patient_id}, {"_id": 0, "status": 1})
            if o.get("status") not in ("verified", "cancelled")
        ),
        "memories": db.memories.count_documents({"patient_id": patient_id}),
    }


def _identity_view(db, p: dict) -> dict:
    episode = repo.get_active_episode(db, p["id"]) or {}
    ehr = p.get("ehr") or {}
    return {
        "id": p["id"],
        "name": p.get("name"),
        "mrn": p.get("mrn"),
        "birth_date": p.get("birth_date"),
        "age": p.get("age"),
        "phone": p.get("phone"),
        "condition": p.get("condition"),
        "ehr_fhir_id": ehr.get("fhir_id"),
        "ehr_identifiers": ehr.get("identifiers") or [],
        "latest_discharge": episode.get("discharge_date"),
        "facility": (episode.get("ehr") or {}).get("facility"),
        "created_at": p.get("created_at"),
        **_footprint(db, p["id"]),
    }


def _identity_case(db, p: dict) -> dict:
    review = p.get("identity_review") or {}
    candidates = [c for c in (repo.get_patient(db, cid) for cid in review.get("candidates") or []) if c]
    return {
        "patient": _identity_view(db, p),
        "reason": review.get("reason"),
        "status": review.get("status"),
        "candidates": [_identity_view(db, c) for c in candidates],
    }


def _pending_case(db, patient_id: str) -> dict:
    p = repo.get_patient(db, patient_id)
    if not p:
        raise HTTPException(status_code=404, detail="Patient not found")
    if (p.get("identity_review") or {}).get("status") != "needed":
        raise HTTPException(status_code=409, detail="No identity review is pending for this patient.")
    return p


class MergeRequest(BaseModel):
    into: str
    note: str | None = None


class DismissRequest(BaseModel):
    note: str | None = None


@router.post("/identity/{patient_id}/merge")
def merge(patient_id: str, payload: MergeRequest, user: dict = Depends(coordinator)) -> dict:
    """Same person: move every record from the flagged duplicate onto the existing patient."""
    db = get_db()
    source = _pending_case(db, patient_id)
    if payload.into not in ((source.get("identity_review") or {}).get("candidates") or []):
        raise HTTPException(status_code=422, detail="Merge target must be one of the review candidates.")
    target = repo.get_patient(db, payload.into)
    if not target:
        raise HTTPException(status_code=404, detail="Merge target no longer exists.")

    actor = _actor(user)
    now = utcnow()
    moved: dict[str, int] = {}
    for coll in PATIENT_COLLECTIONS:
        result = db[coll].update_many({"patient_id": patient_id}, {"$set": {"patient_id": payload.into}})
        moved[coll] = result.modified_count
    # LangGraph threads key on patient id; the old thread's notes are already in events/memories.

    # Carry the EHR identity over so the next discharge for this person matches exactly.
    s_ehr, t_ehr = source.get("ehr") or {}, target.get("ehr") or {}
    target_update: dict = {"updated_at": now}
    if s_ehr and not t_ehr.get("fhir_id"):
        target_update["ehr"] = {**t_ehr, **s_ehr}
    elif s_ehr:
        ids = list(t_ehr.get("identifiers") or [])
        for ident in s_ehr.get("identifiers") or []:
            if ident not in ids:
                ids.append(ident)
        target_update["ehr.identifiers"] = ids
        if s_ehr.get("fhir_id") and s_ehr["fhir_id"] != t_ehr.get("fhir_id"):
            target_update["ehr.linked_fhir_ids"] = sorted(set((t_ehr.get("linked_fhir_ids") or []) + [s_ehr["fhir_id"]]))
    if source.get("birth_date") and not target.get("birth_date"):
        target_update["birth_date"] = source["birth_date"]
    if source.get("phone") and not target.get("phone"):
        target_update["phone"] = source["phone"]
    db.patients.update_one(
        {"id": payload.into},
        {"$set": target_update, "$push": {"merged_from": {"patient_id": patient_id, "at": now, "by": actor}}},
    )

    merge_id = new_id("mrg")
    db.patient_merges.insert_one(
        {"id": merge_id, "from": source, "into": payload.into, "moved": moved, "actor": actor, "note": payload.note, "at": now}
    )
    db.patients.delete_one({"id": patient_id})
    total = sum(moved.values())
    detail = (
        f"{source.get('name')} [{patient_id}, MRN {source.get('mrn')}] confirmed as the same person; "
        f"{total} records moved ({', '.join(f'{v} {k}' for k, v in moved.items() if v)})."
        + (f" Note: {payload.note}" if payload.note else "")
    )
    repo.audit(
        db,
        patient_id=payload.into,
        actor=actor,
        action="Merged duplicate patient",
        detail=detail,
        before={"duplicate": {k: source.get(k) for k in ("id", "name", "mrn", "birth_date", "ehr", "identity_review")}},
        after={"into": payload.into, "moved": moved, "merge_id": merge_id},
    )
    repo.add_event(
        db,
        {
            "patient_id": payload.into,
            "type": "coordinator",
            "title": "Duplicate record merged",
            "detail": detail,
            "actor": actor,
            "metadata": {"merge_id": merge_id, "from_patient_id": patient_id},
        },
    )
    return {"ok": True, "merged_into": payload.into, "merge_id": merge_id, "moved": moved}


@router.post("/identity/{patient_id}/dismiss")
def dismiss(patient_id: str, payload: DismissRequest, user: dict = Depends(coordinator)) -> dict:
    """Different people: keep both records and clear the flag."""
    db = get_db()
    p = _pending_case(db, patient_id)
    actor = _actor(user)
    review = p.get("identity_review") or {}
    now = utcnow()
    db.patients.update_one(
        {"id": patient_id},
        {"$set": {
            "identity_review.status": "dismissed",
            "identity_review.resolved_by": actor,
            "identity_review.resolved_at": now,
            "identity_review.note": payload.note,
        }},
    )
    detail = f"Not the same person as {', '.join(review.get('candidates') or [])}; both records kept." + (
        f" Note: {payload.note}" if payload.note else ""
    )
    repo.audit(
        db,
        patient_id=patient_id,
        actor=actor,
        action="Dismissed identity review",
        detail=detail,
        before={"identity_review": review},
        after={"identity_review": {**review, "status": "dismissed"}},
    )
    repo.add_event(
        db,
        {"patient_id": patient_id, "type": "coordinator", "title": "Identity review dismissed", "detail": detail, "actor": actor},
    )
    return {"ok": True, "patient_id": patient_id, "status": "dismissed"}
