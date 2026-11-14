"""Domain enums and API request/response schemas."""

from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field


class ObligationStatus(str, Enum):
    pending = "pending"
    scheduled = "scheduled"
    attempted = "attempted"
    patient_reported = "patient_reported"
    verified = "verified"
    overdue = "overdue"
    cancelled = "cancelled"
    escalated = "escalated"
    unknown = "unknown"
    conflict = "conflict"


OPEN_STATUSES = {
    ObligationStatus.pending,
    ObligationStatus.scheduled,
    ObligationStatus.attempted,
    ObligationStatus.patient_reported,
    ObligationStatus.overdue,
    ObligationStatus.conflict,
    ObligationStatus.unknown,
}

# Closed-loop referrals (MGMA): a specialist referral is closed only when the
# visit happened AND the consult note is back with the referring team.
REFERRAL_CATEGORIES = {"cardiology", "respiratory", "diabetes", "referral"}
# Medication reconciliation is a clinician's attestation, never a coordinator's.
MED_REC_CATEGORY = "medication_reconciliation"

# Status changes that a coordinator may request, but that always require
# supporting evidence or an explicit override note.
SENSITIVE_STATUSES = {
    ObligationStatus.verified,
    ObligationStatus.cancelled,
    ObligationStatus.escalated,
}


class EventType(str, Enum):
    document = "document"
    voice_call = "voice_call"
    chat = "chat"
    coordinator = "coordinator"
    system = "system"


class EvidenceKind(str, Enum):
    document = "document"
    patient_report = "patient_report"
    clinic_confirmation = "clinic_confirmation"
    coordinator_override = "coordinator_override"
    voice_transcript = "voice_transcript"
    agent_note = "agent_note"


class ApprovalKind(str, Enum):
    verify_completion = "verify_completion"
    resolve_conflict = "resolve_conflict"
    escalate = "escalate"
    cancel = "cancel"
    override = "override"
    schedule = "schedule"
    follow_up = "follow_up"


class ApprovalStatus(str, Enum):
    pending = "pending"
    approved = "approved"
    rejected = "rejected"


class MemoryKind(str, Enum):
    patient_context = "patient_context"
    care_event = "care_event"
    care_obligation = "care_obligation"
    preference = "preference"
    interaction = "interaction"
    change = "change"
    uncertainty = "uncertainty"


# ── Requests ──────────────────────────────────────────────────────────────


class ChatRequest(BaseModel):
    patient_id: str
    message: str = Field(min_length=1, max_length=8000)


class VoiceReportRequest(BaseModel):
    patient_id: str
    report: str = Field(min_length=1, max_length=8000)
    obligation_id: str | None = None
    reported_status: str | None = None
    reported_date: str | None = None
    conversation_id: str | None = None
    direction: Literal["inbound", "outbound"] = "inbound"


class VoiceEscalateRequest(BaseModel):
    patient_id: str
    reason: str = Field(min_length=1, max_length=4000)
    urgency: Literal["routine", "urgent", "emergency"] = "routine"
    conversation_id: str | None = None


class ApprovalDecisionRequest(BaseModel):
    approve: bool
    note: str | None = None
    # Conflicts only: the date the coordinator confirmed with the clinic,
    # when it is neither the recorded value nor the patient's report.
    chosen_date: str | None = None


class ApprovalBulkRequest(BaseModel):
    ids: list[str] = Field(min_length=1, max_length=100)
    approve: bool
    note: str | None = None


class ObligationStatusRequest(BaseModel):
    status: ObligationStatus
    # Referrals: the consult note's reference (fax id, DocumentReference id).
    consult_note_ref: str | None = None
    evidence_summary: str | None = None
    source_ref: str | None = None
    override_reason: str | None = None


class ObligationCreateRequest(BaseModel):
    patient_id: str
    description: str
    owner: str = "Care team"
    due_date: str | None = None
    source: str = "Coordinator entry"


class EvidenceCreateRequest(BaseModel):
    summary: str
    kind: EvidenceKind = EvidenceKind.agent_note
    source_ref: str | None = None


class DemoDayRequest(BaseModel):
    day: int
    actor: str = "Demo runner"


class DocumentIngestRequest(BaseModel):
    patient_id: str | None = None  # the path parameter wins
    title: str = "Discharge summary"
    text: str = Field(min_length=10)
    source_ref: str | None = None


class IntakeObligation(BaseModel):
    """A structured follow-up recorded manually at intake (alternative to LLM extraction)."""

    description: str = Field(min_length=3)
    owner: str | None = None
    due_date: str | None = None  # YYYY-MM-DD


class PatientIntakeRequest(BaseModel):
    """Create a new patient, their care episode, and their follow-up obligations."""

    name: str = Field(min_length=2)
    age: int | None = Field(default=None, ge=0, le=130)
    mrn: str | None = None
    condition: str | None = None
    risk: Literal["Low", "Medium", "High"] = "Medium"
    pronouns: str | None = None
    phone: str | None = None
    email: str | None = None
    birth_date: str | None = None  # YYYY-MM-DD; second identifier (name + DOB)
    caregiver: str | None = None
    caregiver_phone: str | None = None
    caregiver_relationship: str | None = None
    # HIPAA personal representative / permission to discuss care with them.
    caregiver_may_discuss_care: bool = False

    admission_date: str | None = None  # YYYY-MM-DD
    discharge_date: str | None = None  # YYYY-MM-DD
    reason: str | None = None  # admission / discharge reason
    discharge_summary: str | None = None  # raw text; triggers LLM extraction

    channel: Literal["Voice call", "Text", "Email", "Portal"] = "Voice call"
    best_time: str | None = None
    language: str = "English"
    interpreter_needed: bool = False
    timezone: str | None = None  # IANA name, e.g. "America/Toronto"
    # Consent is opt-in: it must be confirmed with the patient, never assumed.
    consent_to_automated_calls: bool = False
    consent_to_recording: bool = False

    obligations: list[IntakeObligation] = Field(default_factory=list)
    # Set after the coordinator has seen the duplicate warning and confirmed
    # this is a different person.
    confirm_new: bool = False


class CaregiverUpdate(BaseModel):
    name: str | None = None
    phone: str | None = None
    relationship: str | None = None
    may_discuss_care: bool | None = None


class PreferencesUpdate(BaseModel):
    channel: Literal["Voice call", "Text", "Email", "Portal"] | None = None
    best_time: str | None = None
    language: str | None = None
    interpreter_needed: bool | None = None
    timezone: str | None = None
    consent_to_automated_calls: bool | None = None
    consent_to_recording: bool | None = None


class PatientUpdateRequest(BaseModel):
    """Edit a patient after intake. Omitted fields are left as they are."""

    name: str | None = Field(default=None, min_length=2)
    birth_date: str | None = None
    mrn: str | None = None
    condition: str | None = None
    risk: Literal["Low", "Medium", "High"] | None = None
    pronouns: str | None = None
    phone: str | None = None
    email: str | None = None
    caregiver: CaregiverUpdate | None = None
    preferences: PreferencesUpdate | None = None


class ContactAttemptRequest(BaseModel):
    """A contact attempt logged by a person (phone call, text, portal message)."""

    outcome: Literal["reached", "reached_caregiver", "voicemail", "no_answer", "wrong_number", "declined", "message_sent"]
    channel: Literal["Voice call", "Text", "Email", "Portal", "In person"] = "Voice call"
    note: str | None = Field(default=None, max_length=2000)
    at: str | None = None  # ISO timestamp; defaults to now


class ReadmissionRequest(BaseModel):
    admission_date: str | None = None
    discharge_date: str
    reason: str | None = None
    disposition: str | None = None  # e.g. "Home", "Skilled nursing facility"
    discharge_summary: str | None = None  # extracted like an intake summary


class CloseEpisodeRequest(BaseModel):
    reason: Literal["window_complete", "readmitted", "transferred", "died", "declined_follow_up", "other"]
    note: str | None = Field(default=None, max_length=2000)
    # Cancel still-open follow-ups with the reason above as the override.
    close_open: bool = False


class VisitWindowRequest(BaseModel):
    """Clinician override of the TCM visit window; null returns to the risk default."""

    days: Literal[7, 14] | None = None


class CheckinScheduleRequest(BaseModel):
    patient_id: str
    day: int | None = None
    scheduled_for: str | None = None
    reason: str = "Scheduled follow-up check-in"


class ApiError(BaseModel):
    detail: str


class HealthResponse(BaseModel):
    status: str
    app: str
    database: str
    llm: bool
    vector_search: bool
    voice_configured: bool
    demo_today: str


def as_jsonable(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    return value
