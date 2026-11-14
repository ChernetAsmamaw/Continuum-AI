"""MongoDB change streams → live updates.

One watcher thread follows inserts and updates on the task-state collections
and fans them out to subscribers (the console, over server-sent events at
``GET /api/stream``). The resume token is saved in ``stream_state`` after each
event, so after a restart the watcher continues from where it stopped instead
of missing changes or replaying all of them.

Change streams need a replica set (Atlas, Atlas Local); on mongomock the
watcher stays off and the console keeps its 30-second refresh.
"""

from __future__ import annotations

import logging
import queue
import threading
import time

from .config import settings

log = logging.getLogger("continuum.streams")

WATCHED = ["obligations", "approvals", "actions", "ingest_jobs", "evidence", "events", "patients", "call_sessions"]
STATE_ID = "console"

_subscribers: set[queue.Queue] = set()
_lock = threading.Lock()
status = {"running": False, "events": 0, "last_event_at": None, "resumed": False, "error": None}


def subscribe() -> queue.Queue:
    q: queue.Queue = queue.Queue(maxsize=500)
    with _lock:
        _subscribers.add(q)
    return q


def unsubscribe(q: queue.Queue) -> None:
    with _lock:
        _subscribers.discard(q)


def publish(event: dict) -> None:
    with _lock:
        targets = list(_subscribers)
    for q in targets:
        try:
            q.put_nowait(event)
        except queue.Full:
            pass


def summarize(change: dict) -> dict:
    doc = change.get("fullDocument") or {}
    updated = (change.get("updateDescription") or {}).get("updatedFields") or {}
    return {
        "collection": (change.get("ns") or {}).get("coll"),
        "op": change.get("operationType"),
        "id": doc.get("id"),
        "patient_id": doc.get("patient_id") or (doc.get("id") if (change.get("ns") or {}).get("coll") == "patients" else None),
        "status": doc.get("status"),
        "step": doc.get("current_step"),
        "fields": sorted(k.split(".")[0] for k in updated)[:8],
    }


def _watch_forever() -> None:
    from .db import get_db

    db = get_db()
    pipeline = [{"$match": {"ns.coll": {"$in": WATCHED}, "operationType": {"$in": ["insert", "update", "replace"]}}}]
    while True:
        saved = db.stream_state.find_one({"id": STATE_ID}) or {}
        token = saved.get("resume_token")
        try:
            with db.watch(pipeline, full_document="updateLookup", resume_after=token) as stream:
                status.update(running=True, resumed=bool(token), error=None)
                log.info("Change stream watching %s%s.", ", ".join(WATCHED), " (resumed)" if token else "")
                for change in stream:
                    publish(summarize(change))
                    status["events"] += 1
                    status["last_event_at"] = time.time()
                    db.stream_state.update_one({"id": STATE_ID}, {"$set": {"resume_token": stream.resume_token}}, upsert=True)
        except Exception as exc:  # pragma: no cover - environment dependent
            message = str(exc)
            status.update(running=False, error=message[:200])
            if "resume" in message.lower() and token:
                log.warning("Saved resume token no longer valid (%s); starting fresh.", message[:120])
                db.stream_state.delete_one({"id": STATE_ID})
                continue
            if "replica set" in message.lower() or "not supported" in message.lower():
                log.info("Change streams unavailable on this deployment: %s", message[:160])
                return
            log.warning("Change stream interrupted: %s; retrying in 5s.", message[:160])
            time.sleep(5)


def start_watcher() -> bool:
    if settings.is_memory_db:
        return False
    threading.Thread(target=_watch_forever, name="change-stream", daemon=True).start()
    return True
