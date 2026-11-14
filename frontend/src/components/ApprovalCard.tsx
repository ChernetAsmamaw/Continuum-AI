import { useEffect, useState } from "react";
import { api } from "../api";
import { useApp } from "../state";
import type { Approval } from "../types";
import { approvalKind } from "../tones";
import { cx, formatDateTime, pct } from "../ui";

function waited(iso: string): string {
  const minutes = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 60000));
  if (minutes < 60) return `${minutes} min`;
  const hours = Math.round(minutes / 60);
  if (hours < 48) return `${hours} h`;
  return `${Math.round(hours / 24)} days`;
}

function changeSummary(changes: Record<string, unknown>): { key: string; value: string }[] {
  return Object.entries(changes || {})
    // Internal identifiers mean nothing to a coordinator; keep the decision-relevant fields.
    .filter(([key]) => !/(^|_)id$/.test(key) && key !== "item")
    .filter(([, value]) => value !== undefined && value !== null && value !== "" && typeof value !== "object")
    .map(([key, value]) => ({ key: key.replace(/_/g, " "), value: String(value) }));
}

export function ApprovalCard({
  approval,
  onResolved,
  compact = false,
}: {
  approval: Approval;
  onResolved?: () => void;
  compact?: boolean;
}) {
  const { notify, refresh } = useApp();
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState<"approve" | "reject" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [showNote, setShowNote] = useState(false);
  const [cardActive, setCardActive] = useState(false);

  const pending = approval.status === "pending";
  const [chosenDate, setChosenDate] = useState("");
  const [showDate, setShowDate] = useState(false);
  const isConflict = approval.kind === "resolve_conflict" && approval.mode !== "interrupt";
  const isUrgent = approval.urgency === "urgent" || approval.urgency === "emergency";
  const lastPage = approval.pages?.at(-1);
  const kind = approvalKind(approval);

  const acknowledge = async () => {
    setError(null);
    try {
      await api.acknowledgeApproval(approval.id);
      notify("Acknowledged. Re-paging has stopped.");
      onResolved?.();
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not acknowledge.");
    }
  };

  const confirmDate = async () => {
    if (!chosenDate) {
      setError("Pick the date the clinic confirmed.");
      return;
    }
    setBusy("approve");
    setError(null);
    try {
      await api.resolveApproval(approval.id, true, note.trim() || undefined, chosenDate);
      notify(`Conflict resolved to the confirmed date. Earlier values are kept as superseded.`);
      onResolved?.();
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not resolve the conflict.");
    } finally {
      setBusy(null);
    }
  };
  const changes = changeSummary(approval.proposed_changes || {});

  const decide = async (approve: boolean) => {
    setBusy(approve ? "approve" : "reject");
    setError(null);
    try {
      const result = await api.resolveApproval(approval.id, approve, note.trim() || undefined);
      notify(
        approve
          ? result.reply
            ? `Approved. ${result.reply}`
            : "Approval recorded and the change applied."
          : "Proposal rejected. The preserved record is unchanged.",
      );
      onResolved?.();
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not resolve the approval.");
    } finally {
      setBusy(null);
    }
  };

  // Task hotkeys: while the card is hovered or focused, y approves and x
  // rejects the pending decision.
  useEffect(() => {
    if (!cardActive || !pending) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.metaKey || event.ctrlKey || event.altKey) return;
      const target = event.target as HTMLElement | null;
      if (target && ["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName)) return;
      if (document.querySelector(".modal-overlay, .palette-overlay, .help-overlay, .tour-overlay")) return;
      if (busy) return;
      const key = event.key.toLowerCase();
      if (key === "y") {
        event.preventDefault();
        decide(true);
      } else if (key === "x") {
        event.preventDefault();
        decide(false);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [cardActive, pending, busy]);

  return (
    <article
      className={cx("approval-card", compact && "approval-card-compact", !pending && "approval-card-resolved")}
      onMouseEnter={() => setCardActive(true)}
      onMouseLeave={() => setCardActive(false)}
      onFocusCapture={() => setCardActive(true)}
      onBlurCapture={() => setCardActive(false)}
    >
      <div className="approval-head">
        <div>
          <div className="tag-row">
            <span className={cx("tag-tone", `tone-${kind.tone}`)}>{kind.label}</span>
            {isUrgent ? <span className="tag-tone tone-red">{approval.urgency}</span> : null}
            {approval.mode === "interrupt" ? <span className="tag-tone tone-violet">Assistant is paused</span> : null}
            {approval.status !== "pending" ? (
              <span className={cx("tag-tone", approval.status === "approved" ? "tone-green" : "tone-red")}>
                {approval.status === "approved" ? "Approved" : "Rejected"}
              </span>
            ) : null}
          </div>
          <h3>{approval.title}</h3>
          <small className="muted" title={formatDateTime(approval.created_at)}>
            {pending ? `Waiting ${waited(approval.created_at)}` : formatDateTime(approval.created_at)}
            {approval.confidence !== undefined ? `. Assistant confidence ${pct(approval.confidence)}` : ""}
          </small>
        </div>
      </div>

      {isUrgent ? (
        <p className={cx("approval-page", lastPage && !lastPage.ok && "approval-page-failed")}>
          {approval.acknowledged_at
            ? `Acknowledged by ${approval.acknowledged_by} on ${formatDateTime(approval.acknowledged_at)}`
            : lastPage
              ? lastPage.ok
                ? `On-call clinician paged ${approval.pages?.length} ${approval.pages?.length === 1 ? "time" : "times"}, last on ${formatDateTime(lastPage.at)}`
                : `Page could not be sent: ${lastPage.detail}`
              : "Not paged yet"}
        </p>
      ) : null}

      <p className="approval-rationale">{approval.rationale}</p>

      {approval.evidence_summary ? (
        <div className={cx("approval-evidence", approval.clinic_evidence ? "ev-record" : "ev-report")}>
          <span className="ev-label">
            {approval.clinic_evidence ? (
              <span className="tag-tone tone-blue" title={approval.clinic_evidence.source_ref}>
                Clinic record
              </span>
            ) : approval.source === "scheduler" ? (
              <span className="tag-tone tone-neutral">Found by the daily check</span>
            ) : (
              <span className="tag-tone tone-amber">Reported, not yet proven</span>
            )}
          </span>
          <p>{approval.evidence_summary}</p>
        </div>
      ) : null}

      {changes.length > 0 ? (
        <div className="approval-changes">
          <span className="approval-changes-label">If you approve</span>
          <dl>
            {changes.map((change) => (
              <div key={change.key}>
                <dt>{change.key.charAt(0).toUpperCase() + change.key.slice(1)}</dt>
                <dd>{(change.value.charAt(0).toUpperCase() + change.value.slice(1)).replace(/_/g, " ")}</dd>
              </div>
            ))}
          </dl>
        </div>
      ) : null}

      {pending ? (
        <>
          {showNote ? (
            <label className="field">
              <span>Decision note (recorded in the audit log)</span>
              <textarea
                rows={2}
                value={note}
                onChange={(event) => setNote(event.target.value)}
                placeholder="Why are you approving or rejecting this?"
              />
            </label>
          ) : (
            <button className="link-button" onClick={() => setShowNote(true)}>
              + Add decision note
            </button>
          )}
          {error ? <p className="form-error">{error}</p> : null}
          {isConflict ? (
            showDate ? (
              <div className="approval-date">
                <label className="field">
                  <span>Date confirmed with the clinic</span>
                  <input id={`chosen-${approval.id}`} type="date" value={chosenDate} onChange={(event) => setChosenDate(event.target.value)} />
                </label>
                <button className="btn btn-small btn-primary" onClick={confirmDate} disabled={busy !== null}>
                  Use this date
                </button>
              </div>
            ) : (
              <button className="link-button" onClick={() => setShowDate(true)}>
                Neither is right: enter the confirmed date
              </button>
            )
          ) : null}
          <div className="approval-actions">
            {isUrgent && !approval.acknowledged_at ? (
              <button className="btn" onClick={acknowledge} disabled={busy !== null} title="Stop re-paging while you work on it">
                Acknowledge
              </button>
            ) : null}
            <button className="btn btn-danger-ghost" onClick={() => decide(false)} disabled={busy !== null} title="Reject (x)">
              {busy === "reject" ? "Rejecting…" : "Reject"}
            </button>
            <button className="btn btn-primary" onClick={() => decide(true)} disabled={busy !== null} title="Approve (y)">
              {busy === "approve" ? "Applying…" : approval.mode === "interrupt" ? "Approve & resume run" : "Approve change"}
            </button>
          </div>
          {approval.mode === "interrupt" ? (
            <p className="form-hint">
              This decision resumes the paused LangGraph run for this patient thread. Checkpointed state is
              preserved in MongoDB.
            </p>
          ) : null}
        </>
      ) : (
        <div className="approval-resolved">
          <span>
            {approval.status} by {approval.resolved_by || "coordinator"} · {formatDateTime(approval.resolved_at)}
          </span>
          {approval.resolution_note ? <p>{approval.resolution_note}</p> : null}
          {approval.applied_at ? <small>Applied to the obligation record.</small> : null}
        </div>
      )}
    </article>
  );
}
