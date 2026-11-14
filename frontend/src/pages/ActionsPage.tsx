import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api } from "../api";
import { useApp } from "../state";
import type { Tone } from "../tones";
import type { ActionStatus, LedgerAction, LedgerSummary } from "../types";
import { ErrorBanner, Spinner, cx, formatDateTime } from "../ui";

/**
 * Calls and tasks: everything Continuum did outside its own walls (calls,
 * texts, pages, requests to clinics) and whether proof came back. Sent is
 * not done; only the expected proof confirms an action.
 */

type View = "all" | "attention" | "waiting" | "failed" | "confirmed";

const STATE: Record<ActionStatus, { label: string; tone: Tone; meaning: string }> = {
  intended: { label: "Not sent yet", tone: "neutral", meaning: "Decided, not yet sent" },
  sent: { label: "Waiting for proof", tone: "blue", meaning: "Sent. Proof hasn't arrived yet." },
  confirmed: { label: "Confirmed", tone: "green", meaning: "Proof arrived" },
  failed: { label: "Failed", tone: "red", meaning: "The other system said no" },
  unconfirmed: { label: "No proof", tone: "amber", meaning: "Sent, but proof never arrived by the deadline" },
  unknown: { label: "Unknown", tone: "amber", meaning: "Interrupted while sending. It may or may not have gone out." },
};

const VIEWS: { id: View; label: string; status?: string }[] = [
  { id: "all", label: "All" },
  { id: "attention", label: "Needs a person", status: "unconfirmed,unknown" },
  { id: "waiting", label: "Waiting", status: "intended,sent" },
  { id: "failed", label: "Failed", status: "failed" },
  { id: "confirmed", label: "Confirmed", status: "confirmed" },
];

function kindName(a: LedgerAction): string {
  const k = a.kind.toLowerCase();
  if (k.includes("call")) return "Call";
  if (k.includes("sms") || k.includes("text")) return "Text";
  if (k.includes("email")) return "Email";
  if (k.includes("page")) return "Page to on-call clinician";
  if (k.includes("task")) return "Task";
  return k.replace(/[_:.]/g, " ").replace(/^./, (c) => c.toUpperCase());
}

function guidance(a: LedgerAction): string | null {
  if (a.status === "unknown") return "Check the other system before trying again. Continuum stopped mid-send, so it won't resend on its own.";
  if (a.status === "unconfirmed") return "The proof never arrived. Follow up by hand.";
  if (a.status === "failed") return a.history.at(-1)?.detail || null;
  return null;
}

function ActionItem({ action, name }: { action: LedgerAction; name?: string }) {
  const [open, setOpen] = useState(false);
  const st = STATE[action.status] || STATE.intended;
  const hint = guidance(action);
  const callSession = (action.metadata?.session_id as string | undefined) || null;
  return (
    <li className={cx("ac-row", `ac-${st.tone}`)}>
      <button type="button" className="ac-main" onClick={() => setOpen((v) => !v)} aria-expanded={open}>
        <span className={cx("dot", `dot-${st.tone}`)} aria-hidden="true" />
        <span className="ac-what">
          <b>{action.summary}</b>
          <small>
            {kindName(action)}
            {name ? `, ${name}` : ""}
          </small>
        </span>
        <span className="ac-proof">
          {action.status === "confirmed"
            ? action.confirmation || "Proof received"
            : action.proof_expected
              ? `Waiting for ${action.proof_expected.charAt(0).toLowerCase()}${action.proof_expected.slice(1)}`
              : ""}
        </span>
        <span className="ac-when">{formatDateTime(action.intended_at)}</span>
        <span className={cx("ac-state", `tone-${st.tone}`)} title={st.meaning}>
          {st.label}
        </span>
      </button>
      {hint && !open ? <p className={cx("ac-hint", `tone-${st.tone}`)}>{hint}</p> : null}
      {open ? (
        <div className="ac-detail">
          {hint ? <p className={cx("ac-hint ac-hint-in", `tone-${st.tone}`)}>{hint}</p> : null}
          <ol className="ac-steps">
            {action.history.map((h, i) => {
              const t = STATE[h.status]?.tone || "neutral";
              return (
                <li key={`${h.at}-${i}`}>
                  <span className={cx("dot", `dot-${t}`)} aria-hidden="true" />
                  <b className={`tone-${t}`}>{STATE[h.status]?.label || h.status}</b>
                  <span>{h.detail}</span>
                  <small>{formatDateTime(h.at)}</small>
                </li>
              );
            })}
          </ol>
          <div className="im-foot">
            <Link to={`/patients/${action.patient_id}`}>Open {name || "the patient"}</Link>
            {callSession ? <Link to={`/patients/${action.patient_id}/call`}>Open the call room</Link> : null}
            {action.external_ref ? <span className="muted">Reference {action.external_ref}</span> : null}
          </div>
        </div>
      ) : null}
    </li>
  );
}

export function ActionsPage() {
  const { dashboard, refreshKey } = useApp();
  const [params, setParams] = useSearchParams();
  const [actions, setActions] = useState<LedgerAction[] | null>(null);
  const [summary, setSummary] = useState<LedgerSummary | null>(null);
  const [error, setError] = useState<string | null>(null);
  const requested = params.get("view") as View | null;

  const load = useCallback(async () => {
    try {
      const data = await api.actions({ limit: 200 });
      setActions(data.actions);
      setSummary(data.summary);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not load calls and tasks.");
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load, refreshKey]);

  const names = useMemo(() => new Map((dashboard?.patients || []).map((p) => [p.id, p.name])), [dashboard]);

  if (!summary || !actions) return error ? <ErrorBanner message={error} onRetry={load} /> : <Spinner label="Loading calls and tasks" />;

  const c = summary.counts;
  // Open on what needs a person only when something does.
  const view: View = requested || (summary.needs_attention ? "attention" : "all");
  const filter = VIEWS.find((v) => v.id === view)?.status?.split(",");
  const shown = filter ? actions.filter((a) => filter.includes(a.status)) : actions;
  const count = (v: (typeof VIEWS)[number]) => (v.status ? v.status.split(",").reduce((n, s) => n + (c[s as ActionStatus] || 0), 0) : actions.length);

  return (
    <div className="dq">
      <header className="dq-head">
        <div>
          <h1>Calls and tasks</h1>
          <p>Every call, text, page and request Continuum sent, and whether proof came back. Sent does not count as done until the proof arrives.</p>
        </div>
      </header>

      {error ? <ErrorBanner message={error} onRetry={load} /> : null}

      <div className="im-summary">
        {summary.needs_attention ? <span className="flag tone-amber">{summary.needs_attention} need a person</span> : null}
        {c.failed ? <span className="flag tone-red">{c.failed} failed</span> : null}
        {(c.sent || 0) + (c.intended || 0) ? <span className="flag tone-blue">{(c.sent || 0) + (c.intended || 0)} waiting for proof</span> : null}
        <span className="flag tone-green">{c.confirmed || 0} confirmed</span>
        <div className="seg im-filter" role="tablist">
          {VIEWS.map((v) => (
            <button key={v.id} type="button" aria-pressed={view === v.id} onClick={() => setParams(v.id === "all" ? { view: "all" } : { view: v.id })}>
              {v.label} <span className="seg-n">{count(v)}</span>
            </button>
          ))}
        </div>
      </div>

      {shown.length === 0 ? (
        <div className="dq-empty">
          <h2>{view === "attention" ? "Nothing needs a person right now." : "Nothing in this view."}</h2>
          <p>{view === "attention" ? "Every action is confirmed, failed, or still inside its proof window." : "Switch to All to see everything."}</p>
        </div>
      ) : (
        <section className="im-panel">
          <div className="ac-cols" aria-hidden="true">
            <span />
            <span>What</span>
            <span>Proof</span>
            <span>When</span>
            <span />
          </div>
          <ul className="im-list">
            {shown.map((a) => (
              <ActionItem key={a.id} action={a} name={names.get(a.patient_id)} />
            ))}
          </ul>
        </section>
      )}
    </div>
  );
}
