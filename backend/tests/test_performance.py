"""The performance pass restructured hot paths; these pin that the faster
versions return exactly what the straightforward versions did.

Each test compares the optimized code with a reference: either the
one-at-a-time implementation that is still in the module, or a naive
re-implementation of the pre-optimization logic kept here.
"""

from __future__ import annotations

from datetime import timedelta

import httpx
import pytest

from app import repository as repo
from app.db import demo_today
from app.ingest import hl7 as hl7mod
from app.ingest import poller
from app.ingest.builder import step_structured
from app.ingest.fhir_client import FHIRClient
from app.ingest.jobs import STEPS
from app.ingest.packet import DischargePacket
from app.ingest.pull import pull_discharge
from app.ingest.scenarios import index_admission, result_events, visit_number
from app.ingest.service import handle_hl7
from app.schemas import ApprovalStatus, EvidenceKind, ObligationStatus

from evals.fake_fhir import BASE, FakeFHIR, make_patient

PID = "9e1b2c3d-0000-4000-8000-0000000000aa"
MRN = "MRN-PERF01"


def _fake(variant: int = 0, with_results: bool = False) -> FakeFHIR:
    fake = FakeFHIR()
    patient = make_patient(PID, "Imani", "Okafor", mrn=MRN)
    fake.put(patient)
    fake.load(index_admission(patient, discharge=demo_today(), variant=variant))
    if with_results:
        fake.load(result_events(patient, discharge=demo_today(), variant=variant))
    return fake


def _a03(control_id: str = "PERF0001") -> str:
    d = demo_today()
    return hl7mod.build_a03(
        control_id=control_id, mrn=MRN, family="Okafor", given="Imani", birth_date="1950-01-01", sex="F",
        visit_number=visit_number(PID), admit=(d - timedelta(days=4)).isoformat(), discharge=d.isoformat(),
    )


class _Rejecting(FakeFHIR):
    """A server that 400s on the given search parameters (narrower vendor support)."""

    def __init__(self, base: FakeFHIR, reject: callable) -> None:
        super().__init__()
        self.store = base.store
        self.reject = reject

    def handler(self, request: httpx.Request) -> httpx.Response:
        if self.reject(request):
            self.requests.append(str(request.url))
            return httpx.Response(400, json={"resourceType": "OperationOutcome"})
        return super().handler(request)


class _Ignoring(FakeFHIR):
    """A server that silently ignores the given search parameters."""

    def __init__(self, base: FakeFHIR, ignored: set[str]) -> None:
        super().__init__()
        self.store = base.store
        self.ignored = ignored

    def _matches(self, rtype, resource, key, value):
        if key in self.ignored:
            return True
        return super()._matches(rtype, resource, key, value)


def _client(fake: FakeFHIR) -> FHIRClient:
    return FHIRClient(BASE, transport=httpx.MockTransport(fake.handler))


# ── pull: concurrent searches + server-side status filters ───────────────


def _pull(fake: FakeFHIR) -> DischargePacket:
    return pull_discharge(_client(fake), patient_identifiers=[MRN], encounter_identifiers=[visit_number(PID)])


@pytest.mark.parametrize("variant", [0, 1])
def test_pull_sends_status_filters_and_matches_servers_that_ignore_or_reject_them(variant):
    fake = _fake(variant, with_results=True)  # finals present: the status filter has something to drop
    baseline = _pull(fake).model_dump()

    searched = [u for u in fake.requests if "/DiagnosticReport?" in u or "/ServiceRequest?" in u]
    assert searched and all("status=" in u for u in searched)

    ignoring = _Ignoring(fake, {"status"})
    assert _pull(ignoring).model_dump() == baseline

    rejecting = _Rejecting(fake, lambda r: r.url.path.endswith(("DiagnosticReport", "ServiceRequest")) and "status" in r.url.params)
    assert _pull(rejecting).model_dump() == baseline
    # Each rejected filtered search was retried once without the filter.
    assert sum("/ServiceRequest?" in u for u in rejecting.requests) == 2


def test_pull_failure_modes_unchanged():
    fake = _fake()
    from app.ingest.pull import PullError

    with pytest.raises(PullError, match="Patient not found"):
        pull_discharge(_client(fake), patient_identifiers=["nope"], encounter_identifiers=["nope"])
    del fake.store["Encounter"]
    with pytest.raises(PullError, match="No inpatient encounter"):
        _pull(fake)


# ── poller: batched lookups ──────────────────────────────────────────────


def _poll_refs(fake: FakeFHIR) -> list[str]:
    packet = _pull(fake)
    refs = [o.ref for o in packet.orders] + [a.ref for a in packet.appointments] + [r.ref for r in packet.pending_results]
    # Plus references that resolve to nothing, and a duplicate.
    return refs + ["ServiceRequest/missing", "DiagnosticReport/missing", refs[0]]


@pytest.mark.parametrize("variant", [0, 1])
def test_batched_poll_lookup_equals_one_at_a_time(variant):
    fake = _fake(variant)
    refs = _poll_refs(fake)
    fake.load(result_events({"id": PID}, discharge=demo_today(), variant=variant))
    client = _client(fake)
    expected = {r: poller._related(client, r) for r in refs}
    one_at_a_time = len(fake.requests)
    fake.requests.clear()

    got = poller.fetch_related(client, refs)
    assert got == expected
    assert any(got.values())  # the scenario really has related results
    assert len(fake.requests) < one_at_a_time


def test_batched_poll_lookup_falls_back_when_server_rejects_lists():
    fake = _fake(1)
    refs = _poll_refs(fake)
    fake.load(result_events({"id": PID}, discharge=demo_today(), variant=1))
    expected = {r: poller._related(_client(fake), r) for r in refs}

    strict = _Rejecting(fake, lambda r: "," in r.url.params.get("based-on", "") or "," in r.url.params.get("_id", ""))
    assert poller.fetch_related(_client(strict), refs) == expected


def test_batched_poll_lookup_ignores_resources_citing_other_orders():
    """A server that ignores based-on returns everything; only real citations count."""
    fake = _fake(1)
    refs = _poll_refs(fake)
    fake.load(result_events({"id": PID}, discharge=demo_today(), variant=1))
    expected = {r: poller._related(_client(fake), r) for r in refs}
    sloppy = _Ignoring(fake, {"based-on"})
    assert poller.fetch_related(_client(sloppy), refs) == expected


def test_poll_reports_failed_direct_reads_per_reference(db):
    fake = _fake()
    broken = _Rejecting(fake, lambda r: r.url.path.split("/fhir/", 1)[-1].startswith("DiagnosticReport"))
    db.obligations.insert_many([
        {"id": f"obl_perf_poll_{i}", "patient_id": "pat_perf_poll", "description": "Review result", "status": "pending",
         "history": [], "fhir_refs": ["DiagnosticReport/x1"]}
        for i in range(2)
    ])
    try:
        run = poller.poll_ehr(db, _client(broken))
        assert run["errors"].count(next(e for e in run["errors"] if e.startswith("DiagnosticReport/x1"))) == 2
    finally:
        db.obligations.delete_many({"patient_id": "pat_perf_poll"})


# ── repository: bulk creation is the same as one at a time ───────────────

_VOLATILE = {"id", "created_at", "updated_at", "recorded_at", "at", "obligation_id", "evidence_ids", "_id"}


def _strip(doc: dict) -> dict:
    out = {}
    for k, v in doc.items():
        if k in _VOLATILE:
            continue
        if isinstance(v, list):
            v = [_strip(x) if isinstance(x, dict) else x for x in v]
        elif isinstance(v, dict):
            v = _strip(v)
        out[k] = v
    return out


def _items(patient_id: str) -> list[dict]:
    items = []
    for i, two in enumerate([False, True, False]):
        evidence = [{"kind": EvidenceKind.document.value, "summary": f"Ordered #{i}", "source_ref": f"ServiceRequest/b{i}/_history/1",
                     "quote": f"order {i}", "confidence": 0.95}]
        if two:
            evidence.append({"kind": EvidenceKind.clinic_confirmation.value, "summary": "Booked", "source_ref": "Appointment/a/_history/1",
                             "confidence": 0.95})
        items.append({
            "key": f"ServiceRequest/b{i}",
            "reason": f"Created from EHR record ServiceRequest/b{i}",
            "evidence": evidence,
            "doc": {"patient_id": patient_id, "episode_id": "ep_x", "description": f"Follow-up number {i}", "category": "follow_up",
                    "owner": "Clinic", "due_date": "2030-01-0" + str(i + 1), "status": ObligationStatus.pending.value,
                    "source": {"event_id": None, "label": "EHR", "quote": f"order {i}"}, "fhir_refs": [f"ServiceRequest/b{i}"],
                    "confidence": 0.95, "latest_evidence": ""},
        })
    return items


def _written(db, patient_id: str) -> dict:
    obligations = sorted(db.obligations.find({"patient_id": patient_id}, {"_id": 0}), key=lambda o: o["description"])
    evidence_ids = [e for o in obligations for e in o["evidence_ids"]]
    return {
        "obligations": [_strip(o) for o in obligations],
        "evidence": sorted((_strip(e) for e in db.evidence.find({"patient_id": patient_id}, {"_id": 0})), key=repr),
        "evidence_linked": sorted(e["id"] for e in db.evidence.find({"patient_id": patient_id})) == sorted(evidence_ids),
        "audit": sorted((_strip(a) for a in db.agent_audit.find({"patient_id": patient_id}, {"_id": 0})), key=repr),
        "memories": sorted((_strip(m) for m in db.memories.find({"patient_id": patient_id}, {"_id": 0})), key=repr),
    }


def _cleanup(db, *patient_ids):
    for coll in ("obligations", "evidence", "agent_audit", "memories"):
        db[coll].delete_many({"patient_id": {"$in": list(patient_ids)}})


def test_bulk_create_writes_what_one_at_a_time_writes(db):
    one, bulk = "pat_perf_one", "pat_perf_bulk"
    try:
        for item in _items(one):
            obligation = repo.create_obligation(db, item["doc"], actor="EHR ingestion", reason=item["reason"])
            for ev in item["evidence"]:
                repo.add_evidence(db, patient_id=one, obligation_id=obligation["id"], actor="EHR ingestion", **ev)
        created = repo.create_obligations_bulk(db, _items(bulk), actor="EHR ingestion")
        assert len(created) == 3

        a, b = _written(db, one), _written(db, bulk)
        for part in ("obligations", "evidence", "audit", "memories"):
            assert [_swap(x, bulk, one) for x in b[part]] == a[part], part
        assert a["evidence_linked"] and b["evidence_linked"]
    finally:
        _cleanup(db, one, bulk)


def _swap(doc, old: str, new: str):
    if isinstance(doc, dict):
        return {k: _swap(v, old, new) for k, v in doc.items()}
    if isinstance(doc, list):
        return [_swap(v, old, new) for v in doc]
    return new if doc == old else doc


def test_bulk_create_is_idempotent_after_a_partial_write(client, db):  # client: unique indexes
    pid = "pat_perf_retry"
    try:
        items = _items(pid)
        repo.create_obligations_bulk(db, items[:1], actor="EHR ingestion")
        # Crash after the side documents of item 2 but before its obligation.
        db.obligations.delete_one({"id": repo.stable_id("obl", pid, items[0]["key"])})
        repo.create_obligations_bulk(db, items, actor="EHR ingestion")
        assert db.obligations.count_documents({"patient_id": pid}) == 3
        assert db.evidence.count_documents({"patient_id": pid}) == 4
        assert db.agent_audit.count_documents({"patient_id": pid}) == 3
        assert db.memories.count_documents({"patient_id": pid}) == 3
    finally:
        _cleanup(db, pid)


def test_structured_step_skips_tracked_refs_and_rerun_creates_nothing(client, db):
    fake = _fake(1)
    packet = _pull(fake)
    state = {"patient_id": "pat_perf_struct", "episode_id": "ep_perf"}
    try:
        first = step_structured(db, packet, state)
        assert first["created"] and not first["skipped"]
        again = step_structured(db, packet, state)
        assert again["created"] == [] and sorted(again["skipped"]) == sorted(first["planned"])
        for o in db.obligations.find({"patient_id": "pat_perf_struct"}):
            assert len(o["evidence_ids"]) == db.evidence.count_documents({"obligation_id": o["id"]}) >= 1
    finally:
        _cleanup(db, "pat_perf_struct")


# ── repository: read endpoints without N+1 ───────────────────────────────


def _reference_list_patients(db, today):
    out = []
    for patient in db.patients.find({}, {"_id": 0}):
        obligations = [repo.present_obligation(d, today) for d in db.obligations.find({"patient_id": patient["id"]}, {"_id": 0})]
        open_items = [o for o in obligations if o["is_open"]]
        out.append({
            **patient,
            "open_count": len(open_items),
            "overdue_count": sum(o["status"] == "overdue" for o in open_items),
            "conflict_count": sum(o["status"] == "conflict" for o in open_items),
            "escalation_count": sum(o["status"] == "escalated" for o in open_items),
            "pending_verification": sum(o["status"] == "patient_reported" for o in open_items),
            "next_due": min((o["due_date"] for o in open_items if o.get("due_date")), default=None),
            "priority": repo.sort_obligations(open_items)[:3],
            **_reference_deadline(db, patient["id"], open_items, today),
        })
    out.sort(key=lambda p: (-p["overdue_count"], -p["conflict_count"], p["name"]))
    return out


def _reference_deadline(db, patient_id, open_items, today):
    from collections import Counter

    from app.util import parse_date

    dated = sorted((o for o in open_items if o.get("due_date")), key=lambda o: o["due_date"])
    episode = db.care_episodes.find_one({"patient_id": patient_id, "status": "active"}, {"_id": 0}) or {}
    discharge = parse_date(episode.get("discharge_date"))
    names = Counter((o.get("assignee") or {}).get("name") for o in open_items if (o.get("assignee") or {}).get("name"))
    return {
        "next_deadline": {"description": dated[0]["description"], "due_date": dated[0]["due_date"],
                          "overdue": dated[0].get("status") == "overdue"} if dated else None,
        "discharge_date": discharge.isoformat() if discharge else None,
        "days_since_discharge": (today - discharge).days if discharge else None,
        "coordinator": names.most_common(1)[0][0] if names else None,
    }


def test_list_patients_dashboard_and_journey_match_reference(client, db):
    handle_hl7(_a03(), client=_client(_fake(1)))  # an EHR patient next to the seeded ones
    # An orphan obligation (no patient) must not leak into the patient rows.
    db.obligations.insert_one({"id": "obl_orphan", "patient_id": "pat_gone", "description": "Orphan task", "status": "pending", "history": []})
    today = demo_today()
    try:
        assert repo.list_patients(db, today) == _reference_list_patients(db, today)

        dash = repo.dashboard(db, today, audience="coordinator")
        assert dash["patients"] == _reference_list_patients(db, today)
        pending = repo.list_approvals(db, status=ApprovalStatus.pending.value, audience="coordinator")
        assert dash["pending_approvals"] == pending and dash["stats"]["pending_approvals"] == len(pending)
        everything = [repo.present_obligation(d, today) for d in db.obligations.find({}, {"_id": 0})]
        assert dash["stats"]["open_obligations"] == sum(o["is_open"] for o in everything)

        for patient in db.patients.find({}, {"id": 1}):
            journey = repo.journey(db, patient["id"], today)
            assert journey["evidence"] == {o["id"]: repo.get_evidence(db, o["id"]) for o in journey["obligations"]}
    finally:
        db.obligations.delete_one({"id": "obl_orphan"})


# ── jobs: the in-memory job mirrors the database ─────────────────────────


def test_job_result_mirrors_stored_job(client, db):
    from app.ingest.jobs import create_job, run_job

    job = create_job(db, key="perf:mirror", params={"patient_identifiers": [MRN], "encounter_identifiers": [visit_number(PID)]},
                     actor="EHR ingestion", source="fhir")
    result = run_job(db, job["id"], client=_client(_fake()))
    stored = db.ingest_jobs.find_one({"id": job["id"]}, {"_id": 0})
    assert result["status"] == stored["status"] == "done" and stored["lease_owner"] is None
    assert result["steps"] == {s: stored["steps"][s] for s in STEPS}
    assert all(stored["steps"][s]["attempts"] == 1 for s in STEPS)
    assert {k: v for k, v in result.items() if k not in {"status", "resumed_from", "steps"}} == stored["result"]


# ── orders + embeddings ──────────────────────────────────────────────────


def test_remember_orders_learns_each_key_once(db):
    from app.ingest.orders import remember_orders

    packet = _pull(_fake(1))
    plans = [{"key": o.ref, "category": "follow_up"} for o in packet.orders]
    twin = packet.model_copy(update={"orders": packet.orders + [packet.orders[0].model_copy(update={"ref": "ServiceRequest/twin"})]})
    plans_twin = plans + [{"key": "ServiceRequest/twin", "category": "follow_up"}]
    db.order_memory.delete_many({})
    try:
        stored = remember_orders(db, twin, plans_twin)
        assert stored == len({o.code or o.display for o in packet.orders})
        assert remember_orders(db, twin, plans_twin) == 0
    finally:
        db.order_memory.delete_many({})


class _FakeModel:
    dimensions = 3

    def __init__(self):
        self.calls: list[list[str]] = []

    def embed_query(self, text):
        self.calls.append([text])
        return [float(len(text)), 1.0, 0.0]

    def embed_queries(self, texts):
        self.calls.append(list(texts))
        return [[float(len(t)), 1.0, 0.0] for t in texts]


def test_embed_texts_batches_caches_and_matches_embed_text(monkeypatch):
    from app.agent import llm

    model = _FakeModel()
    monkeypatch.setattr(llm, "get_embeddings", lambda: model)
    monkeypatch.setattr(llm, "_embed_failures", 0)
    llm._vector_cache.clear()
    try:
        texts = ["alpha", "beta", "alpha", "gamma delta"]
        batch = llm.embed_texts(texts)
        assert model.calls == [["alpha", "beta", "gamma delta"]]  # one call, de-duplicated
        assert batch == [model.embed_query(t) for t in texts]
        model.calls.clear()
        assert llm.embed_text("beta") == batch[1] and model.calls == []  # cached
        assert llm.embed_texts([]) == []
    finally:
        llm._vector_cache.clear()
