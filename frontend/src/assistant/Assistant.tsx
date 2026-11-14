import {
  AssistantRuntimeProvider,
  ComposerPrimitive,
  MessagePrimitive,
  ThreadPrimitive,
  useLocalRuntime,
  type ChatModelAdapter,
  type ToolCallMessagePartProps,
} from "@assistant-ui/react";
import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";
import { API_BASE, getAuthToken } from "../api";
import "./assistant.css";

/* ── Streaming adapter: /api/assistant/chat (SSE) → assistant-ui parts ── */

type Part =
  | { type: "text"; text: string }
  | { type: "tool-call"; toolCallId: string; toolName: string; args: Record<string, never>; argsText: string; result?: unknown };

type Ctx = { path: string; patient_id: string | null };

function makeAdapter(ctx: { current: Ctx }): ChatModelAdapter {
  return {
    async *run({ messages, abortSignal }) {
      const history = messages.map((m) => ({
        role: m.role,
        content: m.content.map((p) => (p.type === "text" ? p.text : "")).join(""),
      }));
      const res = await fetch(`${API_BASE}/api/assistant/chat`, {
        method: "POST",
        headers: { "Content-Type": "application/json", Authorization: `Bearer ${getAuthToken() ?? ""}` },
        body: JSON.stringify({ messages: history, context: ctx.current }),
        signal: abortSignal,
      });
      if (!res.ok || !res.body) {
        yield { content: [{ type: "text", text: res.status === 401 ? "Your session expired. Sign in again." : "The assistant is unavailable right now." }] };
        return;
      }
      const parts: Part[] = [];
      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const frames = buffer.split("\n\n");
        buffer = frames.pop() ?? "";
        let changed = false;
        for (const frame of frames) {
          const line = frame.split("\n").find((l) => l.startsWith("data: "));
          if (!line) continue;
          const ev = JSON.parse(line.slice(6));
          if (ev.type === "text") {
            const last = parts[parts.length - 1];
            if (last && last.type === "text") last.text += ev.text;
            else parts.push({ type: "text", text: ev.text });
            changed = true;
          } else if (ev.type === "tool") {
            parts.push({ type: "tool-call", toolCallId: ev.id, toolName: ev.name, args: {}, argsText: "{}" });
            changed = true;
          } else if (ev.type === "result") {
            const p = parts.find((x) => x.type === "tool-call" && x.toolCallId === ev.id);
            if (p && p.type === "tool-call") p.result = ev.data;
            else parts.push({ type: "tool-call", toolCallId: ev.id, toolName: ev.name, args: {}, argsText: "{}", result: ev.data });
            changed = true;
          } else if (ev.type === "error") {
            parts.push({ type: "text", text: ev.message });
            changed = true;
          }
        }
        if (changed) yield { content: parts.map((p) => ({ ...p })) as never };
      }
    },
  };
}

/* ── Cards for tool results ─────────────────────────────────────────── */

const TOOL_LABEL: Record<string, string> = {
  needs_you: "Checking what needs you",
  search_patients: "Looking up patients",
  find_followups: "Searching follow-ups",
  patient_summary: "Reading the patient's record",
  followup_detail: "Reading the follow-up",
  call_details: "Reading the last call",
  action_ledger: "Checking the action ledger",
  ingestion_jobs: "Checking EHR imports",
  open_page: "Opening",
  propose_call: "Preparing a call",
  propose_decision: "Preparing a decision",
  propose_assign: "Preparing an assignment",
  propose_contact_log: "Preparing a contact log",
};

const STATUS_WORDS: Record<string, string> = {
  pending: "Open", scheduled: "Scheduled", attempted: "Attempted", patient_reported: "Patient says done",
  verified: "Verified", overdue: "Overdue", cancelled: "Cancelled", escalated: "Escalated", conflict: "Conflict", unknown: "Unknown",
};

type FollowUp = { id: string; patient_id: string; patient_name?: string; description: string; status: string; due_date?: string; days_overdue?: number };

function day(iso?: string | null) {
  if (!iso) return "";
  return new Date(`${iso.slice(0, 10)}T12:00:00`).toLocaleDateString("en-US", { month: "short", day: "numeric" });
}

function FollowupRows({ rows, close }: { rows: FollowUp[]; close: () => void }) {
  if (!rows.length) return <p className="as-empty">Nothing found.</p>;
  return (
    <ul className="as-rows">
      {rows.slice(0, 8).map((r) => (
        <li key={r.id}>
          <Link to={`/patients/${r.patient_id}`} onClick={close}>
            <span className="as-row-main">
              <b>{r.description}</b>
              {r.patient_name ? <small>{r.patient_name}</small> : null}
            </span>
            <span className={`as-status is-${r.status}`}>
              {r.status === "overdue" && r.days_overdue ? `${r.days_overdue}d overdue` : STATUS_WORDS[r.status] || r.status}
              {r.due_date && r.status !== "overdue" ? ` · ${day(r.due_date)}` : ""}
            </span>
          </Link>
        </li>
      ))}
    </ul>
  );
}

function Proposal({ p }: { p: { title: string; detail?: string; method: string; path: string; body: unknown; confirm_label?: string; then_navigate?: string } }) {
  const navigate = useNavigate();
  const [state, setState] = useState<"idle" | "busy" | "done" | "cancelled" | "error">("idle");
  const [error, setError] = useState("");
  const confirm = async () => {
    setState("busy");
    try {
      const res = await fetch(`${API_BASE}${p.path}`, {
        method: p.method,
        headers: { "Content-Type": "application/json", Authorization: `Bearer ${getAuthToken() ?? ""}` },
        body: JSON.stringify(p.body),
      });
      if (!res.ok) {
        const j = await res.json().catch(() => ({}));
        throw new Error(typeof j.detail === "string" ? j.detail : `Request failed (${res.status})`);
      }
      setState("done");
      if (p.then_navigate) navigate(p.then_navigate);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Something went wrong.");
      setState("error");
    }
  };
  return (
    <div className={`as-proposal is-${state}`}>
      <b>{p.title}</b>
      {p.detail ? <p>{p.detail}</p> : null}
      {state === "done" ? (
        <p className="as-ok">Done.</p>
      ) : state === "cancelled" ? (
        <p className="as-muted">Cancelled.</p>
      ) : (
        <div className="as-actions">
          <button type="button" className="as-btn" onClick={confirm} disabled={state === "busy"}>
            {state === "busy" ? "Working" : p.confirm_label || "Confirm"}
          </button>
          <button type="button" className="as-btn as-btn-quiet" onClick={() => setState("cancelled")} disabled={state === "busy"}>
            Cancel
          </button>
        </div>
      )}
      {state === "error" ? <p className="as-err">{error}</p> : null}
    </div>
  );
}

const handledNav = new Set<string>();

function ToolCard(props: ToolCallMessagePartProps) {
  const { toolName, result, toolCallId } = props;
  const navigate = useNavigate();
  const close = useClose();
  const data = (result ?? null) as Record<string, any> | null;

  useEffect(() => {
    if (toolName === "open_page" && data?.navigate && !handledNav.has(toolCallId)) {
      handledNav.add(toolCallId);
      navigate(data.navigate);
    }
  }, [toolName, data, toolCallId, navigate]);

  if (!data) return <p className="as-working">{TOOL_LABEL[toolName] || "Working"}…</p>;
  if (data.error) return <p className="as-err">{data.error}</p>;

  let body: ReactNode = null;
  switch (toolName) {
    case "needs_you":
      body = (
        <>
          <FollowupRows rows={data.followups} close={close} />
          {data.counts?.approvals ? (
            <Link className="as-more" to="/approvals" onClick={close}>
              {data.counts.approvals} decision{data.counts.approvals === 1 ? "" : "s"} waiting
            </Link>
          ) : null}
        </>
      );
      break;
    case "find_followups":
      body = <FollowupRows rows={data.followups} close={close} />;
      break;
    case "search_patients":
      body = (
        <ul className="as-rows">
          {data.patients.slice(0, 8).map((p: any) => (
            <li key={p.id}>
              <Link to={`/patients/${p.id}`} onClick={close}>
                <span className="as-row-main">
                  <b>{p.name}</b>
                  <small>
                    {p.condition}
                    {p.days_since_discharge != null ? `, day ${p.days_since_discharge}` : ""}
                  </small>
                </span>
                <span className={`as-status ${p.overdue ? "is-overdue" : ""}`}>
                  {p.overdue ? `${p.overdue} overdue` : p.needs_you ? `${p.needs_you} need you` : "On track"}
                </span>
              </Link>
            </li>
          ))}
        </ul>
      );
      break;
    case "patient_summary":
      body = (
        <>
          <Link className="as-head" to={`/patients/${data.patient.id}`} onClick={close}>
            <b>{data.patient.name}</b>
            <small>
              {data.patient.age ? `${data.patient.age} · ` : ""}
              {data.patient.condition}
            </small>
          </Link>
          <FollowupRows rows={data.followups.filter((f: FollowUp) => f.status !== "cancelled")} close={close} />
        </>
      );
      break;
    case "followup_detail":
      body = (
        <>
          <FollowupRows rows={[data.followup]} close={close} />
          {data.evidence?.length ? (
            <ul className="as-evidence">
              {data.evidence.map((e: any, i: number) => (
                <li key={i}>
                  <span className={e.verified ? "as-ok" : "as-muted"}>{e.verified ? "Proof" : "Report"}</span> {e.summary}
                </li>
              ))}
            </ul>
          ) : (
            <p className="as-muted">No evidence yet.</p>
          )}
        </>
      );
      break;
    case "call_details":
      body = data.call ? (
        <>
          <Link className="as-head" to={`/patients/${data.patient_id}/call`} onClick={close}>
            <b>Last call with {data.patient_name}</b>
            <small>
              {data.call.mode} · {data.call.status}
            </small>
          </Link>
          {data.call.changes?.length ? (
            <ul className="as-evidence">
              {data.call.changes.map((c: any, i: number) => (
                <li key={i}>{c.title}{c.to ? ` → ${STATUS_WORDS[c.to] || c.to}` : ""}</li>
              ))}
            </ul>
          ) : null}
        </>
      ) : (
        <p className="as-muted">No calls yet with {data.patient_name}.</p>
      );
      break;
    case "action_ledger":
      body = data.actions.length ? (
        <ul className="as-rows">
          {data.actions.slice(0, 8).map((a: any) => (
            <li key={a.id}>
              <Link to="/actions" onClick={close}>
                <span className="as-row-main"><b>{a.summary}</b></span>
                <span className={`as-status is-${a.status}`}>{a.status}</span>
              </Link>
            </li>
          ))}
        </ul>
      ) : (
        <p className="as-muted">No actions match.</p>
      );
      break;
    case "ingestion_jobs":
      body = (
        <ul className="as-rows">
          {data.jobs.map((j: any) => (
            <li key={j.id}>
              <Link to="/ingestion" onClick={close}>
                <span className="as-row-main">
                  <b>EHR import</b>
                  <small>{day(j.created_at)} · {j.attempts} attempt{j.attempts === 1 ? "" : "s"}</small>
                </span>
                <span className={`as-status is-${j.status}`}>{j.status}</span>
              </Link>
            </li>
          ))}
        </ul>
      );
      break;
    case "open_page":
      body = <p className="as-muted">Opened {data.label}.</p>;
      break;
    default:
      if (data.proposal) body = <Proposal p={data.proposal} />;
  }
  return <div className="as-card">{body}</div>;
}

/* ── Panel ──────────────────────────────────────────────────────────── */

const CloseContext = { fn: () => {} };
function useClose() {
  const isNarrow = typeof window !== "undefined" && window.matchMedia?.("(max-width: 760px)").matches;
  return () => {
    if (isNarrow) CloseContext.fn();
  };
}

function suggestionsFor(path: string): string[] {
  if (/^\/patients\/[^/]+\/call/.test(path)) return ["Summarize this patient's last call", "What's still open for this patient?"];
  if (/^\/patients\/[^/]+/.test(path)) return ["What changed since discharge?", "Why is anything overdue here?", "Call this patient"];
  if (path.startsWith("/patients")) return ["Who is overdue?", "Who hasn't had bloodwork verified?"];
  if (path.startsWith("/ingestion")) return ["Show the latest EHR imports", "Did any import need a person?"];
  return ["What needs me today?", "Who hasn't been reached yet?", "Show unconfirmed actions"];
}

function Thread({ path }: { path: string }) {
  return (
    <ThreadPrimitive.Root className="as-thread">
      <ThreadPrimitive.Viewport className="as-viewport">
        <ThreadPrimitive.Empty>
          <div className="as-welcome">
            <p>Ask about your patients, or tell me what to do. I'll show what I find and ask before changing anything.</p>
            <div className="as-suggest">
              {suggestionsFor(path).map((s) => (
                <ThreadPrimitive.Suggestion key={s} prompt={s} send className="as-chip">
                  {s}
                </ThreadPrimitive.Suggestion>
              ))}
            </div>
          </div>
        </ThreadPrimitive.Empty>
        <ThreadPrimitive.Messages
          components={{
            UserMessage: () => (
              <MessagePrimitive.Root className="as-msg as-user">
                <MessagePrimitive.Parts />
              </MessagePrimitive.Root>
            ),
            AssistantMessage: () => (
              <MessagePrimitive.Root className="as-msg as-bot">
                <MessagePrimitive.Parts components={{ tools: { Override: ToolCard } }} />
              </MessagePrimitive.Root>
            ),
          }}
        />
      </ThreadPrimitive.Viewport>
      <ComposerPrimitive.Root className="as-composer">
        <ComposerPrimitive.Input className="as-input" placeholder="Ask or tell Continuum…" autoFocus rows={1} />
        <ComposerPrimitive.Send className="as-send">Send</ComposerPrimitive.Send>
      </ComposerPrimitive.Root>
    </ThreadPrimitive.Root>
  );
}

export function AssistantPanel({ open, onClose }: { open: boolean; onClose: () => void }) {
  const location = useLocation();
  const ctx = useRef<Ctx>({ path: location.pathname, patient_id: null });
  const match = location.pathname.match(/^\/patients\/([^/]+)/);
  ctx.current = { path: location.pathname, patient_id: match && match[1] !== "new" ? match[1] : null };
  const adapter = useMemo(() => makeAdapter(ctx), []);
  const runtime = useLocalRuntime(adapter);
  CloseContext.fn = onClose;

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  return (
    <aside className={`as-panel ${open ? "is-open" : ""}`} aria-label="Continuum assistant" aria-hidden={!open}>
      <header className="as-top">
        <b>Ask Continuum</b>
        <button type="button" className="as-x" onClick={onClose} aria-label="Close assistant">
          ×
        </button>
      </header>
      {open ? (
        <AssistantRuntimeProvider runtime={runtime}>
          <Thread path={location.pathname} />
        </AssistantRuntimeProvider>
      ) : null}
    </aside>
  );
}
