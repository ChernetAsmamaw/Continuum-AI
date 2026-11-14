"""Learn from past orders so a changed EHR doesn't break follow-up creation.

Hospitals change codes and layouts: a referral that arrived as SNOMED
"Referral to cardiology service" may next week arrive as a local
"HFC-01 · HF clinic consult" with no category at all. The rules can't place
that. Instead of dropping it or filing it as a generic to-do, Continuum asks
its own history:

1. **Known code**: has this exact order code been seen and classified before?
2. **Similar order**: Atlas Vector Search over past orders (embedding of the
   order text), above a similarity threshold.
3. **Word overlap**: the same idea without embeddings (mongomock, no model).

Every rule-classified order is remembered (``order_memory``), so the next
encounter of that code is recognised exactly. Inferred classifications are
flagged on the follow-up for a coordinator to confirm.
"""

from __future__ import annotations

import logging
import re

from pymongo.database import Database

from ..config import settings
from ..util import new_id, utcnow

log = logging.getLogger("continuum.ingest.orders")

INDEX_NAME = "orders_vector"
VECTOR_THRESHOLD = 0.72
LEXICAL_THRESHOLD = 0.5
STOP = {"the", "of", "to", "and", "for", "with", "in", "a", "an", "visit", "service", "order", "procedure"}


def order_text(display: str, performer: str | None = None) -> str:
    return " ".join(p for p in (display, performer or "") if p).strip()


def _tokens(text: str) -> set[str]:
    return {t for t in re.findall(r"[a-z0-9]+", text.lower()) if t not in STOP and len(t) > 1}


def _embed(text: str) -> list[float] | None:
    try:
        from ..agent.llm import embed_text

        return embed_text(text)
    except Exception as exc:  # pragma: no cover - model/env dependent
        log.debug("Order embedding unavailable: %s", exc)
        return None


def _embed_many(texts: list[str]) -> list[list[float] | None]:
    try:
        from ..agent.llm import embed_texts

        return embed_texts(texts)
    except Exception as exc:  # pragma: no cover - model/env dependent
        log.debug("Order embedding unavailable: %s", exc)
        return [None] * len(texts)


def order_key(code: str | None, text: str) -> str:
    return f"code:{code}" if code else f"text:{' '.join(sorted(_tokens(text)))}"


def ensure_order_index(db: Database) -> bool:
    """Atlas Vector Search index over past orders (no-op on mongomock)."""
    if settings.is_memory_db:
        return False
    from ..agent.llm import embedding_dimensions

    dims = embedding_dimensions()
    if not dims:
        return False
    try:
        if "order_memory" not in db.list_collection_names():
            db.create_collection("order_memory")
        existing = next(iter(db.order_memory.list_search_indexes(INDEX_NAME)), None)
        if existing:
            fields = (existing.get("latestDefinition") or existing.get("definition") or {}).get("fields") or []
            current = next((f.get("numDimensions") for f in fields if f.get("type") == "vector"), None)
            if current == dims:
                _reembed_orders(db, dims)
                return True
            # The embedding model changed (e.g. local 384 → Voyage 1024): the
            # old index would silently match nothing. Rebuild it and re-embed.
            log.info("Order index has %s dims but the model uses %s; rebuilding.", current, dims)
            db.order_memory.drop_search_index(INDEX_NAME)
            import time

            for _ in range(30):  # Atlas drops asynchronously
                if next(iter(db.order_memory.list_search_indexes(INDEX_NAME)), None) is None:
                    break
                time.sleep(2)
        from pymongo.operations import SearchIndexModel

        db.order_memory.create_search_index(
            SearchIndexModel(
                definition={"fields": [{"type": "vector", "path": "embedding", "numDimensions": dims, "similarity": "cosine"}]},
                name=INDEX_NAME,
                type="vectorSearch",
            )
        )
        _reembed_orders(db, dims)
        return True
    except Exception as exc:  # pragma: no cover - tier/role dependent
        log.info("Order vector index not created: %s", exc)
        return False


def _reembed_orders(db: Database, dims: int, batch: int = 64) -> int:
    """Re-embed remembered orders whose vector came from another model."""
    query = {"$or": [{"embedding": {"$exists": False}}, {"embedding": {"$not": {"$size": dims}}}]}
    done = 0
    while True:
        rows = list(db.order_memory.find(query, {"_id": 0, "id": 1, "text": 1}).limit(batch))
        if not rows:
            break
        vectors = _embed_many([r.get("text") or "" for r in rows])
        updated = 0
        for row, vector in zip(rows, vectors):
            if vector:
                db.order_memory.update_one({"id": row["id"]}, {"$set": {"embedding": vector}})
                updated += 1
        if not updated:
            break  # embeddings unavailable; don't spin
        done += updated
    if done:
        log.info("Re-embedded %d remembered orders (%d dims).", done, dims)
    return done


def _vector_ready(db: Database) -> bool:
    if settings.is_memory_db:
        return False
    try:
        for idx in db.order_memory.list_search_indexes(INDEX_NAME):
            return bool(idx.get("queryable") or idx.get("status") == "READY")
    except Exception:
        return False
    return False


def remember_orders(db: Database, packet, plans: list[dict]) -> int:
    """Store every order the rules (or a coordinator) classified with confidence.

    One lookup for all keys, one embedding call, one insert.
    """
    by_ref = {o.ref: o for o in packet.orders}
    candidates = []
    for plan in plans:
        order = by_ref.get(plan["key"])
        if not order or plan.get("classified_by"):
            continue  # only learn from confident classifications
        text = order_text(order.display, order.performer)
        candidates.append((plan, order, text, order_key(order.code, text)))
    if not candidates:
        return 0
    known = {d["key"] for d in db.order_memory.find({"key": {"$in": [c[3] for c in candidates]}}, {"_id": 0, "key": 1})}
    docs = []
    for plan, order, text, key in candidates:
        if key in known:
            continue
        known.add(key)  # two orders with one code in a packet: remember once
        docs.append({
            "id": new_id("ord"),
            "key": key,
            "code": order.code,
            "text": text,
            "kind": order.kind,
            "category": plan["category"],
            "performer": order.performer,
            "learned_from": "rules",
            "source_ref": order.ref,
            "fhir_base": packet.fhir_base,
            "created_at": utcnow(),
        })
    if not docs:
        return 0
    for doc, vector in zip(docs, _embed_many([d["text"] for d in docs])):
        if vector:
            doc["embedding"] = vector
    db.order_memory.insert_many(docs)
    return len(docs)


def learn_correction(db: Database, *, code: str | None, text: str, kind: str, category: str, performer: str | None, actor: str) -> None:
    """A coordinator confirmed what an unrecognized order is: remember it exactly."""
    key = order_key(code, text)
    doc = {"key": key, "code": code, "text": text, "kind": kind, "category": category, "performer": performer,
           "learned_from": f"coordinator:{actor}", "created_at": utcnow()}
    vector = _embed(text)
    if vector:
        doc["embedding"] = vector
    db.order_memory.update_one({"key": key}, {"$set": doc, "$setOnInsert": {"id": new_id("ord")}}, upsert=True)


def classifier_for(db: Database, fhir_base: str | None):
    # Looked up once per ingest, not once per unrecognized order: whether the
    # vector index is queryable, and (lexical fallback) the past orders.
    cache: dict = {}

    def vector_ready() -> bool:
        if "vector" not in cache:
            cache["vector"] = _vector_ready(db)
        return cache["vector"]

    def past_orders() -> list[dict]:
        if "past" not in cache:
            cache["past"] = list(db.order_memory.find({}, {"_id": 0, "embedding": 0}))
        return cache["past"]

    def classify(order) -> dict | None:
        text = order_text(order.display, order.performer)
        # 1. Exact code seen before.
        if order.code:
            hit = db.order_memory.find_one({"code": order.code}, {"_id": 0, "embedding": 0})
            if hit:
                result = {"kind": hit["kind"], "category": hit["category"], "performer": hit.get("performer"),
                          "method": "known_code", "matched": hit["text"], "score": 1.0, "source_ref": hit.get("source_ref")}
                learned_from = hit.get("learned_from") or ""
                if learned_from.startswith("coordinator:"):
                    # A person already said what this code is: don't ask again.
                    result.update(confirmed=True, confirmed_by=learned_from.split(":", 1)[1],
                                  confirmed_at=hit.get("created_at"), outcome="learned")
                return result
        # 2. Atlas Vector Search over past orders.
        if vector_ready():
            vector = _embed(text)
            if vector:
                hits = list(
                    db.order_memory.aggregate(
                        [
                            {"$vectorSearch": {"index": INDEX_NAME, "path": "embedding", "queryVector": vector, "numCandidates": 50, "limit": 3}},
                            {"$project": {"_id": 0, "embedding": 0, "score": {"$meta": "vectorSearchScore"}}},
                        ]
                    )
                )
                if hits and hits[0]["score"] >= VECTOR_THRESHOLD:
                    h = hits[0]
                    return {"kind": h["kind"], "category": h["category"], "performer": h.get("performer"),
                            "method": "similar_order", "matched": h["text"], "score": round(h["score"], 3), "source_ref": h.get("source_ref")}
                if hits:
                    log.info("Closest past order for %r scored %.2f (< %.2f); leaving for review.", text, hits[0]["score"], VECTOR_THRESHOLD)
                return None
        # 3. Word overlap (no embeddings available).
        mine = _tokens(text)
        best, best_score = None, 0.0
        for past in past_orders():
            theirs = _tokens(past["text"])
            if not mine or not theirs:
                continue
            score = len(mine & theirs) / len(mine | theirs)
            if score > best_score:
                best, best_score = past, score
        if best and best_score >= LEXICAL_THRESHOLD:
            return {"kind": best["kind"], "category": best["category"], "performer": best.get("performer"),
                    "method": "similar_order_lexical", "matched": best["text"], "score": round(best_score, 3), "source_ref": best.get("source_ref")}
        return None

    return classify
