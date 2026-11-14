"""Pull a discharge out of a FHIR server into a ``DischargePacket``.

What we fetch for a patient + inpatient encounter:

| Resource | Why |
| --- | --- |
| Patient, Encounter | identity, admit/discharge dates, reason, attending |
| DocumentReference (LOINC 18842-5) | the discharge summary narrative |
| ServiceRequest (active, from the stay) | referrals, labs, imaging still to happen |
| Appointment (from discharge onwards) | follow-ups already booked |
| MedicationRequest (active, from the stay) | new/changed meds → medication reconciliation (clinician) |
| MedicationStatement (active, optional) | home list to reconcile against |
| DiagnosticReport (registered/partial/preliminary) | results still pending at discharge |

Searches are kept simple (patient + client-side filtering on the encounter or
the stay window) so the same code works against HAPI, Epic and Oracle Health,
which each support a slightly different set of search parameters.

Performance: once the encounter is known, the five per-stay searches are
independent and run concurrently. Where a standard search parameter can cut
the result set on the server (``status`` on DiagnosticReport and
ServiceRequest), it is sent too; the client-side filters stay, so a server
that ignores the parameter gives the same packet, and one that rejects it is
searched again without it.
"""

from __future__ import annotations

import base64
import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone

from ..util import parse_date
from .fhir_client import FHIRClient, FHIRError
from .packet import (
    DischargePacket,
    PacketAppointment,
    PacketEncounter,
    PacketIdentifier,
    PacketMedication,
    PacketOrder,
    PacketPatient,
    PacketPendingResult,
)

log = logging.getLogger("continuum.ingest.pull")

DISCHARGE_SUMMARY_LOINC = "18842-5"
PENDING_REPORT_STATUSES = {"registered", "partial", "preliminary"}
OPEN_ORDER_STATUSES = {"active", "on-hold", "draft"}
LIVE_APPOINTMENT_STATUSES = {"proposed", "pending", "booked", "arrived", "checked-in"}

REFERRAL_CODES = {"3457005", "306206005"}
LAB_CODES = {"108252007", "15220000"}
IMAGING_CODES = {"363679005"}


class PullError(RuntimeError):
    pass


# ── small FHIR helpers ───────────────────────────────────────────────────




def _optional_search(client, resource: str, params: dict) -> list[dict]:
    """A search some servers don't support: an error means "none", not a failed pull."""
    try:
        return client.search(resource, params)
    except Exception:  # noqa: BLE001 - optional enrichment
        return []

def ref(resource: dict) -> str:
    return f"{resource.get('resourceType')}/{resource.get('id')}"


def version_ref(resource: dict) -> str:
    version = (resource.get("meta") or {}).get("versionId") or "1"
    return f"{ref(resource)}/_history/{version}"


def normalize_reference(reference: str | None) -> str | None:
    """'https://x/fhir/ServiceRequest/1/_history/2' → 'ServiceRequest/1'."""
    if not reference:
        return None
    parts = [p for p in reference.split("/") if p]
    if "_history" in parts:
        parts = parts[: parts.index("_history")]
    if len(parts) >= 2:
        return f"{parts[-2]}/{parts[-1]}"
    return reference


def codeable_text(concept: dict | None) -> str:
    if not concept:
        return ""
    if concept.get("text"):
        return concept["text"]
    for coding in concept.get("coding") or []:
        if coding.get("display"):
            return coding["display"]
    return ""


def codes(concepts: list[dict] | dict | None) -> set[str]:
    if isinstance(concepts, dict):
        concepts = [concepts]
    return {c.get("code") for concept in concepts or [] for c in concept.get("coding") or [] if c.get("code")}


def iso(value: str | None) -> str | None:
    d = parse_date(value) if value else None
    return d.isoformat() if d else None


def human_name(patient: dict) -> str:
    names = patient.get("name") or []
    official = next((n for n in names if n.get("use") == "official"), names[0] if names else {})
    if official.get("text"):
        return official["text"]
    given = " ".join(official.get("given") or [])
    return " ".join(p for p in (given, official.get("family", "")) if p).strip() or "Unknown patient"


def _identifiers(resource: dict) -> list[PacketIdentifier]:
    out = []
    for ident in resource.get("identifier") or []:
        if not ident.get("value"):
            continue
        type_code = next(iter(codes(ident.get("type"))), None)
        out.append(PacketIdentifier(system=ident.get("system"), value=ident["value"], type=type_code))
    return out


def _subject_id(resource: dict) -> str | None:
    for key in ("subject", "patient"):
        reference = normalize_reference((resource.get(key) or {}).get("reference"))
        if reference and reference.startswith("Patient/"):
            return reference.split("/", 1)[1]
    return None


# ── lookup ───────────────────────────────────────────────────────────────


def find_patient(client: FHIRClient, *, fhir_id: str | None = None, identifiers: list[str] = ()) -> dict | None:
    if fhir_id:
        found = client.read("Patient", fhir_id)
        if found:
            return found
    for value in identifiers:
        hits = client.search("Patient", {"identifier": value})
        if len(hits) == 1:
            return hits[0]
    return None


def _encounter_lookups(client: FHIRClient, *, fhir_id: str | None, identifiers: list[str]) -> list[list[dict]]:
    """Candidate encounters by EHR id and visit identifier, in priority order.

    Doesn't need the patient, so it can run while the patient is looked up.
    """
    stages: list[list[dict]] = []
    if fhir_id:
        found = client.read("Encounter", fhir_id)
        stages.append([found] if found else [])
    for value in identifiers:
        stages.append(client.search("Encounter", {"identifier": value}))
    return stages


def find_encounter(
    client: FHIRClient,
    patient_id: str,
    *,
    fhir_id: str | None = None,
    identifiers: list[str] = (),
    lookups: list[list[dict]] | None = None,
) -> dict | None:
    if lookups is None:
        lookups = _encounter_lookups(client, fhir_id=fhir_id, identifiers=list(identifiers))
    for stage in lookups:
        hits = [e for e in stage if _subject_id(e) == patient_id]
        if hits:
            return hits[0]
    # Fall back to the most recent inpatient stay.
    stays = [
        e
        for e in client.search("Encounter", {"patient": patient_id, "_sort": "-date", "_count": 50}, max_pages=1)
        if (e.get("class") or {}).get("code") in {"IMP", "ACUTE", "NONAC"}
    ]
    stays.sort(key=_stay_end, reverse=True)
    return stays[0] if stays else None


def _stay_end(encounter: dict) -> datetime:
    """When a stay ended (or started), as a real instant: the strings mix
    time zones ("...T11:00:00Z" vs "...T22:22:27-05:00"), so they can't be
    compared as text."""
    period = encounter.get("period") or {}
    value = period.get("end") or period.get("start") or ""
    try:
        moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return datetime.min.replace(tzinfo=timezone.utc)
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def search_narrowed(client: FHIRClient, resource_type: str, params: dict, narrow: dict) -> list[dict]:
    """Search with extra server-side filters; without them if the server refuses.

    Callers still filter client-side, so this only ever saves transfer and
    paging, never changes what is selected.
    """
    try:
        return client.search(resource_type, {**params, **narrow})
    except FHIRError as exc:
        log.info("%s search rejected %s (%s); retrying without it.", resource_type, sorted(narrow), str(exc)[:120])
        return client.search(resource_type, params)


# ── pull ─────────────────────────────────────────────────────────────────


def _order_kind(sr: dict) -> str:
    cats = codes(sr.get("category"))
    text = " ".join(codeable_text(c) for c in sr.get("category") or []).lower() + " " + codeable_text(sr.get("code")).lower()
    if cats & REFERRAL_CODES or "referral" in text:
        return "referral"
    if cats & IMAGING_CODES or "imaging" in text:
        return "imaging"
    if cats & LAB_CODES or "laboratory" in text or " lab" in f" {text}":
        return "lab"
    return "other"


def _report_kind(report: dict) -> str:
    text = " ".join(codeable_text(c) for c in report.get("category") or []).lower()
    cats = codes(report.get("category"))
    if "LAB" in cats or "lab" in text:
        return "lab"
    if cats & {"RAD", "CUS", "IMG"} or "imaging" in text or "radiology" in text or "cardiology" in text:
        return "imaging"
    return "other"


def _due(sr: dict) -> str | None:
    if sr.get("occurrenceDateTime"):
        return iso(sr["occurrenceDateTime"])
    period = sr.get("occurrencePeriod") or {}
    return iso(period.get("end") or period.get("start"))


def _decode_attachment(client: FHIRClient, attachment: dict) -> str | None:
    if attachment.get("data"):
        try:
            return base64.b64decode(attachment["data"]).decode("utf-8", errors="replace")
        except Exception:
            return None
    url = attachment.get("url")
    if url:
        binary = client.get(url)
        if binary and binary.get("data"):
            return base64.b64decode(binary["data"]).decode("utf-8", errors="replace")
    return None


def pull_discharge(
    client: FHIRClient,
    *,
    patient_fhir_id: str | None = None,
    patient_identifiers: list[str] = (),
    encounter_fhir_id: str | None = None,
    encounter_identifiers: list[str] = (),
    source: str = "fhir",
    source_ref: str | None = None,
) -> DischargePacket:
    with ThreadPoolExecutor(max_workers=5, thread_name_prefix="fhir-pull") as pool:
        # The encounter lookup by id/visit number doesn't depend on the patient.
        encounter_lookups = None
        if encounter_fhir_id or encounter_identifiers:
            encounter_lookups = pool.submit(
                _encounter_lookups, client, fhir_id=encounter_fhir_id, identifiers=list(encounter_identifiers)
            )
        patient = find_patient(client, fhir_id=patient_fhir_id, identifiers=list(patient_identifiers))
        if not patient:
            raise PullError("Patient not found in the EHR")
        pid = patient["id"]
        encounter = find_encounter(
            client, pid, lookups=encounter_lookups.result() if encounter_lookups else []
        )
        if not encounter:
            raise PullError(f"No inpatient encounter found for Patient/{pid}")
        packet_parts = _pull_stay(client, pool, pid, encounter)
    return _assemble(client, patient, encounter, packet_parts, source=source, source_ref=source_ref)


def _pull_stay(client: FHIRClient, pool: ThreadPoolExecutor, pid: str, encounter: dict) -> dict:
    enc_ref = ref(encounter)
    period = encounter.get("period") or {}
    admit = parse_date(period.get("start"))
    discharge = parse_date(period.get("end")) or date.today()
    window_start = admit or discharge - timedelta(days=30)
    window_end = discharge + timedelta(days=1)
    warnings: list[str] = []

    def from_this_stay(resource: dict, *date_keys: str) -> bool:
        enc = normalize_reference((resource.get("encounter") or {}).get("reference"))
        if not enc:
            context = resource.get("context") or {}
            encs = context.get("encounter") if isinstance(context, dict) else None
            if isinstance(encs, list) and encs:
                enc = normalize_reference(encs[0].get("reference"))
        if enc:
            return enc == enc_ref
        for key in date_keys:
            d = parse_date(resource.get(key)) if resource.get(key) else None
            if d:
                return window_start <= d <= window_end
        return False

    def discharge_summary() -> tuple[str | None, str | None]:
        docs = client.search("DocumentReference", {"patient": pid, "type": f"http://loinc.org|{DISCHARGE_SUMMARY_LOINC}"})
        docs = [d for d in docs if d.get("status") != "entered-in-error" and from_this_stay(d, "date")]
        docs.sort(key=lambda d: d.get("date") or "", reverse=True)
        for doc in docs:
            for content in doc.get("content") or []:
                text = _decode_attachment(client, content.get("attachment") or {})
                if text:
                    return text, version_ref(doc)
        return None, None

    # Independent searches, concurrently; results are processed in a fixed order below.
    summary_f = pool.submit(discharge_summary)
    reports_f = pool.submit(
        search_narrowed, client, "DiagnosticReport", {"patient": pid, "_count": 100},
        {"status": ",".join(sorted(PENDING_REPORT_STATUSES))},
    )
    orders_f = pool.submit(
        search_narrowed, client, "ServiceRequest", {"patient": pid, "_count": 100},
        {"status": ",".join(sorted(OPEN_ORDER_STATUSES))},
    )
    appts_f = pool.submit(client.search, "Appointment", {"patient": pid, "date": f"ge{discharge.isoformat()}", "_count": 50})
    meds_f = pool.submit(client.search, "MedicationRequest", {"patient": pid, "status": "active", "_count": 100})
    home_f = pool.submit(_optional_search, client, "MedicationStatement", {"patient": pid, "status": "active", "_count": 100})

    # Discharge summary narrative.
    summary_text, summary_ref = summary_f.result()
    if not summary_text:
        warnings.append("No discharge summary (LOINC 18842-5) found for this stay.")

    # Results still pending at discharge.
    pending: list[PacketPendingResult] = []
    for report in reports_f.result():
        if report.get("status") not in PENDING_REPORT_STATUSES:
            continue
        based_on = [normalize_reference(b.get("reference")) for b in report.get("basedOn") or []]
        if not from_this_stay(report, "effectiveDateTime", "issued"):
            continue
        pending.append(
            PacketPendingResult(
                ref=ref(report),
                version_ref=version_ref(report),
                display=codeable_text(report.get("code")) or "Test result",
                status=report["status"],
                kind=_report_kind(report),
                based_on=[b for b in based_on if b],
            )
        )
    consumed_orders = {b for p in pending for b in p.based_on}

    # Orders still to be carried out after discharge.
    orders: list[PacketOrder] = []
    for sr in orders_f.result():
        if sr.get("status") not in OPEN_ORDER_STATUSES or ref(sr) in consumed_orders:
            continue
        if not from_this_stay(sr, "authoredOn"):
            continue
        performer = codeable_text(sr.get("performerType")) or next(
            (p.get("display") for p in sr.get("performer") or [] if p.get("display")), None
        )
        orders.append(
            PacketOrder(
                ref=ref(sr),
                version_ref=version_ref(sr),
                kind=_order_kind(sr),
                display=codeable_text(sr.get("code")) or "Ordered follow-up",
                code=next(iter(codes(sr.get("code"))), None),
                status=sr["status"],
                authored_on=iso(sr.get("authoredOn")),
                due_date=_due(sr),
                performer=performer,
                note=" ".join(n.get("text", "") for n in sr.get("note") or []) or None,
            )
        )

    # Appointments already booked after discharge.
    appointments: list[PacketAppointment] = []
    for appt in appts_f.result():
        if appt.get("status") not in LIVE_APPOINTMENT_STATUSES:
            continue
        practitioner = next(
            (
                (p.get("actor") or {}).get("display")
                for p in appt.get("participant") or []
                if not (normalize_reference((p.get("actor") or {}).get("reference")) or "").startswith("Patient/")
                and (p.get("actor") or {}).get("display")
            ),
            None,
        )
        service = (
            " ".join(codeable_text(s) for s in appt.get("serviceType") or []).strip()
            or appt.get("description")
            or "Follow-up appointment"
        )
        appointments.append(
            PacketAppointment(
                ref=ref(appt),
                version_ref=version_ref(appt),
                status=appt["status"],
                start=iso(appt.get("start")),
                service=service,
                practitioner=practitioner,
                based_on=[b for b in (normalize_reference(x.get("reference")) for x in appt.get("basedOn") or []) if b],
            )
        )

    # New or changed medications from this stay.
    medications: list[PacketMedication] = []
    for med in meds_f.result():
        if med.get("status") != "active" or not from_this_stay(med, "authoredOn"):
            continue
        display = codeable_text(med.get("medicationCodeableConcept")) or (med.get("medicationReference") or {}).get("display") or "Medication"
        instructions = "; ".join(d.get("text", "") for d in med.get("dosageInstruction") or [] if d.get("text")) or None
        medications.append(PacketMedication(ref=ref(med), version_ref=version_ref(med), display=display, instructions=instructions))

    # The home list the patient or pharmacy reported, when the server has one.
    home_medications: list[PacketMedication] = []
    for med in home_f.result():
        display = codeable_text(med.get("medicationCodeableConcept")) or (med.get("medicationReference") or {}).get("display") or "Medication"
        home_medications.append(PacketMedication(ref=ref(med), version_ref=version_ref(med), display=display))

    return {
        "admit": admit,
        "discharge": discharge,
        "summary_text": summary_text,
        "summary_ref": summary_ref,
        "orders": orders,
        "appointments": appointments,
        "medications": medications,
        "home_medications": home_medications,
        "pending": pending,
        "warnings": warnings,
    }


def _assemble(
    client: FHIRClient, patient: dict, encounter: dict, parts: dict, *, source: str, source_ref: str | None
) -> DischargePacket:
    pid, enc_ref = patient["id"], ref(encounter)
    admit, discharge = parts["admit"], parts["discharge"]
    telecom = next((t.get("value") for t in patient.get("telecom") or [] if t.get("system") == "phone"), None)
    language = next(
        (codeable_text(c.get("language")) for c in patient.get("communication") or [] if c.get("language")), None
    )
    attending = next(
        (
            (p.get("individual") or {}).get("display")
            for p in encounter.get("participant") or []
            if (p.get("individual") or {}).get("display")
        ),
        None,
    )
    reason = " ".join(codeable_text(r) for r in encounter.get("reasonCode") or []).strip() or codeable_text(
        (encounter.get("type") or [None])[0]
    )
    return DischargePacket(
        source=source,
        source_ref=source_ref or version_ref(encounter),
        fhir_base=client.base_url,
        patient=PacketPatient(
            fhir_id=pid,
            identifiers=_identifiers(patient),
            name=human_name(patient),
            birth_date=patient.get("birthDate"),
            gender=patient.get("gender"),
            phone=telecom,
            language=language,
        ),
        encounter=PacketEncounter(
            ref=enc_ref,
            version_ref=version_ref(encounter),
            identifiers=_identifiers(encounter),
            admit_date=admit.isoformat() if admit else None,
            discharge_date=discharge.isoformat(),
            reason=reason or None,
            disposition=codeable_text((encounter.get("hospitalization") or {}).get("dischargeDisposition")) or None,
            attending=attending,
            facility=(encounter.get("serviceProvider") or {}).get("display"),
        ),
        discharge_summary_text=parts["summary_text"],
        discharge_summary_ref=parts["summary_ref"],
        orders=parts["orders"],
        appointments=parts["appointments"],
        medications=parts["medications"],
        pending_results=parts["pending"],
        warnings=parts["warnings"],
    )


__all__ = ["PullError", "FHIRError", "pull_discharge", "find_patient", "find_encounter", "version_ref", "normalize_reference"]
