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


def cmd_discharge(args) -> None:
    p = _pick(args.n)
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
    d = sub.add_parser("discharge", help="Send an ADT^A03 for patient N to Continuum")
    d.add_argument("n", type=int)
    r = sub.add_parser("results", help="Record final results / fulfilled visit for patient N in the EHR")
    r.add_argument("n", type=int)
    sub.add_parser("poll", help="Ask Continuum to poll the EHR now")
    args = parser.parse_args()
    {"load": cmd_load, "list": cmd_list, "discharge": cmd_discharge, "results": cmd_results, "poll": cmd_poll}[args.cmd](args)


if __name__ == "__main__":
    main()
