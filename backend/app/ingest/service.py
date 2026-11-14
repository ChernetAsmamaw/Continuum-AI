"""Orchestration: HL7 trigger → FHIR pull → build follow-ups."""

from __future__ import annotations

import logging

from ..db import get_db
from ..util import new_id, utcnow
from .fhir_client import FHIRClient, FHIRError, client_from_settings
from .hl7 import HL7Error, build_ack, parse_adt
from .pull import PullError, pull_discharge

log = logging.getLogger("continuum.ingest")


class IngestUnavailable(RuntimeError):
    pass


def _client(client: FHIRClient | None) -> FHIRClient:
    client = client or client_from_settings()
    if client is None:
        raise IngestUnavailable("EHR_FHIR_BASE_URL is not configured")
    return client


def pull_and_ingest(
    *,
    patient_fhir_id: str | None = None,
    patient_identifiers: list[str] = (),
    encounter_fhir_id: str | None = None,
    encounter_identifiers: list[str] = (),
    actor: str = "EHR ingestion",
    source: str = "fhir",
    source_ref: str | None = None,
    client: FHIRClient | None = None,
    job_key: str | None = None,
    crash_after: str | None = None,
) -> dict:
    """Run (or resume) the ingestion job for one discharge."""
    from .jobs import create_job, run_job

    from ..config import settings

    if client is None and not settings.ehr_fhir_base_url:
        raise IngestUnavailable("EHR_FHIR_BASE_URL is not configured")
    db = get_db()
    job = create_job(
        db,
        key=job_key or f"pull:{new_id('req')}",
        params={
            "patient_fhir_id": patient_fhir_id,
            "patient_identifiers": list(patient_identifiers),
            "encounter_fhir_id": encounter_fhir_id,
            "encounter_identifiers": list(encounter_identifiers),
            "source_ref": source_ref,
        },
        actor=actor,
        source=source,
    )
    result = run_job(db, job["id"], client=client, crash_after=crash_after)
    if result["status"] == "failed":
        if result.get("failed_step") == "pull" and not result.get("retryable"):
            raise PullError(result["error"])
        raise FHIRError(result["error"])
    if result["status"] != "done":
        raise FHIRError(result.get("detail") or f"Job {job['id']} is {result['status']}")
    packet = (db.ingest_jobs.find_one({"id": job["id"]}, {"packet": 1}) or {}).get("packet") or {}
    result["packet"] = {
        "orders": len(packet.get("orders") or []),
        "appointments": len(packet.get("appointments") or []),
        "pending_results": len(packet.get("pending_results") or []),
        "medications": len(packet.get("medications") or []),
        "discharge_summary": bool(packet.get("discharge_summary_text")),
        "warnings": packet.get("warnings") or [],
    }
    return result


def handle_hl7(raw: str, *, actor: str = "HL7 interface", client: FHIRClient | None = None, crash_after: str | None = None) -> dict:
    """Process one HL7 message. Always returns an ACK (AA/AE/AR) with the outcome."""
    db = get_db()
    try:
        event = parse_adt(raw)
    except (HL7Error, KeyError, IndexError) as exc:
        return {"outcome": "rejected", "detail": str(exc), "ack": build_ack("UNKNOWN", "AR", str(exc))}

    previous = db.hl7_messages.find_one({"control_id": event.control_id, "sending_facility": event.sending_facility}, {"_id": 0})
    still_there = previous and (not previous.get("patient_id") or db.patients.find_one({"id": previous["patient_id"]}, {"_id": 1}))
    if previous and previous.get("outcome") not in {"error"} and still_there:
        return {**previous, "outcome": "duplicate", "ack": build_ack(event.control_id, "AA", "Duplicate; already processed")}

    record = {
        "id": new_id("hl7"),
        "control_id": event.control_id,
        "sending_app": event.sending_app,
        "sending_facility": event.sending_facility,
        "trigger": event.trigger,
        "received_at": utcnow(),
        "visit_number": event.visit_number,
        "patient_identifiers": [i["value"] for i in event.patient_identifiers],
    }
    if not event.is_discharge:
        record.update(outcome="ignored", detail=f"ADT^{event.trigger} does not start follow-up tracking")
        ack = build_ack(event.control_id, "AA", record["detail"], receiving_app=event.sending_app, receiving_facility=event.sending_facility)
    else:
        try:
            result = pull_and_ingest(
                patient_identifiers=event.identifier_values,
                encounter_identifiers=[event.visit_number] if event.visit_number else [],
                actor=actor,
                source="hl7v2+fhir",
                source_ref=f"HL7/{event.sending_facility}/{event.control_id}",
                client=client,
                # Same message re-sent after a crash → same job → resumes, never restarts.
                job_key=f"hl7:{event.sending_facility}:{event.control_id}",
                crash_after=crash_after,
            )
            record.update(
                outcome=result["outcome"],
                detail=result["summary"],
                patient_id=result["patient_id"],
                episode_id=result["episode_id"],
                ingest_run_id=result["id"],
                job_id=result["job_id"],
                resumed_from=result.get("resumed_from"),
            )
            ack = build_ack(event.control_id, "AA", result["summary"], receiving_app=event.sending_app, receiving_facility=event.sending_facility)
        except (PullError, FHIRError, IngestUnavailable) as exc:
            log.warning("ADT^A03 %s could not be processed: %s", event.control_id, exc)
            record.update(outcome="error", detail=str(exc))
            ack = build_ack(event.control_id, "AE", str(exc), receiving_app=event.sending_app, receiving_facility=event.sending_facility)
    db.hl7_messages.update_one(
        {"control_id": event.control_id, "sending_facility": event.sending_facility},
        {"$set": record},
        upsert=True,
    )
    return {**record, "ack": ack}
