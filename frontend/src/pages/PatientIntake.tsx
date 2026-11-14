import { useState, type FormEvent } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api, ApiError } from "../api";
import { useApp } from "../state";
import type { DuplicateCandidate, IntakeObligation } from "../types";
import { Card, ErrorBanner } from "../ui";

const EMPTY_OBLIGATION: IntakeObligation = { description: "", owner: "", due_date: "" };

export function PatientIntake() {
  const navigate = useNavigate();
  const { notify, refresh } = useApp();

  const [name, setName] = useState("");
  const [age, setAge] = useState("");
  const [birthDate, setBirthDate] = useState("");
  const [email, setEmail] = useState("");
  const [caregiverPhone, setCaregiverPhone] = useState("");
  const [caregiverRelationship, setCaregiverRelationship] = useState("");
  const [caregiverMayDiscuss, setCaregiverMayDiscuss] = useState(false);
  const [interpreter, setInterpreter] = useState(false);
  const [timezone, setTimezone] = useState(() => {
    try {
      return Intl.DateTimeFormat().resolvedOptions().timeZone || "";
    } catch {
      return "";
    }
  });
  const [duplicates, setDuplicates] = useState<DuplicateCandidate[] | null>(null);
  const [mrn, setMrn] = useState("");
  const [condition, setCondition] = useState("");
  const [risk, setRisk] = useState<"Low" | "Medium" | "High">("Medium");
  const [pronouns, setPronouns] = useState("");
  const [phone, setPhone] = useState("");
  const [caregiver, setCaregiver] = useState("");

  const [admissionDate, setAdmissionDate] = useState("");
  const [dischargeDate, setDischargeDate] = useState("");
  const [reason, setReason] = useState("");
  const [dischargeSummary, setDischargeSummary] = useState("");

  const [channel, setChannel] = useState<"Voice call" | "Text" | "Email" | "Portal">("Voice call");
  const [bestTime, setBestTime] = useState("");
  const [language, setLanguage] = useState("English");
  const [consentCalls, setConsentCalls] = useState(false);
  const [consentRecording, setConsentRecording] = useState(false);

  const [obligations, setObligations] = useState<IntakeObligation[]>([{ ...EMPTY_OBLIGATION }]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const setObligation = (index: number, patch: Partial<IntakeObligation>) => {
    setObligations((current) => current.map((item, i) => (i === index ? { ...item, ...patch } : item)));
  };

  const submit = async (event: FormEvent | null, confirmNew = false) => {
    event?.preventDefault();
    if (busy) return;
    setBusy(true);
    setError(null);
    try {
      const cleaned = obligations.filter((item) => item.description.trim().length >= 3);
      const result = await api.createPatient({
        name: name.trim(),
        age: age ? Number(age) : undefined,
        birth_date: birthDate || undefined,
        email: email.trim() || undefined,
        caregiver_phone: caregiverPhone.trim() || undefined,
        caregiver_relationship: caregiverRelationship.trim() || undefined,
        caregiver_may_discuss_care: caregiverMayDiscuss,
        interpreter_needed: interpreter,
        timezone: timezone.trim() || undefined,
        confirm_new: confirmNew,
        mrn: mrn.trim() || undefined,
        condition: condition.trim() || undefined,
        risk,
        pronouns: pronouns.trim() || undefined,
        phone: phone.trim() || undefined,
        caregiver: caregiver.trim() || undefined,
        admission_date: admissionDate || undefined,
        discharge_date: dischargeDate || undefined,
        reason: reason.trim() || undefined,
        discharge_summary: dischargeSummary.trim() || undefined,
        channel,
        best_time: bestTime.trim() || undefined,
        language: language.trim() || "English",
        consent_to_automated_calls: consentCalls,
        consent_to_recording: consentRecording,
        obligations: cleaned.map((item) => ({
          description: item.description.trim(),
          owner: item.owner?.trim() || undefined,
          due_date: item.due_date || undefined,
        })),
      });
      await refresh();
      notify(`Patient ${result.patient.name} created with ${result.obligations.length} follow-up(s).`);
      navigate(`/patients/${result.patient.id}`);
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) {
        const data = err.data as { candidates?: DuplicateCandidate[] } | undefined;
        setDuplicates(data?.candidates ?? []);
        window.scrollTo({ top: 0, behavior: "smooth" });
        return;
      }
      setError(err instanceof Error ? err.message : "Intake failed. Try again.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="stack">
      <header className="dq-head">
        <div>
          <h1>Add a discharge</h1>
          <p>
            Most discharges arrive on their own from the hospital record. Use this for one that didn't, or for a patient from another
            hospital. Only the name is required.
          </p>
        </div>
      </header>

      {error ? <ErrorBanner message={error} /> : null}

      {duplicates ? (
        <div className="banner banner-warn duplicate-banner" role="alert">
          <div>
            <strong>This patient may already be registered.</strong>
            <p>Open the existing record instead of creating a second one. Two records split the history and the follow-ups.</p>
            <ul className="duplicate-list">
              {duplicates.map((candidate) => (
                <li key={candidate.id}>
                  <Link to={`/patients/${candidate.id}`}>{candidate.name}</Link>
                  <span className="muted">
                    {candidate.birth_date ? ` · born ${candidate.birth_date}` : ""}
                    {candidate.mrn ? ` · MRN ${candidate.mrn}` : ""} · {candidate.match_reasons.join(", ")}
                  </span>
                </li>
              ))}
            </ul>
          </div>
          <div className="actions">
            <button type="button" className="btn" onClick={() => setDuplicates(null)}>
              Edit details
            </button>
            <button type="button" className="btn btn-primary" onClick={() => void submit(null, true)} disabled={busy}>
              It’s a different person, create
            </button>
          </div>
        </div>
      ) : null}

      <form onSubmit={submit} noValidate className="in-form">
        <section className="in-step">
          <header>
            <span className="in-n">1</span>
            <div>
              <h2>Who is the patient?</h2>
              <p>Enough to find them again and to check it's the right person on a call.</p>
            </div>
          </header>
          <div className="in-grid">
              <label className="field in-wide">
                <span>Full name <em>required</em></span>
                <input value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. Rosa Delgado" required />
              </label>
              <label className="field">
                <span>Date of birth</span>
                <input id="intake-dob" type="date" value={birthDate} onChange={(e) => setBirthDate(e.target.value)} />
              </label>
              <label className="field">
                <span>Age, if birth date unknown</span>
                <input type="number" min={0} max={130} value={age} onChange={(e) => setAge(e.target.value)} placeholder="64" />
              </label>
              <label className="field">
                <span>MRN</span>
                <input value={mrn} onChange={(e) => setMrn(e.target.value)} placeholder="C-10482" />
              </label>
              <label className="field">
                <span>Pronouns</span>
                <input value={pronouns} onChange={(e) => setPronouns(e.target.value)} placeholder="he/him" />
              </label>
              <label className="field">
                <span>Phone</span>
                <input value={phone} onChange={(e) => setPhone(e.target.value)} placeholder="+1 416 555 0100" />
              </label>
              <label className="field">
                <span>Email</span>
                <input id="intake-email" type="email" value={email} onChange={(e) => setEmail(e.target.value)} placeholder="rosa@example.com" />
              </label>
          </div>
        </section>

        <section className="in-step">
          <header>
            <span className="in-n">2</span>
            <div>
              <h2>The hospital stay</h2>
              <p>Dates start the 30-day clock. Paste the discharge summary and the assistant finds the follow-ups in it.</p>
            </div>
          </header>
          <div className="in-grid">
              <label className="field">
                <span>Admitted</span>
                <input type="date" value={admissionDate} onChange={(e) => setAdmissionDate(e.target.value)} />
              </label>
              <label className="field">
                <span>Discharged</span>
                <input type="date" value={dischargeDate} onChange={(e) => setDischargeDate(e.target.value)} />
              </label>
              <label className="field in-wide">
                <span>Reason for the stay</span>
                <input value={reason} onChange={(e) => setReason(e.target.value)} placeholder="e.g. Decompensated heart failure" />
              </label>
              <label className="field">
                <span>Condition</span>
                <input value={condition} onChange={(e) => setCondition(e.target.value)} placeholder="Heart failure" />
              </label>
              <label className="field">
                <span>Readmission risk</span>
                <select value={risk} onChange={(e) => setRisk(e.target.value as "Low" | "Medium" | "High")}>
                  <option>Low</option>
                  <option>Medium</option>
                  <option>High</option>
                </select>
              </label>
              <label className="field in-full">
                <span>
                  Discharge summary <em>optional</em>
                </span>
                <textarea
                  rows={6}
                  value={dischargeSummary}
                  onChange={(e) => setDischargeSummary(e.target.value)}
                  placeholder="Paste the summary. For example: Plan: family physician follow-up in 7 days, bloodwork in 14 days…"
                />
                <small className="form-hint">Read by the assistant a few seconds after you save. Every follow-up it finds links back to its sentence.</small>
              </label>
          </div>
        </section>

        <section className="in-step">
          <header>
            <span className="in-n">3</span>
            <div>
              <h2>Follow-ups you already know about</h2>
              <p>Anything the summary might miss. A blank date means 7 days after discharge.</p>
            </div>
          </header>
          <div className="in-obls">
            {obligations.map((item, index) => (
              <div className="intake-obligation" key={index}>
                <input
                  className="intake-obl-description"
                  value={item.description}
                  onChange={(e) => setObligation(index, { description: e.target.value })}
                  placeholder="What needs to happen, e.g. Cardiology follow-up"
                  aria-label={`Follow-up ${index + 1} description`}
                />
                <input
                  value={item.owner || ""}
                  onChange={(e) => setObligation(index, { owner: e.target.value })}
                  placeholder="Who does it, e.g. Cardiology clinic"
                  aria-label={`Follow-up ${index + 1} owner`}
                />
                <input
                  type="date"
                  value={item.due_date || ""}
                  onChange={(e) => setObligation(index, { due_date: e.target.value })}
                  aria-label={`Follow-up ${index + 1} due date`}
                  title="Leave blank to default to 7 days after discharge"
                />
                {obligations.length > 1 ? (
                  <button
                    type="button"
                    className="intake-obl-remove"
                    onClick={() => setObligations((current) => current.filter((_, i) => i !== index))}
                    aria-label={`Remove follow-up ${index + 1}`}
                  >
                    Remove
                  </button>
                ) : null}
              </div>
            ))}
            <button type="button" className="ob-more in-add" onClick={() => setObligations((current) => [...current, { ...EMPTY_OBLIGATION }])}>
              Add another follow-up
            </button>
          </div>
        </section>

        <section className="in-step">
          <header>
            <span className="in-n">4</span>
            <div>
              <h2>How to reach them</h2>
              <p>The voice assistant only calls patients who agreed to it. Without consent, check-ins become calls for you to make.</p>
            </div>
          </header>
          <div className="in-grid">
              <label className="field">
                <span>Preferred channel</span>
                <select value={channel} onChange={(e) => setChannel(e.target.value as typeof channel)}>
                  <option>Voice call</option>
                  <option>Text</option>
                  <option>Email</option>
                  <option>Portal</option>
                </select>
              </label>
              <label className="field">
                <span>Best time</span>
                <input value={bestTime} onChange={(e) => setBestTime(e.target.value)} placeholder="Mornings, 9–11am" />
              </label>
              <label className="field">
                <span>Language</span>
                <input value={language} onChange={(e) => setLanguage(e.target.value)} />
              </label>
              <label className="field">
                <span>Time zone</span>
                <input id="intake-tz" value={timezone} onChange={(e) => setTimezone(e.target.value)} placeholder="America/Toronto" />
              </label>
              <fieldset className="in-checks in-full">
                <legend>Confirmed with the patient</legend>
                <label>
                  <input type="checkbox" checked={consentCalls} onChange={(e) => setConsentCalls(e.target.checked)} />
                  <span>Agrees to automated check-in calls</span>
                </label>
                <label>
                  <input type="checkbox" checked={consentRecording} onChange={(e) => setConsentRecording(e.target.checked)} />
                  <span>Agrees to calls being recorded</span>
                </label>
                <label>
                  <input id="intake-interpreter" type="checkbox" checked={interpreter} onChange={(e) => setInterpreter(e.target.checked)} />
                  <span>Needs an interpreter</span>
                </label>
              </fieldset>
              <label className="field">
                <span>Caregiver</span>
                <input value={caregiver} onChange={(e) => setCaregiver(e.target.value)} placeholder="Luis Delgado" />
              </label>
              <label className="field">
                <span>Relationship</span>
                <input id="intake-cg-rel" value={caregiverRelationship} onChange={(e) => setCaregiverRelationship(e.target.value)} placeholder="Son" />
              </label>
              <label className="field">
                <span>Caregiver phone</span>
                <input id="intake-cg-phone" value={caregiverPhone} onChange={(e) => setCaregiverPhone(e.target.value)} placeholder="+1 416 555 0123" />
              </label>
              <fieldset className="in-checks in-full">
                <label>
                  <input id="intake-cg-discuss" type="checkbox" checked={caregiverMayDiscuss} onChange={(e) => setCaregiverMayDiscuss(e.target.checked)} />
                  <span>The patient allows us to discuss their care with this caregiver</span>
                </label>
              </fieldset>
          </div>
        </section>

        <div className="in-bar">
          <span className="muted">{name.trim().length < 2 ? "Enter the patient's name to continue." : `Ready to add ${name.trim()}.`}</span>
          <button type="button" className="btn" onClick={() => navigate("/patients")}>
            Cancel
          </button>
          <button type="submit" className="btn btn-primary" disabled={busy || name.trim().length < 2}>
            {busy ? "Adding…" : "Add patient"}
          </button>
        </div>
      </form>
    </div>
  );
}
