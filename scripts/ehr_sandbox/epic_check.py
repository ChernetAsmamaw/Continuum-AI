"""Check the Epic sandbox connection: token, then a read of a test patient.

    backend/.venv/bin/python scripts/ehr_sandbox/epic_check.py <client-id>

Uses ~/.continuum/epic/privatekey.pem and the key id in the published JWKS.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))

from app.ingest.fhir_client import FHIRClient, FHIRError  # noqa: E402

BASE = "https://fhir.epic.com/interconnect-fhir-oauth/api/FHIR/R4"
TOKEN_URL = "https://fhir.epic.com/interconnect-fhir-oauth/oauth2/token"
KEY = Path(os.environ.get("EHR_PRIVATE_KEY_PATH", Path.home() / ".continuum/epic/privatekey.pem"))
KID = os.environ.get("EHR_KEY_ID", "faKQA94wpMA5FoS6OAXgWUB49cX0MgTZhxtggOmRp5o")
# Epic's published sandbox test patient (Camila Lopez).
TEST_PATIENT = os.environ.get("EPIC_TEST_PATIENT", "erXuFYUfucBZaryVksYEcMg3")


def main() -> None:
    client_id = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("EHR_CLIENT_ID")
    if not client_id:
        sys.exit("Usage: epic_check.py <non-production client id>")
    client = FHIRClient(BASE, auth_mode="backend_jwt", client_id=client_id, token_url=TOKEN_URL,
                        private_key=KEY.read_text(), key_id=KID)
    try:
        client._access_token()
    except FHIRError as exc:
        sys.exit(f"Token: FAILED — {exc}\n(A new app can take a while to activate in Epic's sandbox; retry later.)")
    print("Token: OK")
    patient = client.read("Patient", TEST_PATIENT)
    if not patient:
        sys.exit(f"Patient/{TEST_PATIENT}: not found")
    name = (patient.get("name") or [{}])[0]
    print(f"Patient: {name.get('text') or ' '.join(name.get('given', []) + [name.get('family', '')])} (born {patient.get('birthDate')})")
    for rtype, params in [
        ("Encounter", {"patient": TEST_PATIENT}),
        ("ServiceRequest", {"patient": TEST_PATIENT, "category": "108252007"}),
        ("Appointment", {"patient": TEST_PATIENT, "service-category": "appointment"}),
        ("MedicationRequest", {"patient": TEST_PATIENT}),
        ("DiagnosticReport", {"patient": TEST_PATIENT}),
        ("DocumentReference", {"patient": TEST_PATIENT, "category": "clinical-note"}),
    ]:
        try:
            print(f"{rtype}: {len(client.search(rtype, params, max_pages=1))} found")
        except FHIRError as exc:
            print(f"{rtype}: error — {str(exc)[:160]}")


if __name__ == "__main__":
    main()
