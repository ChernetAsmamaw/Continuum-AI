import { useState } from "react";
import { api } from "../api";
import { useAuth } from "../auth";
import { useApp } from "../state";
import type { ContactOutcome, TcmState } from "../types";
import { cx, formatDate, formatDateTime } from "../ui";
import { Modal } from "./Modal";

const OUTCOMES: { id: ContactOutcome; label: string }[] = [
  { id: "reached", label: "Reached the patient" },
  { id: "reached_caregiver", label: "Reached the caregiver" },
  { id: "voicemail", label: "Left a voicemail" },
  { id: "no_answer", label: "No answer" },
  { id: "wrong_number", label: "Wrong number" },
  { id: "declined", label: "Patient declined" },
  { id: "message_sent", label: "Message sent (text, email or portal)" },
];
const OUTCOME_LABEL = Object.fromEntries(OUTCOMES.map((o) => [o.id, o.label])) as Record<ContactOutcome, string>;

const CONTACT_COPY: Record<NonNullable<TcmState["contact_status"]>, { label: string; tone: string }> = {
  met: { label: "Contact made", tone: "good" },
  attempts_documented: { label: "2 attempts documented", tone: "good" },
  due_today: { label: "Due today", tone: "warn" },
  open: { label: "Open", tone: "neutral" },
  missed: { label: "Window missed", tone: "danger" },
};
const VISIT_COPY: Record<NonNullable<TcmState["visit_status"]>, { label: string; tone: string }> = {
  met: { label: "Visit done", tone: "good" },
  booked: { label: "Booked in window", tone: "good" },
  booked_late: { label: "Booked after the window", tone: "warn" },
  not_booked: { label: "Not booked", tone: "warn" },
  missed: { label: "Window missed", tone: "danger" },
};

function daysLeft(iso: string | undefined, today: string | undefined): string {
  if (!iso) return "";
  const end = new Date(`${iso}T12:00:00`).getTime();
  const now = today ? new Date(`${today}T12:00:00`).getTime() : Date.now();
  const days = Math.round((end - now) / 86_400_000);
  if (days > 1) return `${days} days left`;
  if (days === 1) return "1 day left";
  if (days === 0) return "today";
  return `${-days} day${days === -1 ? "" : "s"} late`;
}

/**
 * The Medicare TCM clock for the active episode: interactive contact within
 * 2 business days, the face-to-face visit within 7 or 14 days, medication
 * reconciliation by the visit.
 */
export function TcmPanel({ patientId, tcm, today, onChanged }: { patientId: string; tcm: TcmState; today?: string; onChanged: () => void | Promise<void> }) {
  const { user } = useAuth();
  const { notify } = useApp();
  const [logging, setLogging] = useState(false);
  const [outcome, setOutcome] = useState<ContactOutcome>("reached");
  const [channel, setChannel] = useState("Voice call");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (!tcm.eligible) {
    return (
      <section className="tcm tcm-muted" aria-label="Transitional care">
        <strong>No TCM clock</strong>
        <span>{tcm.reason}</span>
      </section>
    );
  }

  const contact = CONTACT_COPY[tcm.contact_status || "open"];
  const visit = VISIT_COPY[tcm.visit_status || "not_booked"];
  const isClinician = user?.role === "clinician";

  const save = async () => {
    setBusy(true);
    setError(null);
    try {
      await api.logContact(patientId, { outcome, channel, note: note.trim() || undefined });
      setLogging(false);
      notify(`Logged: ${OUTCOME_LABEL[outcome]}.`);
      await onChanged();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not log the attempt.");
    } finally {
      setBusy(false);
    }
  };

  const setWindow = async (days: 7 | 14 | null) => {
    try {
      await api.setVisitWindow(patientId, days);
      notify(days ? `Visit window set to ${days} days.` : "Visit window follows risk again.");
      await onChanged();
    } catch (err) {
      notify(err instanceof Error ? err.message : "Could not change the visit window.");
    }
  };

  return (
    <section className="tcm" aria-label="Transitional care clock">
      <div className="tcm-head">
        <div>
          <span className="tcm-eyebrow">Transitional care · CPT {tcm.code}</span>
          <strong>Day {tcm.days_since_discharge} since discharge on {formatDate(tcm.discharge_date)}</strong>
        </div>
        <button type="button" className="btn btn-small btn-primary" onClick={() => { setError(null); setNote(""); setLogging(true); }}>
          Log contact attempt
        </button>
      </div>
      <div className="tcm-grid">
        <div className={cx("tcm-tile", `tcm-${contact.tone}`)}>
          <span className="tcm-label">Interactive contact by {formatDate(tcm.contact_due)}</span>
          <b>{contact.label}</b>
          <small>
            {tcm.contact_status === "met"
              ? `Reached ${formatDateTime(tcm.contact_made_at)}`
              : `${tcm.contact_attempts} of ${tcm.contact_required_attempts} attempts · ${daysLeft(tcm.contact_due, today)}`}
          </small>
        </div>
        <div className={cx("tcm-tile", `tcm-${visit.tone}`)}>
          <span className="tcm-label">Face-to-face visit by {formatDate(tcm.visit_due)}</span>
          <b>{visit.label}</b>
          <small>
            {tcm.visit_date ? `Visit ${formatDate(tcm.visit_date)} · ` : ""}
            {tcm.visit_window_days}-day window ({tcm.visit_window_source === "clinician" ? "set by clinician" : "from risk"})
            {tcm.visit_status === "met" ? "" : ` · ${daysLeft(tcm.visit_due, today)}`}
          </small>
          {isClinician ? (
            <span className="tcm-window">
              <button type="button" className={cx("chip chip-button", tcm.visit_window_days === 7 && "chip-active")} onClick={() => setWindow(7)}>
                7 days
              </button>
              <button type="button" className={cx("chip chip-button", tcm.visit_window_days === 14 && "chip-active")} onClick={() => setWindow(14)}>
                14 days
              </button>
              {tcm.visit_window_source === "clinician" ? (
                <button type="button" className="link-button" onClick={() => setWindow(null)}>
                  Use risk
                </button>
              ) : null}
            </span>
          ) : null}
        </div>
        <div className="tcm-tile tcm-neutral">
          <span className="tcm-label">Medication reconciliation by</span>
          <b>{formatDate(tcm.med_rec_due)}</b>
          <small>Due no later than the face-to-face visit</small>
        </div>
      </div>
      {tcm.contacts && tcm.contacts.length ? (
        <ol className="tcm-attempts" aria-label="Contact attempts">
          {tcm.contacts.map((c) => (
            <li key={c.id}>
              <span className={cx("tcm-dot", c.interactive && "tcm-dot-good")} aria-hidden="true" />
              <span>
                {formatDateTime(c.at)} · {c.channel} · <b>{OUTCOME_LABEL[c.outcome] || c.outcome}</b>
                {c.note ? ` · ${c.note}` : ""}
              </span>
              <small className="muted">{c.actor}</small>
            </li>
          ))}
        </ol>
      ) : null}

      {logging ? (
        <Modal
          title="Log contact attempt"
          subtitle="TCM needs an interactive contact within 2 business days, or at least two documented attempts."
          onClose={() => (busy ? undefined : setLogging(false))}
          footer={
            <>
              <button className="btn" onClick={() => setLogging(false)} disabled={busy}>
                Cancel
              </button>
              <button className="btn btn-primary" onClick={save} disabled={busy}>
                {busy ? "Saving…" : "Log attempt"}
              </button>
            </>
          }
        >
          <fieldset className="tcm-outcomes">
            <legend>Outcome</legend>
            {OUTCOMES.map((o) => (
              <label key={o.id} className="consent-line">
                <input type="radio" name="tcm-outcome" id={`tcm-outcome-${o.id}`} checked={outcome === o.id} onChange={() => setOutcome(o.id)} />
                <span>{o.label}</span>
              </label>
            ))}
          </fieldset>
          <label className="field">
            <span>Channel</span>
            <select id="tcm-channel" value={channel} onChange={(event) => setChannel(event.target.value)}>
              <option>Voice call</option>
              <option>Text</option>
              <option>Email</option>
              <option>Portal</option>
              <option>In person</option>
            </select>
          </label>
          <label className="field">
            <span>Note (optional)</span>
            <input id="tcm-note" value={note} onChange={(event) => setNote(event.target.value)} placeholder="e.g. Spoke to daughter, call back Tuesday" />
          </label>
          {error ? <p className="form-error">{error}</p> : null}
        </Modal>
      ) : null}
    </section>
  );
}
