"""HL7 v2.x ADT parsing and acknowledgements.

Only what the discharge trigger needs: MSH (routing, control id), EVN, PID
(identity), PV1 (visit number, admit/discharge times, disposition, attending).
The message is the doorbell; the record itself is pulled over FHIR.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

MLLP_START = "\x0b"
MLLP_END = "\x1c\x0d"


class HL7Error(ValueError):
    pass


@dataclass
class Segment:
    name: str
    fields: list[str]
    component_sep: str = "^"
    repetition_sep: str = "~"

    def field(self, n: int) -> str:
        """1-based HL7 field number (MSH is adjusted so MSH-9 is the message type)."""
        idx = n - 1 if self.name == "MSH" else n
        return self.fields[idx] if 0 <= idx < len(self.fields) else ""

    def repetitions(self, n: int) -> list[str]:
        value = self.field(n)
        return [r for r in value.split(self.repetition_sep) if r] if value else []

    def component(self, n: int, c: int = 1, value: str | None = None) -> str:
        raw = self.field(n) if value is None else value
        parts = raw.split(self.component_sep)
        return parts[c - 1] if 0 <= c - 1 < len(parts) else ""


@dataclass
class AdtEvent:
    message_type: str
    trigger: str
    control_id: str
    sending_app: str
    sending_facility: str
    sent_at: str | None
    patient_identifiers: list[dict] = field(default_factory=list)
    family_name: str = ""
    given_name: str = ""
    birth_date: str | None = None
    sex: str | None = None
    phone: str | None = None
    visit_number: str | None = None
    admit_at: str | None = None
    discharge_at: str | None = None
    disposition: str | None = None
    attending: str | None = None
    raw: str = ""

    @property
    def is_discharge(self) -> bool:
        return self.trigger == "A03"

    @property
    def identifier_values(self) -> list[str]:
        mrns = [i["value"] for i in self.patient_identifiers if i.get("type") in ("MR", "PI", "")]
        others = [i["value"] for i in self.patient_identifiers if i["value"] not in mrns]
        return mrns + others


def strip_mllp(text: str) -> str:
    return text.replace(MLLP_START, "").replace("\x1c", "").strip()


def split_segments(text: str) -> list[Segment]:
    text = strip_mllp(text).replace("\r\n", "\r").replace("\n", "\r")
    lines = [l for l in text.split("\r") if l.strip()]
    if not lines or not lines[0].startswith("MSH"):
        raise HL7Error("Message must start with an MSH segment")
    field_sep = lines[0][3]
    encoding = lines[0].split(field_sep)[1] if len(lines[0].split(field_sep)) > 1 else "^~\\&"
    comp = encoding[0] if encoding else "^"
    rep = encoding[1] if len(encoding) > 1 else "~"
    segments = []
    for line in lines:
        parts = line.split(field_sep)
        if parts[0] == "MSH":
            # MSH-1 is the separator itself; keep indices aligned with the spec.
            parts = ["MSH", field_sep, *parts[1:]]
            segments.append(Segment("MSH", parts[1:], comp, rep))
        else:
            segments.append(Segment(parts[0], parts, comp, rep))
    return segments


def hl7_date(value: str) -> str | None:
    digits = "".join(ch for ch in value or "" if ch.isdigit())
    if len(digits) < 8:
        return None
    try:
        return datetime.strptime(digits[:8], "%Y%m%d").date().isoformat()
    except ValueError:
        return None


def hl7_timestamp(value: str) -> str | None:
    digits = "".join(ch for ch in (value or "").split("+")[0].split("-")[0] if ch.isdigit())
    for fmt, n in (("%Y%m%d%H%M%S", 14), ("%Y%m%d%H%M", 12), ("%Y%m%d", 8)):
        if len(digits) >= n:
            try:
                return datetime.strptime(digits[:n], fmt).isoformat()
            except ValueError:
                continue
    return None


def parse_adt(text: str) -> AdtEvent:
    segments = split_segments(text)
    by_name: dict[str, Segment] = {}
    for seg in segments:
        by_name.setdefault(seg.name, seg)
    msh = by_name["MSH"]
    msg_type = msh.component(9, 1)
    trigger = msh.component(9, 2) or (by_name["EVN"].field(1) if "EVN" in by_name else "")
    if msg_type != "ADT":
        raise HL7Error(f"Unsupported message type {msg_type or '(none)'}; expected ADT")
    control_id = msh.field(10)
    if not control_id:
        raise HL7Error("MSH-10 (message control id) is required")
    event = AdtEvent(
        message_type=msg_type,
        trigger=trigger,
        control_id=control_id,
        sending_app=msh.component(3, 1),
        sending_facility=msh.component(4, 1),
        sent_at=hl7_timestamp(msh.field(7)),
        raw=strip_mllp(text),
    )
    pid = by_name.get("PID")
    if pid is None:
        raise HL7Error("ADT message has no PID segment")
    for rep in pid.repetitions(3):
        value = pid.component(3, 1, rep)
        if value:
            event.patient_identifiers.append(
                {"value": value, "authority": pid.component(3, 4, rep), "type": pid.component(3, 5, rep)}
            )
    name = pid.repetitions(5)[0] if pid.repetitions(5) else ""
    event.family_name = pid.component(5, 1, name)
    event.given_name = pid.component(5, 2, name)
    event.birth_date = hl7_date(pid.field(7))
    event.sex = pid.field(8) or None
    phone = pid.repetitions(13)[0] if pid.repetitions(13) else ""
    event.phone = pid.component(13, 1, phone) or None
    pv1 = by_name.get("PV1")
    if pv1 is not None:
        event.visit_number = pv1.component(19, 1) or None
        event.admit_at = hl7_timestamp(pv1.field(44))
        event.discharge_at = hl7_timestamp(pv1.field(45))
        event.disposition = pv1.component(36, 1) or None
        attending = pv1.repetitions(7)[0] if pv1.repetitions(7) else ""
        family, given = pv1.component(7, 2, attending), pv1.component(7, 3, attending)
        event.attending = " ".join(p for p in (given, family) if p) or None
    if not event.patient_identifiers:
        raise HL7Error("PID-3 (patient identifier) is required")
    return event


def build_ack(control_id: str, code: str = "AA", text: str = "", *, receiving_app: str = "", receiving_facility: str = "") -> str:
    """ACK for a received message. code: AA accept, AE error, AR reject."""
    now = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    safe = (text or "").replace("|", "/").replace("\r", " ").replace("\n", " ")[:200]
    return "\r".join(
        [
            f"MSH|^~\\&|CONTINUUM|CONTINUUM|{receiving_app}|{receiving_facility}|{now}||ACK^A03^ACK|ACK{control_id}|P|2.5.1",
            f"MSA|{code}|{control_id}|{safe}",
        ]
    ) + "\r"


def build_a03(
    *,
    control_id: str,
    mrn: str,
    family: str,
    given: str,
    birth_date: str | None,
    sex: str | None,
    visit_number: str,
    admit: str | None,
    discharge: str,
    attending: str = "",
    disposition: str = "01",
    phone: str = "",
    sending_app: str = "EPIC",
    sending_facility: str = "GENHOSP",
    mrn_authority: str = "GENHOSP",
) -> str:
    """Build an ADT^A03 — used by the sandbox scripts and tests."""

    def ts(value: str | None) -> str:
        if not value:
            return ""
        return "".join(ch for ch in value if ch.isdigit())[:12]

    now = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    att_given = att_family = ""
    if attending:
        bits = attending.replace("Dr. ", "").split(" (")[0].split()
        att_given, att_family = (bits[0], bits[-1]) if len(bits) > 1 else ("", bits[0])
    pv1 = [""] * 46  # pv1[n] is PV1-n
    pv1[1], pv1[2] = "1", "I"
    pv1[7] = f"ATT1^{att_family}^{att_given}" if attending else ""
    pv1[19] = f"{visit_number}^^^{sending_facility}^VN"
    pv1[36] = disposition
    pv1[44], pv1[45] = ts(admit), ts(discharge)
    return "\r".join(
        [
            f"MSH|^~\\&|{sending_app}|{sending_facility}|CONTINUUM|CONTINUUM|{now}||ADT^A03^ADT_A03|{control_id}|P|2.5.1",
            f"EVN|A03|{now}",
            f"PID|1||{mrn}^^^{mrn_authority}^MR||{family}^{given}||{ts(birth_date)[:8]}|{sex or ''}|||||{phone}",
            "PV1|" + "|".join(pv1[1:]),
        ]
    ) + "\r"
