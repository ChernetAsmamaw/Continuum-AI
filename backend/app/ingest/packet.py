"""The normalized discharge record every adapter produces.

Nothing downstream of this module knows whether a discharge arrived as an
HL7v2 message, a FHIR pull or an uploaded document.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class PacketIdentifier(BaseModel):
    system: str | None = None
    value: str
    type: str | None = None  # e.g. "MR" for a medical record number


class PacketPatient(BaseModel):
    fhir_id: str | None = None
    identifiers: list[PacketIdentifier] = Field(default_factory=list)
    name: str
    birth_date: str | None = None
    gender: str | None = None
    phone: str | None = None
    language: str | None = None

    @property
    def mrn(self) -> str | None:
        for ident in self.identifiers:
            if (ident.type or "").upper() == "MR":
                return ident.value
        return self.identifiers[0].value if self.identifiers else None


class PacketEncounter(BaseModel):
    ref: str  # Encounter/<id>
    version_ref: str
    identifiers: list[PacketIdentifier] = Field(default_factory=list)
    admit_date: str | None = None
    discharge_date: str | None = None
    reason: str | None = None
    disposition: str | None = None
    attending: str | None = None
    facility: str | None = None


class PacketOrder(BaseModel):
    """An order placed during the stay that still has to happen after discharge."""

    ref: str  # ServiceRequest/<id>
    version_ref: str
    kind: str  # referral | lab | imaging | other
    display: str
    code: str | None = None
    status: str
    authored_on: str | None = None
    due_date: str | None = None
    performer: str | None = None
    note: str | None = None


class PacketAppointment(BaseModel):
    ref: str
    version_ref: str
    status: str
    start: str | None = None  # YYYY-MM-DD
    service: str
    practitioner: str | None = None
    based_on: list[str] = Field(default_factory=list)


class PacketMedication(BaseModel):
    ref: str
    version_ref: str
    display: str
    instructions: str | None = None


class PacketPendingResult(BaseModel):
    """A test that was ordered/collected but has no final result at discharge."""

    ref: str  # DiagnosticReport/<id>
    version_ref: str
    display: str
    status: str
    kind: str  # lab | imaging | other
    based_on: list[str] = Field(default_factory=list)


class DischargePacket(BaseModel):
    source: str  # hl7v2+fhir | fhir | document
    source_ref: str
    fhir_base: str | None = None
    patient: PacketPatient
    encounter: PacketEncounter
    discharge_summary_text: str | None = None
    discharge_summary_ref: str | None = None
    orders: list[PacketOrder] = Field(default_factory=list)
    appointments: list[PacketAppointment] = Field(default_factory=list)
    medications: list[PacketMedication] = Field(default_factory=list)
    # What the patient reported taking at home (MedicationStatement), to reconcile against.
    home_medications: list[PacketMedication] = Field(default_factory=list)
    pending_results: list[PacketPendingResult] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
