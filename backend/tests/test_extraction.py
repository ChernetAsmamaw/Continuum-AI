"""Deterministic extraction (the no-LLM / LLM-failure path)."""

from __future__ import annotations

from app.agent.extraction import heuristic_extract

ANCHOR = "2026-11-01"


def _reports(text: str, input_type: str = "voice_transcript"):
    return {r.category: r for r in heuristic_extract(text, input_type=input_type, anchor=ANCHOR).patient_reports}


def test_negation_is_not_completion():
    reports = _reports("user: I never went to the family doctor, sorry.")
    assert reports["family_physician"].reported_status == "not_completed"
    reports = _reports("user: I haven't seen the cardiologist yet.")
    assert reports["cardiology"].reported_status == "not_completed"


def test_answer_inherits_category_from_agent_question():
    text = (
        "agent: Did you manage to see your family physician?\n"
        "user: Yes, I saw Dr. Shah on 2026-11-05.\n"
        "agent: Have you had your bloodwork done yet?\n"
        "user: Not yet. I'll go next week.\n"
    )
    reports = _reports(text)
    assert reports["family_physician"].reported_status == "completed"
    assert reports["family_physician"].reported_date == "2026-11-05"
    assert reports["bloodwork"].reported_status == "not_completed"
    assert reports["bloodwork"].reported_date is None


def test_agent_script_is_never_a_patient_report_or_symptom():
    text = (
        "agent: If you have chest pain, call 911. Did you see your cardiologist?\n"
        "user: Doing fine, thanks.\n"
    )
    result = heuristic_extract(text, input_type="voice_transcript", anchor=ANCHOR)
    assert result.patient_reports == []
    assert not result.safety.urgent_symptoms and not result.safety.emergency


def test_repeat_follow_ups_become_separate_obligations():
    doc = (
        "Follow-up plan:\n"
        "- Bloodwork within 1 week, then repeat bloodwork in 6 weeks.\n"
        "- Family physician follow-up within 7 days.\n"
    )
    follow_ups = heuristic_extract(doc, input_type="document", anchor=ANCHOR).follow_ups
    blood = [f for f in follow_ups if f.category == "bloodwork"]
    assert [(f.due_in_days, f.occurrence) for f in blood] == [(7, 1), (42, 2)]
    assert blood[1].description.endswith("(repeat)")


def test_heading_is_not_mistaken_for_the_instruction():
    doc = (
        "Discharging service: Cardiology, General Hospital\n"
        "Follow-up plan:\n- Cardiology follow-up within 4 weeks.\n"
    )
    (cardio,) = heuristic_extract(doc, input_type="document", anchor=ANCHOR).follow_ups
    assert cardio.due_in_days == 28
    assert "within 4 weeks" in cardio.quote


def test_red_flag_instructions_in_documents_are_not_follow_ups_or_symptoms():
    doc = "Seek urgent care for chest pain. Return to emergency if short of breath."
    result = heuristic_extract(doc, input_type="document", anchor=ANCHOR)
    assert result.follow_ups == []
    assert not result.safety.urgent_symptoms


def test_safety_terms_need_whole_words_and_respect_negation():
    calm = heuristic_extract("user: No chest pain, no swelling. Omg the weather!", input_type="voice_transcript", anchor=ANCHOR)
    assert not calm.safety.urgent_symptoms and not calm.safety.clinical_question
    urgent = heuristic_extract("user: I've had chest pain since this morning.", input_type="voice_transcript", anchor=ANCHOR)
    assert urgent.safety.urgent_symptoms


def test_medication_question_needs_intent():
    mention = heuristic_extract("user: I picked up my pills.", input_type="voice_transcript", anchor=ANCHOR)
    assert not mention.safety.medication_question
    ask = heuristic_extract("user: Should I take a double dose of my water pill?", input_type="voice_transcript", anchor=ANCHOR)
    assert ask.safety.medication_question
    mg = heuristic_extract("user: Can I increase it to 80 mg?", input_type="voice_transcript", anchor=ANCHOR)
    assert mg.safety.medication_question


def test_not_yet_keeps_the_obligation_open_and_unchanged(client):
    client.post("/api/demo/day/0", json={"day": 0})
    client.post(
        "/api/voice/report",
        json={
            "patient_id": "pat_michael",
            "report": "agent: Have you had your bloodwork done?\nuser: No, not yet.",
        },
    )
    journey = client.get("/api/patients/pat_michael/journey").json()
    blood = next(o for o in journey["obligations"] if o["category"] == "bloodwork")
    assert blood["status"] in {"pending", "overdue"}
    assert "not completed" in (blood.get("next_action") or "").lower() or blood.get("last_patient_update")


def test_repeat_bloodwork_survives_validation(client):
    from app.workflow import ingest_document

    client.post("/api/demo/day/0", json={"day": 0})
    ingest_document(
        patient_id="pat_michael",
        title="Lab requisition",
        text="Repeat bloodwork (creatinine) in 6 weeks to recheck kidney function.",
    )
    blood = [
        o
        for o in client.get("/api/patients/pat_michael/journey").json()["obligations"]
        if o["category"] == "bloodwork"
    ]
    assert len(blood) == 2
    repeat = next(o for o in blood if o.get("occurrence") == 2)
    assert repeat["description"].endswith("(repeat)")
    # Re-ingesting the same requisition must not duplicate it.
    ingest_document(patient_id="pat_michael", title="Lab requisition", text="Repeat bloodwork (creatinine) in 6 weeks.")
    again = [
        o
        for o in client.get("/api/patients/pat_michael/journey").json()["obligations"]
        if o["category"] == "bloodwork"
    ]
    assert len(again) == 2
