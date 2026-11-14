"""Memory retrieval: Atlas Vector Search with a lexical fallback.

Structured operational questions ("what is overdue?") always use MongoDB
queries. This module is only for recalling *context* — prior interactions,
transcripts and narrative notes — so a lexical fallback is acceptable when the
cluster has no vector index.
"""

from __future__ import annotations

import logging
import math
import re
from datetime import date, datetime, timezone

from pymongo.database import Database

from ..config import settings
from ..repository import list_obligations
from ..schemas import ObligationStatus
from ..util import STOPWORDS, truncate, utcnow
from .llm import embed_text, get_embeddings

log = logging.getLogger("continuum.memory")


SUMMARY_TAG = "rolling_summary"
RECENCY_HALF_LIFE_DAYS = 14.0


def _words(text: str) -> list[str]:
    return [w for w in re.findall(r"[a-z0-9]+", (text or "").lower()) if len(w) > 2 and w not in STOPWORDS]


def _age_days(created_at: str | None, now: datetime) -> float:
    try:
        created = datetime.fromisoformat(str(created_at).replace("Z", "+00:00"))
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        return max((now - created).total_seconds() / 86400, 0.0)
    except (TypeError, ValueError):
        return 30.0


def _prior(doc: dict, now: datetime) -> float:
    """Query-independent weight: importance and recency; agent echoes count less."""
    recency = 0.5 ** (_age_days(doc.get("created_at"), now) / RECENCY_HALF_LIFE_DAYS)
    weight = 0.6 + 0.4 * float(doc.get("importance", 0.5))
    weight *= 0.7 + 0.3 * recency
    if "response" in (doc.get("tags") or []):
        weight *= 0.5  # Continuum's own replies restate other memories
    return weight


def _dedupe(results: list[dict], k: int) -> list[dict]:
    seen: set[str] = set()
    out = []
    for r in results:
        key = " ".join(_words(r.get("summary", "")))[:120]
        if key in seen:
            continue
        seen.add(key)
        out.append(r)
        if len(out) >= k:
            break
    return out


def memory_vector_store(db: Database):
    """LangChain ``MongoDBAtlasVectorSearch`` over the ``memories`` collection.

    Returns None when there is no embedding model or the database is the
    in-memory mock (no Atlas Search there). The index itself is created on
    startup by ``ensure_vector_index()`` (see docs/atlas.md).
    """
    if settings.is_memory_db:
        return None
    embeddings = get_embeddings()
    if embeddings is None:
        return None
    try:
        from langchain_mongodb import MongoDBAtlasVectorSearch

        return MongoDBAtlasVectorSearch(
            collection=db.memories,
            embedding=embeddings,
            index_name=settings.mongodb_vector_index,
            text_key="content",
            embedding_key="embedding",
            relevance_score_fn="cosine",
            auto_create_index=False,
        )
    except Exception as exc:  # pragma: no cover - optional dependency
        log.info("LangChain vector store unavailable: %s", exc)
        return None


def patient_memory_retriever(db: Database, patient_id: str, k: int = 6):
    """A LangChain retriever scoped to one patient's memories (or None)."""
    store = memory_vector_store(db)
    if store is None:
        return None
    return store.as_retriever(search_kwargs={"k": k, "pre_filter": {"patient_id": {"$eq": patient_id}}})


def semantic_recall(
    db: Database,
    patient_id: str,
    query: str,
    k: int = 6,
    *,
    allow_lexical: bool = True,
) -> list[dict]:
    now = datetime.now(timezone.utc)
    store = memory_vector_store(db) if query else None
    if store is not None:
        try:
            hits = store.similarity_search_with_score(
                query,
                k=k * 3,
                pre_filter={"patient_id": {"$eq": patient_id}},
                oversampling_factor=max(4, 100 // (k * 3)),
            )
            results = []
            for doc, vector_score in hits:
                meta = {key: v for key, v in doc.metadata.items() if key not in {"_id", "embedding"}}
                results.append({**meta, "content": doc.page_content, "vector_score": vector_score})
            if results:
                # Re-rank vector hits with importance and recency, then dedupe.
                for r in results:
                    r["score"] = round(float(r["vector_score"] or 0) * _prior(r, now), 4)
                    r["retrieval"] = "atlas_vector"
                results.sort(key=lambda r: r["score"], reverse=True)
                return _dedupe(results, k)
        except Exception as exc:  # pragma: no cover - index dependent
            log.info("Vector recall unavailable, using lexical recall: %s", exc)

    if not allow_lexical:
        return []
    return lexical_recall(db, patient_id, query, k)


def lexical_recall(db: Database, patient_id: str, query: str, k: int = 6) -> list[dict]:
    """BM25 over the patient's memories, weighted by importance and recency."""
    now = datetime.now(timezone.utc)
    docs = list(
        db.memories.find({"patient_id": patient_id}, {"_id": 0, "embedding": 0})
        .sort("created_at", -1)
        .limit(1000)
    )
    if not docs:
        return []
    corpus = [
        _words(f"{d.get('summary', '')} {d.get('summary', '')} {d.get('content', '')} {' '.join(d.get('tags', []))}")
        for d in docs
    ]
    q = set(_words(query))
    if not q:
        # No usable terms: most important, most recent first.
        ranked = sorted(docs, key=lambda d: _prior(d, now), reverse=True)
        return _dedupe([{**d, "score": round(_prior(d, now), 4), "retrieval": "lexical"} for d in ranked], k)

    n = len(docs)
    avg_len = sum(len(c) for c in corpus) / n or 1.0
    df = {term: sum(1 for c in corpus if term in c) for term in q}
    k1, b = 1.4, 0.75
    scored = []
    for doc, words in zip(docs, corpus):
        if not words:
            continue
        bm25 = 0.0
        for term in q:
            tf = words.count(term)
            if not tf:
                continue
            idf = math.log(1 + (n - df[term] + 0.5) / (df[term] + 0.5))
            bm25 += idf * tf * (k1 + 1) / (tf + k1 * (1 - b + b * len(words) / avg_len))
        if bm25 <= 0:
            continue
        if SUMMARY_TAG in (doc.get("tags") or []):
            bm25 *= 0.6  # the summary is always in context anyway
        scored.append({**doc, "score": round(bm25 * _prior(doc, now), 4), "retrieval": "lexical"})
    scored.sort(key=lambda d: d["score"], reverse=True)
    return _dedupe(scored, k)


def build_patient_summary(
    db: Database,
    patient_id: str,
    today: date | None = None,
    *,
    patient: dict | None = None,
    obligations: list[dict] | None = None,
) -> str:
    """A compact, always-current narrative of where this patient's care stands.

    ``patient`` / ``obligations`` (from ``list_obligations``) may be passed
    by a caller that already read them, to save the round trips.
    """
    if patient is None:
        patient = db.patients.find_one({"id": patient_id}, {"_id": 0}) or {}
    if obligations is None:
        obligations = list_obligations(db, patient_id, include_closed=True, today=today)
    open_items = [o for o in obligations if o["is_open"]]
    closed = [o for o in obligations if not o["is_open"]]
    prefs = patient.get("preferences") or {}
    lines = [
        f"{patient.get('name', patient_id)}"
        + (f", {patient.get('age')}" if patient.get("age") else "")
        + (f", {patient.get('condition')}" if patient.get("condition") else "")
        + "."
    ]
    if prefs:
        bits = [
            f"prefers {prefs['channel']}" if prefs.get("channel") else "",
            f"best time {prefs['best_time']}" if prefs.get("best_time") else "",
            "no automated calls" if prefs.get("consent_to_automated_calls") is False else "",
        ]
        if patient.get("caregiver"):
            bits.append(f"caregiver {patient['caregiver']}")
        bits = [b for b in bits if b]
        if bits:
            lines.append("Contact: " + ", ".join(bits) + ".")
    if open_items:
        def describe(o: dict) -> str:
            when = o.get("confirmed_date") or o.get("due_date") or "no date"
            extra = ""
            if o["status"] == ObligationStatus.overdue.value:
                extra = f", {o.get('days_overdue')}d overdue"
            elif o["status"] == ObligationStatus.conflict.value:
                extra = ", conflicting dates"
            return f"{o['description']} ({o['status'].replace('_', ' ')}, {when}{extra})"

        lines.append("Open: " + "; ".join(describe(o) for o in open_items) + ".")
    if closed:
        lines.append("Closed: " + "; ".join(f"{o['description']} ({o['status']})" for o in closed) + ".")
    pending = list(db.approvals.find({"patient_id": patient_id, "status": "pending"}, {"_id": 0, "title": 1}))
    if pending:
        lines.append("Waiting on the care team: " + "; ".join(a["title"] for a in pending[:5]) + ".")
    last = db.events.find_one({"patient_id": patient_id}, {"_id": 0}, sort=[("occurred_at", -1)])
    if last:
        lines.append(f"Last contact: {last.get('title')} ({str(last.get('occurred_at', ''))[:10]}).")
    return " ".join(lines)


def refresh_patient_summary(
    db: Database,
    patient_id: str,
    today: date | None = None,
    *,
    patient: dict | None = None,
    obligations: list[dict] | None = None,
) -> dict | None:
    """Upsert the single rolling-summary memory for a patient (no history spam)."""
    if patient is None:
        patient = db.patients.find_one({"id": patient_id}, {"_id": 0})
    if not patient:
        return None
    text = build_patient_summary(db, patient_id, today, patient=patient, obligations=obligations)
    existing = db.memories.find_one({"patient_id": patient_id, "tags": SUMMARY_TAG}, {"_id": 0, "embedding": 0})
    if existing and existing.get("content") == text:
        return existing
    doc = {
        "kind": "patient_context",
        "summary": truncate(f"Current picture: {text}", 400),
        "content": text,
        "tags": [SUMMARY_TAG, "summary"],
        "importance": 1.0,
        "created_at": utcnow(),
    }
    vector = embed_text(text)
    if vector:
        doc["embedding"] = vector
    if existing:
        db.memories.update_one({"id": existing["id"]}, {"$set": doc})
        return {**existing, **{k: v for k, v in doc.items() if k != "embedding"}}
    doc.update({"id": f"mem_summary_{patient_id}", "patient_id": patient_id, "source_event_id": None})
    db.memories.insert_one(dict(doc))
    doc.pop("embedding", None)
    return doc


def retrieve_context(
    db: Database,
    patient_id: str,
    query: str,
    *,
    k: int = 6,
    today: date | None = None,
) -> dict:
    """Build the retrieval bundle used by the reasoning node and chat prompt."""
    obligations = list_obligations(db, patient_id, include_closed=True, today=today)
    open_items = [o for o in obligations if o["is_open"]]
    # One patient read serves the summary and the bundle (nothing below writes it).
    patient = db.patients.find_one({"id": patient_id}, {"_id": 0}) or {}
    summary = refresh_patient_summary(db, patient_id, today, patient=patient or None, obligations=obligations) if patient else None
    memories = [m for m in semantic_recall(db, patient_id, query, k=k + 1) if SUMMARY_TAG not in (m.get("tags") or [])][:k]
    events = list(
        db.events.find({"patient_id": patient_id}, {"_id": 0})
        .sort("occurred_at", -1)
        .limit(8)
    )
    preferences = patient.get("preferences", {})
    return {
        "query": query,
        "patient": patient,
        "open_obligations": open_items,
        "closed_obligations": [o for o in obligations if not o["is_open"]],
        "summary": (summary or {}).get("content", ""),
        "memories": memories,
        "recent_events": events,
        "preferences": preferences,
        "counts": {
            "open": len(open_items),
            "overdue": sum(1 for o in open_items if o["status"] == ObligationStatus.overdue.value),
            "conflicts": sum(1 for o in open_items if o["status"] == ObligationStatus.conflict.value),
            "patient_reported": sum(
                1 for o in open_items if o["status"] == ObligationStatus.patient_reported.value
            ),
        },
    }


def render_context(bundle: dict) -> str:
    """Compact, prompt-safe rendering of the retrieval bundle."""
    lines: list[str] = []
    patient = bundle.get("patient") or {}
    if patient:
        lines.append(
            f"Patient: {patient.get('name')}, {patient.get('age')}, {patient.get('condition')}. "
            f"Preferred channel: {bundle.get('preferences', {}).get('channel', 'unspecified')}."
        )
    if bundle.get("summary"):
        lines.append(f"Current picture: {bundle['summary']}")
    lines.append("Open obligations:")
    for o in bundle.get("open_obligations", []):
        conflict = f" | conflict: {o.get('conflicts')}" if o.get("conflicts") else ""
        lines.append(
            f"- [{o['id']}] {o['description']} | owner: {o.get('owner')} | "
            f"due: {o.get('due_date') or 'unspecified'} | status: {o['status']}{conflict}"
        )
    if not bundle.get("open_obligations"):
        lines.append("- none")
    lines.append("Recent interactions:")
    for e in bundle.get("recent_events", [])[:6]:
        lines.append(f"- {e.get('occurred_at')}: {e.get('title')} — {e.get('detail', '')}")
    lines.append("Recalled memory:")
    for m in bundle.get("memories", [])[:6]:
        lines.append(f"- ({m.get('kind')}, {m.get('retrieval')}) {m.get('summary')}")
    return "\n".join(lines)
