import { useState } from "react";
import { api, ApiError } from "../api";
import { useApp } from "../state";
import type { Episode } from "../types";
import { Modal } from "./Modal";

const CLOSE_REASONS = [
  { id: "window_complete", label: "Follow-up window complete" },
  { id: "transferred", label: "Transferred to another facility or team" },
  { id: "declined_follow_up", label: "Patient declined further follow-up" },
  { id: "died", label: "Patient died" },
  { id: "other", label: "Other" },
] as const;

type Mode = "readmit" | "close" | null;

/** Readmission (a new stay, its own TCM clock) and closing an episode. */
export function EpisodeActions({ patientId, episode, onChanged }: { patientId: string; episode?: Episode | null; onChanged: () => void | Promise<void> }) {
  const { notify } = useApp();
  const [mode, setMode] = useState<Mode>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [admission, setAdmission] = useState("");
  const [discharge, setDischarge] = useState("");
  const [reason, setReason] = useState("");
  const [disposition, setDisposition] = useState("Home");
  const [summary, setSummary] = useState("");

  const [closeReason, setCloseReason] = useState<(typeof CLOSE_REASONS)[number]["id"]>("window_complete");
  const [note, setNote] = useState("");
  const [openItems, setOpenItems] = useState<{ id: string; description: string; status: string }[] | null>(null);
  const [cancelOpen, setCancelOpen] = useState(false);

  const start = (next: Mode) => {
    setError(null);
    setOpenItems(null);
    setCancelOpen(false);
    setMode(next);
  };
  const close = () => (busy ? undefined : setMode(null));

  const readmit = async () => {
    if (!discharge) {
      setError("Enter the discharge date of the new stay.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const result = await api.readmit(patientId, {
        admission_date: admission || undefined,
        discharge_date: discharge,
        reason: reason.trim() || undefined,
        disposition: disposition || undefined,
        discharge_summary: summary.trim() || undefined,
      });
      setMode(null);
      notify(`New episode opened. ${result.carried_over.length} open follow-up(s) carried over for review.`);
      await onChanged();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not record the readmission.");
    } finally {
      setBusy(false);
    }
  };

  const closeEpisode = async () => {
    setBusy(true);
    setError(null);
    try {
      const result = await api.closeEpisode(patientId, { reason: closeReason, note: note.trim() || undefined, close_open: cancelOpen });
      setMode(null);
      notify(`Episode closed${result.cancelled.length ? `; ${result.cancelled.length} follow-up(s) ended with it` : ""}.`);
      await onChanged();
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) {
        setOpenItems(((err.data as { candidates?: { id: string; description: string; status: string }[] }) || {}).candidates || []);
        setError(err.message);
      } else {
        setError(err instanceof Error ? err.message : "Could not close the episode.");
      }
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="episode-actions">
      <button type="button" className="link-button" onClick={() => start("readmit")}>
        Record readmission
      </button>
      {episode ? (
        <button type="button" className="link-button" onClick={() => start("close")}>
          Close episode
        </button>
      ) : null}

      {mode === "readmit" ? (
        <Modal
          title="Record readmission"
          subtitle="Opens a new episode with its own TCM clock. Open follow-ups from this stay carry over, marked for review."
          onClose={close}
          wide
          footer={
            <>
              <button className="btn" onClick={close} disabled={busy}>
                Cancel
              </button>
              <button className="btn btn-primary" onClick={readmit} disabled={busy}>
                {busy ? "Opening…" : "Open new episode"}
              </button>
            </>
          }
        >
          <div className="intake-grid">
            <label className="field">
              <span>Admission date</span>
              <input id="readmit-admission" type="date" value={admission} onChange={(e) => setAdmission(e.target.value)} />
            </label>
            <label className="field">
              <span>Discharge date</span>
              <input id="readmit-discharge" type="date" value={discharge} onChange={(e) => setDischarge(e.target.value)} />
            </label>
            <label className="field">
              <span>Reason</span>
              <input id="readmit-reason" value={reason} onChange={(e) => setReason(e.target.value)} placeholder="Heart failure exacerbation" />
            </label>
            <label className="field">
              <span>Discharged to</span>
              <select id="readmit-disposition" value={disposition} onChange={(e) => setDisposition(e.target.value)}>
                <option>Home</option>
                <option>Home with home care</option>
                <option>Assisted living</option>
                <option>Skilled nursing facility</option>
                <option>Inpatient rehab</option>
                <option>Hospice</option>
              </select>
            </label>
          </div>
          <label className="field">
            <span>Discharge summary (optional)</span>
            <textarea id="readmit-summary" rows={6} value={summary} onChange={(e) => setSummary(e.target.value)} placeholder="Paste the new discharge summary to extract its follow-ups" />
          </label>
          {error ? <p className="form-error">{error}</p> : null}
        </Modal>
      ) : null}

      {mode === "close" ? (
        <Modal
          title="Close episode"
          subtitle="Closing ends this stay's follow-up. Open follow-ups must be closed first, or ended with the episode and the reason below."
          onClose={close}
          footer={
            <>
              <button className="btn" onClick={close} disabled={busy}>
                Cancel
              </button>
              <button className="btn btn-primary" onClick={closeEpisode} disabled={busy || (openItems !== null && openItems.length > 0 && !cancelOpen)}>
                {busy ? "Closing…" : "Close episode"}
              </button>
            </>
          }
        >
          <label className="field">
            <span>Reason</span>
            <select id="close-reason" value={closeReason} onChange={(e) => setCloseReason(e.target.value as typeof closeReason)}>
              {CLOSE_REASONS.map((r) => (
                <option key={r.id} value={r.id}>
                  {r.label}
                </option>
              ))}
            </select>
          </label>
          <label className="field">
            <span>Note (optional)</span>
            <input id="close-note" value={note} onChange={(e) => setNote(e.target.value)} />
          </label>
          {openItems && openItems.length ? (
            <>
              <ul className="duplicate-list">
                {openItems.map((item) => (
                  <li key={item.id}>
                    {item.description} <span className="muted">· {item.status.replace(/_/g, " ")}</span>
                  </li>
                ))}
              </ul>
              <label className="consent-line">
                <input id="close-cancel-open" type="checkbox" checked={cancelOpen} onChange={(e) => setCancelOpen(e.target.checked)} />
                <span>End these with the episode (recorded as a coordinator override with the reason above)</span>
              </label>
            </>
          ) : null}
          {error ? <p className="form-error">{error}</p> : null}
        </Modal>
      ) : null}
    </div>
  );
}
