"""Ingestion eval: EHR pull vs. today's "paste the discharge summary" intake.

    cd backend
    python -m evals.ingestion            # prints a table, writes evals/results/ingestion-*.json

Each case is a synthetic heart-failure discharge (two scenario variants) in an
in-memory FHIR server. Both systems see the same hospital record:

* **ehr_ingestion**: ADT^A03 → FHIR pull → structured follow-ups + narrative
  extraction; results are found by the poller and matched by order id.
* **paste_summary**: a coordinator pastes the discharge narrative (the current
  intake flow); results arrive by clinic push and are matched by text.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

os.environ["CONTINUUM_DB_MODE"] = "memory"
os.environ.setdefault("MONGODB_DB", "continuum_ingest_eval")
os.environ["AUTO_SEED"] = "false"
os.environ["SCHEDULER_INTERVAL_SECONDS"] = "0"
os.environ["EMBEDDING_PROVIDER"] = os.environ.get("EMBEDDING_PROVIDER", "none") or "none"

from app.db import demo_today, get_db  # noqa: E402
from app.ingest.hl7 import build_a03  # noqa: E402
from app.ingest.poller import poll_ehr  # noqa: E402
from app.ingest.scenarios import expected_followups, index_admission, result_events, visit_number  # noqa: E402
from app.ingest.service import handle_hl7  # noqa: E402
from app.integrations import ingest_fhir  # noqa: E402
from app.schemas import PatientIntakeRequest  # noqa: E402
from app.workflow import intake_patient  # noqa: E402

from .fake_fhir import FakeFHIR, make_patient  # noqa: E402

RESULTS_DIR = Path(__file__).resolve().parent / "results"
NAMES = [
    ("Ezekiel", "Walter"), ("Shalanda", "Veum"), ("Cristobal", "Montero"), ("Jody", "Hickle"),
    ("Larissa", "Swift"), ("Clarinda", "Christiansen"), ("Wallace", "Effertz"), ("Lindsay", "Zieme"),
]
CLOSURES = {  # result resource id suffix → category it must close
    0: {"dr-bmp": "bloodwork", "dr-culture": "pending_result", "appt-pcp": "family_physician"},
    1: {"dr-bmp": "bloodwork", "dr-echo": "diagnostic_imaging"},
}

METRICS = {
    "followup_recall": "Expected follow-ups tracked",
    "pending_results_tracked": "Tests pending at discharge tracked",
    "duplicate_rate": "Duplicate follow-ups (lower is better)",
    "structured_share": "Follow-ups backed by a structured EHR order",
    "provenance": "Follow-ups traceable to an EHR record",
    "closure_correct": "Results matched to the right follow-up",
    "manual_steps": "Manual steps per discharge (lower is better)",
}


def _reset() -> None:
    db = get_db()
    for name in db.list_collection_names():
        db[name].delete_many({})


def _score(patient_id: str, variant: int) -> dict:
    db = get_db()
    obligations = list(db.obligations.find({"patient_id": patient_id}, {"_id": 0}))
    expected = expected_followups(variant)
    cats = [o.get("category") for o in obligations]
    pending_cat = "pending_result" if variant == 0 else "diagnostic_imaging"
    return {
        "followup_recall": sum(1 for c in expected if c in cats) / len(expected),
        "pending_results_tracked": 1.0 if any(
            c == pending_cat and "pending" in (o.get("description") or "").lower() for o, c in zip(obligations, cats)
        ) else 0.0,
        "duplicate_rate": (len(cats) - len(set(cats))) / max(len(cats), 1),
        "structured_share": sum(1 for o in obligations if o.get("origin") == "ehr_structured") / max(len(obligations), 1),
        "provenance": sum(1 for o in obligations if o.get("fhir_refs")) / max(len(obligations), 1),
    }


def _closure_score(patient_id: str, variant: int, results: list[dict]) -> float:
    db = get_db()
    by_id = {o["id"]: o for o in db.obligations.find({"patient_id": patient_id}, {"_id": 0})}
    expected = CLOSURES[variant]
    correct = 0
    for suffix, category in expected.items():
        hit = next((r for r in results if f"-{suffix}/" in (r.get("source_ref") or "")), None)
        if hit and hit.get("outcome") in {"verification_ready", "scheduled"} and by_id.get(hit.get("obligation_id"), {}).get("category") == category:
            correct += 1
    return correct / len(expected)


def run_case(index: int) -> dict:
    variant = index % 2
    given, family = NAMES[index % len(NAMES)]
    discharge = demo_today()
    fhir_id = f"pt{index:02d}-{family.lower()}-synthea"
    patient = make_patient(fhir_id, given, family, mrn=f"MRN-{index:04d}")
    out: dict = {"id": f"case-{index}", "variant": variant, "systems": {}}

    # ── EHR ingestion ────────────────────────────────────────────────────
    _reset()
    fake = FakeFHIR()
    fake.put(patient)
    fake.load(index_admission(patient, discharge=discharge, variant=variant))
    message = build_a03(
        control_id=f"EVAL{index:04d}", mrn=f"MRN-{index:04d}", family=family, given=given,
        birth_date=patient["birthDate"], sex="M", visit_number=visit_number(fhir_id),
        admit=(discharge - timedelta(days=4)).isoformat(), discharge=discharge.isoformat(),
    )
    ingested = handle_hl7(message, client=fake.client())
    scores = _score(ingested["patient_id"], variant)
    for resource in result_events(patient, discharge=discharge, variant=variant):
        fake.put(resource)
    polled = poll_ehr(get_db(), fake.client())["results"]
    scores["closure_correct"] = _closure_score(ingested["patient_id"], variant, polled)
    scores["manual_steps"] = 0
    out["systems"]["ehr_ingestion"] = scores

    # ── Paste-the-summary baseline ───────────────────────────────────────
    _reset()
    narrative = next(r for r in index_admission(patient, discharge=discharge, variant=variant) if r["resourceType"] == "DocumentReference")
    import base64

    text = base64.b64decode(narrative["content"][0]["attachment"]["data"]).decode()
    created = intake_patient(
        PatientIntakeRequest(
            name=f"{given} {family}", mrn=f"MRN-{index:04d}", discharge_date=discharge.isoformat(),
            reason="Acute decompensated heart failure", discharge_summary=text,
        ),
        actor="Coordinator (pasted summary)",
    )
    pid = created["patient"]["id"]
    get_db().patients.update_one({"id": pid}, {"$set": {"ehr.fhir_id": fhir_id}})  # same identity resolution for both
    scores = _score(pid, variant)
    pushed = []
    for resource in result_events(patient, discharge=discharge, variant=variant):
        resource = {**resource, "meta": {"versionId": "2"}}
        pushed.extend(ingest_fhir(resource, actor="Clinic push")["results"])
    scores["closure_correct"] = _closure_score(pid, variant, pushed)
    scores["manual_steps"] = 1
    out["systems"]["paste_summary"] = scores
    return out


def run(n: int = 8) -> dict:
    cases = [run_case(i) for i in range(n)]
    summary = {
        system: {key: sum(c["systems"][system][key] for c in cases) / len(cases) for key in METRICS}
        for system in ("ehr_ingestion", "paste_summary")
    }
    return {"ran_at": datetime.now(timezone.utc).isoformat(), "cases": cases, "summary": summary}


def _fmt(key: str, value: float) -> str:
    return f"{value:.1f}" if key == "manual_steps" else f"{value * 100:.0f}%"


def main() -> None:
    report = run()
    print(f"\n{'Metric':48} {'EHR ingestion':>14} {'Paste summary':>14}")
    for key, label in METRICS.items():
        print(f"{label:48} {_fmt(key, report['summary']['ehr_ingestion'][key]):>14} {_fmt(key, report['summary']['paste_summary'][key]):>14}")
    RESULTS_DIR.mkdir(exist_ok=True)
    path = RESULTS_DIR / f"ingestion-{datetime.now().strftime('%Y%m%d-%H%M%S')}.json"
    path.write_text(json.dumps(report, indent=2))
    print(f"\nWrote {path.relative_to(Path.cwd()) if path.is_relative_to(Path.cwd()) else path}", file=sys.stderr)


if __name__ == "__main__":
    main()
