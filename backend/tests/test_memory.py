"""Rolling patient summary and recall ranking."""

from __future__ import annotations

from app.agent.memory import SUMMARY_TAG, lexical_recall, refresh_patient_summary, retrieve_context

MICHAEL = "pat_michael"


def test_summary_is_one_memory_that_tracks_the_record(client, db):
    client.post("/api/demo/day/0", json={"day": 0})
    client.post("/api/demo/day/3", json={"day": 3})
    summaries = list(db.memories.find({"patient_id": MICHAEL, "tags": SUMMARY_TAG}))
    assert len(summaries) == 1
    text = summaries[0]["content"]
    assert "Michael Chen" in text
    assert "Family physician" in text and "patient reported" in text
    assert "Waiting on the care team" in text

    client.post("/api/demo/seed?reset=true")  # Day-7 baseline: cardiology confirmed
    client.post("/api/demo/day/10", json={"day": 10})
    text = db.memories.find_one({"patient_id": MICHAEL, "tags": SUMMARY_TAG})["content"]
    assert "conflicting dates" in text
    assert db.memories.count_documents({"patient_id": MICHAEL, "tags": SUMMARY_TAG}) == 1


def test_summary_is_in_every_retrieval_bundle(client, db):
    bundle = retrieve_context(db, MICHAEL, "cardiology date")
    assert bundle["summary"].startswith("Michael Chen")
    assert all(SUMMARY_TAG not in (m.get("tags") or []) for m in bundle["memories"])


def test_bm25_prefers_specific_and_important_memories(client, db):
    from app import repository as repo

    repo.add_memory(db, patient_id=MICHAEL, kind="preference", summary="Daughter Lin prefers calls after 5pm",
                    content="Caregiver preference", tags=["caregiver"], importance=0.9)
    repo.add_memory(db, patient_id=MICHAEL, kind="interaction", summary="Continuum response: noted the caregiver",
                    content="caregiver caregiver caregiver", tags=["agent", "response"], importance=0.35)
    results = lexical_recall(db, MICHAEL, "when should we call his daughter", k=3)
    assert results[0]["summary"].startswith("Daughter Lin")


def test_recall_dedupes_repeated_memories(client, db):
    from app import repository as repo

    for _ in range(3):
        repo.add_memory(db, patient_id=MICHAEL, kind="interaction", summary="Asked about echocardiogram parking",
                        content="parking question", tags=["parking"], importance=0.5)
    results = lexical_recall(db, MICHAEL, "echocardiogram parking", k=5)
    assert sum(1 for r in results if r["summary"] == "Asked about echocardiogram parking") == 1


def test_voice_context_includes_recalled_history(client):
    client.post("/api/demo/day/3", json={"day": 3})
    ctx = client.get(f"/api/voice/context/{MICHAEL}").json()
    assert ctx["summary"].startswith("Michael Chen")
    assert "What we remember" in ctx["context"]


def test_refresh_is_a_no_op_when_nothing_changed(client, db):
    first = refresh_patient_summary(db, MICHAEL)
    second = refresh_patient_summary(db, MICHAEL)
    assert first["content"] == second["content"]
