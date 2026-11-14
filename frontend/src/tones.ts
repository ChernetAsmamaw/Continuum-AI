/**
 * One color vocabulary for the whole console. Color always means the same
 * thing, and it is only ever a dot, a short label or a thin rule:
 *
 *   red     overdue, missed, failed, emergency
 *   amber   waiting on a person: a decision, or proof of a patient's report
 *   green   verified, confirmed, done
 *   blue    came from a clinic or the EHR (a record, not a report)
 *   violet  a call or a message with the patient
 *   neutral everything else
 */
import type { Approval, EventItem, Obligation } from "./types";

export type Tone = "red" | "amber" | "green" | "blue" | "violet" | "neutral";

export const APPROVAL_KIND: Record<string, { label: string; tone: Tone }> = {
  verify_completion: { label: "Verify completion", tone: "green" },
  resolve_conflict: { label: "Conflicting dates", tone: "amber" },
  escalate: { label: "Clinical escalation", tone: "red" },
  cancel: { label: "Cancel follow-up", tone: "neutral" },
  override: { label: "Override", tone: "amber" },
  schedule: { label: "Booking request", tone: "violet" },
  follow_up: { label: "Follow-up request", tone: "blue" },
};

export function approvalKind(a: Pick<Approval, "kind" | "urgency">): { label: string; tone: Tone } {
  const base = APPROVAL_KIND[a.kind] || { label: a.kind.replace(/_/g, " "), tone: "neutral" as Tone };
  if (a.urgency === "emergency" || a.urgency === "urgent") return { label: base.label, tone: "red" };
  return base;
}

export function obligationTone(o: Pick<Obligation, "status">): Tone {
  switch (o.status) {
    case "overdue":
    case "escalated":
      return "red";
    case "conflict":
    case "patient_reported":
    case "attempted":
      return "amber";
    case "verified":
      return "green";
    case "scheduled":
      return "blue";
    default:
      return "neutral";
  }
}

/** What kind of thing happened, for the History tab. */
export function eventKind(e: Pick<EventItem, "type" | "title" | "actor">): { label: string; tone: Tone } {
  const t = e.type;
  const title = (e.title || "").toLowerCase();
  if (t === "voice_call" || t.startsWith("voice") || title.includes("call")) return { label: "Call", tone: "violet" };
  if (t === "document" || t === "intake" || t.startsWith("ehr") || t.startsWith("ingest")) return { label: "Record", tone: "blue" };
  if (title.includes("verified") || title.includes("confirmed")) return { label: "Verified", tone: "green" };
  if (title.includes("conflict") || title.includes("approval") || title.includes("decision")) return { label: "Decision", tone: "amber" };
  if (title.includes("overdue") || title.includes("missed") || title.includes("escalat")) return { label: "Alert", tone: "red" };
  if (t === "chat") return { label: "Assistant", tone: "neutral" };
  if (t === "coordinator") return { label: "Coordinator", tone: "neutral" };
  return { label: "Update", tone: "neutral" };
}
