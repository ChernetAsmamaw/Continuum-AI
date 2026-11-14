"""Connect Continuum to MongoDB Atlas and prove every piece works.

    cd backend
    .venv/bin/python -m app.atlas_setup            # uses MONGODB_URI from .env
    .venv/bin/python -m app.atlas_setup --check    # read-only: connect + report, change nothing

Steps: config sanity → connect (with plain-English diagnosis of the usual
failures) → indexes, demo users and seed data → embeddings + Atlas Vector
Search index (waits until queryable) → a real ``$vectorSearch`` query,
patient-filtered → LangGraph checkpoint collections. Exit code 0 only when
everything the app needs is working.
"""

from __future__ import annotations

import argparse
import os
import sys
import time

OK, FAIL, WARN = "✓", "✗", "!"


def _say(mark: str, text: str) -> None:
    print(f"  {mark} {text}")


def _redact(uri: str) -> str:
    if "@" not in uri or "://" not in uri:
        return uri
    scheme, rest = uri.split("://", 1)
    creds, host = rest.split("@", 1)
    user = creds.split(":", 1)[0]
    return f"{scheme}://{user}:****@{host}"


def diagnose(exc: Exception, uri: str) -> str:
    text = str(exc)
    low = text.lower()
    if "authentication failed" in low or "bad auth" in low or getattr(exc, "code", None) == 18:
        return (
            "Wrong database username or password. Use the *database user* from Atlas → Database Access "
            "(not your Atlas login), and URL-encode special characters in the password (@ → %40, : → %3A, / → %2F)."
        )
    if "query name does not exist" in low or "nxdomain" in low or ("dns" in low and "mongodb" in low) or "_mongodb._tcp" in low:
        return "The cluster host in the URI doesn't resolve. Copy the connection string again from Atlas → Connect → Drivers."
    if "timed out" in low or "serverselectiontimeout" in type(exc).__name__.lower() or "no servers" in low:
        if "mongodb.net" in uri:
            return (
                "Can't reach the cluster. Most likely your IP isn't in Atlas → Network Access "
                "(add your current IP, or 0.0.0.0/0 for the hackathon), or the cluster is paused/still provisioning."
            )
        return "Can't reach the server. Is it running, and is the host/port in MONGODB_URI right?"
    if "not authorized" in low or getattr(exc, "code", None) == 13:
        return "Connected, but the database user lacks permissions. Give it 'Read and write to any database' (or atlasAdmin for index creation)."
    return text


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--uri", help="Override MONGODB_URI (otherwise read from .env)")
    parser.add_argument("--db", help="Override MONGODB_DB")
    parser.add_argument("--check", action="store_true", help="Read-only: connect and report, create nothing")
    parser.add_argument("--wait", type=int, default=180, help="Seconds to wait for the vector index to become queryable")
    args = parser.parse_args()

    os.environ["CONTINUUM_DB_MODE"] = "atlas"
    os.environ["SCHEDULER_INTERVAL_SECONDS"] = "0"
    if args.uri:
        os.environ["MONGODB_URI"] = args.uri
    if args.db:
        os.environ["MONGODB_DB"] = args.db

    from .config import settings

    print("\nContinuum → MongoDB Atlas\n")
    print("1. Configuration")
    uri = settings.mongodb_uri or ""
    if not uri or "<" in uri or (uri == "mongodb://localhost:27017" and not args.uri):
        _say(FAIL, "MONGODB_URI is not set (or still the template). Paste the Atlas connection string into .env.")
        return 1
    _say(OK, f"URI {_redact(uri)}")
    _say(OK, f"Database '{settings.mongodb_db}'")

    print("2. Connection")
    from .db import get_client, get_db

    try:
        client = get_client()
        client.admin.command("ping")
        info = client.server_info()
    except Exception as exc:
        _say(FAIL, diagnose(exc, uri))
        return 1
    hosts = ", ".join(f"{h}:{p}" for h, p in list(client.nodes)[:3]) or "?"
    flavour = "Atlas" if "mongodb.net" in uri else "self-hosted / Atlas Local"
    _say(OK, f"Connected to MongoDB {info.get('version')} ({flavour}) at {hosts}")

    db = get_db()
    if args.check:
        names = sorted(db.list_collection_names())
        _say(OK, f"{len(names)} collections: {', '.join(names[:12])}{'…' if len(names) > 12 else ''}")
        from .db import vector_search_available

        _say(OK if vector_search_available() else WARN, f"Vector Search index queryable: {vector_search_available()}")
        return 0

    print("3. Indexes, users, data")
    from .auth import ensure_clinician_user, ensure_demo_user
    from .db import ensure_indexes

    try:
        ensure_indexes()
        ensure_demo_user(db)
        ensure_clinician_user(db)
    except Exception as exc:
        _say(FAIL, diagnose(exc, uri))
        return 1
    _say(OK, "Indexes created (including EHR ingestion collections)")
    if settings.auto_seed:
        from .seed import seed_if_empty

        seed_if_empty()
    _say(OK, f"{db.patients.count_documents({})} patients, {db.obligations.count_documents({})} follow-ups, {db.memories.count_documents({})} memories")

    print("4. Embeddings + Vector Search")
    from .agent.llm import embedding_dimensions
    from .db import _existing_vector_index, backfill_embeddings, ensure_vector_index, vector_search_available

    dims = embedding_dimensions()
    if not dims:
        _say(WARN, "No embedding model configured (EMBEDDING_PROVIDER=none?) — recall will be lexical. Set EMBEDDING_PROVIDER=auto.")
        vector_ok = False
    else:
        _say(OK, f"Embedding model ready ({dims} dimensions)")
        n = backfill_embeddings()
        _say(OK, f"Embedded {n} memories" if n else "All memories already embedded")
        if not ensure_vector_index():
            _say(FAIL, "Could not create the Vector Search index (tier or role may not allow it). Create it in the Atlas UI: see docs/atlas.md.")
            vector_ok = False
        else:
            deadline = time.time() + args.wait
            while not vector_search_available() and time.time() < deadline:
                status = (_existing_vector_index() or {}).get("status", "PENDING")
                print(f"    … index {settings.mongodb_vector_index}: {status}", end="\r", flush=True)
                time.sleep(3)
            vector_ok = vector_search_available()
            print(" " * 60, end="\r")
            _say(OK if vector_ok else FAIL, f"Index '{settings.mongodb_vector_index}' queryable" if vector_ok else f"Index not queryable after {args.wait}s — rerun later")

    print("5. $vectorSearch smoke test")
    if vector_ok:
        sample = db.memories.find_one({"embedding": {"$exists": True}}, {"patient_id": 1, "embedding": 1, "summary": 1})
        hits: list = []
        for _ in range(10):  # freshly indexed docs can take a few seconds to appear
            hits = list(
                db.memories.aggregate(
                    [
                        {
                            "$vectorSearch": {
                                "index": settings.mongodb_vector_index,
                                "path": "embedding",
                                "queryVector": sample["embedding"],
                                "numCandidates": 50,
                                "limit": 3,
                                "filter": {"patient_id": sample["patient_id"]},
                            }
                        },
                        {"$project": {"_id": 0, "patient_id": 1, "summary": 1, "score": {"$meta": "vectorSearchScore"}}},
                    ]
                )
            )
            if hits:
                break
            time.sleep(3)
        leaked = [h for h in hits if h["patient_id"] != sample["patient_id"]]
        if hits and not leaked:
            _say(OK, f"{len(hits)} hit(s) for {sample['patient_id']}, top score {hits[0]['score']:.3f}; no other patient's memories returned")
        else:
            _say(FAIL, "Vector query returned nothing" if not hits else "Patient filter leaked other patients' memories")
            vector_ok = False
    else:
        _say(WARN, "Skipped (no queryable index)")

    print("6. Agent checkpoints")
    try:
        from .agent.checkpoint import get_checkpointer

        saver = get_checkpointer()
        _say(OK, f"LangGraph checkpointer: {type(saver).__name__}")
    except Exception as exc:
        _say(FAIL, f"Checkpointer unavailable: {exc}")
        return 1

    print("\nReady." if vector_ok else "\nConnected and set up; vector recall will fall back to lexical until the index works.")
    print("Start the API with CONTINUUM_DB_MODE=atlas in .env, then check /api/health (database: connected, vector_search: true).\n")
    return 0 if vector_ok else 2


if __name__ == "__main__":
    sys.exit(main())
