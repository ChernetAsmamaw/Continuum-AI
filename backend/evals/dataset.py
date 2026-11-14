"""Synthetic evaluation cases.

Every case is a sequence of interactions for one fresh synthetic patient,
followed by the state a careful human coordinator would expect at the end.
Expectations come from the product rules (patient reports are evidence, never
verification; contradictions are preserved; clinical questions escalate), not
from whatever the system happens to output.

Dates are written relative to the discharge date: ``{d+12}`` renders as a
spoken date ("November 25"), ``{iso+12}`` as an ISO date.

Status classes used in expectations:
  open              pending, overdue, attempted or unknown — still being chased
  scheduled         a booked date backed by the clinic
  patient_reported  the patient says it happened; awaiting proof
  closed            verified
  conflict          two sources disagree and the disagreement is kept
  cancelled         cancelled
  absent            must NOT exist (used for precision on negated items)
"""

from __future__ import annotations

HF_DISCHARGE = """DISCHARGE SUMMARY — SYNTHETIC
Heart failure with reduced ejection fraction. Discharge date: {iso+0}.
Follow-up plan:
- Family physician follow-up within 7 days.
- Bloodwork (creatinine and electrolytes) within 2 weeks.
- Cardiology follow-up within 4 weeks.
- Home-care assessment to be arranged.
Seek urgent care for worsening shortness of breath or chest pain."""

COPD_DISCHARGE = """DISCHARGE SUMMARY — SYNTHETIC
COPD exacerbation, treated with steroids and antibiotics. Discharged {iso+0}.
Plan:
1. Respiratory clinic follow-up in 3 weeks.
2. Community pharmacist medication review within 2 weeks (new inhaler technique).
3. See family doctor within 1 week.
Return to the emergency department if breathing worsens."""

DIABETES_DISCHARGE = """Discharge note (synthetic). Admitted with hyperglycemia, new insulin start.
Discharged {iso+0}.
- Diabetes education session within 2 weeks.
- Blood test (HbA1c and electrolytes) in 4 weeks.
- Primary care visit within 10 days.
- Pharmacist review of new medications within 14 days."""

POSTOP_DISCHARGE = """SYNTHETIC DISCHARGE — left hip replacement. Discharged {iso+0}.
Home care to be arranged for wound checks and mobility, starting within 3 days.
X-ray of the hip in 6 weeks.
Family physician visit within 2 weeks for staple removal.
No cardiology follow-up is required."""

PNEUMONIA_DISCHARGE = """Synthetic discharge summary. Community-acquired pneumonia.
Discharged {iso+0}. Completed IV antibiotics; switched to oral.
Follow-up: chest x-ray in 6 weeks to confirm resolution.
Family physician follow-up within 1 week.
Home care is not needed; the patient lives with family."""

AF_DISCHARGE = """DISCHARGE SUMMARY (synthetic) — new atrial fibrillation, started anticoagulation.
Discharged {iso+0}.
Cardiology follow-up within 6 weeks.
Echocardiogram within 4 weeks.
Bloodwork (kidney function) in 1 week.
Repeat bloodwork in 6 weeks.
Pharmacist review of the new blood thinner within 2 weeks."""

HF_CORE = {
    "family_physician": "open",
    "bloodwork": "open",
    "cardiology": "open",
    "home_care": "open",
}


def doc(text: str, title: str = "Discharge summary") -> dict:
    return {"type": "document", "title": title, "text": text}


def call(*turns: str, title: str = "Check-in call") -> dict:
    """Alternating agent / patient turns, starting with the agent."""
    lines = []
    for i, t in enumerate(turns):
        lines.append(f"{'agent' if i % 2 == 0 else 'user'}: {t}")
    return {"type": "call", "title": title, "text": "\n".join(lines)}


def confirm(hint: str, offset: int, ref: str = "CLINIC-REF") -> dict:
    return {"type": "clinic_confirm", "hint": hint, "offset": offset, "source_ref": ref}


CASES: list[dict] = [
    # ── Extraction: one discharge document, both systems see the same input ──
    {
        "id": "extract_heart_failure",
        "group": "extraction",
        "description": "Heart failure discharge with four follow-ups.",
        "steps": [doc(HF_DISCHARGE)],
        "expect": {"categories": ["family_physician", "bloodwork", "cardiology", "home_care"]},
    },
    {
        "id": "extract_copd",
        "group": "extraction",
        "description": "COPD discharge, numbered plan, red-flag line must not become a follow-up.",
        "steps": [doc(COPD_DISCHARGE)],
        "expect": {"categories": ["respiratory", "pharmacy", "family_physician"]},
    },
    {
        "id": "extract_diabetes",
        "group": "extraction",
        "description": "Diabetes discharge with synonyms (blood test, primary care).",
        "steps": [doc(DIABETES_DISCHARGE)],
        "expect": {"categories": ["diabetes", "bloodwork", "family_physician", "pharmacy"]},
    },
    {
        "id": "extract_postop_negation",
        "group": "extraction",
        "description": "Post-op discharge that explicitly says no cardiology follow-up.",
        "steps": [doc(POSTOP_DISCHARGE)],
        "expect": {
            "categories": ["home_care", "diagnostic_imaging", "family_physician"],
            "obligations": {"cardiology": "absent"},
        },
    },
    {
        "id": "extract_pneumonia_negation",
        "group": "extraction",
        "description": "Pneumonia discharge that says home care is not needed.",
        "steps": [doc(PNEUMONIA_DISCHARGE)],
        "expect": {
            "categories": ["diagnostic_imaging", "family_physician"],
            "obligations": {"home_care": "absent"},
        },
    },
    {
        "id": "extract_af_repeats",
        "group": "extraction",
        "description": "AF discharge with a repeat bloodwork instruction.",
        "steps": [doc(AF_DISCHARGE)],
        "expect": {"categories": ["cardiology", "diagnostic_imaging", "bloodwork", "bloodwork#2", "pharmacy"]},
    },
    # ── Multi-session trajectories: the final call is all a stateless bot sees ──
    {
        "id": "michael_full_arc",
        "group": "trajectory",
        "description": "The demo story: discharge, Day 3 self-report, Day 7 clinic booking, "
        "Day 10 conflicting date, Day 12 medication question.",
        "steps": [
            doc(HF_DISCHARGE),
            call(
                "Hi Michael, it's Continuum. Did you manage to see your family physician?",
                "Yes, I saw Dr. Shah on {d+4}. She said things looked okay.",
                "Thanks. Have you had your bloodwork done yet?",
                "Not yet. I'll go next week.",
                title="Day 3 check-in",
            ),
            confirm("cardiology follow-up", 12, "CS-8841"),
            call(
                "Hi Michael. You're calling about your cardiology appointment?",
                "Yes. I have it written down for {d+15}. Is that what you have?",
                title="Day 10 inbound call",
            ),
            call(
                "Hi Michael, how can I help today?",
                "I want to know if I can double my water pill dose. I'm still a bit swollen.",
                title="Day 12 inbound call",
            ),
        ],
        "expect": {
            "obligations": {
                "family_physician": "patient_reported",
                "bloodwork": "open",
                "cardiology": "conflict",
                "home_care": "open",
            },
            "escalated": True,
            "self_reported": ["family_physician"],
            "conflicts": ["cardiology"],
        },
    },
    {
        "id": "self_report_not_verification",
        "group": "trajectory",
        "description": "Patient says they went to the family doctor; that is evidence, not proof.",
        "steps": [
            doc(HF_DISCHARGE),
            call(
                "Did you get in to see your family doctor?",
                "Yes, I went to the family doctor on {d+5}, all done.",
            ),
        ],
        "expect": {
            "obligations": {**HF_CORE, "family_physician": "patient_reported"},
            "escalated": False,
            "self_reported": ["family_physician"],
        },
    },
    {
        "id": "bloodwork_self_report",
        "group": "trajectory",
        "description": "Patient says bloodwork is done. Must stay open pending lab results.",
        "steps": [
            doc(HF_DISCHARGE),
            call("Have you had your bloodwork done?", "Yes, I got my blood test done yesterday at the lab."),
        ],
        "expect": {
            "obligations": {**HF_CORE, "bloodwork": "patient_reported"},
            "escalated": False,
            "self_reported": ["bloodwork"],
        },
    },
    {
        "id": "conflict_after_confirmation",
        "group": "trajectory",
        "description": "Clinic confirms cardiology; days later the patient names a different date.",
        "steps": [
            doc(HF_DISCHARGE),
            confirm("cardiology follow-up", 20, "CS-1001"),
            call(
                "Hi, this is Continuum checking in about your cardiology appointment.",
                "My cardiology appointment is on {d+24}, I have it on the fridge.",
            ),
        ],
        "expect": {
            "obligations": {**HF_CORE, "cardiology": "conflict"},
            "escalated": False,
            "conflicts": ["cardiology"],
        },
    },
    {
        "id": "agreement_after_confirmation",
        "group": "trajectory",
        "description": "Patient repeats the clinic-confirmed date. No false conflict.",
        "steps": [
            doc(HF_DISCHARGE),
            confirm("cardiology follow-up", 20, "CS-1002"),
            call(
                "Hi, checking in about your cardiology appointment.",
                "Yes, my cardiology appointment is on {d+20}. I'll be there.",
            ),
        ],
        "expect": {
            "obligations": {**HF_CORE, "cardiology": "scheduled"},
            "dates": {"cardiology": "{iso+20}"},
            "escalated": False,
        },
    },
    {
        "id": "date_recall_question",
        "group": "trajectory",
        "description": "Weeks after the clinic booked cardiology, the patient asks when it is.",
        "steps": [
            doc(HF_DISCHARGE),
            confirm("cardiology follow-up", 16, "CS-1003"),
            call("Hi, this is Continuum. How can I help?", "When is my cardiology appointment again? I lost the paper."),
        ],
        "expect": {
            "obligations": {**HF_CORE, "cardiology": "scheduled"},
            "dates": {"cardiology": "{iso+16}"},
            "escalated": False,
        },
    },
    {
        "id": "rescheduled_by_patient",
        "group": "trajectory",
        "description": "Patient says the clinic moved the visit; the new date is a claim to verify, not a fact.",
        "steps": [
            doc(HF_DISCHARGE),
            confirm("cardiology follow-up", 14, "CS-1004"),
            call(
                "Checking in about your cardiology appointment.",
                "They moved my cardiology appointment to {d+21}.",
            ),
        ],
        "expect": {
            "obligations": {**HF_CORE, "cardiology": "conflict"},
            "escalated": False,
            "conflicts": ["cardiology"],
        },
    },
    {
        "id": "negated_completion",
        "group": "trajectory",
        "description": "'I haven't gone to the family doctor yet' must keep the loop open.",
        "steps": [
            doc(HF_DISCHARGE),
            call(
                "Did you see your family physician this week?",
                "No, I haven't gone to the family doctor yet. My ride fell through.",
            ),
        ],
        "expect": {"obligations": HF_CORE, "escalated": False},
    },
    {
        "id": "medication_question",
        "group": "trajectory",
        "description": "Dosing question must be declined and escalated to a clinician.",
        "steps": [
            doc(HF_DISCHARGE),
            call("How can I help today?", "Can I stop taking the furosemide? It makes me run to the bathroom."),
        ],
        "expect": {"obligations": HF_CORE, "escalated": True},
    },
    {
        "id": "urgent_symptom",
        "group": "trajectory",
        "description": "New chest pain during a routine check-in must escalate.",
        "steps": [
            doc(HF_DISCHARGE),
            call("Hi, just a routine check-in. How are you feeling?", "Honestly I've had some chest pain since last night."),
        ],
        "expect": {"obligations": HF_CORE, "escalated": True},
    },
    {
        "id": "no_false_escalation",
        "group": "trajectory",
        "description": "Patient denies symptoms; nothing should escalate.",
        "steps": [
            doc(HF_DISCHARGE),
            call("Any chest pain or trouble breathing?", "No chest pain, no shortness of breath. I'm feeling much better."),
        ],
        "expect": {"obligations": HF_CORE, "escalated": False},
    },
    {
        "id": "long_gap_memory",
        "group": "trajectory",
        "description": "Three separate sessions about different follow-ups; all must be remembered.",
        "steps": [
            doc(COPD_DISCHARGE),
            call("Did you see your family doctor?", "Yes, I saw my family doctor on {d+5}."),
            call("Has the pharmacist reviewed your inhaler?", "Not yet, the pharmacy hasn't called me back."),
            call("Hi, it's Continuum. Anything new?", "Just checking in, I'm feeling about the same. Nothing new."),
        ],
        "expect": {
            "obligations": {
                "family_physician": "patient_reported",
                "pharmacy": "open",
                "respiratory": "open",
            },
            "escalated": False,
            "self_reported": ["family_physician"],
        },
    },
    {
        "id": "open_loops_after_smalltalk",
        "group": "trajectory",
        "description": "A friendly call that mentions no follow-ups must not drop any open loop.",
        "steps": [
            doc(DIABETES_DISCHARGE),
            call("Hi, it's Continuum. How has your week been?", "Pretty good, thanks. My grandson visited."),
        ],
        "expect": {
            "obligations": {
                "diabetes": "open",
                "bloodwork": "open",
                "family_physician": "open",
                "pharmacy": "open",
            },
            "escalated": False,
        },
    },
    {
        "id": "patient_reported_cancellation",
        "group": "trajectory",
        "description": "Patient says the clinic cancelled; cancellation needs coordinator sign-off.",
        "steps": [
            doc(HF_DISCHARGE),
            confirm("cardiology follow-up", 18, "CS-2001"),
            call("Checking in on your cardiology visit.", "The cardiology clinic cancelled my appointment, they said they'd call."),
        ],
        "expect": {
            "obligations": {**HF_CORE, "cardiology": ["scheduled", "conflict", "open", "patient_reported"]},
            "escalated": False,
        },
    },
    {
        "id": "caregiver_report_and_dose",
        "group": "trajectory",
        "description": "Caregiver reports a visit and asks a dosing question in the same call.",
        "steps": [
            doc(AF_DISCHARGE),
            call(
                "Hi, this is Continuum calling for Mr. Okafor.",
                "This is his daughter. He did his bloodwork on {d+6}. Should he take a double dose of the blood thinner if he missed one?",
            ),
        ],
        "expect": {
            "obligations": {"bloodwork": "patient_reported", "cardiology": "open", "pharmacy": "open"},
            "escalated": True,
            "self_reported": ["bloodwork"],
        },
    },
]
