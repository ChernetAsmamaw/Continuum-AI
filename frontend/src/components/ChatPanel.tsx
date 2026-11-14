import { useCallback, useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";
import type { AgentResult, Approval, ThreadState } from "../types";
import { cx, formatDateTime, pct } from "../ui";
import { ApprovalCard } from "./ApprovalCard";

interface ChatMessage {
  id: string;
  role: "user" | "agent" | "system";
  text: string;
  at: string;
  result?: AgentResult;
}

const SUGGESTIONS = [
  { label: "What is still open?", message: "What is still open for this patient?" },
  { label: "Bloodwork is done", message: "The lab confirmed the bloodwork is done — close that loop." },
  { label: "Appointment date changed", message: "Michael says his cardiology appointment is on a different date." },
  { label: "Medication question", message: "He asked whether he can double his water pill dose." },
];

let counter = 0;
const nextId = () => `msg_${Date.now()}_${counter++}`;

export function ChatPanel({
  patientId,
  patientName,
  onResult,
}: {
  patientId: string;
  patientName?: string;
  onResult?: (result: AgentResult, thread: ThreadState | null) => void;
}) {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [thread, setThread] = useState<ThreadState | null>(null);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const logRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);

  const paused = Boolean(thread?.paused);

  const loadThread = useCallback(async () => {
    try {
      const snapshot = await api.thread(patientId);
      setThread(snapshot);
      return snapshot;
    } catch {
      setThread(null);
      return null;
    }
  }, [patientId]);

  useEffect(() => {
    setMessages([]);
    setInput("");
    setError(null);
    let cancelled = false;
    api
      .thread(patientId)
      .then((snapshot) => {
        if (cancelled) return;
        setThread(snapshot);
        setMessages([
          {
            id: nextId(),
            role: "agent",
            at: new Date().toISOString(),
            text: `I can see ${patientName || "this patient"}'s open obligations, evidence and stored memory. Ask what is still open, or share an update. Sensitive closures pause for your approval.`,
          },
        ]);
        if (snapshot.paused) {
          setMessages((current) => [
            ...current,
            {
              id: nextId(),
              role: "system",
              at: new Date().toISOString(),
              text: "This patient thread is paused at a human-in-the-loop approval. Resolve the pending approval below to resume the checkpointed LangGraph run.",
            },
          ]);
        }
      })
      .catch(() => {
        if (!cancelled) {
          setThread(null);
          setMessages([
            {
              id: nextId(),
              role: "agent",
              at: new Date().toISOString(),
              text: "I couldn't load the LangGraph thread. Check that the Continuum API is running on :8000.",
            },
          ]);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [patientId, patientName]);

  useEffect(() => {
    logRef.current?.scrollTo({ top: logRef.current.scrollHeight, behavior: "smooth" });
  }, [messages, busy]);

  const send = async (text: string) => {
    const message = text.trim();
    if (!message || busy) return;
    setInput("");
    setError(null);
    setBusy(true);
    setMessages((current) => [
      ...current,
      { id: nextId(), role: "user", text: message, at: new Date().toISOString() },
    ]);
    try {
      const result = await api.chat(patientId, message);
      setMessages((current) => [
        ...current,
        {
          id: nextId(),
          role: "agent",
          text: result.reply || "No reply was returned.",
          at: new Date().toISOString(),
          result,
        },
      ]);
      const snapshot = await loadThread();
      onResult?.(result, snapshot);
      if (result.status === "awaiting_approval") {
        setMessages((current) => [
          ...current,
          {
            id: nextId(),
            role: "system",
            at: new Date().toISOString(),
            text: "Sensitive change requested. The patient thread is paused until a coordinator resolves the approval below.",
          },
        ]);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "The agent could not complete the run.");
      setMessages((current) => [
        ...current,
        {
          id: nextId(),
          role: "system",
          at: new Date().toISOString(),
          text: "The agent run failed. Check the backend logs and try again.",
        },
      ]);
    } finally {
      setBusy(false);
      window.setTimeout(() => inputRef.current?.focus(), 30);
    }
  };

  const pendingApprovals: Approval[] = thread?.pending_approvals || [];
  const lastResult = [...messages].reverse().find((message) => message.result)?.result;
  const started = messages.some((message) => message.role === "user");

  return (
    <div className="chat">
      <div className="chat-head">
        <div>
          <h3>Coordinator chat</h3>
          <p className="muted">
            Same LangGraph workflow as documents and voice · checkpointed in MongoDB
          </p>
        </div>
        <span className={cx("tag", paused ? "tag-amber" : "tag-green")}>
          <span className={cx("dot", paused ? "dot-amber" : "dot-green")} />
          {paused ? "Paused for approval" : busy ? "Running" : "Ready"}
        </span>
      </div>

      {paused ? (
        <div className="chat-paused">
          <div>
            <strong>Human-in-the-loop pause</strong>
            <p>
              The graph interrupted at <code>await_approval</code>. Approve or reject below to resume the run
              from its MongoDB checkpoint.
            </p>
          </div>
          <Link to="/approvals" className="btn btn-small">
            All approvals
          </Link>
        </div>
      ) : null}

      <div className="chat-log" ref={logRef}>
        {messages.map((message) => (
          <div key={message.id} className={cx("chat-message", `chat-message-${message.role}`)}>
            <div className="chat-bubble">
              <div className="chat-bubble-head">
                <strong>{message.role === "user" ? "You" : message.role === "agent" ? "Continuum" : "System"}</strong>
                <small>{formatDateTime(message.at)}</small>
              </div>
              <p>{message.text}</p>
              {message.result?.retrieval?.counts ? (
                <div className="chat-trace">
                  <span>{message.result.retrieval.counts.memories ?? 0} memories recalled</span>
                  {message.result.applied && message.result.applied.length > 0 ? (
                    <span>{message.result.applied.length} administrative updates applied</span>
                  ) : null}
                  {message.result.notes && message.result.notes.length > 0 ? (
                    <span>{message.result.notes.length} validation notes</span>
                  ) : null}
                </div>
              ) : null}
            </div>
          </div>
        ))}
        {busy ? (
          <div className="chat-message chat-message-agent">
            <div className="chat-bubble chat-typing">
              <span className="typing-dot" />
              <span className="typing-dot" />
              <span className="typing-dot" />
              <em>Running the graph…</em>
            </div>
          </div>
        ) : null}
      </div>

      {pendingApprovals.length > 0 ? (
        <div className="chat-approvals">
          <h4>Pending approvals</h4>
          {pendingApprovals.map((approval) => (
            <ApprovalCard key={approval.id} approval={approval} compact onResolved={loadThread} />
          ))}
        </div>
      ) : null}

      {lastResult?.proposals && lastResult.proposals.length > 0 ? (
        <details className="chat-proposals">
          <summary>
            Last run · {lastResult.proposals.length} proposal{lastResult.proposals.length === 1 ? "" : "s"}
          </summary>
          <ul>
            {lastResult.proposals.map((proposal) => (
              <li key={proposal.id}>
                <div>
                  <strong>{proposal.title}</strong>
                  <p>{proposal.rationale}</p>
                </div>
                <div className="tag-row">
                  <span className="tag tag-muted">{proposal.kind.replace(/_/g, " ")}</span>
                  {proposal.requires_approval ? <span className="tag tag-amber">approval</span> : null}
                  {proposal.interrupt ? <span className="tag tag-purple">interrupt</span> : null}
                  {proposal.confidence !== undefined ? (
                    <span className="tag tag-blue">{pct(proposal.confidence)}</span>
                  ) : null}
                </div>
              </li>
            ))}
          </ul>
        </details>
      ) : null}

      {!started ? (
        <div className="chat-suggestions">
          <span className="chat-suggestions-label">Try asking</span>
          {SUGGESTIONS.map((suggestion) => (
            <button
              key={suggestion.label}
              className="chip chip-button"
              onClick={() => send(suggestion.message)}
              disabled={busy || paused}
            >
              {suggestion.label}
            </button>
          ))}
        </div>
      ) : null}

      {error ? <p className="form-error">{error}</p> : null}

      <form
        className="chat-form"
        onSubmit={(event) => {
          event.preventDefault();
          void send(input);
        }}
      >
        <textarea
          ref={inputRef}
          rows={2}
          value={input}
          onChange={(event) => setInput(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter" && !event.shiftKey) {
              event.preventDefault();
              void send(input);
            }
          }}
          placeholder={
            paused
              ? "Resolve the pending approval to continue…"
              : "Ask about obligations, evidence, memory, or share an update…"
          }
          disabled={busy || paused}
        />
        <button className="btn btn-primary" type="submit" disabled={busy || paused || !input.trim()}>
          {busy ? "Running…" : "Send"}
        </button>
      </form>
      <p className="chat-disclaimer">
        Continuum coordinates administrative follow-ups. It does not diagnose, prescribe or change treatment.
      </p>
    </div>
  );
}
