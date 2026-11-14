import { useState } from "react";
import { Link } from "react-router-dom";
import type { ActionStatus, LedgerAction } from "../types";
import { cx, formatDateTime, relativeDate } from "../ui";

export const ACTION_STATUSES: ActionStatus[] = ["intended", "sent", "confirmed", "failed", "unconfirmed", "unknown"];

const LABELS: Record<ActionStatus, string> = {
  intended: "Intended",
  sent: "Sent",
  confirmed: "Confirmed",
  failed: "Failed",
  unconfirmed: "Unconfirmed",
  unknown: "Unknown",
};

const TONES: Record<ActionStatus, string> = {
  intended: "neutral",
  sent: "blue",
  confirmed: "green",
  failed: "danger",
  unconfirmed: "amber",
  unknown: "purple",
};

const MEANING: Record<ActionStatus, string> = {
  intended: "Decided, not yet sent",
  sent: "Sent; waiting for proof",
  confirmed: "Proof arrived",
  failed: "The other system said no",
  unconfirmed: "Sent, but no proof by the deadline",
  unknown: "Interrupted mid-send; may or may not have gone out",
};

export function actionTone(status: string): string {
  return TONES[status as ActionStatus] || "neutral";
}

export function ActionChip({ status }: { status: ActionStatus | string }) {
  return (
    <span className={cx("pill", `pill-${actionTone(status)}`, "action-chip")} title={MEANING[status as ActionStatus]}>
      <span className="pill-dot" />
      {LABELS[status as ActionStatus] || status}
    </span>
  );
}

function kindLabel(kind: string): string {
  return kind.replace(/[_:.]/g, " ");
}

function guidance(action: LedgerAction): string | null {
  if (action.status === "unknown")
    return "Check the other system before retrying: Continuum stopped between deciding and hearing back, so it won't resend on its own.";
  if (action.status === "unconfirmed")
    return `Sent, but the expected proof never arrived${action.confirm_by ? ` by ${formatDateTime(action.confirm_by)}` : ""}. Follow up manually.`;
  return null;
}

export function ActionRow({
  action,
  patientName,
  showPatient = false,
  compact = false,
}: {
  action: LedgerAction;
  patientName?: string;
  showPatient?: boolean;
  compact?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const waiting = action.status === "sent" || action.status === "intended" || action.status === "unconfirmed" || action.status === "unknown";
  const hint = guidance(action);
  return (
    <article className={cx("action-row", `action-row-${actionTone(action.status)}`, compact && "action-row-compact")}>
      <div className="action-row-top">
        <ActionChip status={action.status} />
        <div className="action-row-main">
          <strong>{action.summary}</strong>
          <small>
            {kindLabel(action.kind)}
            {showPatient ? (
              <>
                {" · "}
                <Link to={`/patients/${action.patient_id}`} className="link-inline">
                  {patientName || action.patient_id}
                </Link>
              </>
            ) : null}
            {" · "}
            {action.actor} · {relativeDate(action.intended_at)}
          </small>
        </div>
        <button className="link-button" onClick={() => setOpen((value) => !value)} aria-expanded={open}>
          {open ? "Hide history" : `History · ${action.history.length}`}
        </button>
      </div>

      {action.proof_expected ? (
        <p className={cx("action-proof", !waiting && "action-proof-done")}>
          <span>{action.status === "confirmed" ? "Proof received" : waiting ? "Waiting for" : "Expected proof"}</span>
          {action.status === "confirmed" && action.confirmation ? action.confirmation : action.proof_expected}
          {waiting && action.confirm_by ? <small> · by {formatDateTime(action.confirm_by)}</small> : null}
        </p>
      ) : null}

      {hint ? <p className="action-hint">{hint}</p> : null}

      {open ? (
        <ol className="mini-timeline action-history">
          {[...action.history].reverse().map((entry, index) => (
            <li key={`${entry.at}-${index}`}>
              <span className={cx("dot", `dot-${actionTone(entry.status) === "neutral" ? "neutral" : actionTone(entry.status)}`)} />
              <div>
                <p>
                  <b>{LABELS[entry.status] || entry.status}</b> · {entry.detail}
                </p>
                <small>{formatDateTime(entry.at)}</small>
              </div>
            </li>
          ))}
          {action.external_ref ? (
            <li className="action-ref">
              <small>
                External reference <code>{action.external_ref}</code> · action {action.id}
              </small>
            </li>
          ) : null}
        </ol>
      ) : null}
    </article>
  );
}

export function ActionList({
  actions,
  patientNames,
  showPatient = false,
  compact = false,
}: {
  actions: LedgerAction[];
  patientNames?: Map<string, string>;
  showPatient?: boolean;
  compact?: boolean;
}) {
  return (
    <div className="action-list">
      {actions.map((action) => (
        <ActionRow key={action.id} action={action} compact={compact} showPatient={showPatient} patientName={patientNames?.get(action.patient_id)} />
      ))}
    </div>
  );
}
