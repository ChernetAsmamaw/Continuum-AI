"""Care-plan extraction via LangChain structured output, with deterministic
heuristics as an always-available fallback."""

from __future__ import annotations

import logging
import re
from datetime import date
from typing import Literal

from pydantic import BaseModel, Field

from ..db import demo_today
from ..util import date_from_offset, extract_date
from .llm import get_chat_model

log = logging.getLogger("continuum.extraction")


class FollowUpExtract(BaseModel):
    description: str = Field(description="Concrete follow-up activity, e.g. 'Family physician visit'")
    category: str = Field(description="short category key, e.g. family_physician, bloodwork, cardiology, home_care, pharmacy")
    owner_hint: str | None = Field(default=None, description="Proposed responsible party")
    due_in_days: int | None = Field(default=None, description="Days from discharge/anchor if stated")
    due_date: str | None = Field(default=None, description="Explicit ISO date if stated")
    confidence: float = Field(default=0.8, ge=0.0, le=1.0)
    quote: str | None = Field(default=None, description="Verbatim source text")
    occurrence: int = Field(
        default=1,
        ge=1,
        description="1 for the first follow-up of this kind; 2, 3… for repeats of the same kind (e.g. repeat bloodwork)",
    )


class PatientReportExtract(BaseModel):
    obligation_hint: str = Field(description="Which follow-up this report refers to")
    category: str | None = None
    reported_status: Literal[
        "completed", "attended", "scheduled", "rescheduled", "cancelled", "not_completed", "unknown"
    ] = Field(
        default="unknown",
        description="not_completed when the patient says it has NOT happened yet (e.g. 'not yet', 'I haven't gone')",
    )
    reported_date: str | None = Field(default=None, description="Date mentioned by the patient, ISO if possible")
    details: str = ""


class RequestedAction(BaseModel):
    action: Literal[
        "verify_completion", "cancel", "resolve_conflict", "escalate", "schedule_checkin", "none"
    ] = "none"
    obligation_hint: str = ""
    rationale: str = ""


class SafetyAssessment(BaseModel):
    clinical_question: bool = False
    medication_question: bool = False
    urgent_symptoms: bool = False
    emergency: bool = False
    summary: str = ""


class ExtractionResult(BaseModel):
    summary: str = ""
    facts: list[str] = []
    follow_ups: list[FollowUpExtract] = []
    patient_reports: list[PatientReportExtract] = []
    requested_actions: list[RequestedAction] = []
    safety: SafetyAssessment = SafetyAssessment()
    uncertainty: list[str] = []


# ── Heuristic catalogue ──────────────────────────────────────────────────

CATEGORY_RULES: list[dict] = [
    {
        "category": "family_physician",
        "description": "Family physician follow-up",
        "owner": "Family physician",
        "keywords": ["family physician", "family doctor", "primary care", "family practice", "gp"],
        "default_days": 7,
    },
    {
        "category": "bloodwork",
        "description": "Bloodwork",
        "owner": "Laboratory",
        "keywords": ["bloodwork", "blood work", "labs", "laboratory", "blood test", "creatinine", "electrolytes"],
        "default_days": 14,
    },
    {
        "category": "cardiology",
        "description": "Cardiology follow-up",
        "owner": "Cardiology clinic",
        "keywords": ["cardiology", "cardiologist", "heart clinic", "cardiac"],
        "default_days": 28,
    },
    {
        "category": "home_care",
        "description": "Home-care assessment",
        "owner": "Community care",
        "keywords": ["home care", "home-care", "homecare", "community care", "ccac", "personal support"],
        "default_days": 7,
    },
    {
        "category": "pharmacy",
        "description": "Pharmacist medication review",
        "owner": "Community pharmacist",
        "keywords": ["pharmacist", "pharmacy", "medication review"],
        "default_days": 14,
    },
    {
        "category": "respiratory",
        "description": "Respiratory clinic follow-up",
        "owner": "Respiratory clinic",
        "keywords": ["respiratory", "respirology", "respiratory clinic", "pulmonology"],
        "default_days": 21,
    },
    {
        "category": "diabetes",
        "description": "Diabetes education session",
        "owner": "Diabetes education program",
        "keywords": ["diabetes education", "diabetes program", "diabetic educator"],
        "default_days": 14,
    },
    {
        "category": "diagnostic_imaging",
        "description": "Diagnostic imaging",
        "owner": "Medical imaging",
        "keywords": ["x-ray", "ultrasound", "echocardiogram", "imaging", "ct scan", "mri"],
        "default_days": 21,
    },
]

MEDICATION_TERMS = [
    "dose", "dosage", "medication", "medications", "medicine", "pill", "pills", "prescription",
    "tablet", "tablets", "water pill", "diuretic", "furosemide", "lasix",
]
MEDICATION_INTENT = [
    "should i take", "can i take", "can i stop", "should i stop", "change", "changing", "increase",
    "decrease", "double", "skip", "stop taking", "start taking", "more of", "less of", "cut",
]
URGENT_TERMS = [
    "chest pain", "can't breathe", "cannot breathe", "shortness of breath", "short of breath",
    "fainting", "fainted", "passed out", "collapsed", "chest tightness", "severe pain",
    "coughing blood", "confused", "swollen legs",
]
EMERGENCY_TERMS = ["911", "emergency", "unconscious", "not breathing", "can't breathe", "cannot breathe"]
CLINICAL_TERMS = ["symptom", "symptoms", "pain", "dizzy", "swelling", "weight gain", "fever", "nausea"]
COMPLETION_TERMS = [
    "saw", "seen", "visited", "went to", "went in", "attended", "completed", "had the", "had my",
    "got the", "got my", "did the", "did my", "finished", "done", "appointment was", "was there",
]
SCHEDULE_TERMS = ["booked", "scheduled", "appointment is", "set for", "booked for", "have it written down", "it's on", "is on"]
RESCHEDULE_TERMS = ["rescheduled", "moved", "changed to", "new date", "instead", "pushed"]
CANCEL_TERMS = ["cancelled", "canceled", "cancel"]
NEGATION_PATTERNS = [
    r"\bnot yet\b", r"\bhaven'?t\b", r"\bhave not\b", r"\bhasn'?t\b", r"\bdidn'?t\b",
    r"\bdid not\b", r"\bnever\b", r"\bwasn'?t able\b", r"\bcouldn'?t\b", r"\bnot been\b",
    r"\bmissed\b", r"\bno,", r"^no\b", r"\bnope\b", r"\bnot gone\b",
]
FUTURE_PATTERNS = [r"\bi'?ll go\b", r"\bi will go\b", r"\bgoing to go\b", r"\bplan to\b", r"\bnext week\b", r"\blater this\b"]
REPEAT_PATTERNS = [r"\brepeat\b", r"\bagain\b", r"\bsecond\b", r"\bfollow-?up (?:labs|bloodwork)\b", r"\bthen\b"]
NOT_ORDERED = (
    r"^\s*no\b|\bnot (?:be )?(?:required|needed|necessary|indicated)\b|\bno need\b|\bunnecessary\b"
    r"|\bnot (?:being )?(?:arranged|referred|booked)\b|\bdeclined\b"
)
SYMPTOM_NEGATORS = r"(?:no|not|denies|denied|without|never|any)"

AGENT_PREFIXES = ("agent:", "assistant:", "ai:", "continuum:")
PATIENT_PREFIXES = ("user:", "patient:", "caller:", "caregiver:")


def has_term(term: str, text: str) -> bool:
    """Whole-word / whole-phrase match, so 'gp' never matches 'gpu' and 'mg' never matches 'omg'."""
    return re.search(rf"(?<![a-z0-9]){re.escape(term)}(?![a-z0-9])", text) is not None


def any_term(terms: list[str], text: str) -> bool:
    return any(has_term(t, text) for t in terms)


def _affirmed_term(term: str, text: str) -> bool:
    """True when the term appears and is not negated just before it ('no chest pain')."""
    for m in re.finditer(rf"(?<![a-z0-9]){re.escape(term)}(?![a-z0-9])", text):
        window = text[max(0, m.start() - 30): m.start()]
        if not re.search(rf"\b{SYMPTOM_NEGATORS}\b(?:\s+\w+){{0,2}}\s*$", window):
            return True
    return False


def is_negated(text: str) -> bool:
    return any(re.search(p, text) for p in NEGATION_PATTERNS)


def _rule_for(text: str) -> dict | None:
    lowered = text.lower()
    for rule in CATEGORY_RULES:
        if any_term(rule["keywords"], lowered):
            return rule
    return None


def _split_lines(text: str) -> list[str]:
    parts = re.split(r"(?<=[.;!?])\s+|\n+", text)
    return [p.strip(" -•\t") for p in parts if p and len(p.strip()) > 4]


def split_turns(text: str) -> list[tuple[str, str]]:
    """Split a transcript into (speaker, utterance). Untagged text is the patient's."""
    turns: list[tuple[str, str]] = []
    tagged = False
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        low = line.lower()
        if low.startswith(AGENT_PREFIXES):
            turns.append(("agent", line.split(":", 1)[1].strip()))
            tagged = True
        elif low.startswith(PATIENT_PREFIXES):
            turns.append(("patient", line.split(":", 1)[1].strip()))
            tagged = True
        elif turns:
            speaker, prev = turns[-1]
            turns[-1] = (speaker, f"{prev} {line}")
        else:
            turns.append(("patient", line))
    if not tagged:
        return [("patient", text.strip())]
    return turns


def classify_report(utterance: str) -> str:
    """Map one patient utterance to a reported status, handling negation first."""
    low = utterance.lower()
    if any_term(CANCEL_TERMS, low):
        return "cancelled"
    if is_negated(low) or any(re.search(p, low) for p in FUTURE_PATTERNS) and not any_term(SCHEDULE_TERMS, low):
        return "not_completed"
    if any_term(RESCHEDULE_TERMS, low):
        return "rescheduled"
    if any_term(SCHEDULE_TERMS, low):
        return "scheduled"
    if any_term(COMPLETION_TERMS, low):
        return "completed"
    return "unknown"


def _patient_reports(text: str, anchor: str) -> list[PatientReportExtract]:
    """One report per care category the patient spoke about.

    Each patient turn is read on its own. When the patient answers without
    naming the follow-up ("Not yet."), the category comes from the question the
    agent just asked. Later statements about the same category win.
    """
    reports: dict[str, PatientReportExtract] = {}
    last_agent_rule: dict | None = None
    for speaker, utterance in split_turns(text):
        if speaker == "agent":
            last_agent_rule = _rule_for(utterance) or last_agent_rule
            continue
        # Questions inside a report ("Should he double the dose if he missed
        # one?") are not statements about the follow-up; read the statements.
        sentences = [x for x in re.split(r"(?<=[.!?])\s+", utterance.strip()) if x]
        statements = " ".join(x for x in sentences if not x.endswith("?"))
        if statements and len(statements) < len(utterance.strip()):
            utterance = statements
        rule = _rule_for(utterance) or last_agent_rule
        if not rule:
            continue
        low = utterance.lower()
        status = classify_report(utterance)
        mentions_date = extract_date(utterance, anchor) is not None
        if status == "unknown" and not mentions_date and "appointment" not in low:
            continue
        if status == "unknown" and mentions_date:
            status = "scheduled"
        reported_date = extract_date(utterance, anchor)
        reports[rule["category"]] = PatientReportExtract(
            obligation_hint=rule["description"],
            category=rule["category"],
            reported_status=status,  # type: ignore[arg-type]
            reported_date=reported_date,
            details=utterance.strip()[:1000],
        )
        last_agent_rule = None  # answered
    return list(reports.values())


def _document_follow_ups(lines: list[str], anchor: str) -> list[FollowUpExtract]:
    """One obligation per instruction; repeats of the same kind become separate
    obligations when they carry a different timing ('repeat bloodwork in 6 weeks')."""
    follow_ups: list[FollowUpExtract] = []
    seen: dict[str, list[int]] = {}
    instruction = re.compile(
        r"\b(follow-?up|arrange|arranged|book|schedule|refer|referral|assessment|see|visit|repeat|check|within|review|session)\b"
    )
    timing = re.compile(r"(?:within|in|after)\s+\d{1,2}\s*(?:day|week|month)|\b(?:by|on)\s+[a-z]{3}")
    # Lines with explicit timing first, so a heading such as "Discharging
    # service: Cardiology" never stands in for the real instruction.
    timed = [l for l in lines if timing.search(l.lower())]
    untimed = [l for l in lines if l not in timed and instruction.search(l.lower())]
    for line in timed + untimed:
        rule = _rule_for(line)
        if not rule:
            continue
        low = line.lower()
        # Return/red-flag instructions mention clinics but are not follow-ups.
        if re.search(r"\b(seek|return to|go to the) (urgent|emergency)", low):
            continue
        if re.search(NOT_ORDERED, low):
            # "No cardiology follow-up is required" / "Home care is not needed".
            continue
        offsets: list[int] = []
        for m in re.finditer(r"(?:within|in|after)\s+(\d{1,2})\s*(day|days|week|weeks|month|months)", low):
            n = int(m.group(1))
            n *= 7 if m.group(2).startswith("week") else 30 if m.group(2).startswith("month") else 1
            offsets.append(n)
        due = date_from_offset(line, anchor)
        if not offsets and due:
            parsed = extract_date(due, anchor)
            if parsed:
                offsets.append(max((date.fromisoformat(parsed) - date.fromisoformat(anchor)).days, 0))
        prior = seen.get(rule["category"], [])
        is_repeat_phrase = any(re.search(p, low) for p in REPEAT_PATTERNS)
        if not offsets:
            if prior:
                continue
            offsets = [rule["default_days"]]
        for days in offsets:
            if days in prior:
                continue
            if prior and not (is_repeat_phrase or len(offsets) > 1):
                # A second mention without repeat language is the same follow-up.
                continue
            occurrence = len(prior) + 1
            if not prior and len(offsets) == 1 and re.search(r"\b(repeat|again|recheck)\b", low):
                # "Repeat bloodwork in 6 weeks" on its own refers to a second
                # instance of something already ordered.
                occurrence = 2
            follow_ups.append(
                FollowUpExtract(
                    description=rule["description"] + (" (repeat)" if occurrence > 1 else ""),
                    category=rule["category"],
                    owner_hint=rule["owner"],
                    due_in_days=days,
                    confidence=0.86 if due or len(offsets) > 0 and days != rule["default_days"] else 0.7,
                    quote=line[:400],
                    occurrence=occurrence,
                )
            )
            prior = prior + [days]
        seen[rule["category"]] = prior
    return follow_ups


def heuristic_extract(
    text: str,
    *,
    input_type: str = "document",
    anchor: str | None = None,
) -> ExtractionResult:
    anchor = anchor or demo_today().isoformat()
    lowered = text.lower()
    lines = _split_lines(text)
    follow_ups: list[FollowUpExtract] = []

    if input_type == "document":
        follow_ups = _document_follow_ups(lines, anchor)
    else:
        # Voice/chat may contain new instructions such as "you need to book an echo".
        # Only the care team's instructions count, never the patient's own plans.
        instruction_text = "\n".join(
            u for sp, u in split_turns(text) if sp == "agent" or input_type in {"chat", "coordinator"}
        )
        if re.search(r"\b(?:need to|needs to|should|must|have to|arrange|book|schedule)\b", instruction_text.lower()):
            seen_categories: set[str] = set()
            for line in _split_lines(instruction_text):
                rule = _rule_for(line)
                if not rule or rule["category"] in seen_categories:
                    continue
                if not re.search(r"\b(?:need to|needs to|should|must|have to|arrange|book|schedule)\b", line.lower()):
                    continue
                due = date_from_offset(line, anchor)
                follow_ups.append(
                    FollowUpExtract(
                        description=rule["description"],
                        category=rule["category"],
                        owner_hint=rule["owner"],
                        due_in_days=(
                            (date.fromisoformat(due) - date.fromisoformat(anchor)).days if due else None
                        ),
                        confidence=0.6,
                        quote=line[:400],
                    )
                )
                seen_categories.add(rule["category"])

    reports: list[PatientReportExtract] = []
    if input_type in {"voice_transcript", "voice_report", "chat"}:
        reports = _patient_reports(text, anchor)

    requested: list[RequestedAction] = []
    if input_type in {"chat", "coordinator"}:
        rule = _rule_for(text)
        hint = rule["description"] if rule else text[:120]
        if re.search(r"\b(verify|confirm|mark (?:it )?(?:as )?(?:complete|done|verified)|close (?:the )?(?:loop|obligation))\b", lowered):
            requested.append(
                RequestedAction(
                    action="verify_completion",
                    obligation_hint=hint,
                    rationale="Coordinator requested evidence-based closure.",
                )
            )
        elif re.search(r"\bcancel\b", lowered):
            requested.append(
                RequestedAction(action="cancel", obligation_hint=hint, rationale="Coordinator requested cancellation.")
            )
        elif re.search(r"\b(schedule|book)\b.*\b(call|check-in|check in)\b", lowered):
            requested.append(
                RequestedAction(
                    action="schedule_checkin",
                    obligation_hint="voice check-in",
                    rationale="Coordinator requested a scheduled voice check-in.",
                )
            )

    # Only patient speech counts for safety. Discharge documents contain
    # red-flag instructions, and voice transcripts contain the agent's own
    # safety script — neither is evidence that the patient has symptoms.
    if input_type == "document":
        safety_text = ""
    elif input_type in {"voice_transcript", "voice_report"}:
        safety_text = "\n".join(u for sp, u in split_turns(text) if sp == "patient")
    else:
        safety_text = text
    safety_lowered = safety_text.lower()

    mentions_medication = any_term(MEDICATION_TERMS, safety_lowered) or bool(
        re.search(r"\b\d+\s?mg\b", safety_lowered)
    )
    medication_question = mentions_medication and any_term(MEDICATION_INTENT, safety_lowered)
    urgent = any(_affirmed_term(t, safety_lowered) for t in URGENT_TERMS)
    emergency = any(_affirmed_term(t, safety_lowered) for t in EMERGENCY_TERMS)
    clinical = medication_question or urgent or any(_affirmed_term(t, safety_lowered) for t in CLINICAL_TERMS)
    safety = SafetyAssessment(
        clinical_question=clinical,
        medication_question=medication_question,
        urgent_symptoms=urgent,
        emergency=emergency,
        summary=(
            "Emergency language detected." if emergency
            else "Urgent symptom language detected." if urgent
            else "Medication question detected." if medication_question
            else "Clinical question detected." if clinical
            else ""
        ),
    )
    summary = (
        f"Extracted {len(follow_ups)} follow-up requirement(s) and "
        f"{len(reports)} patient report(s)."
    )
    uncertainty: list[str] = []
    if input_type == "document" and not follow_ups:
        uncertainty.append("No explicit follow-up instruction detected.")
    for r in reports:
        if r.reported_status == "unknown":
            uncertainty.append(f"Unclear whether {r.obligation_hint.lower()} happened.")
    return ExtractionResult(
        summary=summary,
        facts=[],
        follow_ups=follow_ups,
        patient_reports=reports,
        requested_actions=requested,
        safety=safety,
        uncertainty=uncertainty,
    )


EXTRACTION_SYSTEM = """You are the extraction chain inside Continuum, a post-discharge care
coordination system. You convert a care document, chat message or call transcript into
structured facts, follow-up requirements, patient reports and safety flags.

Rules:
- Extract only what is explicitly stated. Never invent a follow-up, date or diagnosis.
- A follow-up requirement is an action that still needs to happen (e.g. family physician
  within 7 days, bloodwork within 2 weeks).
- A patient report is something the patient says happened or is scheduled. It is NOT
  verified completion. If the patient says it has not happened ("not yet", "I haven't
  gone", "I missed it"), use reported_status "not_completed".
- Emit one follow_up per distinct instruction. Repeats of the same kind (e.g. bloodwork in
  1 week and again in 6 weeks) are separate follow_ups with occurrence 1, 2, …
- Set safety.medication_question for dosage/medication questions and
  safety.urgent_symptoms for potentially urgent symptoms.
- Safety flags apply only to patient/chat utterances. In a discharge document,
  instructions such as "return if you have chest pain" are care instructions,
  not patient symptoms. In a call transcript, ignore the agent's own safety script.
- requested_actions only when the speaker explicitly asks Continuum to verify, cancel,
  resolve a conflict, escalate or schedule a check-in.
- Do not provide clinical advice.
- Use ISO dates (YYYY-MM-DD) when a date is explicit. due_in_days is relative to the
  discharge/anchor date supplied.
"""


def llm_extract(
    text: str,
    *,
    input_type: str,
    patient: dict,
    existing_obligations: list[dict],
    anchor: str,
) -> ExtractionResult:
    model = get_chat_model()
    if model is None:
        raise RuntimeError("No chat model configured")

    obligation_lines = "\n".join(
        f"- [{o['id']}] {o['description']} | due {o.get('due_date')} | status {o['status']}"
        for o in existing_obligations
    ) or "- none"

    user = f"""Anchor date (discharge or today): {anchor}
Patient: {patient.get('name')}, age {patient.get('age')}, condition {patient.get('condition')}
Input type: {input_type}

Existing obligations:
{obligation_lines}

Input:
\"\"\"
{text[:12000]}
\"\"\"
"""
    structured = model.with_structured_output(ExtractionResult)
    return structured.invoke(
        [
            ("system", EXTRACTION_SYSTEM),
            ("human", user),
        ]
    )


def extract(
    text: str,
    *,
    input_type: str = "document",
    patient: dict | None = None,
    existing_obligations: list[dict] | None = None,
    anchor: str | None = None,
) -> dict:
    anchor = anchor or demo_today().isoformat()
    result: ExtractionResult
    method = "llm"
    try:
        result = llm_extract(
            text,
            input_type=input_type,
            patient=patient or {},
            existing_obligations=existing_obligations or [],
            anchor=anchor,
        )
    except Exception as exc:
        log.info("LLM extraction unavailable (%s); using heuristics.", exc)
        result = heuristic_extract(text, input_type=input_type, anchor=anchor)
        method = "heuristic" if get_chat_model() is None else "heuristic_fallback"

    payload = result.model_dump()
    payload["method"] = method
    payload["anchor"] = anchor
    return payload
