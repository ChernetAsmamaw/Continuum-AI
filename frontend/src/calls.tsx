import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { Link, useLocation } from "react-router-dom";
import { ConversationProvider, useConversation } from "@elevenlabs/react";
import { api } from "./api";
import { useApp } from "./state";
import type { CallMode, CallSession } from "./types";
import { cx } from "./ui";

/**
 * One call at a time, owned by the app shell rather than the call page.
 *
 * The ElevenLabs session used to live inside the Call Room, so opening the
 * patient's record mid-call unmounted the provider and hung up. Here the
 * provider sits above the router outlet: the call keeps going while you look
 * something up, and a bar at the bottom of every page shows who you are
 * talking to and takes you back.
 */

const LIVE = new Set<CallSession["status"]>(["preparing", "live"]);

export const isLiveCall = (session: CallSession | null | undefined): boolean => !!session && LIVE.has(session.status);

export function callPath(session: CallSession): string {
  return `/patients/${session.patient_id}/call`;
}

export function elapsed(from?: string, to?: string): string {
  if (!from) return "0:00";
  const secs = Math.max(0, Math.round(((to ? Date.parse(to) : Date.now()) - Date.parse(from)) / 1000));
  return `${Math.floor(secs / 60)}:${String(secs % 60).padStart(2, "0")}`;
}

interface StartOptions {
  phone?: string;
  agentId?: string | null;
  patientName?: string;
}

interface CallState {
  /** The call this tab is following: the live one, or the one that just ended here. */
  session: CallSession | null;
  live: boolean;
  error: string | null;
  voice: {
    status: string;
    isSpeaking: boolean;
    isMuted: boolean;
    setMuted: (muted: boolean) => void;
  };
  start: (patientId: string, mode: CallMode, options?: StartOptions) => Promise<CallSession>;
  end: () => Promise<CallSession | null>;
  /** Follow a session the call room found already running (another tab, the scheduler, a phone call). */
  follow: (session: CallSession | null) => void;
  clearError: () => void;
}

const CallContext = createContext<CallState | null>(null);

function CallSessionProvider({ children }: { children: ReactNode }) {
  const { refreshKey } = useApp();
  const [session, setSession] = useState<CallSession | null>(null);
  const [error, setError] = useState<string | null>(null);
  const sessionRef = useRef<CallSession | null>(null);
  sessionRef.current = session;

  const conversation = useConversation({
    onMessage: (event: unknown) => {
      const current = sessionRef.current;
      if (!current) return;
      const data = event as Record<string, unknown>;
      const type = String(data.type ?? "");
      if (type === "user_transcript") void api.calls.line(current.id, "patient", String(data.user_transcript ?? ""));
      else if (type === "agent_response") void api.calls.line(current.id, "agent", String(data.agent_response ?? ""));
    },
    onError: (message: string) => setError(message || "The voice session failed."),
    onDisconnect: () => {
      const current = sessionRef.current;
      if (current && current.mode === "browser" && LIVE.has(current.status)) {
        void api.calls
          .end(current.id)
          .then((r) => setSession(r.session))
          .catch(() => undefined);
      }
    },
  });

  const load = useCallback(async (id: string) => {
    try {
      const { session: next } = await api.calls.get(id);
      // Ignore a late response for a call we have since stopped following.
      if (sessionRef.current?.id === id) setSession(next);
    } catch {
      /* transient; the next tick retries */
    }
  }, []);

  // Change streams bump refreshKey; also poll every second while the call is
  // live so the timer, transcript and effects move in memory mode too.
  useEffect(() => {
    if (session) void load(session.id);
  }, [refreshKey]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (!session || !LIVE.has(session.status)) return;
    const timer = window.setInterval(() => void load(session.id), 1000);
    return () => window.clearInterval(timer);
  }, [session?.id, session?.status, load]); // eslint-disable-line react-hooks/exhaustive-deps

  // Closing the tab is the one thing that still ends a browser call. Say so.
  useEffect(() => {
    if (!session || session.mode !== "browser" || !LIVE.has(session.status)) return;
    const warn = (event: BeforeUnloadEvent) => {
      event.preventDefault();
      event.returnValue = "";
    };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [session?.id, session?.mode, session?.status]);

  const start = useCallback(
    async (patientId: string, mode: CallMode, options: StartOptions = {}) => {
      setError(null);
      const { session: started } = await api.calls.start(patientId, mode, mode === "phone" ? options.phone : undefined);
      setSession(started);
      sessionRef.current = started;
      if (mode === "browser") {
        if (!options.agentId) throw new Error("No ElevenLabs agent configured.");
        const conversationId = await conversation.startSession({
          agentId: options.agentId,
          dynamicVariables: { patient_id: patientId, patient_name: options.patientName || "", call_session_id: started.id },
        });
        const { session: connected } = await api.calls.connected(started.id, typeof conversationId === "string" ? conversationId : null);
        setSession(connected);
        return connected;
      }
      return started;
    },
    [conversation],
  );

  const end = useCallback(async () => {
    const current = sessionRef.current;
    if (!current) return null;
    if (current.mode === "browser" && conversation.status === "connected") await conversation.endSession();
    const { session: ended } = await api.calls.end(current.id);
    setSession(ended);
    return ended;
  }, [conversation]);

  const follow = useCallback((next: CallSession | null) => {
    // Never drop a live call for an older one.
    if (isLiveCall(sessionRef.current) && (!next || next.id !== sessionRef.current!.id)) return;
    setSession(next);
  }, []);

  const clearError = useCallback(() => setError(null), []);

  const value = useMemo<CallState>(
    () => ({
      session,
      live: isLiveCall(session),
      error,
      voice: {
        status: conversation.status,
        isSpeaking: conversation.isSpeaking,
        isMuted: conversation.isMuted,
        setMuted: conversation.setMuted,
      },
      start,
      end,
      follow,
      clearError,
    }),
    [session, error, conversation.status, conversation.isSpeaking, conversation.isMuted, conversation.setMuted, start, end, follow, clearError],
  );

  return <CallContext.Provider value={value}>{children}</CallContext.Provider>;
}

export function CallProvider({ children }: { children: ReactNode }) {
  return (
    <ConversationProvider>
      <CallSessionProvider>{children}</CallSessionProvider>
    </ConversationProvider>
  );
}

export function useCall(): CallState {
  const context = useContext(CallContext);
  if (!context) throw new Error("useCall must be used within CallProvider");
  return context;
}

/** Shown on every page except the call room itself while a call is live. */
export function CallBar() {
  const { session, live, end } = useCall();
  const location = useLocation();
  const [busy, setBusy] = useState(false);
  const [, tick] = useState(0);

  useEffect(() => {
    if (!live) return;
    const timer = window.setInterval(() => tick((n) => n + 1), 1000);
    return () => window.clearInterval(timer);
  }, [live]);

  if (!session || !live || location.pathname === callPath(session)) return null;

  const name = session.brief?.patient_name || "patient";
  const phone = session.phone || session.brief?.phone;

  const hangUp = async () => {
    setBusy(true);
    try {
      await end();
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="call-bar" role="status" aria-live="polite">
      <span className={cx("call-bar-dot", session.status === "live" && "call-bar-dot-live")} aria-hidden="true" />
      <span className="call-bar-text">
        <strong>{session.status === "live" ? "On a call with" : "Calling"} {name}</strong>
        <small>
          {phone ? `${phone} · ` : ""}
          {session.mode} · <span className="call-bar-timer">{elapsed(session.started_at || session.created_at)}</span>
        </small>
      </span>
      <Link to={callPath(session)} className="btn btn-small btn-primary">
        Return to call
      </Link>
      <button className="btn btn-small btn-danger-ghost" onClick={hangUp} disabled={busy}>
        {busy ? "Ending…" : "End call"}
      </button>
    </div>
  );
}
