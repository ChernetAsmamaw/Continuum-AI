"""Local EHR sandbox: Synthea patients in a HAPI FHIR server, discharged into Continuum.

Run with the backend virtualenv from the repo root:

    backend/.venv/bin/python scripts/ehr_sandbox/sandbox.py load --synthea <synthea>/output/fhir
    backend/.venv/bin/python scripts/ehr_sandbox/sandbox.py list
    backend/.venv/bin/python scripts/ehr_sandbox/sandbox.py discharge 1      # sends ADT^A03 to Continuum
    backend/.venv/bin/python scripts/ehr_sandbox/sandbox.py results 1        # lab finals + visit fulfilled in the EHR
    backend/.venv/bin/python scripts/ehr_sandbox/sandbox.py poll             # ask Continuum to check the EHR now

Environment: FHIR_BASE (default http://localhost:8080/fhir), CONTINUUM_URL
(default http://localhost:8000), INTEGRATION_SECRET (from .env).
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import re
import sys
import uuid
from datetime import date, timedelta
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from app.ingest.hl7 import build_a03  # noqa: E402
from app.ingest.scenarios import index_admission, result_events, visit_number  # noqa: E402

MANIFEST = Path(__file__).resolve().parent / ".manifest.json"
FHIR_BASE = os.environ.get("FHIR_BASE", "http://localhost:8080/fhir").rstrip("/")
CONTINUUM = os.environ.get("CONTINUUM_URL", "http://localhost:8000").rstrip("/")
DROP = {"Claim", "ExplanationOfBenefit", "Provenance", "SupplyDelivery", "MedicationAdministration", "ImagingStudy"}
FHIR_HEADERS = {"Content-Type": "application/fhir+json", "Accept": "application/fhir+json"}


def _secret() -> str:
    if os.environ.get("INTEGRATION_SECRET"):
        return os.environ["INTEGRATION_SECRET"]
    env = ROOT / ".env"
    if env.exists():
        for line in env.read_text().splitlines():
            if line.startswith("INTEGRATION_SECRET="):
                return line.split("=", 1)[1].strip()
    sys.exit("Set INTEGRATION_SECRET (in .env or the environment) so Continuum accepts the HL7 feed.")


def _clean_name(text: str) -> str:
    return re.sub(r"(?<=[A-Za-zá-ú])\d+", "", text)


def _post_bundle(client: httpx.Client, bundle: dict) -> dict:
    response = client.post(FHIR_BASE, json=bundle, headers=FHIR_HEADERS, timeout=300)
    if response.status_code >= 400:
        raise SystemExit(f"FHIR server rejected bundle ({response.status_code}): {response.text[:600]}")
    return response.json()


def _as_puts(bundle: dict) -> dict:
    """Synthea POST/urn:uuid transaction → idempotent PUTs with stable ids."""
    keep = [e for e in bundle["entry"] if e["resource"]["resourceType"] not in DROP]
    ids = {e["fullUrl"]: f"{e['resource']['resourceType']}/{e['resource']['id']}" for e in keep}
    text = json.dumps({"entries": keep})
    for full, rel in ids.items():
        text = text.replace(f'"{full}"', f'"{rel}"')
    entries = json.loads(text)["entries"]
    for e in entries:
        r = e["resource"]
        e["fullUrl"] = f"{FHIR_BASE}/{r['resourceType']}/{r['id']}"
        e["request"] = {"method": "PUT", "url": f"{r['resourceType']}/{r['id']}"}
        if r["resourceType"] == "Patient":
            for name in r.get("name") or []:
                name["given"] = [_clean_name(g) for g in name.get("given") or []]
                name["family"] = _clean_name(name.get("family", ""))
                name.pop("prefix", None)
        if isinstance(r.get("subject"), dict) and r["subject"].get("display"):
            r["subject"]["display"] = _clean_name(r["subject"]["display"])
    return {"resourceType": "Bundle", "type": "transaction", "entry": entries}


def _heart_failure_patients(folder: str) -> list[str]:
    out = []
    for path in sorted(glob.glob(os.path.join(folder, "*.json"))):
        name = os.path.basename(path)
        if name.startswith(("hospitalInformation", "practitionerInformation")):
            continue
        bundle = json.load(open(path))
        resources = [e["resource"] for e in bundle["entry"]]
        patient = next(r for r in resources if r["resourceType"] == "Patient")
        if patient.get("deceasedDateTime"):
            continue
        if any(
            r["resourceType"] == "Condition" and "heart failure" in (r.get("code", {}).get("text") or "").lower()
            for r in resources
        ):
            out.append(path)
    return out


def cmd_load(args) -> None:
    discharge = date.fromisoformat(args.discharge) if args.discharge else date.today()
    with httpx.Client() as client:
        meta = client.get(f"{FHIR_BASE}/metadata", headers=FHIR_HEADERS, timeout=30)
        meta.raise_for_status()
        print(f"FHIR server: {meta.json().get('software', {}).get('name')} {meta.json().get('fhirVersion')}")
        for shared in sorted(glob.glob(os.path.join(args.synthea, "hospitalInformation*.json")) + glob.glob(os.path.join(args.synthea, "practitionerInformation*.json"))):
            _post_bundle(client, json.load(open(shared)))
            print(f"  loaded {os.path.basename(shared)}")
        manifest = []
        for i, path in enumerate(_heart_failure_patients(args.synthea)[: args.count]):
            bundle = _as_puts(json.load(open(path)))
            _post_bundle(client, bundle)
            patient = next(e["resource"] for e in bundle["entry"] if e["resource"]["resourceType"] == "Patient")
            scenario = index_admission(patient, discharge=discharge, variant=i)
            _post_bundle(
                client,
                {
                    "resourceType": "Bundle",
                    "type": "transaction",
                    "entry": [
                        {"resource": r, "request": {"method": "PUT", "url": f"{r['resourceType']}/{r['id']}"}}
                        for r in scenario
                    ],
                },
            )
            mrn = next(
                (x["value"] for x in patient.get("identifier", []) if any(c.get("code") == "MR" for c in (x.get("type") or {}).get("coding", []))),
                patient["id"],
            )
            name = patient["name"][0]
            entry = {
                "n": i + 1,
                "fhir_id": patient["id"],
                "mrn": mrn,
                "given": " ".join(name.get("given") or []),
                "family": name.get("family", ""),
                "birth_date": patient.get("birthDate"),
                "sex": (patient.get("gender") or "u")[0].upper(),
                "phone": next((t["value"] for t in patient.get("telecom") or [] if t.get("system") == "phone"), ""),
                "variant": i % 2,
                "discharge": discharge.isoformat(),
                "visit_number": visit_number(patient["id"]),
                "history_resources": len(bundle["entry"]),
            }
            manifest.append(entry)
            print(f"  [{entry['n']}] {entry['given']} {entry['family']} — {entry['history_resources']} history resources + discharge scenario (variant {entry['variant']})")
    MANIFEST.write_text(json.dumps(manifest, indent=2))
    print(f"Loaded {len(manifest)} heart-failure patients. Next: sandbox.py discharge 1")


def _manifest() -> list[dict]:
    if not MANIFEST.exists():
        sys.exit("No sandbox loaded yet: run `sandbox.py load --synthea <dir>` first.")
    return json.loads(MANIFEST.read_text())


def _pick(n: int) -> dict:
    found = next((p for p in _manifest() if p["n"] == n), None)
    if not found:
        sys.exit(f"No patient #{n}; see `sandbox.py list`.")
    return found


def cmd_list(_args) -> None:
    for p in _manifest():
        print(f"[{p['n']}] {p['given']} {p['family']}  MRN {p['mrn']}  visit {p['visit_number']}  discharge {p['discharge']}  variant {p['variant']}")


def cmd_manifest(_args) -> None:
    """Rebuild .manifest.json from the sandbox patients already in the FHIR server."""
    manifest = []
    with httpx.Client(headers=FHIR_HEADERS, timeout=30) as client:
        encounters = client.get(f"{FHIR_BASE}/Encounter", params={"identifier": "urn:continuum:sandbox:visit|", "_count": 50}).json()
        for i, entry in enumerate(encounters.get("entry") or []):
            enc = entry["resource"]
            pid = enc["subject"]["reference"].split("/")[1]
            patient = client.get(f"{FHIR_BASE}/Patient/{pid}").json()
            orders = client.get(f"{FHIR_BASE}/ServiceRequest", params={"patient": pid, "_count": 50}).json()
            order_ids = [e["resource"]["id"] for e in orders.get("entry") or []]
            mrn = next(
                (x["value"] for x in patient.get("identifier", []) if any(c.get("code") == "MR" for c in (x.get("type") or {}).get("coding", []))),
                patient["id"],
            )
            name = patient["name"][0]
            manifest.append({
                "n": i + 1,
                "fhir_id": patient["id"],
                "mrn": mrn,
                "given": " ".join(name.get("given") or []),
                "family": name.get("family", ""),
                "birth_date": patient.get("birthDate"),
                "sex": (patient.get("gender") or "u")[0].upper(),
                "phone": next((t["value"] for t in patient.get("telecom") or [] if t.get("system") == "phone"), ""),
                "variant": 0 if any(o.endswith("-sr-culture") for o in order_ids) else 1,
                "discharge": enc["period"]["end"][:10],
                "visit_number": enc["identifier"][0]["value"],
                "history_resources": None,
            })
    MANIFEST.write_text(json.dumps(manifest, indent=2))
    print(f"Rebuilt manifest with {len(manifest)} patient(s):")
    cmd_list(None)


def _recode_orders(p: dict) -> None:
    """Rewrite this patient's discharge orders the way a hospital that changed
    its code set would: local categories, local codes, a new display, and one
    order Continuum has never seen. The rules can't place these; order memory
    (known code → Vector Search → word overlap) has to.
    """
    s = p["fhir_id"].replace("-", "")[:12]
    with httpx.Client(headers=FHIR_HEADERS, timeout=30) as client:
        cardio = client.get(f"{FHIR_BASE}/ServiceRequest/{s}-sr-cardio").json()
        cardio["category"] = [{"coding": [{"system": "urn:hosp:local", "code": "CONS"}]}]
        cardio.pop("performerType", None)
        cardio["code"] = {"coding": [{"system": "http://snomed.info/sct", "code": "183519002", "display": "HF consult"}], "text": "HF consult"}
        bmp = client.get(f"{FHIR_BASE}/ServiceRequest/{s}-sr-bmp").json()
        bmp["category"] = [{"coding": [{"system": "urn:hosp:local", "code": "LBX"}]}]  # no display: nothing says "lab"
        # Same test, new local code and wording: no rule keyword, almost no word
        # overlap with "Basic metabolic panel (creatinine, electrolytes)", but
        # the same meaning — only Vector Search over past orders can place it.
        bmp["code"] = {"coding": [{"system": "urn:hosp:local", "code": "RFP-7"}], "text": "Renal function panel"}
        bmp.pop("note", None)
        wound = {
            "resourceType": "ServiceRequest", "id": f"{s}-sr-wound", "status": "active", "intent": "order",
            "category": [{"coding": [{"system": "urn:hosp:local", "code": "ZZ"}]}],
            "code": {"text": "Wound check"},
            "subject": cardio["subject"], "encounter": cardio["encounter"],
            "authoredOn": cardio.get("authoredOn"), "requester": cardio.get("requester"),
        }
        for r in (cardio, bmp, wound):
            response = client.put(f"{FHIR_BASE}/{r['resourceType']}/{r['id']}", json=r)
            if response.status_code >= 400:
                raise SystemExit(f"FHIR server rejected {r['id']} ({response.status_code}): {response.text[:300]}")
    print("→ Recoded the EHR's orders before discharge:")
    print(f"  {s}-sr-cardio  same SNOMED code, new display 'HF consult', local category CONS, no performer")
    print(f"  {s}-sr-bmp     local code RFP-7 'Renal function panel', local category LBX, no note")
    print(f"  {s}-sr-wound   new order 'Wound check', local category ZZ (never seen)")


RECODED_KEYS = ["code:RFP-7", "code:BMP-7", "text:check wound"]  # what a --recode run can teach


def cmd_reset(args) -> None:
    """Forget patient N in Continuum (and anything a --recode run taught), so the
    next discharge is a brand-new patient again. Needs the backend's .env."""
    from app import repository as repo
    from app.db import get_db

    p = _pick(args.n)
    db = get_db()
    patient = db.patients.find_one({"$or": [{"ehr.fhir_id": p["fhir_id"]}, {"mrn": p["mrn"]}]}, {"_id": 0, "id": 1, "name": 1})
    if patient:
        repo.delete_patient(db, patient["id"])
        print(f"Forgot {patient['name']} ({patient['id']}) and every derived record.")
    else:
        print(f"Patient #{args.n} is not in Continuum; nothing to forget.")
    forgotten = db.order_memory.delete_many({"key": {"$in": RECODED_KEYS}}).deleted_count
    print(f"Forgot {forgotten} remembered order(s) learned from recoded codes.")


def cmd_discharge(args) -> None:
    p = _pick(args.n)
    if getattr(args, "recode", False):
        _recode_orders(p)
    discharge = date.fromisoformat(p["discharge"])
    message = build_a03(
        control_id=f"ADT{uuid.uuid4().hex[:10].upper()}",
        mrn=p["mrn"], family=p["family"], given=p["given"], birth_date=p["birth_date"], sex=p["sex"],
        visit_number=p["visit_number"], admit=(discharge - timedelta(days=4)).isoformat(), discharge=p["discharge"],
        attending="Dr. Priya Raman", phone=p["phone"],
    )
    print("→ ADT^A03\n  " + message.replace("\r", "\n  "))
    response = httpx.post(
        f"{CONTINUUM}/api/ingest/hl7",
        content=message,
        headers={"content-type": "text/plain", "X-Continuum-Integration-Token": _secret()},
        timeout=120,
    )
    body = response.json()
    if response.status_code >= 400:
        sys.exit(f"Continuum returned {response.status_code}: {body}")
    print("← ACK\n  " + body["ack"].replace("\r", "\n  "))
    print(f"Outcome: {body['outcome']} — {body.get('detail')}")
    if body.get("patient_id"):
        print(f"Open: {CONTINUUM}/patients/{body['patient_id']}")


def cmd_results(args) -> None:
    p = _pick(args.n)
    patient = {"id": p["fhir_id"]}
    with httpx.Client() as client:
        for r in result_events(patient, discharge=date.fromisoformat(p["discharge"]), variant=p["variant"]):
            response = client.put(f"{FHIR_BASE}/{r['resourceType']}/{r['id']}", json=r, headers=FHIR_HEADERS, timeout=60)
            response.raise_for_status()
            print(f"  EHR now has {r['resourceType']}/{r['id']} ({r['status']})")
    print("Results recorded in the EHR. Continuum's sweep will find them; or run `sandbox.py poll` now.")


def cmd_poll(_args) -> None:
    response = httpx.post(f"{CONTINUUM}/api/ingest/poll", headers={"X-Continuum-Integration-Token": _secret()}, timeout=120)
    body = response.json()
    if response.status_code >= 400:
        sys.exit(f"Continuum returned {response.status_code}: {body}")
    print(f"Checked {body['checked_obligations']} open follow-up(s), saw {body['resources_seen']} EHR resource version(s), applied {body['applied']}.")
    for r in body["results"]:
        print(f"  {r['source_ref']} → {r['outcome']} ({', '.join(r.get('match_reasons') or [])})")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    load = sub.add_parser("load", help="Load Synthea heart-failure patients + discharge scenarios into the FHIR server")
    load.add_argument("--synthea", required=True, help="Synthea output/fhir directory")
    load.add_argument("--count", type=int, default=6)
    load.add_argument("--discharge", help="Discharge date (YYYY-MM-DD); default today")
    sub.add_parser("list", help="List loaded sandbox patients")
    sub.add_parser("manifest", help="Rebuild .manifest.json from the patients already in the FHIR server")
    d = sub.add_parser("discharge", help="Send an ADT^A03 for patient N to Continuum")
    d.add_argument("n", type=int)
    d.add_argument("--recode", action="store_true", help="First rewrite the EHR's orders with changed codes/categories (demo: adapting to a changed system)")
    r = sub.add_parser("results", help="Record final results / fulfilled visit for patient N in the EHR")
    r.add_argument("n", type=int)
    sub.add_parser("poll", help="Ask Continuum to poll the EHR now")
    rs = sub.add_parser("reset", help="Forget patient N in Continuum so the next discharge starts fresh (demo rehearsals)")
    rs.add_argument("n", type=int)
    args = parser.parse_args()
    {"load": cmd_load, "list": cmd_list, "manifest": cmd_manifest, "discharge": cmd_discharge, "results": cmd_results,
     "poll": cmd_poll, "reset": cmd_reset}[args.cmd](args)


if __name__ == "__main__":
    main()
