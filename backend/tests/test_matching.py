"""Matching a patient's update to the right follow-up."""

from __future__ import annotations

from app.engine import _ambiguity, rank_obligations


def _o(id_, category, description, due, owner="Clinic", occurrence=1, status="pending", keywords=None):
    return {
        "id": id_, "category": category, "description": description, "due_date": due, "owner": owner,
        "occurrence": occurrence, "status": status, "keywords": keywords or [],
    }


BLOOD = ["bloodwork", "blood test", "labs"]


def test_reported_date_picks_the_right_repeat():
    obligations = [
        _o("b1", "bloodwork", "Bloodwork", "2026-11-08", "Laboratory", 1, keywords=BLOOD),
        _o("b2", "bloodwork", "Bloodwork (repeat)", "2026-12-13", "Laboratory", 2, keywords=BLOOD),
    ]
    ranked = rank_obligations(obligations, "Bloodwork", "bloodwork", details="I did my blood test on 2026-12-12", reported_date="2026-12-12")
    assert ranked[0][0]["id"] == "b2"
    assert any("date within" in r for r in ranked[0][2])


def test_without_a_date_the_earliest_open_instance_wins():
    obligations = [
        _o("b2", "bloodwork", "Bloodwork (repeat)", "2026-12-13", "Laboratory", 2, keywords=BLOOD),
        _o("b1", "bloodwork", "Bloodwork", "2026-11-08", "Laboratory", 1, keywords=BLOOD),
    ]
    ranked = rank_obligations(obligations, "Bloodwork", "bloodwork", details="I got my bloodwork done")
    assert ranked[0][0]["id"] == "b1"
    assert _ambiguity(ranked) is None


def test_closed_items_are_never_matched():
    obligations = [
        _o("c1", "cardiology", "Cardiology follow-up", "2026-11-20", status="verified", keywords=["cardiology"]),
    ]
    assert rank_obligations(obligations, "Cardiology follow-up", "cardiology") == []


def test_two_identical_candidates_are_flagged_as_ambiguous():
    obligations = [
        _o("x1", "diagnostic_imaging", "Diagnostic imaging", "2026-11-20", keywords=["x-ray", "imaging"]),
        _o("x2", "diagnostic_imaging", "Diagnostic imaging", "2026-11-20", keywords=["x-ray", "imaging"]),
    ]
    ranked = rank_obligations(obligations, "Diagnostic imaging", "diagnostic_imaging", details="I had the x-ray")
    assert _ambiguity(ranked) is not None


def test_ambiguous_report_creates_a_review_note(client, db):
    from app.workflow import ingest_document

    client.post("/api/demo/day/0", json={"day": 0})
    ingest_document(
        patient_id="pat_michael",
        title="Imaging orders",
        text="Echocardiogram within 3 weeks.",
    )
    db.obligations.insert_one(
        {
            **db.obligations.find_one({"patient_id": "pat_michael", "category": "diagnostic_imaging"}, {"_id": 0}),
            "id": "obl_dup_echo",
        }
    )
    result = client.post(
        "/api/voice/report",
        json={"patient_id": "pat_michael", "report": "user: I had the echocardiogram yesterday."},
    ).json()
    memories = client.get("/api/patients/pat_michael/journey").json()["memories"]
    assert any("Ambiguous report" in m["summary"] for m in memories), result
