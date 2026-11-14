"""Synthetic discharge scenarios for the EHR sandbox.

Synthea produces realistic histories (encounters, labs, meds, notes) but no
discharge orders: no ServiceRequests, no future Appointments, no pending
results. A real Epic/Oracle record has those, so this module layers a fresh
heart-failure admission on top of a Synthea patient, shaped like US Core R4:

* Encounter (IMP) ending on the discharge date
* DocumentReference, LOINC 18842-5, with a narrative discharge summary
* ServiceRequests: specialty referral, outpatient lab, primary-care follow-up
* Appointment booked against one referral (variant A)
* A test collected in hospital whose DiagnosticReport is still preliminary
* New MedicationRequest(s)

The narrative also mentions one follow-up that has **no** structured order
(home care / repeat imaging), which is what AI extraction is there to catch.
All ids are deterministic per patient so reloading is idempotent (PUT).
"""

from __future__ import annotations

import base64
from datetime import date, timedelta

SCT = "http://snomed.info/sct"
LOINC = "http://loinc.org"
RXNORM = "http://www.nlm.nih.gov/research/umls/rxnorm"
VISIT_SYSTEM = "urn:continuum:sandbox:visit"
FACILITY = "Continuum General Hospital"
HOSPITALIST = "Dr. Priya Raman"


def _cc(system: str, code: str, display: str, text: str | None = None) -> dict:
    return {"coding": [{"system": system, "code": code, "display": display}], "text": text or display}


def _dt(d: date, hour: int = 9) -> str:
    return f"{d.isoformat()}T{hour:02d}:00:00Z"


def short_id(patient_id: str) -> str:
    return patient_id.replace("-", "")[:12]


def visit_number(patient_id: str) -> str:
    return f"V{short_id(patient_id).upper()}"


def patient_display(patient: dict) -> str:
    name = (patient.get("name") or [{}])[0]
    return " ".join([*(name.get("given") or []), name.get("family", "")]).strip()


def index_admission(patient: dict, *, discharge: date, variant: int = 0, length_of_stay: int = 4) -> list[dict]:
    pid = patient["id"]
    s = short_id(pid)
    subject = {"reference": f"Patient/{pid}", "display": patient_display(patient)}
    admit = discharge - timedelta(days=length_of_stay)
    enc_ref = {"reference": f"Encounter/{s}-adm"}
    variant = variant % 2

    encounter = {
        "resourceType": "Encounter",
        "id": f"{s}-adm",
        "identifier": [{"use": "official", "system": VISIT_SYSTEM, "value": visit_number(pid)}],
        "status": "finished",
        "class": {"system": "http://terminology.hl7.org/CodeSystem/v3-ActCode", "code": "IMP", "display": "inpatient encounter"},
        "type": [_cc(SCT, "32485007", "Hospital admission (procedure)")],
        "subject": subject,
        "participant": [{"individual": {"display": f"{HOSPITALIST} (Hospitalist)"}}],
        "period": {"start": _dt(admit, 14), "end": _dt(discharge, 11)},
        "reasonCode": [_cc(SCT, "42343007", "Congestive heart failure (disorder)", "Acute decompensated heart failure")],
        "hospitalization": {
            "dischargeDisposition": _cc(
                "http://terminology.hl7.org/CodeSystem/discharge-disposition", "home", "Home"
            )
        },
        "serviceProvider": {"display": FACILITY},
    }

    def service_request(suffix, category, code, *, occurrence=None, period_days=None, performer=None, authored=discharge, note=None):
        sr = {
            "resourceType": "ServiceRequest",
            "id": f"{s}-sr-{suffix}",
            "status": "active",
            "intent": "order",
            "category": [category],
            "code": code,
            "subject": subject,
            "encounter": enc_ref,
            "authoredOn": _dt(authored, 10),
            "requester": {"display": HOSPITALIST},
        }
        if occurrence:
            sr["occurrenceDateTime"] = _dt(occurrence)
        if period_days:
            sr["occurrencePeriod"] = {"start": _dt(discharge), "end": _dt(discharge + timedelta(days=period_days))}
        if performer:
            sr["performerType"] = {"text": performer}
        if note:
            sr["note"] = [{"text": note}]
        return sr

    referral = _cc(SCT, "3457005", "Patient referral")
    lab = _cc(SCT, "108252007", "Laboratory procedure")
    imaging = _cc(SCT, "363679005", "Imaging")

    cardio = service_request(
        "cardio", referral, _cc(SCT, "183519002", "Referral to cardiology service"),
        period_days=14, performer="Cardiology", note="Post-discharge heart failure review; titrate GDMT.",
    )
    bmp = service_request(
        "bmp", lab, _cc(LOINC, "51990-0", "Basic metabolic panel - Blood", "Basic metabolic panel (creatinine, electrolytes)"),
        occurrence=discharge + timedelta(days=7), note="Recheck creatinine and potassium on new diuretic.",
    )
    pcp = service_request(
        "pcp", referral, _cc(SCT, "185389009", "Follow-up visit (procedure)", "Primary care follow-up visit"),
        period_days=7, performer="Primary care",
    )
    resources: list[dict] = [encounter, cardio, bmp, pcp]

    if variant == 0:
        pending_code = _cc(LOINC, "600-7", "Bacteria identified in Blood by Culture", "Blood culture")
        pending_order = service_request("culture", lab, pending_code, authored=admit + timedelta(days=1))
        pending_report = {
            "resourceType": "DiagnosticReport",
            "id": f"{s}-dr-culture",
            "status": "preliminary",
            "category": [_cc("http://terminology.hl7.org/CodeSystem/v2-0074", "LAB", "Laboratory")],
            "code": pending_code,
            "subject": subject,
            "encounter": enc_ref,
            "basedOn": [{"reference": f"ServiceRequest/{s}-sr-culture"}],
            "effectiveDateTime": _dt(admit + timedelta(days=1), 6),
            "issued": _dt(admit + timedelta(days=3), 6),
            "conclusion": "No growth at 48 hours. Final report at 5 days.",
        }
        appointment = {
            "resourceType": "Appointment",
            "id": f"{s}-appt-pcp",
            "status": "booked",
            "serviceType": [{"text": "Primary care follow-up"}],
            "basedOn": [{"reference": f"ServiceRequest/{s}-sr-pcp"}],
            "start": _dt(discharge + timedelta(days=5), 10),
            "end": f"{(discharge + timedelta(days=5)).isoformat()}T10:30:00Z",
            "participant": [
                {"actor": {"reference": f"Patient/{pid}", "display": subject["display"]}, "status": "accepted"},
                {"actor": {"display": "Dr. Lena Ortiz (Primary care)"}, "status": "accepted"},
            ],
        }
        meds = [("313988", "Furosemide 40 MG Oral Tablet", "40 mg by mouth once daily")]
        narrative_extra = "- Home care nursing assessment within 7 days for daily weights and medication support.\n"
        pending_line = "- Blood cultures drawn on admission are still pending; the discharging team will review the final result.\n"
        resources += [pending_order, pending_report, appointment]
    else:
        pending_code = _cc(LOINC, "34552-0", "2D echocardiogram panel", "Transthoracic echocardiogram")
        pending_order = service_request("echo", imaging, pending_code, authored=admit + timedelta(days=2))
        pending_report = {
            "resourceType": "DiagnosticReport",
            "id": f"{s}-dr-echo",
            "status": "registered",
            "category": [_cc("http://terminology.hl7.org/CodeSystem/v2-0074", "CUS", "Cardiac Ultrasound")],
            "code": pending_code,
            "subject": subject,
            "encounter": enc_ref,
            "basedOn": [{"reference": f"ServiceRequest/{s}-sr-echo"}],
            "effectiveDateTime": _dt(discharge - timedelta(days=1), 15),
        }
        meds = [
            ("313988", "Furosemide 40 MG Oral Tablet", "40 mg by mouth twice daily"),
            ("313096", "Spironolactone 25 MG Oral Tablet", "25 mg by mouth once daily"),
        ]
        narrative_extra = "- Home care nursing assessment within 7 days; patient lives alone.\n"
        pending_line = "- Echocardiogram performed the day before discharge; formal report pending, review when available.\n"
        resources += [pending_order, pending_report]

    for i, (code, display, sig) in enumerate(meds):
        resources.append(
            {
                "resourceType": "MedicationRequest",
                "id": f"{s}-mr-{i + 1}",
                "status": "active",
                "intent": "order",
                "medicationCodeableConcept": _cc(RXNORM, code, display),
                "subject": subject,
                "encounter": enc_ref,
                "authoredOn": _dt(discharge, 10),
                "requester": {"display": HOSPITALIST},
                "dosageInstruction": [{"text": sig}],
            }
        )

    med_lines = "; ".join(f"{d} ({sig})" for _, d, sig in meds)
    narrative = (
        f"DISCHARGE SUMMARY\n"
        f"Patient: {subject['display']}\n"
        f"Admitted: {admit.isoformat()}    Discharged: {discharge.isoformat()}    Attending: {HOSPITALIST}\n\n"
        f"Diagnosis: Acute decompensated heart failure.\n"
        f"Hospital course: Admitted with dyspnea and leg edema; diuresed with IV furosemide with good response. "
        f"Discharged home in stable condition.\n\n"
        f"Discharge medications (new or changed): {med_lines}.\n\n"
        f"Follow-up plan:\n"
        f"- Follow up with the primary care physician within 7 days.\n"
        f"- Cardiology follow-up within 2 weeks.\n"
        f"- Bloodwork (creatinine and electrolytes) in 1 week.\n"
        f"{narrative_extra}"
        f"{pending_line}"
        f"- Daily weights; call the clinic for weight gain over 2 kg in 3 days.\n"
    )
    resources.append(
        {
            "resourceType": "DocumentReference",
            "id": f"{s}-dc-summary",
            "status": "current",
            "type": _cc(LOINC, "18842-5", "Discharge summary"),
            "category": [
                _cc("http://hl7.org/fhir/us/core/CodeSystem/us-core-documentreference-category", "clinical-note", "Clinical Note")
            ],
            "subject": subject,
            "date": _dt(discharge, 12),
            "author": [{"display": HOSPITALIST}],
            "content": [
                {
                    "attachment": {
                        "contentType": "text/plain",
                        "data": base64.b64encode(narrative.encode()).decode(),
                    }
                }
            ],
            "context": {"encounter": [enc_ref], "period": encounter["period"]},
        }
    )
    return resources


def result_events(patient: dict, *, discharge: date, variant: int = 0) -> list[dict]:
    """What the EHR records over the following days — used to demo loop closure."""
    pid = patient["id"]
    s = short_id(pid)
    subject = {"reference": f"Patient/{pid}"}
    lab_cat = [_cc("http://terminology.hl7.org/CodeSystem/v2-0074", "LAB", "Laboratory")]
    events = [
        {
            "resourceType": "DiagnosticReport",
            "id": f"{s}-dr-bmp",
            "status": "final",
            "category": lab_cat,
            "code": _cc(LOINC, "51990-0", "Basic metabolic panel - Blood"),
            "subject": subject,
            "basedOn": [{"reference": f"ServiceRequest/{s}-sr-bmp"}],
            "effectiveDateTime": _dt(discharge + timedelta(days=7), 8),
            "issued": _dt(discharge + timedelta(days=7), 14),
            "conclusion": "Creatinine 1.2 mg/dL, potassium 4.1 mmol/L.",
        }
    ]
    if variant % 2 == 0:
        events.append(
            {
                "resourceType": "DiagnosticReport",
                "id": f"{s}-dr-culture",
                "status": "final",
                "category": lab_cat,
                "code": _cc(LOINC, "600-7", "Bacteria identified in Blood by Culture", "Blood culture"),
                "subject": subject,
                "basedOn": [{"reference": f"ServiceRequest/{s}-sr-culture"}],
                "effectiveDateTime": _dt(discharge - timedelta(days=3), 6),
                "issued": _dt(discharge + timedelta(days=1), 6),
                "conclusion": "No growth at 5 days.",
            }
        )
        events.append(
            {
                "resourceType": "Appointment",
                "id": f"{s}-appt-pcp",
                "status": "fulfilled",
                "serviceType": [{"text": "Primary care follow-up"}],
                "basedOn": [{"reference": f"ServiceRequest/{s}-sr-pcp"}],
                "start": _dt(discharge + timedelta(days=5), 10),
                "end": f"{(discharge + timedelta(days=5)).isoformat()}T10:30:00Z",
                "participant": [
                    {"actor": {"reference": f"Patient/{pid}"}, "status": "accepted"},
                    {"actor": {"display": "Dr. Lena Ortiz (Primary care)"}, "status": "accepted"},
                ],
            }
        )
    else:
        events.append(
            {
                "resourceType": "DiagnosticReport",
                "id": f"{s}-dr-echo",
                "status": "final",
                "category": [_cc("http://terminology.hl7.org/CodeSystem/v2-0074", "CUS", "Cardiac Ultrasound")],
                "code": _cc(LOINC, "34552-0", "2D echocardiogram panel", "Transthoracic echocardiogram"),
                "subject": subject,
                "basedOn": [{"reference": f"ServiceRequest/{s}-sr-echo"}],
                "effectiveDateTime": _dt(discharge - timedelta(days=1), 15),
                "issued": _dt(discharge + timedelta(days=2), 9),
                "conclusion": "LVEF 35%. Moderate LV systolic dysfunction.",
            }
        )
    return events


def expected_followups(variant: int = 0) -> dict:
    """Ground truth for the ingestion eval: category → expected origin."""
    return {
        "cardiology": "ehr_structured",
        "bloodwork": "ehr_structured",
        "family_physician": "ehr_structured",
        ("pending_result" if variant % 2 == 0 else "diagnostic_imaging"): "ehr_structured",
        "medication_reconciliation": "ehr_structured",
        "home_care": "ai_extracted",
    }
