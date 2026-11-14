import { useEffect, useState, type FormEvent } from "react";
import { useParams } from "react-router-dom";
import { api } from "../api";
import type { PortalFollowUp } from "../types";
import { formatDate } from "../ui";

type Answer = { status: "done" | "booked" | "not_yet" | "need_help"; date?: string };

const CHOICES: { id: Answer["status"]; label: string }[] = [
  { id: "done", label: "Done" },
  { id: "booked", label: "Booked" },
  { id: "not_yet", label: "Not booked yet" },
  { id: "need_help", label: "I need help" },
];

/**
 * The patient's check-in page, reached from a text or email link. The link
 * plus date of birth identify the patient; nothing shows before both match.
 */
export function CheckinPage() {
  const { token = "" } = useParams();
  const [stage, setStage] = useState<"loading" | "verify" | "form" | "done" | "error">("loading");
  const [name, setName] = useState("");
  const [verifyWith, setVerifyWith] = useState<"birth_date" | "phone_last4">("birth_date");
  const [answer, setAnswer] = useState("");
  const [session, setSession] = useState("");
  const [items, setItems] = useState<PortalFollowUp[]>([]);
  const [answers, setAnswers] = useState<Record<string, Answer>>({});
  const [message, setMessage] = useState("");
  const [callback, setCallback] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [thanks, setThanks] = useState("");

  useEffect(() => {
    api
      .portalOpen(token)
      .then((info) => {
        setName(info.first_name);
        setVerifyWith(info.verify_with);
        setStage("verify");
      })
      .catch((err) => {
        setError(err instanceof Error ? err.message : "This link is not valid.");
        setStage("error");
      });
  }, [token]);

  const verify = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const result = await api.portalVerify(token, answer.trim());
      setSession(result.session);
      setItems(result.follow_ups);
      setStage("form");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Those details don't match our records.");
    } finally {
      setBusy(false);
    }
  };

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const updates = Object.entries(answers).map(([obligation_id, a]) => ({ obligation_id, status: a.status, date: a.date || undefined }));
      const result = await api.portalSubmit({ session, updates, message: message.trim() || undefined, callback });
      setThanks(result.message);
      setStage("done");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not send your update. Please try again.");
    } finally {
      setBusy(false);
    }
  };

  const set = (id: string, patch: Partial<Answer>) =>
    setAnswers((current) => ({ ...current, [id]: { ...(current[id] || { status: "done" }), ...patch } as Answer }));

  return (
    <main className="checkin-page">
      <div className="checkin-card">
        <p className="checkin-kicker">Your care team · after your hospital stay</p>
        {stage === "loading" ? <p>Loading…</p> : null}
        {stage === "error" ? (
          <>
            <h1>This link isn't working</h1>
            <p>{error}</p>
          </>
        ) : null}
        {stage === "verify" ? (
          <form onSubmit={verify} className="checkin-form">
            <h1>Hi {name}</h1>
            <p>To keep your information private, please confirm {verifyWith === "birth_date" ? "your date of birth" : "the last 4 digits of your phone number"}.</p>
            <label className="checkin-field">
              <span>{verifyWith === "birth_date" ? "Date of birth" : "Last 4 digits of your phone"}</span>
              <input
                id="checkin-answer"
                type={verifyWith === "birth_date" ? "date" : "text"}
                inputMode={verifyWith === "birth_date" ? undefined : "numeric"}
                value={answer}
                onChange={(e) => setAnswer(e.target.value)}
                required
              />
            </label>
            {error ? <p className="checkin-error">{error}</p> : null}
            <button type="submit" className="checkin-button" disabled={busy || !answer}>
              {busy ? "Checking…" : "Continue"}
            </button>
          </form>
        ) : null}
        {stage === "form" ? (
          <form onSubmit={submit} className="checkin-form">
            <h1>How are your follow-ups going?</h1>
            <p>Tell us where each one stands. We'll confirm with the clinic, so don't worry about exact details.</p>
            {items.length === 0 ? <p>You have no open follow-ups right now.</p> : null}
            {items.map((item) => {
              const current = answers[item.id];
              return (
                <fieldset key={item.id} className="checkin-item">
                  <legend>{item.description}</legend>
                  {item.confirmed_date || item.due_date ? (
                    <small>{item.confirmed_date ? `Booked for ${formatDate(item.confirmed_date)}` : `Due by ${formatDate(item.due_date)}`}</small>
                  ) : null}
                  <div className="checkin-choices" role="radiogroup" aria-label={item.description}>
                    {CHOICES.map((choice) => (
                      <label key={choice.id} className={current?.status === choice.id ? "checkin-choice checkin-choice-on" : "checkin-choice"}>
                        <input type="radio" name={`c-${item.id}`} id={`c-${item.id}-${choice.id}`} checked={current?.status === choice.id} onChange={() => set(item.id, { status: choice.id })} />
                        {choice.label}
                      </label>
                    ))}
                  </div>
                  {current?.status === "booked" || current?.status === "done" ? (
                    <label className="checkin-field">
                      <span>{current.status === "booked" ? "Appointment date" : "When was it? (optional)"}</span>
                      <input type="date" id={`d-${item.id}`} value={current.date || ""} onChange={(e) => set(item.id, { date: e.target.value })} />
                    </label>
                  ) : null}
                </fieldset>
              );
            })}
            <label className="checkin-field">
              <span>Anything else you want your care team to know?</span>
              <textarea id="checkin-message" rows={4} value={message} onChange={(e) => setMessage(e.target.value)} />
            </label>
            <label className="checkin-check">
              <input id="checkin-callback" type="checkbox" checked={callback} onChange={(e) => setCallback(e.target.checked)} />
              Please call me back
            </label>
            <p className="checkin-note">For medication or symptom questions, a clinician will follow up. If this is an emergency, call 911.</p>
            {error ? <p className="checkin-error">{error}</p> : null}
            <button type="submit" className="checkin-button" disabled={busy}>
              {busy ? "Sending…" : "Send to my care team"}
            </button>
          </form>
        ) : null}
        {stage === "done" ? (
          <>
            <h1>Thank you</h1>
            <p>{thanks}</p>
          </>
        ) : null}
      </div>
    </main>
  );
}
