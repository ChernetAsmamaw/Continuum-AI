import { useCallback, useState } from "react";
import { ConversationProvider, useConversation } from "@elevenlabs/react";
import { useApp } from "../state";
import { cx } from "../ui";
import { Icon } from "./Icon";

interface VoiceLine {
  role: "agent" | "patient";
  message: string;
  at: string;
}

function VoicePanel({ patientId, patientName }: { patientId: string; patientName: string }) {
  const { config } = useApp();
  const [lines, setLines] = useState<VoiceLine[]>([]);
  const [error, setError] = useState<string | null>(null);

  const push = useCallback((role: VoiceLine["role"], message: string) => {
    const text = message.trim();
    if (!text) return;
    setLines((current) => {
      const last = current[current.length - 1];
      if (last && last.role === role && last.message === text) return current;
      return [...current, { role, message: text, at: new Date().toISOString() }];
    });
  }, []);

  const conversation = useConversation({
    onMessage: (event: unknown) => {
      const data = event as Record<string, unknown>;
      const type = String(data.type ?? "");
      if (type === "user_transcript") {
        push("patient", String(data.user_transcript ?? ""));
      } else if (type === "agent_response") {
        push("agent", String(data.agent_response ?? ""));
      }
    },
    onError: (message: string) => setError(message || "The voice session failed."),
    onDisconnect: () => setError(null),
  });

  const agentId = config?.elevenlabs_agent_id || (import.meta.env.VITE_ELEVENLABS_AGENT_ID as string | undefined);
  const connected = conversation.status === "connected";
  const connecting = conversation.status === "connecting";

  const toggle = () => {
    if (connected) {
      conversation.endSession();
      return;
    }
    setError(null);
    setLines([]);
    if (!agentId) {
      setError("Set ELEVENLABS_AGENT_ID in the backend .env to enable the live voice agent.");
      return;
    }
    conversation.startSession({
      agentId,
      dynamicVariables: {
        patient_id: patientId,
        patient_name: patientName,
      },
    });
  };

  return (
    <div className="voice-widget">
      <div className="voice-head">
        <div>
          <h3>Voice channel</h3>
          <p className="muted">ElevenLabs Conversational AI · patient_id passed as a dynamic variable</p>
        </div>
        <span
          className={cx(
            "tag",
            connected ? "tag-green" : conversation.status === "error" ? "tag-danger" : "tag-muted",
          )}
        >
          <span
            className={cx(
              "dot",
              connected ? "dot-green" : conversation.status === "error" ? "dot-danger" : "dot-neutral",
            )}
          />
          {connected ? (conversation.isSpeaking ? "Agent speaking" : "Listening") : conversation.status}
        </span>
      </div>

      <div className={cx("voice-stage", connected && "voice-stage-live", conversation.isSpeaking && "voice-stage-speaking")}>
        <button
          className={cx("voice-orb", connected && "voice-orb-live")}
          onClick={toggle}
          aria-label={connected ? "End voice call" : "Start voice call"}
        >
          <span className="voice-orb-glyph">
            <Icon name={connected ? "phone-off" : "phone"} />
          </span>
        </button>
        <div className="voice-copy">
          <strong>{connected ? "Live with Continuum voice agent" : "Start a synthetic demo call"}</strong>
          <p>
            {connected
              ? "The agent discloses that it is AI, confirms consent, and escalates clinical questions."
              : `Call ${patientName} through the configured ElevenLabs agent. No real phone numbers are dialled from the browser.`}
          </p>
        </div>
      </div>

      <div className="voice-controls">
        <button className={cx("btn", connected ? "btn-danger-ghost" : "btn-primary")} onClick={toggle} disabled={connecting}>
          {connecting ? "Connecting…" : connected ? "End call" : "Start voice session"}
        </button>
        {connected ? (
          <button className="btn btn-small" onClick={() => conversation.setMuted(!conversation.isMuted)}>
            {conversation.isMuted ? "Unmute mic" : "Mute mic"}
          </button>
        ) : null}
        <span className="muted voice-note">Post-call webhook extracts obligations and memory.</span>
      </div>

      {error ? <p className="form-error">{error}</p> : null}

      {lines.length > 0 ? (
        <div className="voice-transcript">
          {lines.map((line, index) => (
            <div key={`${line.at}-${index}`} className={cx("voice-line", `voice-line-${line.role}`)}>
              <span>{line.role === "agent" ? "Continuum" : "Patient"}</span>
              <p>{line.message}</p>
            </div>
          ))}
        </div>
      ) : (
        <p className="empty empty-small">
          Live transcripts appear here. Each call is also recorded in the patient journey and audit log.
        </p>
      )}
    </div>
  );
}

export function VoiceWidget({ patientId, patientName }: { patientId: string; patientName: string }) {
  return (
    <ConversationProvider>
      <VoicePanel patientId={patientId} patientName={patientName} />
    </ConversationProvider>
  );
}
