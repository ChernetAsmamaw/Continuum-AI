"""MongoDB access, index setup and startup seeding."""

from __future__ import annotations

import logging
from datetime import date
from functools import lru_cache

from pymongo import ASCENDING, DESCENDING, MongoClient
from pymongo.database import Database

from .config import settings
from .util import parse_date

log = logging.getLogger("continuum.db")

_client: MongoClient | None = None


def get_client() -> MongoClient:
    global _client
    if _client is not None:
        return _client

    if settings.is_memory_db:
        import mongomock

        _client = mongomock.MongoClient()
        log.warning("Using in-memory mongomock database (CONTINUUM_DB_MODE=memory).")
        return _client

    _client = MongoClient(
        settings.mongodb_uri,
        serverSelectionTimeoutMS=8000,
        appname="continuum-hackathon",
    )
    return _client


def get_db() -> Database:
    return get_client()[settings.mongodb_db]


def reset_client() -> None:
    global _client
    if _client is not None:
        _client.close()
    _client = None


@lru_cache
def demo_today() -> date:
    return parse_date(settings.demo_anchor_date) or date.today()


def ping() -> bool:
    try:
        get_client().admin.command("ping")
        return True
    except Exception as exc:  # pragma: no cover - depends on environment
        log.warning("MongoDB ping failed: %s", exc)
        return False


def ensure_indexes() -> None:
    db = get_db()
    db.patients.create_index([("id", ASCENDING)], unique=True)
    db.patients.create_index([("name", ASCENDING)])

    db.care_episodes.create_index([("id", ASCENDING)], unique=True)
    db.care_episodes.create_index([("patient_id", ASCENDING), ("discharge_date", DESCENDING)])

    db.events.create_index([("id", ASCENDING)], unique=True)
    db.events.create_index([("patient_id", ASCENDING), ("occurred_at", DESCENDING)])
    db.events.create_index([("episode_id", ASCENDING)])

    db.obligations.create_index([("id", ASCENDING)], unique=True)
    db.obligations.create_index([("patient_id", ASCENDING), ("status", ASCENDING)])
    db.obligations.create_index([("patient_id", ASCENDING), ("due_date", ASCENDING)])

    db.evidence.create_index([("id", ASCENDING)], unique=True)
    db.evidence.create_index([("obligation_id", ASCENDING), ("recorded_at", DESCENDING)])
    db.evidence.create_index([("patient_id", ASCENDING)])

    db.memories.create_index([("id", ASCENDING)], unique=True)
    db.memories.create_index([("patient_id", ASCENDING), ("created_at", DESCENDING)])
    db.memories.create_index([("patient_id", ASCENDING), ("kind", ASCENDING)])

    db.agent_audit.create_index([("id", ASCENDING)], unique=True)
    db.agent_audit.create_index([("patient_id", ASCENDING), ("created_at", DESCENDING)])
    db.agent_audit.create_index([("thread_id", ASCENDING)])

    db.voice_calls.create_index([("id", ASCENDING)], unique=True)
    db.voice_calls.create_index([("patient_id", ASCENDING), ("created_at", DESCENDING)])
    db.voice_calls.create_index([("elevenlabs_conversation_id", ASCENDING)], unique=True, sparse=True)

    db.approvals.create_index([("id", ASCENDING)], unique=True)
    db.approvals.create_index([("patient_id", ASCENDING), ("status", ASCENDING)])
    db.approvals.create_index([("thread_id", ASCENDING)])
    db.approvals.create_index([("run_id", ASCENDING)])

    db.checkins.create_index([("patient_id", ASCENDING), ("scheduled_for", ASCENDING)])

    db.users.create_index([("id", ASCENDING)], unique=True)
    db.users.create_index([("email", ASCENDING)], unique=True)

    # EHR ingestion (app/ingest): identity, idempotency and exact result matching.
    db.patients.create_index([("ehr.fhir_id", ASCENDING), ("ehr.fhir_base", ASCENDING)], sparse=True)
    db.patients.create_index([("mrn", ASCENDING)], sparse=True)
    db.care_episodes.create_index([("ehr.encounter_ref", ASCENDING), ("ehr.fhir_base", ASCENDING)], sparse=True)
    db.obligations.create_index([("patient_id", ASCENDING), ("fhir_refs", ASCENDING)])
    db.evidence.create_index([("source_ref", ASCENDING)], sparse=True)
    db.hl7_messages.create_index([("control_id", ASCENDING), ("sending_facility", ASCENDING)], unique=True)
    db.hl7_messages.create_index([("received_at", DESCENDING)])
    db.ingest_runs.create_index([("at", DESCENDING)])
    db.ingest_runs.create_index([("patient_id", ASCENDING)])
    db.ehr_poll_runs.create_index([("at", DESCENDING)])

    # Hot paths found with explain() (docs/performance.md): each of these was
    # a COLLSCAN (+ in-memory SORT for the dashboard feeds).
    db.events.create_index([("occurred_at", DESCENDING)])  # dashboard: recent events
    db.agent_audit.create_index([("created_at", DESCENDING)])  # dashboard: recent audit
    db.approvals.create_index([("status", ASCENDING), ("created_at", DESCENDING)])  # approval queues
    db.ingest_jobs.create_index([("id", ASCENDING)], unique=True)  # every job step
    db.ingest_jobs.create_index([("key", ASCENDING)])  # idempotent create_job
    db.ingest_jobs.create_index([("status", ASCENDING)])  # resume_pending
    db.ingest_runs.create_index([("job_id", ASCENDING)], sparse=True)  # verify step
    db.order_memory.create_index([("key", ASCENDING)])
    db.order_memory.create_index([("code", ASCENDING)], sparse=True)
    db.obligations.create_index(  # result poller: open EHR-backed follow-ups only
        [("status", ASCENDING)],
        name="ehr_followups_by_status",
        partialFilterExpression={"fhir_refs": {"$exists": True}},
    )
    db.actions.create_index([("id", ASCENDING)], unique=True)
    db.actions.create_index([("key", ASCENDING)], sparse=True)
    db.actions.create_index([("external_ref", ASCENDING)], sparse=True)
    db.actions.create_index([("status", ASCENDING), ("intended_at", DESCENDING)])
    db.actions.create_index([("status", ASCENDING), ("confirm_by", ASCENDING)])


def _validators() -> dict[str, dict]:
    """$jsonSchema rules: the database itself refuses malformed task state."""
    from .ledger import STATUSES as ACTION_STATUSES
    from .schemas import ObligationStatus

    return {
        "obligations": {
            "bsonType": "object",
            "required": ["id", "patient_id", "description", "status", "history"],
            "properties": {
                "id": {"bsonType": "string"},
                "patient_id": {"bsonType": "string"},
                "description": {"bsonType": "string", "minLength": 3},
                "status": {"enum": [s.value for s in ObligationStatus]},
                "history": {"bsonType": "array"},
                "fhir_refs": {"bsonType": "array", "items": {"bsonType": "string"}},
                "confidence": {"bsonType": ["double", "int"], "minimum": 0, "maximum": 1},
            },
        },
        "actions": {
            "bsonType": "object",
            "required": ["id", "kind", "patient_id", "status", "intended_at", "proof_expected", "history"],
            "properties": {
                "status": {"enum": ACTION_STATUSES},
                "kind": {"bsonType": "string"},
                "history": {"bsonType": "array", "minItems": 1},
            },
        },
        "ingest_jobs": {
            "bsonType": "object",
            "required": ["id", "key", "status", "steps", "params"],
            "properties": {
                "status": {"enum": ["queued", "running", "done", "failed"]},
                "steps": {"bsonType": "object", "required": ["pull", "identify", "structured", "narrative", "verify"]},
            },
        },
        "evidence": {
            "bsonType": "object",
            "required": ["id", "patient_id", "kind", "summary", "recorded_at"],
        },
    }


def ensure_validators() -> list[str]:
    """Attach schema validation to the task-state collections (Atlas/MongoDB only)."""
    if settings.is_memory_db:
        return []
    db = get_db()
    existing = set(db.list_collection_names())
    applied = []
    for name, schema in _validators().items():
        options = {"validator": {"$jsonSchema": schema}, "validationLevel": "moderate", "validationAction": "error"}
        try:
            if name in existing:
                db.command({"collMod": name, **options})
            else:
                db.create_collection(name, **options)
            applied.append(name)
        except Exception as exc:  # pragma: no cover - role dependent
            log.warning("Schema validation not applied to %s: %s", name, exc)
    return applied


def _vector_index_definition(dimensions: int) -> dict:
    return {
        "fields": [
            {
                "type": "vector",
                "path": "embedding",
                "numDimensions": dimensions,
                "similarity": "cosine",
            },
            # Required for the $vectorSearch `filter` / LangChain `pre_filter` on patient_id.
            {"type": "filter", "path": "patient_id"},
        ]
    }


def _existing_vector_index() -> dict | None:
    try:
        for idx in get_db().memories.list_search_indexes(settings.mongodb_vector_index):
            return idx
    except Exception:
        return None
    return None


def ensure_vector_index() -> bool:
    """Create (or rebuild) the Atlas Vector Search index for memory recall.

    The index must be ``type: vectorSearch`` (a plain search index needs field
    mappings and is rejected), and its dimensions must match the embedding
    model in use; a mismatched index is dropped and recreated.
    """
    if settings.is_memory_db:
        return False
    from .agent.llm import embedding_dimensions

    dims = embedding_dimensions()
    if not dims:
        log.info("No embedding model; vector index not needed (lexical recall).")
        return False
    try:
        db = get_db()
        if "memories" not in db.list_collection_names():
            db.create_collection("memories")
        existing = _existing_vector_index()
        if existing:
            fields = (existing.get("latestDefinition") or existing.get("definition") or {}).get("fields") or []
            current = next((f.get("numDimensions") for f in fields if f.get("type") == "vector"), None)
            if current == dims:
                return True
            log.info("Vector index has %s dims but the model uses %s; rebuilding.", current, dims)
            db.memories.drop_search_index(settings.mongodb_vector_index)
            import time

            for _ in range(30):  # Atlas drops asynchronously
                if not _existing_vector_index():
                    break
                time.sleep(2)
        from pymongo.operations import SearchIndexModel

        db.memories.create_search_index(
            SearchIndexModel(
                definition=_vector_index_definition(dims),
                name=settings.mongodb_vector_index,
                type="vectorSearch",
            )
        )
        log.info("Requested Atlas Vector Search index %s (%d dims).", settings.mongodb_vector_index, dims)
        return True
    except Exception as exc:  # pragma: no cover - environment dependent
        log.info("Vector index not created (%s); using lexical recall.", exc)
        return False


def vector_search_available() -> bool:
    """True once the vector index exists and Atlas reports it queryable."""
    if settings.is_memory_db:
        return False
    idx = _existing_vector_index()
    return bool(idx and (idx.get("queryable") or idx.get("status") == "READY"))


def backfill_embeddings(batch: int = 64) -> int:
    """Embed memories that have no vector, or one from a different model."""
    if settings.is_memory_db:
        return 0
    from .agent.llm import embedding_dimensions, get_embeddings

    model, dims = get_embeddings(), embedding_dimensions()
    if model is None or not dims:
        return 0
    db = get_db()
    query = {"$or": [{"embedding": {"$exists": False}}, {"embedding": {"$not": {"$size": dims}}}]}
    done = 0
    while True:
        docs = list(db.memories.find(query, {"_id": 1, "summary": 1, "content": 1}).limit(batch))
        if not docs:
            break
        vectors = model.embed_documents([f"{d.get('summary', '')}\n{d.get('content', '')}" for d in docs])
        for d, v in zip(docs, vectors):
            db.memories.update_one({"_id": d["_id"]}, {"$set": {"embedding": v}})
        done += len(docs)
    if done:
        log.info("Backfilled embeddings for %d memories.", done)
    return done


def bootstrap() -> None:
    """Create indexes, ask Atlas for the vector index, and seed when empty."""
    ensure_validators()
    ensure_indexes()
    from .auth import ensure_clinician_user, ensure_demo_user

    try:
        ensure_demo_user(get_db())
        ensure_clinician_user(get_db())
    except Exception as exc:  # pragma: no cover - environment dependent
        log.warning("Could not ensure demo users: %s", exc)
    if settings.auto_seed:
        from .seed import seed_if_empty

        seed_if_empty()
    # Best effort, off the request path: pick the embedding model, embed any
    # memories that lack vectors, then make sure the Atlas index matches.
    # Recall falls back to lexical scoring until the index is queryable.
    import threading

    def _vector_setup() -> None:
        try:
            backfill_embeddings()
            ensure_vector_index()
            from .ingest.orders import ensure_order_index

            ensure_order_index(get_db())
        except Exception as exc:  # pragma: no cover - environment dependent
            log.info("Vector setup skipped: %s", exc)

    threading.Thread(target=_vector_setup, name="vector-setup", daemon=True).start()
