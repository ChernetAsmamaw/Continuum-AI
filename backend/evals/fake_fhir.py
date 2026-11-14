"""A tiny in-memory FHIR R4 server behind httpx.MockTransport (tests + evals)."""

from __future__ import annotations

import json
from datetime import date

import httpx

BASE = "http://ehr.test/fhir"


def make_patient(pid: str, given: str, family: str, birth: str = "1946-03-02", mrn: str | None = None) -> dict:
    return {
        "resourceType": "Patient",
        "id": pid,
        "identifier": [
            {
                "system": "http://hospital.smarthealthit.org",
                "type": {"coding": [{"system": "http://terminology.hl7.org/CodeSystem/v2-0203", "code": "MR"}]},
                "value": mrn or f"MRN-{pid[:6].upper()}",
            },
            {
                "system": "http://hl7.org/fhir/sid/us-ssn",
                "type": {"coding": [{"system": "http://terminology.hl7.org/CodeSystem/v2-0203", "code": "SS"}]},
                "value": "999-99-1234",
            },
        ],
        "name": [{"use": "official", "family": family, "given": [given]}],
        "telecom": [{"system": "phone", "value": "555-201-7788", "use": "home"}],
        "gender": "male",
        "birthDate": birth,
    }


def _refs(resource: dict, key: str) -> list[str]:
    value = resource.get(key)
    if isinstance(value, dict):
        return [value.get("reference", "")]
    if isinstance(value, list):
        return [v.get("reference", "") for v in value if isinstance(v, dict)]
    return []


class FakeFHIR:
    def __init__(self) -> None:
        self.store: dict[str, dict[str, dict]] = {}
        self.requests: list[str] = []

    def put(self, resource: dict) -> dict:
        bucket = self.store.setdefault(resource["resourceType"], {})
        previous = bucket.get(resource["id"])
        version = int((previous or {}).get("meta", {}).get("versionId", "0")) + 1
        stored = json.loads(json.dumps(resource))
        stored["meta"] = {"versionId": str(version), "lastUpdated": f"{date.today().isoformat()}T00:00:00Z"}
        bucket[resource["id"]] = stored
        return stored

    def load(self, resources: list[dict]) -> None:
        for r in resources:
            self.put(r)

    # ── search semantics (just enough for the pull + poller) ─────────────

    def _matches(self, rtype: str, resource: dict, key: str, value: str) -> bool:
        if key == "_id":
            return resource.get("id") in value.split(",")
        if key.startswith("_"):
            return True
        if key == "patient":
            target = value if "/" in value else f"Patient/{value}"
            return target in _refs(resource, "subject") + _refs(resource, "patient") + [
                (p.get("actor") or {}).get("reference", "") for p in resource.get("participant") or []
            ]
        if key == "identifier":
            wanted = value.split("|")[-1]
            return any(i.get("value") == wanted for i in resource.get("identifier") or [])
        if key == "type":
            code = value.split("|")[-1]
            return any(c.get("code") == code for c in (resource.get("type") or {}).get("coding") or [])
        if key == "status":
            return resource.get("status") in value.split(",")
        if key == "based-on":
            return any(v in _refs(resource, "basedOn") for v in value.split(","))
        if key == "date":
            when = (resource.get("start") or resource.get("date") or (resource.get("period") or {}).get("start") or "")[:10]
            if value.startswith("ge"):
                return when >= value[2:]
            return when.startswith(value)
        if key == "class":
            return (resource.get("class") or {}).get("code") == value
        return True

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(str(request.url))
        path = request.url.path.split("/fhir/", 1)[-1].strip("/")
        parts = path.split("/")
        rtype = parts[0]
        if len(parts) >= 2:
            found = self.store.get(rtype, {}).get(parts[1])
            if not found:
                return httpx.Response(404, json={"resourceType": "OperationOutcome"})
            return httpx.Response(200, json=found)
        params = dict(request.url.params)
        hits = [
            r for r in self.store.get(rtype, {}).values()
            if all(self._matches(rtype, r, k, v) for k, v in params.items())
        ]
        return httpx.Response(
            200,
            json={
                "resourceType": "Bundle",
                "type": "searchset",
                "total": len(hits),
                "entry": [{"resource": r, "search": {"mode": "match"}} for r in hits],
            },
        )

    def client(self):
        from app.ingest.fhir_client import FHIRClient

        return FHIRClient(BASE, transport=httpx.MockTransport(self.handler))
