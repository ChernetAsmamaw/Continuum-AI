"""MongoDB features mongomock can't fake: schema validation and change streams.

Runs only when ATLAS_TEST_URI points at a replica set (Atlas, or Atlas Local:
``docker run -p 27018:27017 mongodb/mongodb-atlas-local`` →
``ATLAS_TEST_URI="mongodb://user:pass@localhost:27018/?directConnection=true"``).
"""

from __future__ import annotations

import os
import threading
import uuid

import pytest

URI = os.environ.get("ATLAS_TEST_URI")
pytestmark = pytest.mark.skipif(not URI, reason="ATLAS_TEST_URI not set")


@pytest.fixture()
def live_db():
    from pymongo import MongoClient

    client = MongoClient(URI, serverSelectionTimeoutMS=5000)
    db = client[f"continuum_live_test_{uuid.uuid4().hex[:6]}"]
    yield db
    client.drop_database(db.name)
    client.close()


def _apply_validators(db):
    from app.db import _validators

    for name, schema in _validators().items():
        db.create_collection(name, validator={"$jsonSchema": schema}, validationLevel="moderate", validationAction="error")


def test_schema_validation_rejects_bad_task_state(live_db):
    from pymongo.errors import WriteError

    _apply_validators(live_db)
    live_db.obligations.insert_one({"id": "o1", "patient_id": "p1", "description": "Cardiology follow-up", "status": "pending", "history": []})
    with pytest.raises(WriteError):
        live_db.obligations.insert_one({"id": "o2", "patient_id": "p1", "description": "Bloodwork", "status": "kinda-done", "history": []})
    with pytest.raises(WriteError):
        live_db.obligations.update_one({"id": "o1"}, {"$set": {"status": "closed"}})
    with pytest.raises(WriteError):
        live_db.actions.insert_one({"id": "a1", "kind": "outbound_call", "patient_id": "p1", "status": "probably_sent",
                                    "intended_at": "x", "proof_expected": "transcript", "history": [{"status": "intended"}]})
    with pytest.raises(WriteError):
        live_db.ingest_jobs.insert_one({"id": "j1", "key": "k", "status": "running", "params": {}, "steps": {"pull": {}}})


def test_change_stream_sees_task_state_changes_and_resumes(live_db):
    from app.streams import summarize

    live_db.create_collection("obligations")
    seen: list[dict] = []
    ready = threading.Event()
    token_box: dict = {}

    def watch():
        with live_db.watch([{"$match": {"operationType": {"$in": ["insert", "update"]}}}], full_document="updateLookup") as stream:
            ready.set()
            for change in stream:
                seen.append(summarize(change))
                token_box["token"] = stream.resume_token
                if len(seen) == 2:
                    return

    t = threading.Thread(target=watch, daemon=True)
    t.start()
    ready.wait(10)
    live_db.obligations.insert_one({"id": "o1", "patient_id": "p1", "description": "x", "status": "pending", "history": []})
    live_db.obligations.update_one({"id": "o1"}, {"$set": {"status": "verified"}})
    t.join(15)
    assert [(e["collection"], e["op"], e["status"]) for e in seen] == [("obligations", "insert", "pending"), ("obligations", "update", "verified")]
    assert seen[1]["patient_id"] == "p1" and seen[1]["fields"] == ["status"]

    # A watcher restarted with the saved token picks up exactly what it missed.
    live_db.obligations.insert_one({"id": "o2", "patient_id": "p2", "description": "y", "status": "pending", "history": []})
    with live_db.watch(full_document="updateLookup", resume_after=token_box["token"]) as stream:
        missed = summarize(next(stream))
    assert missed["id"] == "o2"
