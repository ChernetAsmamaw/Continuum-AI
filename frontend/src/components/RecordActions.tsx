import { useState } from "react";
import { api } from "../api";
import { useApp } from "../state";
import { Modal } from "./Modal";

type Mode = "follow-up" | "document" | null;

function plusDays(iso: string | undefined, days: number): string {
  const base = iso ? new Date(`${iso}T12:00:00`) : new Date();
  base.setDate(base.getDate() + days);
  return base.toISOString().slice(0, 10);
}

/**
 * Journey-page actions that keep a record current after intake: add a
 * follow-up the discharge missed, or add a document (consult note, new
 * discharge summary) that runs through the same extraction as intake.
 */
export function RecordActions({
  patientId,
  dischargeDate,
  onChanged,
}: {
  patientId: string;
  dischargeDate?: string;
  onChanged: () => void | Promise<void>;
}) {
  const { notify } = useApp();
  const [mode, setMode] = useState<Mode>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [description, setDescription] = useState("");
  const [owner, setOwner] = useState("");
  const [dueDate, setDueDate] = useState("");

  const [title, setTitle] = useState("Consult note");
  const [text, setText] = useState("");
  const [sourceRef, setSourceRef] = useState("");

  const open = (next: Mode) => {
    setError(null);
    setDescription("");
    setOwner("");
    setDueDate(plusDays(dischargeDate, 7));
    setTitle("Consult note");
    setText("");
    setSourceRef("");
    setMode(next);
  };

  const close = () => (busy ? undefined : setMode(null));

  const saveFollowUp = async () => {
    if (description.trim().length < 3) {
      setError("Describe the follow-up in a few words, for example \"Podiatry review\".");
      return;
    }
    if (!dueDate) {
      setError("Set a due date so Continuum can chase it if it slips.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await api.createObligation({
        patient_id: patientId,
        description: description.trim(),
        owner: owner.trim() || undefined,
        due_date: dueDate,
      });
      setMode(null);
      notify(`Follow-up added: ${description.trim()}.`);
      await onChanged();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not add the follow-up.");
    } finally {
      setBusy(false);
    }
  };

  const saveDocument = async () => {
    if (text.trim().length < 10) {
      setError("Paste the document text. It needs at least a sentence for extraction.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await api.addDocument(patientId, {
        title: title.trim() || "Document",
        text: text.trim(),
        source_ref: sourceRef.trim() || undefined,
      });
      setMode(null);
      notify(`${title.trim() || "Document"} added. New follow-ups, if any, are on the list.`);
      await onChanged();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not add the document.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <button type="button" className="btn" onClick={() => open("follow-up")}>
        + Follow-up
      </button>
      <button type="button" className="btn" onClick={() => open("document")}>
        + Document
      </button>

      {mode === "follow-up" ? (
        <Modal
          title="Add follow-up"
          subtitle="Track something the discharge missed or a new request from the care team"
          onClose={close}
          footer={
            <>
              <button className="btn" onClick={close} disabled={busy}>
                Cancel
              </button>
              <button className="btn btn-primary" onClick={saveFollowUp} disabled={busy}>
                {busy ? "Adding…" : "Add follow-up"}
              </button>
            </>
          }
        >
          <label className="field">
            <span>What needs to happen</span>
            <input
              id="record-followup-description"
              value={description}
              onChange={(event) => setDescription(event.target.value)}
              placeholder="e.g. Podiatry review for foot ulcer"
              autoFocus
            />
          </label>
          <label className="field">
            <span>Owner</span>
            <input
              id="record-followup-owner"
              value={owner}
              onChange={(event) => setOwner(event.target.value)}
              placeholder="e.g. Podiatry clinic (defaults to Care team)"
            />
          </label>
          <label className="field">
            <span>Due date</span>
            <input id="record-followup-due" type="date" value={dueDate} onChange={(event) => setDueDate(event.target.value)} />
          </label>
          {error ? <p className="form-error">{error}</p> : null}
        </Modal>
      ) : null}

      {mode === "document" ? (
        <Modal
          title="Add document"
          subtitle="Consult notes, letters or a new discharge summary. Continuum extracts follow-ups and links each to its sentence."
          onClose={close}
          wide
          footer={
            <>
              <button className="btn" onClick={close} disabled={busy}>
                Cancel
              </button>
              <button className="btn btn-primary" onClick={saveDocument} disabled={busy}>
                {busy ? "Reading document…" : "Add document"}
              </button>
            </>
          }
        >
          <label className="field">
            <span>Title</span>
            <input id="record-doc-title" value={title} onChange={(event) => setTitle(event.target.value)} />
          </label>
          <label className="field">
            <span>Document text</span>
            <textarea
              id="record-doc-text"
              rows={10}
              value={text}
              onChange={(event) => setText(event.target.value)}
              placeholder="Paste the note. For example: Seen in cardiology clinic. Plan: repeat echo in 3 months, BNP in 2 weeks…"
              autoFocus
            />
          </label>
          <label className="field">
            <span>Source reference (optional)</span>
            <input
              id="record-doc-source"
              value={sourceRef}
              onChange={(event) => setSourceRef(event.target.value)}
              placeholder="Fax number, letter date or EHR document id"
            />
          </label>
          {error ? <p className="form-error">{error}</p> : null}
        </Modal>
      ) : null}
    </>
  );
}
