import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { ConversationProvider, useConversation } from "@elevenlabs/react";
import { api } from "../api";
import { useApp } from "../state";
import type { CallBrief, CallEffect, CallEvent, CallMode, CallModes, CallSession, LedgerAction } from "../types";
import { Card, ErrorBanner, Spinner, cx, statusLabel } from "../ui";

const MODE_COPY: Record<CallMode, { title: string; detail: string }> = {
  simulated: { title: "Simulated", detail: "A scripted call built from this patient's open follow-ups. Uses the real tools. No keys needed." },
  browser: { title: "Browser", detail: "Talk to the ElevenLabs agent here. You play the patient." },
  phone: { title: "Phone", detail: "ElevenLabs dials a real number. Answer it and play the patient." },
};

const LIVE = new Set(["preparing", "live"]);

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

function elapsed(from?: string, to?: string): string {
  if (!from) return "0:00";
  const secs = Math.max(0, Math.round(((to ? Date.parse(to) : Date.now()) - Date.parse(from)) / 1000));
  return `${Math.floor(secs / 60)}:${String(secs % 60).padStart(2, "0")}`;
}

function CallRoomInner() {
  const { patientId = "" } = useParams();
  const { refreshKey, notify } = useApp();
  const [modes, setModes] = useState<CallModes | null>(null);
  const [brief, setBrief] = useState<CallBrief | null>(null);
  const [session, setSession] = useState<CallSession | null>(null);
  const [mode, setMode] = useState<CallMode>("simulated");
  const [phone, setPhone] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [ledger, setLedger] = useState<LedgerAction | null>(null);
  const [, tick] = useState(0);
  const sessionRef = useRef<CallSession | null>(null);
  sessionRef.current = session;
  const transcriptEnd = useRef<HTMLDivElement | null>(null);

  const conversation = useConversation({
    onMessage: (event: unknown) => {
      const s = sessionRef.current;
      if (!s) return;
      const data = event as Record<string, unknown>;
      const type = String(data.type ?? "");
      if (type === "user_transcript") void api.calls.line(s.id, "patient", String(data.user_transcript ?? ""));
      else if (type === "agent_response") void api.calls.line(s.id, "agent", String(data.agent_response ?? ""));
    },
    onError: (message: string) => setError(message || "The voice session failed."),
    onDisconnect: () => {
      const s = sessionRef.current;
      if (s && s.mode === "browser" && LIVE.has(s.status)) void api.calls.end(s.id).then((r) => setSession(r.session));
    },
  });

  const loadSession = useCallback(async (id: string) => {
    const { session: s } = await api.calls.get(id);
    setSession(s);
  }, []);

  useEffect(() => {
    let cancelled = false;
    Promise.all([api.calls.modes(), api.calls.brief(patientId), api.calls.forPatient(patientId)])
      .then(([m, b, mine]) => {
        if (cancelled) return;
        setModes(m);
        setBrief(b);
        setPhone(b.phone || "");
        setSession(mine.active || mine.recent[0] || null);
      })
      .catch((err) => setError(err instanceof Error ? err.message : "Could not load the call room"));
    return () => {
      cancelled = true;
    };
  }, [patientId]);

  // Live: the change stream bumps refreshKey; poll as well in case it's off (memory mode).
  useEffect(() => {
    if (!session) return;
    void loadSession(session.id);
  }, [refreshKey]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (!session || !LIVE.has(session.status)) return;
    const t = window.setInterval(() => {
      void loadSession(session.id);
      tick((n) => n + 1);
    }, 1000);
    return () => window.clearInterval(t);
  }, [session?.id, session?.status, loadSession]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (!session) return;
    api
      .actions({ patient_id: session.patient_id, limit: 50 })
      .then((r) => setLedger(r.actions.find((a) => a.id === session.action_id) || null))
      .catch(() => setLedger(null));
  }, [session?.id, session?.status, session?.events.length]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    transcriptEnd.current?.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }, [session?.events.length]);

  const start = async () => {
    setBusy(true);
    setError(null);
    try {
      const { session: s } = await api.calls.start(patientId, mode, mode === "phone" ? phone : undefined);
      setSession(s);
      if (mode === "browser") {
        const agentId = modes?.agent_id;
        if (!agentId) throw new Error("No ElevenLabs agent configured.");
        const conversationId = await conversation.startSession({
          agentId,
          dynamicVariables: { patient_id: patientId, patient_name: brief?.patient_name || "", call_session_id: s.id },
        });
        const connectedSession = await api.calls.connected(s.id, typeof conversationId === "string" ? conversationId : null);
        setSession(connectedSession.session);
      }
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
      if (session.mode === "browser" && conversation.status === "connected") await conversation.endSession();
      const { session: s } = await api.calls.end(session.id);
      setSession(s);
      notify("Call ended. What it changed is listed on the right.");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not end the call");
    } finally {
      setBusy(false);
    }
  };

  const events = session?.events || [];
  const live = !!session && LIVE.has(session.status);
  const agenda = (session?.brief || brief)?.agenda || [];
  const effects = useMemo(() => {
    if (session && !LIVE.has(session.status) && session.effects?.length) return session.effects;
    return events.flatMap((e) => e.effects || []);
  }, [session, events]);
  const turns = events.filter((e) => e.kind !== "system" || e.text.startsWith("Simulation error"));

  if (!brief || !modes) {
    return error ? <ErrorBanner message={error} /> : <Spinner label="Preparing the call room" />;
  }

  return (
    <div className="call-room">
      <div className="call-head">
        <div>
          <p className="eyebrow">
            <Link to={`/patients/${patientId}`}>{brief.patient_name}</Link> · Call room
          </p>
          <h1>{live ? "On the call" : session ? "Last call" : "Start a check-in call"}</h1>
          <p className="muted">
            {brief.language} · best time {brief.best_time || "any"} ·{" "}
            {brief.consent_to_recording ? "consented to recording" : "no recording"}
            {brief.consent_to_automated_calls ? "" : " · no automated calls"}
          </p>
        </div>
        {session ? (
          <div className="call-status">
            <span className={cx("call-pill", `call-pill-${session.status}`)}>
              {session.status === "live" ? "● Live" : session.status}
              {" · "}
              {MODE_COPY[session.mode].title}
            </span>
            <span className="call-timer">{elapsed(session.started_at || session.created_at, session.ended_at)}</span>
            {live ? (
              <button className="btn btn-danger-ghost" onClick={end} disabled={busy}>
                End call
              </button>
            ) : null}
          </div>
        ) : null}
      </div>

      {error ? <ErrorBanner message={error} /> : null}

      {!live ? (
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
            <button className="btn btn-primary" onClick={start} disabled={busy || !brief.consent_to_automated_calls}>
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
            <div className="call-transcript">
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
              <div ref={transcriptEnd} />
            </div>
          )}
          {live && session?.mode === "browser" ? (
            <p className="muted call-hint">
              {conversation.isSpeaking ? "Agent speaking…" : "Listening. Speak as the patient."}{" "}
              <button className="btn btn-small" onClick={() => conversation.setMuted(!conversation.isMuted)}>
                {conversation.isMuted ? "Unmute" : "Mute"}
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

export function CallRoom() {
  return (
    <ConversationProvider>
      <CallRoomInner />
    </ConversationProvider>
  );
}
