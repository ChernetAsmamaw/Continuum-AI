"""Coordinator review queue: confirm/correct inferred order types, resolve identity flags."""

from __future__ import annotations

from datetime import date

import pytest

from app.db import demo_today
from app.ingest.orders import classifier_for
from app.ingest.packet import PacketOrder
from app.ingest.scenarios import index_admission
from app.ingest.service import pull_and_ingest
from evals.fake_fhir import FakeFHIR, make_patient

PID = "99cc33dd-0000-4000-8000-000000000003"
OTHER = "aa44ee55-1111-4000-8000-000000000004"


@pytest.fixture()
def ehr():
    fake = FakeFHIR()
    patient = make_patient(PID, "Lena", "Okafor", mrn="MRN-REVIEW1")
    fake.put(patient)
    fake.load(index_admission(patient, discharge=demo_today(), variant=0))
    return fake


def _mystery_stay(fake, code="WND-1", text="Wound check"):
    """A second patient whose EHR sends one order the rules can't place."""
    other = make_patient(OTHER, "Omar", "Haddad", mrn="MRN-REVIEW2")
    fake.put(other)
    resources = index_admission(other, discharge=demo_today(), variant=1)
    resources.append({
        "resourceType": "ServiceRequest", "id": "mystery-1", "status": "active", "intent": "order",
        "category": [{"coding": [{"system": "urn:hosp:local", "code": "ZZ"}]}],
        "code": {"coding": [{"system": "urn:hosp:local", "code": code}], "text": text},
        "subject": {"reference": f"Patient/{OTHER}"}, "encounter": {"reference": f"Encounter/{resources[0]['id']}"},
        "authoredOn": f"{demo_today().isoformat()}T10:00:00Z",
    })
    fake.load(resources)
    result = pull_and_ingest(patient_fhir_id=OTHER, client=fake.client())
    return result["patient_id"]


def _mystery(db, patient_id):
    return db.obligations.find_one({"patient_id": patient_id, "fhir_refs": "ServiceRequest/mystery-1"}, {"_id": 0})


def test_review_requires_auth(client):
    client.headers.pop("Authorization")
    assert client.get("/api/review").status_code == 401
    assert client.post("/api/review/orders/x", json={"category": "home_care"}).status_code == 401
    assert client.post("/api/review/identity/x/dismiss", json={}).status_code == 401


def test_overview_lists_unrecognized_orders_with_source(client, db, ehr):
    pull_and_ingest(patient_fhir_id=PID, client=ehr.client())
    patient_id = _mystery_stay(ehr)
    body = client.get("/api/review").json()
    item = next(o for o in body["orders"] if o["patient_id"] == patient_id and o["classified_by"]["method"] == "unrecognized")
    assert item["source_order"]["code"] == "WND-1"
    assert item["patient_name"] == "Omar Haddad"
    assert body["counts"]["orders"] >= 1
    assert {"home_care", "cardiology", "follow_up"} <= {c["category"] for c in body["categories"]}
    # Filter by patient.
    only = client.get(f"/api/review?patient_id={patient_id}").json()
    assert all(o["patient_id"] == patient_id for o in only["orders"])


def test_correcting_an_order_type_is_audited_and_learned(client, db, ehr):
    pull_and_ingest(patient_fhir_id=PID, client=ehr.client())
    patient_id = _mystery_stay(ehr)
    ob = _mystery(db, patient_id)
    before_category = ob["category"]

    res = client.post(f"/api/review/orders/{ob['id']}", json={"category": "home_care", "note": "Nurse wound visit"})
    assert res.status_code == 200, res.text
    out = res.json()
    assert out["outcome"] == "corrected"
    updated = _mystery(db, patient_id)
    assert updated["category"] == "home_care"
    assert updated["classified_by"]["confirmed"] is True
    assert updated["classified_by"]["original_category"] == before_category
    assert "next_action" not in updated
    assert updated["history"][-1]["reason"].startswith("Order type corrected")

    audit = db.agent_audit.find_one({"patient_id": patient_id, "action": "Corrected order type"})
    assert audit and audit["before"]["category"] == before_category and audit["after"]["category"] == "home_care"

    # Learned exactly: the next identical order (same local code) is a known_code hit.
    memory = db.order_memory.find_one({"key": "code:WND-1"})
    assert memory and memory["category"] == "home_care" and memory["learned_from"].startswith("coordinator:")
    order = PacketOrder(ref="ServiceRequest/next", version_ref="ServiceRequest/next/_history/1", kind="other",
                        display="Wound check", code="WND-1", status="active")
    hit = classifier_for(db, None)(order)
    assert hit and hit["method"] == "known_code" and hit["category"] == "home_care"

    # Gone from the queue; a second confirmation is refused.
    assert all(o["id"] != ob["id"] for o in client.get("/api/review").json()["orders"])
    assert client.post(f"/api/review/orders/{ob['id']}", json={"category": "home_care"}).status_code == 409


def test_confirming_keeps_category(client, db, ehr):
    pull_and_ingest(patient_fhir_id=PID, client=ehr.client())
    patient_id = _mystery_stay(ehr)
    ob = _mystery(db, patient_id)
    res = client.post(f"/api/review/orders/{ob['id']}", json={"category": ob["category"]})
    assert res.status_code == 200 and res.json()["outcome"] == "confirmed"
    assert db.agent_audit.find_one({"patient_id": patient_id, "action": "Confirmed order type"})


def test_order_review_validation(client, db, ehr):
    pull_and_ingest(patient_fhir_id=PID, client=ehr.client())
    patient_id = _mystery_stay(ehr)
    ob = _mystery(db, patient_id)
    assert client.post("/api/review/orders/nope", json={"category": "home_care"}).status_code == 404
    assert client.post(f"/api/review/orders/{ob['id']}", json={"category": "astrology"}).status_code == 422
    plain = db.obligations.find_one({"patient_id": patient_id, "classified_by": {"$exists": False}})
    assert client.post(f"/api/review/orders/{plain['id']}", json={"category": "home_care"}).status_code == 422


def _flagged(db, ehr):
    db.patients.insert_one({"id": "pat_existing", "name": "Lena Okafor", "mrn": "LOCAL-77", "phone": "555-0100", "age": 71})
    db.obligations.insert_one({"id": "obl_existing", "patient_id": "pat_existing", "description": "Old item", "status": "pending",
                               "owner": "Care team", "category": "follow_up", "due_date": demo_today().isoformat()})
    result = pull_and_ingest(patient_fhir_id=PID, client=ehr.client())
    assert result["identity_review"] is True
    return result["patient_id"]


def test_identity_overview_shows_candidates_side_by_side(client, db, ehr):
    dup = _flagged(db, ehr)
    case = next(c for c in client.get("/api/review").json()["identities"] if c["patient"]["id"] == dup)
    assert case["status"] == "needed"
    assert [c["id"] for c in case["candidates"]] == ["pat_existing"]
    assert case["patient"]["ehr_fhir_id"] == PID and case["patient"]["follow_ups"] > 0
    assert case["candidates"][0]["follow_ups"] == 1


def test_merge_moves_everything_and_is_audited(client, db, ehr):
    dup = _flagged(db, ehr)
    dup_obligations = db.obligations.count_documents({"patient_id": dup})
    dup_evidence = db.evidence.count_documents({"patient_id": dup})
    assert dup_obligations > 0 and dup_evidence > 0

    assert client.post(f"/api/review/identity/{dup}/merge", json={"into": "pat_sarah"}).status_code == 422
    res = client.post(f"/api/review/identity/{dup}/merge", json={"into": "pat_existing", "note": "Same DOB on paper chart"})
    assert res.status_code == 200, res.text
    moved = res.json()["moved"]
    assert moved["obligations"] == dup_obligations and moved["evidence"] == dup_evidence

    assert db.patients.find_one({"id": dup}) is None
    for coll in ("obligations", "evidence", "events", "care_episodes", "ingest_jobs"):
        assert db[coll].count_documents({"patient_id": dup}) == 0, coll
    assert db.obligations.count_documents({"patient_id": "pat_existing"}) == dup_obligations + 1
    target = db.patients.find_one({"id": "pat_existing"})
    assert target["ehr"]["fhir_id"] == PID  # next discharge matches exactly
    assert target["merged_from"][0]["patient_id"] == dup
    tomb = db.patient_merges.find_one({"into": "pat_existing"})
    assert tomb and tomb["from"]["id"] == dup
    audit = db.agent_audit.find_one({"patient_id": "pat_existing", "action": "Merged duplicate patient"})
    assert audit and audit["before"]["duplicate"]["id"] == dup and "Same DOB" in audit["detail"]
    assert db.events.find_one({"patient_id": "pat_existing", "title": "Duplicate record merged"})

    # Re-ingesting the same discharge now lands on the merged patient, without a new review.
    again = pull_and_ingest(patient_fhir_id=PID, client=ehr.client())
    assert again["patient_id"] == "pat_existing"
    assert client.post(f"/api/review/identity/{dup}/merge", json={"into": "pat_existing"}).status_code == 404


def test_dismiss_keeps_both_records(client, db, ehr):
    dup = _flagged(db, ehr)
    res = client.post(f"/api/review/identity/{dup}/dismiss", json={"note": "Different birth dates"})
    assert res.status_code == 200
    p = db.patients.find_one({"id": dup})
    assert p["identity_review"]["status"] == "dismissed" and p["identity_review"]["note"] == "Different birth dates"
    assert db.patients.find_one({"id": "pat_existing"})
    assert db.agent_audit.find_one({"patient_id": dup, "action": "Dismissed identity review"})
    assert all(c["patient"]["id"] != dup for c in client.get("/api/review").json()["identities"])
    assert client.post(f"/api/review/identity/{dup}/dismiss", json={}).status_code == 409


def test_clinician_cannot_resolve_reviews(client, db, ehr):
    dup = _flagged(db, ehr)
    creds = client.get("/api/auth/demo").json()["clinician"]
    token = client.post("/api/auth/login", json={"email": creds["email"], "password": creds["password"]}).json()["token"]
    headers = {"Authorization": f"Bearer {token}"}
    assert client.get("/api/review", headers=headers).status_code == 200
    assert client.post(f"/api/review/identity/{dup}/dismiss", json={}, headers=headers).status_code == 403


# ── fixes: learned codes, guessed stays, narrative duplicates ────────────

THIRD = "bb55ff66-2222-4000-8000-000000000005"


def test_a_code_taught_by_a_coordinator_is_not_asked_again(client, db, ehr):
    pull_and_ingest(patient_fhir_id=PID, client=ehr.client())
    patient_id = _mystery_stay(ehr)
    ob = _mystery(db, patient_id)
    assert client.post(f"/api/review/orders/{ob['id']}", json={"category": "home_care"}).status_code == 200

    # A new patient's stay carries the same local code.
    third = make_patient(THIRD, "Ines", "Moreau", mrn="MRN-REVIEW3")
    ehr.put(third)
    resources = index_admission(third, discharge=demo_today(), variant=0)
    resources.append({
        "resourceType": "ServiceRequest", "id": "mystery-3", "status": "active", "intent": "order",
        "category": [{"coding": [{"system": "urn:hosp:local", "code": "ZZ"}]}],
        "code": {"coding": [{"system": "urn:hosp:local", "code": "WND-1"}], "text": "Wound check"},
        "subject": {"reference": f"Patient/{THIRD}"}, "encounter": {"reference": f"Encounter/{resources[0]['id']}"},
        "authoredOn": f"{demo_today().isoformat()}T10:00:00Z",
    })
    ehr.load(resources)
    third_id = pull_and_ingest(patient_fhir_id=THIRD, client=ehr.client())["patient_id"]
    again = db.obligations.find_one({"patient_id": third_id, "fhir_refs": "ServiceRequest/mystery-3"}, {"_id": 0})
    assert again["category"] == "home_care"
    assert again["classified_by"]["method"] == "known_code"
    assert again["classified_by"]["confirmed"] is True and again["classified_by"]["outcome"] == "learned"
    assert not again.get("next_action")
    assert again["confidence"] == 0.95
    assert all(o["id"] != again["id"] for o in client.get("/api/review").json()["orders"])


def test_stay_end_compares_instants_not_strings():
    from app.ingest.pull import _stay_end

    utc = {"period": {"end": "2026-11-13T11:00:00Z"}}
    toronto = {"period": {"end": "2026-11-13T09:00:00-05:00"}}  # 14:00Z: later, though it sorts first as text
    assert _stay_end(toronto) > _stay_end(utc)
    assert _stay_end({"period": {}}) < _stay_end(utc)


def test_a_guessed_empty_stay_does_not_supersede_the_active_episode(client, db, ehr):
    first = pull_and_ingest(patient_fhir_id=PID, client=ehr.client())
    real = db.care_episodes.find_one({"patient_id": first["patient_id"], "status": "active"}, {"_id": 0})
    # A later inpatient stay with nothing to follow up (no orders, no summary in its window).
    later = date.fromordinal(demo_today().toordinal() + 2).isoformat()
    ehr.put({
        "resourceType": "Encounter", "id": "later-empty", "status": "finished", "class": {"code": "IMP"},
        "subject": {"reference": f"Patient/{PID}"},
        "period": {"start": f"{later}T08:00:00Z", "end": f"{later}T20:00:00Z"},
    })
    second = pull_and_ingest(patient_fhir_id=PID, client=ehr.client())
    assert second["patient_id"] == first["patient_id"]
    episodes = {e["id"]: e for e in db.care_episodes.find({"patient_id": first["patient_id"]}, {"_id": 0})}
    assert episodes[real["id"]]["status"] == "active"
    guessed = next(e for e in episodes.values() if (e.get("ehr") or {}).get("encounter_ref") == "Encounter/later-empty")
    assert guessed["status"] == "historical"


def _ai_follow_up(db, patient_id, episode_id, *, category, due, quote):
    from app import repository as repo

    return repo.create_obligation(db, {
        "patient_id": patient_id, "episode_id": episode_id, "description": f"{category.replace('_', ' ').capitalize()} follow-up",
        "category": category, "occurrence": 1, "owner": "Care team", "due_date": due, "status": "pending",
        "keywords": [], "source": {"event_id": None, "label": "Discharge summary", "quote": quote},
        "origin": "ai_extracted", "confidence": 0.7, "latest_evidence": "",
    }, actor="Continuum", reason="test")


def test_narrative_duplicate_is_linked_then_resolved_by_confirming_the_type(client, db, ehr):
    from app.ingest.builder import link_possible_duplicates

    pull_and_ingest(patient_fhir_id=PID, client=ehr.client())
    patient_id = _mystery_stay(ehr)
    order = _mystery(db, patient_id)
    ai = _ai_follow_up(db, patient_id, order["episode_id"], category="respiratory", due=order["due_date"],
                       quote="See the wound clinic in two weeks.")
    links = link_possible_duplicates(db, order["episode_id"], [ai["id"]])
    assert links == [{"ai": ai["id"], "order": order["id"], "category": "respiratory"}]
    assert _mystery(db, patient_id)["suggested_category"] == "respiratory"
    assert db.obligations.find_one({"id": ai["id"]})["possible_duplicate_of"] == order["id"]
    assert db.agent_audit.find_one({"patient_id": patient_id, "action": "Linked possible duplicate"})

    item = next(o for o in client.get("/api/review").json()["orders"] if o["id"] == order["id"])
    assert [d["id"] for d in item["possible_duplicates"]] == [ai["id"]]

    res = client.post(f"/api/review/orders/{order['id']}", json={"category": "respiratory"})
    assert res.status_code == 200, res.text
    assert res.json()["duplicates"] == [{"id": ai["id"], "resolution": "cancelled_duplicate"}]
    dup = db.obligations.find_one({"id": ai["id"]}, {"_id": 0})
    assert dup["status"] == "cancelled" and dup["duplicate_of"] == order["id"] and "possible_duplicate_of" not in dup
    settled = _mystery(db, patient_id)
    assert "suggested_category" not in settled
    assert any("same follow-up" in (e.get("summary") or "") for e in db.evidence.find({"obligation_id": order["id"]}))


def test_narrative_link_is_cleared_when_the_types_differ(client, db, ehr):
    from app.ingest.builder import link_possible_duplicates

    pull_and_ingest(patient_fhir_id=PID, client=ehr.client())
    patient_id = _mystery_stay(ehr)
    order = _mystery(db, patient_id)
    ai = _ai_follow_up(db, patient_id, order["episode_id"], category="respiratory", due=order["due_date"], quote="Skin check.")
    assert link_possible_duplicates(db, order["episode_id"], [ai["id"]])
    res = client.post(f"/api/review/orders/{order['id']}", json={"category": "home_care"})
    assert res.json()["duplicates"] == [{"id": ai["id"], "resolution": "kept"}]
    kept = db.obligations.find_one({"id": ai["id"]}, {"_id": 0})
    assert kept["status"] == "pending" and "possible_duplicate_of" not in kept and not kept.get("next_action")


def test_unrelated_narrative_follow_up_is_not_linked(client, db, ehr):
    from app.ingest.builder import link_possible_duplicates

    pull_and_ingest(patient_fhir_id=PID, client=ehr.client())
    patient_id = _mystery_stay(ehr)
    order = _mystery(db, patient_id)
    far = demo_today().toordinal() + 60
    ai = _ai_follow_up(db, patient_id, order["episode_id"], category="respiratory",
                       due=date.fromordinal(far).isoformat(), quote="Annual skin screening.")
    assert link_possible_duplicates(db, order["episode_id"], [ai["id"]]) == []
