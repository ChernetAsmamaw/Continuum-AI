import { ApprovalCard } from "./ApprovalCard";
import { ObligationCard } from "./ObligationCard";
import { approvalKind, obligationTone, type Tone } from "../tones";
import type { Approval, Evidence, Obligation } from "../types";
import { cx, formatDate } from "../ui";

/**
 * One follow-up as a single row: tone dot, title, status in its tone, one
 * sentence. Opens in place to the full card. Used on the patient page and
 * on Assigned to me so a follow-up looks the same everywhere.
 */

export function sentence(text?: string | null, max = 150): string {
  if (!text) return "";
  const clean = text.replace(/\s+/g, " ").trim();
  if (clean.length <= max) return clean;
  const cut = clean.slice(0, max - 3);
  return `${cut.slice(0, Math.max(cut.lastIndexOf(" "), max - 50)).trimEnd()}…`;
}

export const shortDate = (iso?: string | null) => formatDate(iso, { year: undefined });

export function statusFor(o: Obligation, approval?: Approval): { text: string; tone: Tone; badge?: boolean } {
  if (approval) {
    const urgent = approval.urgency === "urgent" || approval.urgency === "emergency";
    const kind = approvalKind(approval);
    return urgent ? { text: `${approval.urgency}`, tone: "red", badge: true } : { text: kind.label, tone: kind.tone === "neutral" ? "amber" : kind.tone };
  }
  switch (o.status) {
    case "overdue":
      return { text: o.days_overdue ? `${o.days_overdue} day${o.days_overdue === 1 ? "" : "s"} overdue` : "Overdue", tone: "red" };
    case "conflict":
      return { text: "Two dates on record", tone: "amber" };
    case "patient_reported":
      return { text: "Needs proof", tone: "amber" };
    case "escalated":
      return { text: "With the clinician", tone: "red" };
    case "scheduled":
      return { text: o.confirmed_date ? `Booked ${shortDate(o.confirmed_date)}` : `Due ${shortDate(o.due_date)}`, tone: "blue" };
    case "verified":
      return { text: `Verified ${shortDate(o.clinic_completed_on || o.updated_at)}`, tone: "green" };
    case "cancelled":
      return { text: "Cancelled", tone: "neutral" };
    default:
      return { text: o.due_date ? `Due ${shortDate(o.due_date)}` : "No date", tone: "neutral" };
  }
}

function subFor(o: Obligation, approval?: Approval): string {
  if (approval) return sentence(approval.rationale || approval.title);
  const conflict = (o.conflicts || []).find((c) => !c.resolved);
  if (o.status === "conflict" && conflict) {
    return `Clinic record says ${conflict.recorded_value || "nothing"}. The patient says ${conflict.reported_value || "nothing"}.`;
  }
  if (o.status === "overdue" && o.next_action) return sentence(o.next_action);
  if (o.latest_evidence) return sentence(o.latest_evidence);
  if (o.next_action) return sentence(o.next_action);
  return `Owner: ${o.owner}.`;
}

export function Status({ text, tone, badge }: { text: string; tone: Tone; badge?: boolean }) {
  return <span className={cx("fu-status", `tone-${tone}`, badge && "fu-badge")}>{text}</span>;
}

export function FollowUpRow({
  obligation,
  evidence = [],
  approval,
  patientName,
  open,
  onToggle,
  onChanged,
}: {
  obligation: Obligation;
  evidence?: Evidence[];
  approval?: Approval;
  patientName?: string;
  open: boolean;
  onToggle: () => void;
  onChanged: () => void;
}) {
  const status = statusFor(obligation, approval);
  const tone = approval ? (status.tone === "red" ? "red" : "amber") : obligationTone(obligation);
  const done = !obligation.is_open;
  return (
    <div className={cx("fu-row", `fu-${tone}`, done && "fu-done", open && "fu-open")}>
      <button type="button" className="fu-main" onClick={onToggle} aria-expanded={open}>
        <span className={cx("dot", `dot-${tone}`)} aria-hidden="true" />
        <b className="fu-title">
          {patientName ? <span className="fu-patient">{patientName}</span> : null}
          {obligation.description}
        </b>
        <Status {...status} />
        <span className="fu-sub">{subFor(obligation, approval)}</span>
        <span className="fu-chev" aria-hidden="true" />
      </button>
      {open ? (
        <div className="fu-detail">
          {approval ? <ApprovalCard approval={approval} compact onResolved={onChanged} /> : null}
          <ObligationCard obligation={obligation} evidence={evidence} onChanged={onChanged} />
        </div>
      ) : null}
    </div>
  );
}
