import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { ConversationProvider, useConversation } from "@elevenlabs/react";
import { api } from "./api";
import type { CallSession } from "./types";

/* The live call lives above the pages, so moving around the console (to look
   up a patient, check a follow-up) no longer hangs up. The call room and the
   "on a call" bar in the layout both read it from here. */

const LIVE = new Set(["preparing", "live"]);

interface CallState {
  /** The call in progress, if any, whichever page is showing. */
  active: CallSession | null;
  patientName: string | null;
  error: string | null;
  isSpeaking: boolean;
  isMuted: boolean;
  setMuted: (muted: boolean) => void;
  /** Track a call the call room just started (any mode). */
  track: (session: CallSession, patientName?: string) => void;
  /** Open the browser voice session for a call that was just created. */
  connectBrowser: (session: CallSession, agentId: string, patientName: string) => Promise<CallSession>;
  end: () => Promise<CallSession | null>;
}

const CallContext = createContext<CallState | null>(null);

function CallController({ children }: { children: ReactNode }) {
  const [active, setActive] = useState<CallSession | null>(null);
  const [patientName, setPatientName] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const activeRef = useRef<CallSession | null>(null);
  activeRef.current = active;

  const conversation = useConversation({
    onMessage: (event: unknown) => {
      const s = activeRef.current;
      if (!s) return;
      const data = event as Record<string, unknown>;
      const type = String(data.type ?? "");
      if (type === "user_transcript") void api.calls.line(s.id, "patient", String(data.user_transcript ?? ""));
      else if (type === "agent_response") void api.calls.line(s.id, "agent", String(data.agent_response ?? ""));
    },
    onError: (message: string) => setError(message || "The voice session failed."),
    onDisconnect: () => {
      const s = activeRef.current;
      if (s && s.mode === "browser" && LIVE.has(s.status)) {
        void api.calls.end(s.id).finally(() => setActive(null));
      }
    },
  });

  // Keep the tracked call fresh, and drop it once the server says it is over.
  useEffect(() => {
    if (!active) return;
    const timer = window.setInterval(() => {
      api.calls
        .get(active.id)
        .then(({ session }) => setActive((current) => (current?.id !== session.id ? current : LIVE.has(session.status) ? session : null)))
        .catch(() => undefined);
    }, 2000);
    return () => window.clearInterval(timer);
  }, [active?.id]); // eslint-disable-line react-hooks/exhaustive-deps

  // Closing or reloading the tab would still drop a browser call: ask first.
  useEffect(() => {
    if (!active || active.mode !== "browser") return;
    const warn = (event: BeforeUnloadEvent) => {
      event.preventDefault();
    };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [active?.id, active?.mode]); // eslint-disable-line react-hooks/exhaustive-deps

  const track = useCallback((session: CallSession, name?: string) => {
    setError(null);
    setActive(LIVE.has(session.status) ? session : null);
    setPatientName(name || session.brief?.patient_name || null);
  }, []);

  const connectBrowser = useCallback(
    async (session: CallSession, agentId: string, name: string) => {
      const conversationId = await conversation.startSession({
        agentId,
        dynamicVariables: { patient_id: session.patient_id, patient_name: name, call_session_id: session.id },
      });
      const { session: connected } = await api.calls.connected(session.id, typeof conversationId === "string" ? conversationId : null);
      setActive(connected);
      return connected;
    },
    [conversation],
  );

  const end = useCallback(async () => {
    const s = activeRef.current;
    if (!s) return null;
    // Clear first so the disconnect handler doesn't end the same call twice.
    activeRef.current = null;
    setActive(null);
    if (s.mode === "browser" && conversation.status === "connected") await conversation.endSession();
    const { session } = await api.calls.end(s.id);
    return session;
  }, [conversation]);

  const value = useMemo(
    () => ({
      active,
      patientName,
      error,
      isSpeaking: conversation.isSpeaking,
      isMuted: conversation.isMuted,
      setMuted: conversation.setMuted,
      track,
      connectBrowser,
      end,
    }),
    [active, patientName, error, conversation.isSpeaking, conversation.isMuted, conversation.setMuted, track, connectBrowser, end],
  );

  return <CallContext.Provider value={value}>{children}</CallContext.Provider>;
}

export function CallProvider({ children }: { children: ReactNode }) {
  return (
    <ConversationProvider>
      <CallController>{children}</CallController>
    </ConversationProvider>
  );
}

export function useCall(): CallState {
  const context = useContext(CallContext);
  if (!context) throw new Error("useCall must be used within CallProvider");
  return context;
}
