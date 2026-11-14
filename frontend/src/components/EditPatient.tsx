import { useState } from "react";
import { Link } from "react-router-dom";
import { api, ApiError } from "../api";
import { useApp } from "../state";
import type { DuplicateCandidate, Patient, PatientUpdate } from "../types";
import { Modal } from "./Modal";

type Form = {
  name: string;
  birth_date: string;
  mrn: string;
  phone: string;
  email: string;
  condition: string;
  risk: "Low" | "Medium" | "High";
  pronouns: string;
  cg_name: string;
  cg_relationship: string;
  cg_phone: string;
  cg_may_discuss: boolean;
  channel: "Voice call" | "Text" | "Email" | "Portal";
  best_time: string;
  language: string;
  interpreter: boolean;
  timezone: string;
  consent_calls: boolean;
  consent_recording: boolean;
};

function fromPatient(p: Patient): Form {
  const prefs = p.preferences || {};
  const cg = p.caregiver_contact || {};
  return {
    name: p.name || "",
    birth_date: p.birth_date || "",
    mrn: p.mrn || "",
    phone: p.phone || "",
    email: p.email || "",
    condition: p.condition || "",
    risk: (["Low", "Medium", "High"].includes(p.risk) ? p.risk : "Medium") as Form["risk"],
    pronouns: p.pronouns || "",
    cg_name: cg.name || (p.caregiver_contact ? "" : p.caregiver || ""),
    cg_relationship: cg.relationship || "",
    cg_phone: cg.phone || "",
    cg_may_discuss: cg.may_discuss_care === true,
    channel: (prefs.channel as Form["channel"]) || "Voice call",
    best_time: prefs.best_time || "",
    language: prefs.language || "English",
    interpreter: prefs.interpreter_needed === true,
    timezone: prefs.timezone || "",
    consent_calls: prefs.consent_to_automated_calls === true,
    consent_recording: prefs.consent_to_recording === true,
  };
}

/** Only what changed, so the audit entry lists exactly what the person edited. */
function diff(before: Form, after: Form): PatientUpdate {
  const out: PatientUpdate = {};
  const top = ["name", "birth_date", "mrn", "phone", "email", "condition", "pronouns"] as const;
  for (const key of top) if (after[key].trim() !== before[key]) out[key] = after[key].trim();
  if (after.risk !== before.risk) out.risk = after.risk;
  const cg: NonNullable<PatientUpdate["caregiver"]> = {};
  if (after.cg_name.trim() !== before.cg_name) cg.name = after.cg_name.trim();
  if (after.cg_relationship.trim() !== before.cg_relationship) cg.relationship = after.cg_relationship.trim();
  if (after.cg_phone.trim() !== before.cg_phone) cg.phone = after.cg_phone.trim();
  if (after.cg_may_discuss !== before.cg_may_discuss) cg.may_discuss_care = after.cg_may_discuss;
  if (Object.keys(cg).length) out.caregiver = cg;
  const prefs: NonNullable<PatientUpdate["preferences"]> = {};
  if (after.channel !== before.channel) prefs.channel = after.channel;
  if (after.best_time.trim() !== before.best_time) prefs.best_time = after.best_time.trim();
  if (after.language.trim() !== before.language) prefs.language = after.language.trim();
  if (after.interpreter !== before.interpreter) prefs.interpreter_needed = after.interpreter;
  if (after.timezone.trim() !== before.timezone) prefs.timezone = after.timezone.trim();
  if (after.consent_calls !== before.consent_calls) prefs.consent_to_automated_calls = after.consent_calls;
  if (after.consent_recording !== before.consent_recording) prefs.consent_to_recording = after.consent_recording;
  if (Object.keys(prefs).length) out.preferences = prefs;
  return out;
}

export function EditPatient({ patient, onChanged }: { patient: Patient; onChanged: () => void | Promise<void> }) {
  const { notify } = useApp();
  const [open, setOpen] = useState(false);
  const [initial, setInitial] = useState<Form>(() => fromPatient(patient));
  const [form, setForm] = useState<Form>(initial);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [clashes, setClashes] = useState<DuplicateCandidate[] | null>(null);

  const set = <K extends keyof Form>(key: K, value: Form[K]) => setForm((current) => ({ ...current, [key]: value }));

  const start = () => {
    const fresh = fromPatient(patient);
    setInitial(fresh);
    setForm(fresh);
    setError(null);
    setClashes(null);
    setOpen(true);
  };

  const close = () => (busy ? undefined : setOpen(false));

  const save = async () => {
    const changes = diff(initial, form);
    if (Object.keys(changes).length === 0) {
      setOpen(false);
      return;
    }
    if (form.name.trim().length < 2) {
      setError("Enter the patient's full name.");
      return;
    }
    setBusy(true);
    setError(null);
    setClashes(null);
    try {
      await api.updatePatient(patient.id, changes);
      setOpen(false);
      notify("Patient record updated. The change is in the audit log.");
      await onChanged();
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) {
        setClashes(((err.data as { candidates?: DuplicateCandidate[] }) || {}).candidates || []);
        setError(err.message);
      } else {
        setError(err instanceof Error ? err.message : "Could not save the changes.");
      }
    } finally {
      setBusy(false);
    }
  };

  const text = (key: keyof Form, label: string, props: { type?: string; placeholder?: string } = {}) => (
    <label className="field">
      <span>{label}</span>
      <input
        id={`edit-${key}`}
        type={props.type || "text"}
        value={form[key] as string}
        placeholder={props.placeholder}
        onChange={(event) => set(key, event.target.value as never)}
      />
    </label>
  );

  const check = (key: keyof Form, label: string) => (
    <label className="consent-line">
      <input id={`edit-${key}`} type="checkbox" checked={form[key] as boolean} onChange={(event) => set(key, event.target.checked as never)} />
      <span>{label}</span>
    </label>
  );

  return (
    <>
      <button type="button" className="btn" onClick={start}>
        Edit details
      </button>
      {open ? (
        <Modal
          title="Edit patient"
          subtitle="Every change is recorded in the audit log with its old and new value."
          onClose={close}
          wide
          footer={
            <>
              <button className="btn" onClick={close} disabled={busy}>
                Cancel
              </button>
              <button className="btn btn-primary" onClick={save} disabled={busy}>
                {busy ? "Saving…" : "Save changes"}
              </button>
            </>
          }
        >
          <div className="edit-patient">
            <fieldset>
              <legend>Identity</legend>
              <div className="intake-grid">
                {text("name", "Full name")}
                {text("birth_date", "Date of birth", { type: "date" })}
                {text("mrn", "MRN")}
                {text("pronouns", "Pronouns")}
                {text("condition", "Condition")}
                <label className="field">
                  <span>Risk</span>
                  <select id="edit-risk" value={form.risk} onChange={(event) => set("risk", event.target.value as Form["risk"])}>
                    <option>Low</option>
                    <option>Medium</option>
                    <option>High</option>
                  </select>
                </label>
              </div>
            </fieldset>
            <fieldset>
              <legend>Contact</legend>
              <div className="intake-grid">
                {text("phone", "Phone", { placeholder: "+1 416 555 0100" })}
                {text("email", "Email", { type: "email" })}
                <label className="field">
                  <span>Preferred channel</span>
                  <select id="edit-channel" value={form.channel} onChange={(event) => set("channel", event.target.value as Form["channel"])}>
                    <option>Voice call</option>
                    <option>Text</option>
                    <option>Email</option>
                    <option>Portal</option>
                  </select>
                </label>
                {text("best_time", "Best time", { placeholder: "Mornings, 9–11am" })}
                {text("language", "Language")}
                {text("timezone", "Time zone", { placeholder: "America/Toronto" })}
              </div>
              <div className="stack stack-tight">
                {check("interpreter", "Needs an interpreter")}
                {check("consent_calls", "Consent to automated check-in calls and messages")}
                {check("consent_recording", "Consent to call recording")}
              </div>
            </fieldset>
            <fieldset>
              <legend>Caregiver</legend>
              <div className="intake-grid">
                {text("cg_name", "Name")}
                {text("cg_relationship", "Relationship", { placeholder: "Daughter" })}
                {text("cg_phone", "Phone")}
              </div>
              {check("cg_may_discuss", "Patient allows the care team to discuss their care with this caregiver")}
            </fieldset>
          </div>
          {error ? <p className="form-error">{error}</p> : null}
          {clashes && clashes.length ? (
            <ul className="duplicate-list">
              {clashes.map((candidate) => (
                <li key={candidate.id}>
                  <Link to={`/patients/${candidate.id}`}>{candidate.name}</Link>
                  <span className="muted"> · {candidate.match_reasons.join(", ")}</span>
                </li>
              ))}
            </ul>
          ) : null}
        </Modal>
      ) : null}
    </>
  );
}
