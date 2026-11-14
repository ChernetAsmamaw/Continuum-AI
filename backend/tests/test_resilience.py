"""Finish the Job: resumable ingestion, the action ledger, and adapting to EHR changes."""

from __future__ import annotations

import socket
from datetime import datetime, timedelta, timezone

import pytest

from app import ledger
from app import scheduler as sched
from app.db import demo_today
from app.ingest import hl7 as hl7mod
from app.ingest.jobs import STEPS, SimulatedCrash, resume_pending, run_job
from app.ingest.scenarios import index_admission, visit_number
from app.ingest.service import handle_hl7, pull_and_ingest
from evals.fake_fhir import FakeFHIR, make_patient

PID = "77aa11bb-0000-4000-8000-000000000001"
MRN = "MRN-RESIL1"


@pytest.fixture()
def ehr():
    fake = FakeFHIR()
    patient = make_patient(PID, "Rosa", "Delgado", mrn=MRN)
    fake.put(patient)
    fake.load(index_admission(patient, discharge=demo_today(), variant=0))
    return fake, patient


def _a03(control_id="RES0001"):
    d = demo_today()
    return hl7mod.build_a03(
        control_id=control_id, mrn=MRN, family="Delgado", given="Rosa", birth_date="1946-03-02", sex="F",
        visit_number=visit_number(PID), admit=(d - timedelta(days=4)).isoformat(), discharge=d.isoformat(),
    )


# ── resumable ingestion ──────────────────────────────────────────────────


def test_crash_after_structured_resumes_at_narrative_without_refetching(client, db, ehr):
    fake, _ = ehr
    with pytest.raises(SimulatedCrash):
        handle_hl7(_a03(), client=fake.client(), crash_after="structured")

    job = db.ingest_jobs.find_one({"key": "hl7:GENHOSP:RES0001"})
    assert [job["steps"][s]["status"] for s in STEPS] == ["done", "done", "done", "pending", "pending"]
    assert job["status"] == "running" and job["packet"]["encounter"]["ref"].startswith("Encounter/")
    pid = job["state"]["patient_id"]
    assert db.obligations.count_documents({"patient_id": pid}) == 5  # structured only; narrative not run yet
    requests_before = len(fake.requests)

    # The interface engine never got an ACK, so it re-sends the same message.
    result = handle_hl7(_a03(), client=fake.client())
    assert result["outcome"] == "new_patient" and result["resumed_from"] == "narrative"
    assert len(fake.requests) == requests_before  # the EHR record was not pulled again

    job = db.ingest_jobs.find_one({"key": "hl7:GENHOSP:RES0001"})
    assert job["status"] == "done"
    assert job["steps"]["pull"]["attempts"] == 1 and job["steps"]["structured"]["attempts"] == 1
    assert job["steps"]["narrative"]["attempts"] == 1
    obligations = list(db.obligations.find({"patient_id": pid}))
    assert len(obligations) == 6 and len({o["category"] for o in obligations}) == 6
    assert db.patients.count_documents({"ehr.fhir_id": PID}) == 1


def test_sweep_resumes_job_whose_worker_died(client, db, ehr):
    fake, _ = ehr
    with pytest.raises(SimulatedCrash):
        pull_and_ingest(patient_fhir_id=PID, client=fake.client(), job_key="pull:dead-worker", crash_after="identify")
    future = (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()
    db.ingest_jobs.update_one(
        {"key": "pull:dead-worker"},
        {"$set": {"lease_owner": f"{socket.gethostname()}:999999", "lease_until": future}},  # a pid that doesn't exist
    )
    resumed = resume_pending(db, client=fake.client())
    assert len(resumed) == 1 and resumed[0]["status"] == "done" and resumed[0]["resumed_from"] == "structured"


def test_live_lease_on_another_host_is_not_stolen(client, db, ehr):
    fake, _ = ehr
    with pytest.raises(SimulatedCrash):
        pull_and_ingest(patient_fhir_id=PID, client=fake.client(), job_key="pull:other-host", crash_after="pull")
    future = (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()
    db.ingest_jobs.update_one({"key": "pull:other-host"}, {"$set": {"lease_owner": "other-box:123", "lease_until": future}})
    assert resume_pending(db, client=fake.client()) == []
    db.ingest_jobs.update_one({"key": "pull:other-host"}, {"$set": {"lease_until": "2000-01-01T00:00:00+00:00"}})
    assert resume_pending(db, client=fake.client())[0]["status"] == "done"


def test_verify_step_catches_a_write_that_did_not_land(client, db, ehr):
    fake, _ = ehr
    with pytest.raises(SimulatedCrash):
        pull_and_ingest(patient_fhir_id=PID, client=fake.client(), job_key="pull:verify", crash_after="narrative")
    job = db.ingest_jobs.find_one({"key": "pull:verify"})
    # Something outside Continuum removes a follow-up before verification runs.
    db.obligations.delete_one({"patient_id": job["state"]["patient_id"], "fhir_refs": job["state"]["planned"][0]})
    result = run_job(db, job["id"], client=fake.client())
    assert result["status"] == "failed" and result["failed_step"] == "verify" and result["retryable"]
    assert "not found after writing" in result["error"]


def test_job_endpoints(client, db, ehr):
    fake, _ = ehr
    pull_and_ingest(patient_fhir_id=PID, client=fake.client(), job_key="pull:endpoints")
    jobs = client.get("/api/ingest/jobs").json()["jobs"]
    assert jobs[0]["status"] == "done" and "packet" not in jobs[0]
    assert all(jobs[0]["steps"][s]["status"] == "done" for s in STEPS)
    detail = client.get(f"/api/ingest/jobs/{jobs[0]['id']}").json()["job"]
    assert detail["steps"]["verify"]["summary"].endswith("verified in the database")


# ── action ledger ────────────────────────────────────────────────────────


def _patient(db, pid="pat_michael"):
    return db.patients.find_one({"id": pid}, {"_id": 0})


def test_coordinator_task_is_confirmed_only_by_the_coordinator(client, db):
    patient = _patient(db)
    done = sched._queue_task(db, patient=patient, title="Call Michael about cardiology", rationale="test", changes={"item": "call"}, obligation_id="t1")
    declined = sched._queue_task(db, patient=patient, title="Fax referral", rationale="test", changes={"item": "fax"}, obligation_id="t2")
    a1 = db.actions.find_one({"external_ref": done["id"]})
    assert a1["status"] == "sent" and a1["kind"] == "coordinator_task"

    client.post(f"/api/agent/approvals/{done['id']}/resolve", json={"approve": True, "actor": "Jordan"})
    client.post(f"/api/agent/approvals/{declined['id']}/resolve", json={"approve": False, "actor": "Jordan", "note": "clinic closed"})
    assert db.actions.find_one({"external_ref": done["id"]})["status"] == "confirmed"
    failed = db.actions.find_one({"external_ref": declined["id"]})
    assert failed["status"] == "failed" and "clinic closed" in failed["history"][-1]["detail"]
    assert [h["status"] for h in db.actions.find_one({"external_ref": done["id"]})["history"]] == ["intended", "sent", "confirmed"]


def _due_checkin(db, patient_id="pat_michael"):
    checkin = repo_schedule(db, patient_id)
    return checkin


def repo_schedule(db, patient_id):
    from app import repository as repo

    return repo.schedule_checkin(db, {"patient_id": patient_id, "scheduled_for": demo_today().isoformat(), "reason": "test check-in"})


def test_call_goes_intended_sent_confirmed_by_transcript(client, db, monkeypatch):
    db.checkins.delete_many({})
    db.patients.update_one({"id": "pat_michael"}, {"$set": {"phone": "+1 416 555 0101"}})
    checkin = _due_checkin(db)
    monkeypatch.setattr(sched, "telephony_configured", lambda: True)
    monkeypatch.setattr(sched, "within_calling_hours", lambda now: True)  # wall-clock independent
    monkeypatch.setattr(sched, "place_outbound_call", lambda p, c, o: {"ok": True, "conversation_id": "conv-123", "detail": "Call placed"})
    sched.dispatch_due_checkins(db, demo_today(), datetime.now(timezone.utc))
    action = db.actions.find_one({"key": f"checkin:{checkin['id']}:call"})
    assert action["status"] == "sent" and action["external_ref"] == "conv-123"

    from app.workflow import handle_post_call

    handle_post_call({"data": {"conversation_id": "conv-123", "transcript": [
        {"role": "agent", "message": "Hi Michael, this is Continuum."},
        {"role": "user", "message": "Hi, I'm doing fine."},
    ]}}, patient_id="pat_michael")
    assert db.actions.find_one({"id": action["id"]})["status"] == "confirmed"


def test_interrupted_call_is_not_redialled(client, db, monkeypatch):
    db.checkins.delete_many({})
    db.patients.update_one({"id": "pat_michael"}, {"$set": {"phone": "+1 416 555 0101"}})
    checkin = _due_checkin(db)
    # A previous run decided to call, then died before recording the result.
    ledger.intend(db, kind="outbound_call", key=f"checkin:{checkin['id']}:call", patient_id="pat_michael",
                  summary="Check-in call", proof="transcript", actor="test")
    calls = []
    monkeypatch.setattr(sched, "telephony_configured", lambda: True)
    monkeypatch.setattr(sched, "within_calling_hours", lambda now: True)  # wall-clock independent
    monkeypatch.setattr(sched, "place_outbound_call", lambda *a: calls.append(a) or {"ok": True, "conversation_id": "x", "detail": ""})
    sched.dispatch_due_checkins(db, demo_today(), datetime.now(timezone.utc))
    assert calls == []
    assert db.actions.find_one({"key": f"checkin:{checkin['id']}:call"})["status"] == "unknown"
    assert db.checkins.find_one({"id": checkin["id"]})["status"] == "awaiting_manual_call"


def test_reconcile_flags_unconfirmed_and_unknown(client, db):
    sent = ledger.intend(db, kind="coordinator_task", patient_id="pat_michael", summary="Fax", proof="x", actor="t", confirm_within_hours=1)
    ledger.mark_sent(db, sent["id"], external_ref="ext-1")
    stuck = ledger.intend(db, kind="outbound_call", patient_id="pat_michael", summary="Call", proof="x", actor="t")
    later = datetime.now(timezone.utc) + timedelta(hours=2)
    flagged = {a["id"]: a["status"] for a in ledger.reconcile(db, later)}
    assert flagged == {sent["id"]: "unconfirmed", stuck["id"]: "unknown"}
    body = client.get("/api/actions").json()
    assert body["summary"]["needs_attention"] == 2
    # Late proof still counts.
    assert ledger.confirm(db, external_ref="ext-1", evidence="arrived late")["status"] == "confirmed"


# ── adapting to EHR changes ──────────────────────────────────────────────


def test_unrecognized_order_is_classified_from_past_orders(client, db, ehr):
    fake, _ = ehr
    pull_and_ingest(patient_fhir_id=PID, client=fake.client())  # teaches order_memory with normal codes
    assert db.order_memory.count_documents({}) >= 3

    # A second hospital stay where the EHR switched to local codes and dropped categories.
    other = make_patient("88bb22cc-0000-4000-8000-000000000002", "Omar", "Haddad", mrn="MRN-RESIL2")
    fake.put(other)
    resources = index_admission(other, discharge=demo_today(), variant=1)
    for r in resources:
        if r["resourceType"] == "ServiceRequest" and r["id"].endswith("-sr-cardio"):
            r["category"] = [{"coding": [{"system": "urn:hosp:local", "code": "CONS"}]}]
            r.pop("performerType")
            # same standard code, new local display: known_code path
            r["code"] = {"coding": [{"system": "http://snomed.info/sct", "code": "183519002", "display": "HF consult"}], "text": "HF consult"}
        if r["resourceType"] == "ServiceRequest" and r["id"].endswith("-sr-bmp"):
            r["category"] = [{"coding": [{"system": "urn:hosp:local", "code": "LBX"}]}]
            r["code"] = {"coding": [{"system": "urn:hosp:local", "code": "BMP-7"}], "text": "Basic metabolic panel"}  # word-overlap path
    resources.append({
        "resourceType": "ServiceRequest", "id": "mystery-1", "status": "active", "intent": "order",
        "category": [{"coding": [{"system": "urn:hosp:local", "code": "ZZ"}]}], "code": {"text": "Wound check"},
        "subject": {"reference": f"Patient/{other['id']}"}, "encounter": {"reference": f"Encounter/{resources[0]['id']}"},
        "authoredOn": f"{demo_today().isoformat()}T10:00:00Z",
    })
    fake.load(resources)
    result = pull_and_ingest(patient_fhir_id=other["id"], client=fake.client())
    obligations = {o["fhir_refs"][0]: o for o in db.obligations.find({"patient_id": result["patient_id"], "fhir_refs": {"$exists": True}})}

    cardio = next(o for k, o in obligations.items() if k.endswith("-sr-cardio"))
    assert cardio["category"] == "cardiology" and cardio["classified_by"]["method"] == "known_code"
    bmp = next(o for k, o in obligations.items() if k.endswith("-sr-bmp"))
    assert bmp["category"] == "bloodwork" and bmp["classified_by"]["method"] == "similar_order_lexical"
    assert "confirm" in bmp["next_action"]
    mystery = obligations["ServiceRequest/mystery-1"]
    assert mystery["classified_by"]["method"] == "unrecognized" and "Unrecognized" in mystery["next_action"]
    # Nothing was dropped, and the narrative didn't duplicate the reclassified items.
    cats = [o["category"] for o in db.obligations.find({"patient_id": result["patient_id"]})]
    assert cats.count("cardiology") == 1 and cats.count("bloodwork") == 1


def test_stream_refuses_patient_checkin_token(client):
    from app import messaging
    from app.db import get_db

    patient = get_db().patients.find_one({}, {"_id": 0})
    link_token = messaging.checkin_link(patient).rsplit("/", 1)[-1]
    assert client.get(f"/api/stream?token={link_token}").status_code == 401


def test_message_text_joins_list_content():
    from types import SimpleNamespace

    from app.agent.llm import message_text

    assert message_text(SimpleNamespace(content="hi")) == "hi"
    assert message_text(SimpleNamespace(content=[{"type": "text", "text": "Hello "}, {"type": "thinking", "thinking": "x"}, {"type": "text", "text": "Sam"}])) == "Hello Sam"
    assert message_text(SimpleNamespace(content=None)) == ""
