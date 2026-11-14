import { useState } from "react";
import { api } from "../api";
import { useAuth } from "../auth";
import { useApp } from "../state";
import type { ContactOutcome, TcmState } from "../types";
import { cx } from "../ui";
import { Modal } from "./Modal";

export const OUTCOMES: { id: ContactOutcome; label: string }[] = [
  { id: "reached", label: "Reached the patient" },
  { id: "reached_caregiver", label: "Reached the caregiver" },
  { id: "voicemail", label: "Left a voicemail" },
  { id: "no_answer", label: "No answer" },
  { id: "wrong_number", label: "Wrong number" },
  { id: "declined", label: "Patient declined" },
  { id: "message_sent", label: "Message sent (text, email or portal)" },
];
export const OUTCOME_LABEL = Object.fromEntries(OUTCOMES.map((o) => [o.id, o.label])) as Record<ContactOutcome, string>;

/**
 * "Log update": record a contact attempt for the TCM clock. One button, one
 * dialog. Used from the patient page header and the care clock.
 */
export function LogContactButton({
  patientId,
  onChanged,
  className = "btn",
  label = "Log update",
}: {
  patientId: string;
  onChanged: () => void | Promise<void>;
  className?: string;
  label?: string;
}) {
  const { notify } = useApp();
  const [logging, setLogging] = useState(false);
  const [outcome, setOutcome] = useState<ContactOutcome>("reached");
  const [channel, setChannel] = useState("Voice call");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

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

  return (
    <>
      <button
        type="button"
        className={className}
        onClick={() => {
          setError(null);
          setNote("");
          setLogging(true);
        }}
      >
        {label}
      </button>
      {logging ? (
        <Modal
          title="Log a contact attempt"
          subtitle="Transitional care needs an interactive contact within 2 business days, or at least two documented attempts."
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
    </>
  );
}

/** Clinicians can move the face-to-face window between 7 and 14 days. */
export function VisitWindowControl({ patientId, tcm, onChanged }: { patientId: string; tcm: TcmState; onChanged: () => void | Promise<void> }) {
  const { user } = useAuth();
  const { notify } = useApp();
  if (user?.role !== "clinician") return null;

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
    <span className="tcm-window">
      <span className="muted">Visit window</span>
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
  );
}
