import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api } from "../api";
import { callPath, elapsed, isLiveCall, useCall } from "../calls";
import { useApp } from "../state";
import type { CallBrief, CallEffect, CallEvent, CallMode, CallModes, CallSession, LedgerAction, Patient } from "../types";
import { Card, ErrorBanner, Spinner, cx, formatDate, initials, statusLabel } from "../ui";

const MODE_COPY: Record<CallMode, { title: string; detail: string }> = {
  simulated: { title: "Simulated", detail: "A scripted call built from this patient's open follow-ups. Uses the real tools. No keys needed." },
  browser: { title: "Browser", detail: "Talk to the ElevenLabs agent here. You play the patient." },
  phone: { title: "Phone", detail: "ElevenLabs dials a real number. Answer it and play the patient." },
};

function covered(item: CallBrief["agenda"][number], events: CallEvent[]): boolean {
  const tools = events.filter((e) => e.kind === "tool");
  if (item.key === "disclosure") return events.some((e) => e.kind === "transcript" && e.role === "agent");
  if (item.key === "identity") return tools.some((t) => t.tool === "identify");
  if (item.key === "safety") return tools.some((t) => t.tool === "escalate");
  return tools.some(
    (t) =>
      (t.detail as { obligation_id?: string } | undefined)?.obligation_id === item.key ||
      (t.effects || []).some((e) => e.obligation_id === item.key),
  );
}

function effectLine(e: CallEffect): { title: string; body: string; tone: string } {
  switch (e.type) {
    case "status_changed":
      return {
        title: e.title || "Follow-up",
        body: `${statusLabel(e.from || "")} → ${statusLabel(e.to || "")}${e.to === "patient_reported" ? " · needs clinic proof" : ""}`,
        tone: e.verified ? "green" : e.to === "conflict" ? "danger" : "amber",
      };
    case "conflict_recorded":
      return { title: e.title || "Follow-up", body: "Conflicting dates kept side by side for review", tone: "danger" };
    case "date_reported":
      return { title: e.title || "Follow-up", body: `Patient reported ${e.date}`, tone: "amber" };
    case "followup_created":
      return { title: e.title || "New follow-up", body: "New follow-up from the call", tone: "neutral" };
    case "approval_queued":
      return {
        title: e.title || "Approval",
        body: e.audience === "clinician" ? "Sent to the clinician queue" : "Waiting for a coordinator decision",
        tone: e.audience === "clinician" ? "danger" : "neutral",
      };
    default:
      return { title: e.title || "Change", body: e.type, tone: "neutral" };
  }
}

/** +14165550142 → +1 416 555 0142; anything else is shown as typed. */
export function formatPhone(phone: string): string {
  const d = phone.replace(/[^\d+]/g, "");
  const m = d.match(/^\+?1?(\d{3})(\d{3})(\d{4})$/);
  const note = phone.match(/\(([^)]+)\)\s*$/);
  return m ? `+1 ${m[1]} ${m[2]} ${m[3]}${note ? ` (${note[1]})` : ""}` : phone;
}

function telHref(phone: string): string | null {
  const digits = phone.replace(/[^\d+]/g, "");
  return digits.length >= 7 ? `tel:${digits}` : null;
}

/** Who you are calling, in one glance: the details a coordinator otherwise opens the record for. */
function WhoCard({ patient, brief, phone }: { patient: Patient | null; brief: CallBrief; phone?: string | null }) {
  const name = patient?.name || brief.patient_name || "Patient";
  const number = phone || brief.phone || patient?.phone;
  const tel = number ? telHref(number) : null;
  const prefs = patient?.preferences || {};
  const caregiver = patient?.caregiver_contact;
  return (
    <section className="call-who" aria-label="Who you are calling">
      <span className={cx("avatar call-who-avatar", patient?.risk === "High" && "avatar-high")}>{patient?.initials || initials(name)}</span>
      <div className="call-who-main">
        <h2>
          <Link to={`/patients/${brief.patient_id}`}>{name}</Link>
          {patient?.pronouns ? <small>{patient.pronouns}</small> : null}
        </h2>
        <p className="call-who-meta">
          {patient ? (
            <span className="nq-facts">
              <span>{patient.age} years</span>
              <span>{patient.condition}</span>
              <span className="nq-mrn">{patient.mrn}</span>
            </span>
          ) : (
            "Loading the record…"
          )}
        </p>
        <dl className="call-who-facts">
          <div>
            <dt>Verify with</dt>
            <dd>{patient?.birth_date ? `DOB ${formatDate(patient.birth_date)}` : "Phone on file"}</dd>
          </div>
          <div>
            <dt>Language</dt>
            <dd>
              {brief.language || prefs.language || "English"}
              {prefs.interpreter_needed ? ", interpreter" : ""}
            </dd>
          </div>
          <div>
            <dt>Best time</dt>
            <dd>
              {brief.best_time || prefs.best_time || "Any"}
              {prefs.timezone ? ` (${prefs.timezone})` : ""}
            </dd>
          </div>
          <div>
            <dt>Consent</dt>
            <dd>
              {brief.consent_to_automated_calls ? "Automated calls" : "No automated calls"}
              {brief.consent_to_recording ? ", recording" : ", no recording"}
            </dd>
          </div>
          {caregiver?.name || patient?.caregiver ? (
            <div>
              <dt>Caregiver</dt>
              <dd>
                {caregiver?.name || patient?.caregiver}
                {caregiver?.phone ? `, ${caregiver.phone}` : ""}
              </dd>
            </div>
          ) : null}
          {prefs.accessibility ? (
            <div>
              <dt>Accessibility</dt>
              <dd>{prefs.accessibility}</dd>
            </div>
          ) : null}
        </dl>
      </div>
      <div className="call-who-phone">
        <span className="call-who-label">Phone</span>
        {number ? (
          tel ? (
            <a className="call-who-number" href={tel}>
              {formatPhone(number)}
            </a>
          ) : (
            <span className="call-who-number">{formatPhone(number)}</span>
          )
        ) : (
          <span className="call-who-number call-who-number-missing">No number on file</span>
        )}
        {patient?.email ? <small>{patient.email}</small> : null}
      </div>
    </section>
  );
}

export function CallRoom() {
  const { patientId = "" } = useParams();
  const { refreshKey, notify } = useApp();
  const call = useCall();
  const [modes, setModes] = useState<CallModes | null>(null);
  const [brief, setBrief] = useState<CallBrief | null>(null);
  const [patient, setPatient] = useState<Patient | null>(null);
  const [recent, setRecent] = useState<CallSession | null>(null);
  const [mode, setMode] = useState<CallMode>("simulated");
  const [phone, setPhone] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [ledger, setLedger] = useState<LedgerAction | null>(null);
  const [, tick] = useState(0);
  const transcriptBox = useRef<HTMLDivElement | null>(null);
  const [picking, setPicking] = useState(false);

  // The app-wide call wins when it belongs to this patient; otherwise show the last one.
  const session = call.session && call.session.patient_id === patientId ? call.session : recent;
  const live = isLiveCall(session);
  const events = session?.events || [];

  useEffect(() => {
    let cancelled = false;
    setError(null);
    Promise.all([api.calls.modes(), api.calls.brief(patientId), api.calls.forPatient(patientId), api.journey(patientId).catch(() => null)])
      .then(([m, b, mine, journey]) => {
        if (cancelled) return;
        setModes(m);
        setBrief(b);
        setPatient(journey?.patient ?? null);
        setPhone(b.phone || "");
        if (mine.active) call.follow(mine.active);
        setRecent(mine.active || mine.recent[0] || null);
      })
      .catch((err) => setError(err instanceof Error ? err.message : "Could not load the call room"));
    return () => {
      cancelled = true;
    };
  }, [patientId]); // eslint-disable-line react-hooks/exhaustive-deps

  // Keep the "last call" view current when the provider is not following it.
  useEffect(() => {
    if (!recent || (call.session && call.session.id === recent.id)) return;
    api.calls
      .get(recent.id)
      .then((r) => setRecent(r.session))
      .catch(() => undefined);
  }, [refreshKey]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (!live) return;
    const timer = window.setInterval(() => tick((n) => n + 1), 1000);
    return () => window.clearInterval(timer);
  }, [live]);

  useEffect(() => {
    if (!session) return;
    api
      .actions({ patient_id: session.patient_id, limit: 50 })
      .then((r) => setLedger(r.actions.find((a) => a.id === session.action_id) || null))
      .catch(() => setLedger(null));
  }, [session?.id, session?.status, session?.events.length]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    // Scroll the transcript itself; scrolling the page would jump it on load.
    const box = transcriptBox.current;
    if (box) box.scrollTop = box.scrollHeight;
  }, [session?.events.length]);

  useEffect(() => {
    if (call.error) setError(call.error);
  }, [call.error]);

  const start = async () => {
    if (call.live && call.session && call.session.patient_id !== patientId) {
      setError(`You are already on a call with ${call.session.brief?.patient_name || "another patient"}. End it first.`);
      return;
    }
    setBusy(true);
    setError(null);
    call.clearError();
    try {
      await call.start(patientId, mode, { phone, agentId: modes?.agent_id, patientName: brief?.patient_name });
    } catch (err) {
      setError(err instanceof Error ? err.message : "The call could not start");
    } finally {
      setBusy(false);
    }
  };

  const end = async () => {
    if (!session) return;
    setBusy(true);
    try {
      const ended = await call.end();
      if (ended) setRecent(ended);
      notify("Call ended. What it changed is listed on the right.");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not end the call");
    } finally {
      setBusy(false);
    }
  };

  const agenda = (session?.brief || brief)?.agenda || [];
  const effects = useMemo(() => {
    if (session && !isLiveCall(session) && session.effects?.length) return session.effects;
    return events.flatMap((e) => e.effects || []);
  }, [session, events]);
  const turns = events.filter((e) => e.kind !== "system" || e.text.startsWith("Simulation error"));
  const otherCall = call.live && call.session && call.session.patient_id !== patientId ? call.session : null;

  if (!brief || !modes) {
    return error ? <ErrorBanner message={error} /> : <Spinner label="Preparing the call room" />;
  }

  return (
    <div className="call-room">
      <nav className="breadcrumb" aria-label="Breadcrumb">
        <Link to="/patients">Patients</Link>
        <span aria-hidden="true">/</span>
        <Link to={`/patients/${patientId}`}>{brief.patient_name}</Link>
      </nav>
      <div className="call-head">
        <div>
          <h1>{live ? `On a call with ${brief.patient_name?.split(" ")[0]}` : session ? "Last call" : "Start a check-in call"}</h1>
          <p className="muted">
            {live
              ? "Leaving this page does not end the call. A bar at the bottom brings you back."
              : "What the agent will raise, the conversation, and what each answer changed."}
          </p>
        </div>
        {session ? (
          <div className="call-status">
            <span
              className={cx(
                "flag",
                session.status === "live" ? "tone-red" : session.status === "failed" ? "tone-red" : session.status === "ended" ? "tone-green" : "tone-amber",
              )}
            >
              {session.status === "live" ? "Live" : session.status === "ended" ? "Ended" : session.status === "failed" ? "Failed" : "Connecting"}
            </span>
            <span className="muted">{MODE_COPY[session.mode].title} call</span>
            <span className="call-timer">{elapsed(session.started_at || session.created_at, session.ended_at)}</span>
            {live ? (
              <button className="btn btn-danger-ghost" onClick={end} disabled={busy}>
                End call
              </button>
            ) : null}
          </div>
        ) : null}
      </div>

      <WhoCard patient={patient} brief={brief} phone={session?.phone} />

      {error ? <ErrorBanner message={error} /> : null}

      {otherCall ? (
        <div className="banner">
          <div>
            <strong>You are on a call with {otherCall.brief?.patient_name || "another patient"}.</strong>
            <p>
              <Link to={callPath(otherCall)}>Return to that call</Link> or end it before starting this one.
            </p>
          </div>
        </div>
      ) : null}

      {!live && session && !picking ? (
        <div className="call-again">
          <button type="button" className="btn" onClick={() => setPicking(true)} disabled={!!otherCall}>
            Start another call
          </button>
        </div>
      ) : null}

      {!live && (!session || picking) ? (
        <Card title={session ? "Start another call" : "How should this call happen?"}>
          <div className="call-modes" role="radiogroup" aria-label="Call mode">
            {(["simulated", "browser", "phone"] as CallMode[]).map((m) => {
              const available = modes[m].available;
              return (
                <button
                  key={m}
                  role="radio"
                  aria-checked={mode === m}
                  className={cx("call-mode", mode === m && "call-mode-on", !available && "call-mode-off")}
                  onClick={() => available && setMode(m)}
                  disabled={!available}
                >
                  <strong>{MODE_COPY[m].title}</strong>
                  <span>{available ? MODE_COPY[m].detail : modes[m].why}</span>
                </button>
              );
            })}
          </div>
          {mode === "phone" ? (
            <label className="call-phone">
              Number to dial
              <input value={phone} onChange={(e) => setPhone(e.target.value)} placeholder="+1 416 555 0142" />
            </label>
          ) : null}
          <div className="call-start">
            <button className="btn btn-primary" onClick={start} disabled={busy || !brief.consent_to_automated_calls || !!otherCall}>
              {busy ? "Starting…" : `Start ${MODE_COPY[mode].title.toLowerCase()} call`}
            </button>
            {!brief.consent_to_automated_calls ? (
              <span className="muted">This patient hasn't consented to automated calls. Call them yourself.</span>
            ) : null}
          </div>
        </Card>
      ) : null}

      <div className="call-grid">
        <section className="call-col" aria-label="Call brief">
          <h2>Brief</h2>
          <p className="muted call-col-sub">What the agent will raise, and why. Built from open follow-ups and memory.</p>
          <ol className="call-agenda">
            {agenda.map((item) => {
              const done = covered(item, events);
              return (
                <li key={item.key} className={cx("call-agenda-item", done && "call-agenda-done")}>
                  <span className="call-check" aria-hidden="true">
                    {done ? "✓" : ""}
                  </span>
                  <div>
                    <strong>{item.title}</strong>
                    {item.status ? <span className="call-agenda-status">{statusLabel(item.status)}</span> : null}
                    <p>{item.why}</p>
                  </div>
                </li>
              );
            })}
          </ol>
          {(session?.brief || brief).remembered.length ? (
            <div className="call-memory">
              <h3>Remembered from earlier contact</h3>
              <ul>
                {(session?.brief || brief).remembered.map((m) => (
                  <li key={m}>{m}</li>
                ))}
              </ul>
            </div>
          ) : null}
        </section>

        <section className="call-col call-col-main" aria-label="Conversation" aria-live="polite">
          <h2>Conversation</h2>
          {turns.length === 0 ? (
            <p className="empty empty-small">
              {live ? "Connecting…" : "The conversation appears here as it happens, with each action the agent takes."}
            </p>
          ) : (
            <div className="call-transcript" ref={transcriptBox}>
              {turns.map((e) =>
                e.kind === "transcript" ? (
                  <div key={e.id} className={cx("call-turn", `call-turn-${e.role}`)}>
                    <span>{e.role === "agent" ? "Continuum" : brief.patient_name?.split(" ")[0] || "Patient"}</span>
                    <p>{e.text}</p>
                  </div>
                ) : (
                  <div key={e.id} className={cx("call-action", e.tool === "escalate" && "call-action-alert")}>
                    <span className="call-action-mark">▸</span>
                    <p>{e.text}</p>
                  </div>
                ),
              )}
            </div>
          )}
          {live && session?.mode === "browser" ? (
            <p className="muted call-hint">
              {call.voice.isSpeaking ? "Agent speaking…" : "Listening. Speak as the patient."}{" "}
              <button className="btn btn-small" onClick={() => call.voice.setMuted(!call.voice.isMuted)}>
                {call.voice.isMuted ? "Unmute" : "Mute"}
              </button>
            </p>
          ) : null}
        </section>

        <section className="call-col" aria-label="What changed">
          <h2>What changed</h2>
          <p className="muted call-col-sub">Nothing is marked done on the patient's word. Proof still has to arrive.</p>
          {effects.length === 0 ? (
            <p className="empty empty-small">No changes yet.</p>
          ) : (
            <ul className="call-effects">
              {effects.map((e, i) => {
                const line = effectLine(e);
                return (
                  <li key={`${e.type}-${e.obligation_id || e.approval_id}-${i}`} className={cx("call-effect", `call-effect-${line.tone}`)}>
                    <strong>{line.title}</strong>
                    <span>{line.body}</span>
                  </li>
                );
              })}
            </ul>
          )}
          {ledger ? (
            <div className="call-ledger">
              <h3>This call in the action ledger</h3>
              <ol>
                {ledger.history.map((h, i) => (
                  <li key={i} className={cx("call-ledger-step", `call-ledger-${h.status}`)}>
                    <strong>{h.status}</strong>
                    <span>{h.detail}</span>
                  </li>
                ))}
              </ol>
              <p className="muted">Proof expected: {ledger.proof_expected}</p>
            </div>
          ) : null}
          {session && !live && effects.length ? (
            <p className="call-next">
              Next: <Link to="/approvals">review the queued decisions</Link> · <Link to={`/patients/${patientId}`}>open the journey</Link>
            </p>
          ) : null}
        </section>
      </div>
    </div>
  );
}
