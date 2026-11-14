import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";
import { useApp } from "../state";
import type { Conflict, Evidence, Obligation, ReviewQueue } from "../types";
import { obligationTone } from "../tones";
import { cx, dueLabel, formatDate, formatDateTime, pct, statusLabel, statusTone } from "../ui";
import { AssignControl } from "./AssignControl";
import { Modal } from "./Modal";
import { needsTypeReview, OrderTypeForm, ProvenanceDetails, ProvenanceTags } from "./Provenance";

type UpdateMode = "evidence" | "confirm" | "status" | "type";

const STATUS_OPTIONS = [
  "pending",
  "scheduled",
  "attempted",
  "patient_reported",
  "verified",
  "escalated",
  "cancelled",
] as const;

const SENSITIVE = new Set(["verified", "escalated", "cancelled"]);

const NO_BOOKED_DATE = new Set(["pending_result"]);
const REFERRAL = new Set(["cardiology", "respiratory", "diabetes", "referral"]);
const MED_REC = "medication_reconciliation";

function todayIso(): string {
  return new Date().toISOString().slice(0, 10);
}

export function ObligationCard({
  obligation,
  evidence = [],
  patientName,
  onChanged,
  dense = false,
}: {
  obligation: Obligation;
  evidence?: Evidence[];
  patientName?: string;
  onChanged?: () => void;
  dense?: boolean;
}) {
  const { notify, refresh } = useApp();
  const [expanded, setExpanded] = useState(false);
  const [mode, setMode] = useState<UpdateMode | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [statusValue, setStatusValue] = useState<string>(obligation.status === "conflict" ? "scheduled" : obligation.status);
  const [summary, setSummary] = useState(obligation.latest_evidence || "");
  const [sourceRef, setSourceRef] = useState("");
  const [overrideReason, setOverrideReason] = useState("");
  const [consultNoteRef, setConsultNoteRef] = useState("");
  const isReferral = REFERRAL.has(obligation.category || "");
  const isMedRec = obligation.category === MED_REC;
  const [confirmedDate, setConfirmedDate] = useState(
    obligation.confirmed_date || obligation.due_date || todayIso(),
  );
  const [cardActive, setCardActive] = useState(false);
  const [reviewItem, setReviewItem] = useState<ReviewQueue["orders"][number] | null>(null);
  const [reviewCategories, setReviewCategories] = useState<ReviewQueue["categories"] | undefined>(undefined);
  const typeReview = needsTypeReview(obligation);
  const tone = obligationTone(obligation);
  const statusText =
    obligation.status === "overdue"
      ? `${obligation.days_overdue || ""} ${obligation.days_overdue === 1 ? "day" : "days"} overdue`.trim()
      : obligation.status === "scheduled" && obligation.confirmed_date
        ? `Booked ${formatDate(obligation.confirmed_date)}`
        : obligation.status === "pending" || obligation.status === "unknown"
          ? dueLabel(obligation.days_until_due)
          : statusLabel(obligation.status);

  const unresolved = (obligation.conflicts || []).filter((conflict) => !conflict.resolved);
  const isClosed = obligation.status === "verified" || obligation.status === "cancelled";
  // Any follow-up can have a booked date except a lab result still pending,
  // which arrives rather than being scheduled.
  const hasAppointment = !NO_BOOKED_DATE.has(obligation.category || "");

  const modalOpen = () =>
    document.querySelector(".modal-overlay, .palette-overlay, .help-overlay, .tour-overlay") !== null;

  // Task hotkeys: while the card is hovered or focused, e / u / c open the
  // evidence, update and confirm-date dialogs respectively.
  useEffect(() => {
    if (!cardActive) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.metaKey || event.ctrlKey || event.altKey) return;
      const target = event.target as HTMLElement | null;
      if (target && ["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName)) return;
      if (modalOpen()) return;
      const key = event.key.toLowerCase();
      if (isClosed) return;
      if (key === "e") {
        event.preventDefault();
        openModal("evidence");
      } else if (key === "u") {
        event.preventDefault();
        openModal("status");
      } else if (key === "c" && hasAppointment) {
        event.preventDefault();
        openModal("confirm");
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [cardActive, isClosed, hasAppointment]);

  const openModal = (next: UpdateMode) => {
    setError(null);
    setSummary(obligation.latest_evidence || "");
    setSourceRef("");
    setOverrideReason("");
    setStatusValue(obligation.status === "conflict" ? "scheduled" : obligation.status);
    setConfirmedDate(obligation.confirmed_date || obligation.due_date || todayIso());
    setMode(next);
    if (next === "type") {
      api
        .review(obligation.patient_id)
        .then((queue) => {
          setReviewCategories(queue.categories);
          setReviewItem(queue.orders.find((item) => item.id === obligation.id) || null);
        })
        .catch(() => undefined);
    }
  };

  const done = async (message: string) => {
    setMode(null);
    notify(message);
    onChanged?.();
    await refresh();
  };

  const submitEvidence = async () => {
    if (!summary.trim()) {
      setError("Describe the evidence you are recording.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await api.addEvidence(obligation.id, summary.trim(), sourceRef.trim() || undefined);
      await done("Evidence attached to the obligation.");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not add evidence.");
    } finally {
      setBusy(false);
    }
  };

  const submitConfirm = async () => {
    setBusy(true);
    setError(null);
    try {
      await api.confirmDate(obligation.id, confirmedDate, sourceRef.trim() || undefined);
      await done(`Appointment confirmed for ${formatDate(confirmedDate)}.`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not confirm the appointment.");
    } finally {
      setBusy(false);
    }
  };

  const submitStatus = async () => {
    if (SENSITIVE.has(statusValue) && !summary.trim() && !overrideReason.trim()) {
      setError("Verified, escalated and cancelled updates need evidence or a coordinator override reason.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await api.changeStatus(obligation.id, {
        status: statusValue,
        evidence_summary: summary.trim() || undefined,
        override_reason: overrideReason.trim() || undefined,
        source_ref: sourceRef.trim() || undefined,
        consult_note_ref: consultNoteRef.trim() || undefined,
      });
      await done(`Status updated to ${statusLabel(statusValue)}.`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not update the obligation.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <article
      className={cx("obligation-card ob", `ob-${tone}`, dense && "obligation-card-dense")}
      onMouseEnter={() => setCardActive(true)}
      onMouseLeave={() => setCardActive(false)}
      onFocusCapture={() => setCardActive(true)}
      onBlurCapture={() => setCardActive(false)}
    >
      <div className="ob-head">
        <span className={cx("dot", `dot-${tone}`)} aria-hidden="true" />
        <div className="ob-heading">
          <h3>{obligation.description}</h3>
          <p className="ob-meta">
            {patientName ? <b>{patientName}</b> : null}
            <span>Owner: {obligation.owner}</span>
            {obligation.assignee ? <span>Chased by {obligation.assignee.name}</span> : null}
            <ProvenanceTags obligation={obligation} />
          </p>
        </div>
        <div className={cx("ob-when", `tone-${tone}`)}>
          <b>{statusText}</b>
          {obligation.due_date && !isClosed ? <small>Due {formatDate(obligation.due_date)}</small> : null}
        </div>
      </div>

      {unresolved.length > 0 ? (
        <div className="ob-conflict">
          {unresolved.map((conflict: Conflict, index) => (
            <p key={`${conflict.reported_value}-${index}`}>
              <span>Clinic record</span> <b>{conflict.recorded_value || "none"}</b>
              <span>Patient says</span> <b className="tone-amber">{conflict.reported_value || "none"}</b>
            </p>
          ))}
          <Link to="/approvals">Choose the right date</Link>
        </div>
      ) : null}

      {obligation.next_action || obligation.latest_evidence ? (
        <dl className="ob-facts">
          {obligation.next_action ? (
            <div>
              <dt>Next step</dt>
              <dd>{obligation.next_action}</dd>
            </div>
          ) : null}
          {obligation.latest_evidence ? (
            <div>
              <dt>Latest evidence</dt>
              <dd>{obligation.latest_evidence}</dd>
            </div>
          ) : null}
        </dl>
      ) : null}

      <div className="ob-actions">
        {!isClosed ? (
          <>
            <button className="btn btn-small btn-primary" onClick={() => openModal("status")} title="Log update (u)">
              Log update
            </button>
            <button className="btn btn-small" onClick={() => openModal("evidence")} title="Add evidence (e)">
              Add evidence
            </button>
            {hasAppointment ? (
              <button className="btn btn-small" onClick={() => openModal("confirm")} title="Confirm date (c)">
                Confirm date
              </button>
            ) : null}
            {typeReview ? (
              <button className="btn btn-small" onClick={() => openModal("type")} title="Confirm or correct the order type">
                Confirm type
              </button>
            ) : null}
            <AssignControl obligation={obligation} onChanged={onChanged} />
          </>
        ) : (
          <span className="tone-green ob-closed">Closed with proof</span>
        )}
        <button className="ob-more" onClick={() => setExpanded((value) => !value)} aria-expanded={expanded}>
          {expanded ? "Hide history" : "History and sources"}
        </button>
      </div>

      {expanded ? (
        <div className="obligation-details">
          <dl className="kv">
            <div>
              <dt>Source</dt>
              <dd>{obligation.source?.label || "Care interaction"}</dd>
            </div>
            <div>
              <dt>Confirmed date</dt>
              <dd>
                {formatDate(obligation.confirmed_date)}
                {obligation.confirmed_source ? <small> · {obligation.confirmed_source}</small> : null}
              </dd>
            </div>
            <div>
              <dt>Verification</dt>
              <dd>{obligation.verification_required ? "Required before closure" : "Not required"}</dd>
            </div>
            {obligation.confidence !== undefined ? (
              <div>
                <dt>Extraction</dt>
                <dd>{pct(obligation.confidence)} confidence</dd>
              </div>
            ) : null}
            <div>
              <dt>Record version</dt>
              <dd>v{obligation.version ?? 1}</dd>
            </div>
            <div>
              <dt>Last updated</dt>
              <dd>{formatDateTime(obligation.updated_at)}</dd>
            </div>
          </dl>

          <ProvenanceDetails obligation={obligation} />

          {obligation.source?.quote ? <blockquote className="quote">{obligation.source.quote}</blockquote> : null}

          {evidence.length > 0 ? (
            <div className="detail-block">
              <h4>Evidence trail</h4>
              <ul className="evidence-list">
                {evidence.map((item) => (
                  <li key={item.id}>
                    <span className={cx("dot", `dot-${item.kind === "clinic_confirmation" ? "green" : "amber"}`)} />
                    <div>
                      <p>{item.summary}</p>
                      <small>
                        {item.kind.replace(/_/g, " ")} · {item.recorded_by} · {formatDateTime(item.recorded_at)}
                        {item.source_ref ? ` · ${item.source_ref}` : ""}
                      </small>
                    </div>
                  </li>
                ))}
              </ul>
            </div>
          ) : null}

          {obligation.history && obligation.history.length > 0 ? (
            <div className="detail-block">
              <h4>Status history</h4>
              <ol className="mini-timeline">
                {[...obligation.history].reverse().map((entry, index) => (
                  <li key={`${entry.at}-${index}`}>
                    <span className={cx("dot", `dot-${statusTone(entry.status)}`)} />
                    <div>
                      <p>
                        <b>{statusLabel(entry.status)}</b> · {entry.actor}
                      </p>
                      <small>
                        {entry.reason} · {formatDateTime(entry.at)}
                      </small>
                    </div>
                  </li>
                ))}
              </ol>
            </div>
          ) : null}
        </div>
      ) : null}

      {mode === "type" ? (
        <Modal title="Confirm order type" subtitle={obligation.description} onClose={() => setMode(null)}>
          <OrderTypeForm
            obligation={obligation}
            sourceOrder={reviewItem?.source_order}
            duplicates={reviewItem?.possible_duplicates}
            categories={reviewCategories}
            onCancel={() => setMode(null)}
            onDone={(outcome, n) =>
              void done((outcome === "corrected" ? "Order type corrected and learned." : "Order type confirmed and learned.") + (n ? ` ${n === 1 ? "The AI copy was" : `${n} AI copies were`} cancelled as a duplicate.` : ""))
            }
          />
        </Modal>
      ) : null}

      {mode === "evidence" ? (
        <Modal
          title="Add evidence"
          subtitle={obligation.description}
          onClose={() => setMode(null)}
          footer={
            <>
              <button className="btn" onClick={() => setMode(null)}>
                Cancel
              </button>
              <button className="btn btn-primary" onClick={submitEvidence} disabled={busy}>
                {busy ? "Saving…" : "Attach evidence"}
              </button>
            </>
          }
        >
          <p className="form-hint">
            Evidence is append-only. A patient report alone never verifies completion — attach clinic,
            pharmacy or coordinator confirmation.
          </p>
          <label className="field">
            <span>Evidence summary</span>
            <textarea
              rows={4}
              value={summary}
              onChange={(event) => setSummary(event.target.value)}
              placeholder="e.g. Clinic record CS-8841 confirms the appointment for 14 Nov."
              autoFocus
            />
          </label>
          <label className="field">
            <span>Source reference (optional)</span>
            <input
              value={sourceRef}
              onChange={(event) => setSourceRef(event.target.value)}
              placeholder="Clinic record / fax / call reference"
            />
          </label>
          {error ? <p className="form-error">{error}</p> : null}
        </Modal>
      ) : null}

      {mode === "confirm" ? (
        <Modal
          title="Confirm appointment"
          subtitle={obligation.description}
          onClose={() => setMode(null)}
          footer={
            <>
              <button className="btn" onClick={() => setMode(null)}>
                Cancel
              </button>
              <button className="btn btn-primary" onClick={submitConfirm} disabled={busy}>
                {busy ? "Saving…" : "Record confirmation"}
              </button>
            </>
          }
        >
          <p className="form-hint">
            Confirmed dates are supported by clinic evidence and update the obligation's due date. The
            previous value stays in the audit history.
          </p>
          <div className="form-grid">
            <label className="field">
              <span>Confirmed date</span>
              <input type="date" value={confirmedDate} onChange={(event) => setConfirmedDate(event.target.value)} />
            </label>
            <label className="field">
              <span>Source reference</span>
              <input
                value={sourceRef}
                onChange={(event) => setSourceRef(event.target.value)}
                placeholder="e.g. CS-8841"
              />
            </label>
          </div>
          {error ? <p className="form-error">{error}</p> : null}
        </Modal>
      ) : null}

      {mode === "status" ? (
        <Modal
          title="Log a coordinator update"
          subtitle={obligation.description}
          onClose={() => setMode(null)}
          footer={
            <>
              <button className="btn" onClick={() => setMode(null)}>
                Cancel
              </button>
              <button className="btn btn-primary" onClick={submitStatus} disabled={busy}>
                {busy ? "Saving…" : "Save update"}
              </button>
            </>
          }
        >
          <label className="field">
            <span>New status</span>
            <select value={statusValue} onChange={(event) => setStatusValue(event.target.value)}>
              {STATUS_OPTIONS.map((status) => (
                <option key={status} value={status}>
                  {statusLabel(status)}
                </option>
              ))}
            </select>
          </label>
          {statusValue === "overdue" ? (
            <p className="form-hint">Overdue is derived automatically from the due date.</p>
          ) : null}
          {SENSITIVE.has(statusValue) ? (
            <p className="form-hint form-hint-warn">
              <b>{statusLabel(statusValue)}</b> is a sensitive closure. Add supporting evidence or an explicit
              override reason — both are recorded in the audit log.
            </p>
          ) : (
            <p className="form-hint">
              Patient-reported updates are recorded as evidence but keep the loop open until verified.
            </p>
          )}
          {isMedRec && statusValue === "verified" ? (
            <p className="form-hint form-hint-warn">
              Medication reconciliation closes only with a clinician's attestation. Describe what you compared and what changed.
            </p>
          ) : null}
          <label className="field">
            <span>{isMedRec && statusValue === "verified" ? "Clinician attestation" : "Evidence summary"}</span>
            <textarea
              rows={3}
              value={summary}
              onChange={(event) => setSummary(event.target.value)}
              placeholder={
                isMedRec && statusValue === "verified"
                  ? "e.g. Compared the discharge list with the home list; stopped duplicate furosemide"
                  : "What supports this update?"
              }
            />
          </label>
          {isReferral && statusValue === "verified" ? (
            <label className="field">
              <span>Consult note reference</span>
              <input
                id={`consult-${obligation.id}`}
                value={consultNoteRef}
                onChange={(event) => setConsultNoteRef(event.target.value)}
                placeholder={obligation.consult_note_ref || "Fax id, letter date or EHR document id"}
              />
              <small className="form-hint">A referral closes when the specialist's note is back. Without it, give an override reason.</small>
            </label>
          ) : null}
          <label className="field">
            <span>Coordinator override reason</span>
            <textarea
              rows={2}
              value={overrideReason}
              onChange={(event) => setOverrideReason(event.target.value)}
              placeholder="Only if using professional judgement without new evidence"
            />
          </label>
          <label className="field">
            <span>Source reference (optional)</span>
            <input value={sourceRef} onChange={(event) => setSourceRef(event.target.value)} placeholder="Clinic record" />
          </label>
          {error ? <p className="form-error">{error}</p> : null}
        </Modal>
      ) : null}
    </article>
  );
}
