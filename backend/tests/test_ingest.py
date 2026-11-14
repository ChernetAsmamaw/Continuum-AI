"""EHR ingestion: ADT trigger → FHIR pull → structured follow-ups → exact closure."""

from __future__ import annotations

from datetime import timedelta

import pytest

from app.db import demo_today
from app.ingest import hl7 as hl7mod
from app.ingest.builder import plan_followups
from app.ingest.poller import poll_ehr
from app.ingest.pull import pull_discharge
from app.ingest.scenarios import expected_followups, index_admission, result_events, visit_number
from app.ingest.service import handle_hl7, pull_and_ingest
from app.integrations import _ingest_one

from evals.fake_fhir import FakeFHIR, make_patient

PID = "4f083ce3-f12b-bb4b-7353-e17f0cd55b0a"


@pytest.fixture()
def ehr():
    fake = FakeFHIR()
    patient = make_patient(PID, "Ezekiel", "Walter")
    discharge = demo_today()
    fake.put(patient)
    fake.load(index_admission(patient, discharge=discharge, variant=0))
    # Older history that must NOT become follow-ups: a previous stay's order and med.
    fake.put({
        "resourceType": "ServiceRequest", "id": "old-sr", "status": "active", "intent": "order",
        "category": [{"text": "Laboratory"}], "code": {"text": "Lipid panel"},
        "subject": {"reference": f"Patient/{PID}"}, "authoredOn": "2023-01-10T10:00:00Z",
        "encounter": {"reference": "Encounter/older"},
    })
    fake.put({
        "resourceType": "MedicationRequest", "id": "old-mr", "status": "active", "intent": "order",
        "medicationCodeableConcept": {"text": "Simvastatin 20 MG"}, "subject": {"reference": f"Patient/{PID}"},
        "authoredOn": "2022-05-01T10:00:00Z", "encounter": {"reference": "Encounter/older"},
    })
    return fake, patient, discharge


def _a03(patient, discharge, control_id="MSG0001"):
    mrn = patient["identifier"][0]["value"]
    return hl7mod.build_a03(
        control_id=control_id, mrn=mrn, family="Walter", given="Ezekiel", birth_date=patient["birthDate"],
        sex="M", visit_number=visit_number(PID), admit=(discharge - timedelta(days=4)).isoformat(),
        discharge=discharge.isoformat(), attending="Dr. Priya Raman",
    )


# ── HL7 ──────────────────────────────────────────────────────────────────


def test_parse_a03_and_ack(ehr):
    _, patient, discharge = ehr
    event = hl7mod.parse_adt(_a03(patient, discharge))
    assert event.is_discharge and event.control_id == "MSG0001"
    assert event.identifier_values[0] == patient["identifier"][0]["value"]
    assert event.visit_number == visit_number(PID)
    assert event.discharge_at.startswith(discharge.isoformat())
    assert event.attending == "Priya Raman"
    ack = hl7mod.build_ack("MSG0001", "AA", "ok")
    assert "MSA|AA|MSG0001" in ack


def test_rejects_non_adt():
    result = handle_hl7("MSH|^~\\&|LAB|HOSP|X|Y|20260101||ORU^R01|1|P|2.5.1\rPID|1||123")
    assert result["outcome"] == "rejected" and "MSA|AR" in result["ack"]


# ── pull + build ─────────────────────────────────────────────────────────


def test_pull_builds_packet_from_this_stay_only(ehr):
    fake, _, discharge = ehr
    packet = pull_discharge(fake.client(), patient_identifiers=["MRN-4F083C"], encounter_identifiers=[visit_number(PID)])
    assert packet.encounter.discharge_date == discharge.isoformat()
    assert "DISCHARGE SUMMARY" in packet.discharge_summary_text
    assert {o.kind for o in packet.orders} == {"referral", "lab"}
    assert "ServiceRequest/old-sr" not in {o.ref for o in packet.orders}
    # The culture order is represented by its pending report, not as a to-do order.
    assert [p.display for p in packet.pending_results] == ["Blood culture"]
    assert all("culture" not in o.ref for o in packet.orders)
    assert [m.display for m in packet.medications] == ["Furosemide 40 MG Oral Tablet"]
    assert packet.appointments[0].status == "booked"
    # National ids are never used as matching keys downstream.
    plans = plan_followups(packet)
    pcp = next(p for p in plans if p["category"] == "family_physician")
    assert pcp["status"] == "scheduled" and pcp["confirmed_date"] == (discharge + timedelta(days=5)).isoformat()


def test_adt_discharge_creates_patient_and_followups(client, db, ehr):
    fake, patient, discharge = ehr
    result = handle_hl7(_a03(patient, discharge), client=fake.client())
    assert result["outcome"] == "new_patient", result
    assert "MSA|AA|MSG0001" in result["ack"]
    pid = result["patient_id"]

    stored = db.patients.find_one({"id": pid})
    assert stored["ehr"]["fhir_id"] == PID and stored["mrn"] == "MRN-4F083C"
    assert all(i["type"] != "SS" for i in stored["ehr"]["identifiers"])

    obligations = client.get(f"/api/patients/{pid}/journey").json()["obligations"]
    by_cat = {o["category"]: o for o in obligations}
    expected = expected_followups(0)
    assert set(expected) <= set(by_cat), sorted(by_cat)
    for category, origin in expected.items():
        assert by_cat[category]["origin"] == origin, (category, by_cat[category].get("origin"))
    # No duplicates: one item per category even though the narrative repeats them.
    assert len(obligations) == len(by_cat)
    structured = [o for o in obligations if o.get("origin") == "ehr_structured"]
    assert len(structured) / len(obligations) >= 0.8
    # Provenance: every structured item has evidence pointing at an EHR resource version.
    for o in structured:
        assert o["fhir_refs"] and o["source"]["label"].startswith("EHR · ")
    pcp = by_cat["family_physician"]
    assert pcp["status"] == "scheduled" and pcp["confirmed_date"]


def test_replay_is_a_no_op(client, db, ehr):
    fake, patient, discharge = ehr
    first = handle_hl7(_a03(patient, discharge), client=fake.client())
    count = db.obligations.count_documents({"patient_id": first["patient_id"]})
    again = handle_hl7(_a03(patient, discharge), client=fake.client())
    assert again["outcome"] == "duplicate"
    # A new message for the same stay (e.g. A03 re-sent with a new id) adds nothing.
    third = handle_hl7(_a03(patient, discharge, control_id="MSG0002"), client=fake.client())
    assert third["outcome"] == "updated"
    assert db.obligations.count_documents({"patient_id": first["patient_id"]}) == count


def test_new_order_after_discharge_is_picked_up_on_repull(client, db, ehr):
    fake, patient, discharge = ehr
    first = pull_and_ingest(patient_fhir_id=PID, client=fake.client())
    fake.put({
        "resourceType": "ServiceRequest", "id": "late-echo", "status": "active", "intent": "order",
        "category": [{"coding": [{"code": "363679005", "display": "Imaging"}]}],
        "code": {"text": "Transthoracic echocardiogram"}, "subject": {"reference": f"Patient/{PID}"},
        "encounter": {"reference": f"Encounter/{first['encounter_ref'].split('/')[1]}"},
        "authoredOn": f"{discharge.isoformat()}T15:00:00Z",
    })
    second = pull_and_ingest(patient_fhir_id=PID, client=fake.client())
    assert second["outcome"] == "updated" and len(second["created"]) == 1
    assert db.obligations.find_one({"fhir_refs": "ServiceRequest/late-echo"})["category"] == "diagnostic_imaging"


def test_narrative_restating_orders_under_other_categories_is_not_duplicated(client, db, ehr, monkeypatch):
    """An LLM extractor names categories its own way ("primary_care",
    "lab_review"); those restate EHR orders and must not become second items."""
    from app import repository as repo
    from app import workflow

    def fake_llm_extraction(*, patient_id, title, text, source_ref=None, actor="x"):
        for category, description in (
            ("primary_care", "Follow up with the primary care physician"),
            ("lab_review", "Review pending blood culture result"),
            ("home_care", "Home care nursing assessment for daily weights"),
        ):
            repo.create_obligation(
                db, {"patient_id": patient_id, "description": description, "category": category,
                     "source": {"event_id": None, "label": title, "quote": description}},
                actor, "extracted",
            )
        return {"status": "completed"}

    monkeypatch.setattr(workflow, "ingest_document", fake_llm_extraction)
    fake, patient, discharge = ehr
    result = handle_hl7(_a03(patient, discharge, control_id="MSG-LLM-1"), client=fake.client())
    assert result["outcome"] == "new_patient", result
    obligations = list(db.obligations.find({"patient_id": result["patient_id"]}, {"_id": 0}))
    open_items = [o for o in obligations if o["status"] != "cancelled"]
    by_cat: dict[str, list[dict]] = {}
    for o in open_items:
        by_cat.setdefault(o["category"], []).append(o)
    assert all(len(v) == 1 for v in by_cat.values()), {k: len(v) for k, v in by_cat.items()}
    assert by_cat["family_physician"][0]["origin"] == "ehr_structured"
    assert by_cat["pending_result"][0]["origin"] == "ehr_structured"
    assert by_cat["home_care"][0]["origin"] == "ai_extracted"
    retired = [o for o in obligations if o["status"] == "cancelled"]
    assert {o["extracted_category"] for o in retired} == {"primary_care", "lab_review"}
    assert all(o["duplicate_of"] for o in retired)
    assert "1 from the discharge narrative" in result["detail"]


def test_unknown_patient_returns_error_ack(ehr):
    fake, patient, discharge = ehr
    msg = _a03(patient, discharge).replace("MRN-4F083C", "NOPE-1")
    result = handle_hl7(msg, client=fake.client())
    assert result["outcome"] == "error" and "MSA|AE" in result["ack"]


def test_same_name_without_identifier_is_not_merged(client, db, ehr):
    fake, patient, discharge = ehr
    db.patients.insert_one({"id": "pat_other", "name": "Ezekiel Walter", "mrn": "LOCAL-9", "phone": ""})
    result = pull_and_ingest(patient_fhir_id=PID, client=fake.client())
    assert result["patient_id"] != "pat_other" and result["identity_review"] is True


# ── closure ──────────────────────────────────────────────────────────────


def test_results_close_loops_by_exact_reference(client, db, ehr):
    fake, patient, discharge = ehr
    pid = pull_and_ingest(patient_fhir_id=PID, client=fake.client())["patient_id"]
    for resource in result_events(patient, discharge=discharge, variant=0):
        fake.put(resource)

    poll = poll_ehr(db, fake.client())
    outcomes = {r["obligation_id"]: r for r in poll["results"]}
    obligations = {o["id"]: o for o in client.get(f"/api/patients/{pid}/journey").json()["obligations"]}
    by_cat = {o["category"]: o for o in obligations.values()}

    # BMP, blood culture and the PCP visit each matched their own order exactly.
    for category in ("bloodwork", "pending_result", "family_physician"):
        hit = outcomes[by_cat[category]["id"]]
        assert hit["outcome"] == "verification_ready", (category, hit)
        assert hit["match_reasons"] == ["exact EHR reference"]
    # The blood culture did NOT get attached to the bloodwork follow-up by keyword.
    assert outcomes[by_cat["bloodwork"]["id"]]["source_ref"].startswith("DiagnosticReport/")
    assert "culture" not in outcomes[by_cat["bloodwork"]["id"]]["source_ref"]

    # Closing still needs a human: approve the BMP card and it verifies.
    approval_id = outcomes[by_cat["bloodwork"]["id"]]["approval_id"]
    client.post(f"/api/agent/approvals/{approval_id}/resolve", json={"approve": True, "actor": "Jordan"})
    journey = client.get(f"/api/patients/{pid}/journey").json()["obligations"]
    assert next(o for o in journey if o["category"] == "bloodwork")["status"] == "verified"

    # Polling again finds nothing new.
    assert poll_ehr(db, fake.client())["applied"] == 0


def test_backend_jwt_assertion_is_signed_rs384():
    import jwt
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    from app.ingest.fhir_client import FHIRClient

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode()
    client = FHIRClient(
        "https://fhir.epic.com/interconnect-fhir-oauth/api/FHIR/R4", auth_mode="backend_jwt",
        client_id="abc123", token_url="https://fhir.epic.com/interconnect-fhir-oauth/oauth2/token",
        private_key=pem, key_id="k1",
    )
    token = client.client_assertion()
    header = jwt.get_unverified_header(token)
    claims = jwt.decode(token, key.public_key(), algorithms=["RS384"], audience=client.token_url)
    assert header["alg"] == "RS384" and header["kid"] == "k1"
    assert claims["iss"] == claims["sub"] == "abc123" and claims["exp"] - claims["iat"] <= 300


def test_hl7_endpoint_requires_auth(client):
    client.headers.pop("Authorization", None)
    response = client.post("/api/ingest/hl7", content="MSH|^~\\&|", headers={"content-type": "text/plain"})
    assert response.status_code in (401, 403)


def test_ingestion_eval_targets():
    from evals.ingestion import run

    summary = run(n=2)["summary"]
    ehr, paste = summary["ehr_ingestion"], summary["paste_summary"]
    assert ehr["followup_recall"] == 1.0 and ehr["pending_results_tracked"] == 1.0
    assert ehr["duplicate_rate"] == 0.0 and ehr["closure_correct"] == 1.0
    assert ehr["structured_share"] >= 0.8 and ehr["manual_steps"] == 0
    assert paste["closure_correct"] < ehr["closure_correct"]
