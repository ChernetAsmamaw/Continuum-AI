import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api } from "../api";
import { useAuth } from "../auth";
import { ApprovalCard } from "../components/ApprovalCard";
import { ReviewPage } from "./ReviewPage";
import { useApp } from "../state";
import { approvalKind } from "../tones";
import type { Approval } from "../types";
import { ErrorBanner, Spinner, cx, formatDateTime } from "../ui";

/**
 * Decisions: a queue on the left, one decision open on the right. Urgent
 * items first, then oldest first, so nothing waits longest by accident.
 */

type Tab = "pending" | "checks" | "resolved";

function waited(iso: string): string {
  const minutes = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 60000));
  if (minutes < 60) return `${minutes} min`;
  const hours = Math.round(minutes / 60);
  if (hours < 48) return `${hours} h`;
  return `${Math.round(hours / 24)} d`;
}

const urgencyRank = (a: Approval) => (a.urgency === "emergency" ? 0 : a.urgency === "urgent" ? 1 : 2);

export function ApprovalsPage() {
  const { user } = useAuth();
  const { dashboard, refreshKey, refresh, notify, ops } = useApp();
  const [params, setParams] = useSearchParams();
  const isClinician = user?.role === "clinician";
  const [approvals, setApprovals] = useState<Approval[]>([]);
  const tab: Tab = (["pending", "checks", "resolved"] as Tab[]).includes(params.get("tab") as Tab) ? (params.get("tab") as Tab) : "pending";
  const setTab = (t: Tab) => setParams(t === "pending" ? {} : { tab: t }, { replace: true });
  const [patientFilter, setPatientFilter] = useState("");
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [bulkBusy, setBulkBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const openId = params.get("id");

  const load = useCallback(async () => {
    try {
      if (tab === "checks") return;
      const data = await api.approvals({ status: tab === "pending" ? "pending" : "" });
      setApprovals(tab === "resolved" ? data.approvals.filter((a) => a.status !== "pending") : data.approvals);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not load decisions.");
    } finally {
      setLoading(false);
    }
  }, [tab]);

  useEffect(() => {
    setLoading(true);
    void load();
  }, [load, refreshKey]);

  const [patientNames, setPatientNames] = useState<Map<string, string>>(new Map());
  useEffect(() => {
    api.patients().then((r) => setPatientNames(new Map(r.patients.map((p) => [p.id, p.name])))).catch(() => undefined);
  }, [refreshKey]);
  const names = useMemo(() => {
    const m = new Map((dashboard?.patients || []).map((p) => [p.id, p.name]));
    patientNames.forEach((v, k) => m.set(k, v));
    return m;
  }, [dashboard, patientNames]);
  const nameOf = (id: string) => names.get(id) || "Patient record removed";

  const visible = useMemo(() => {
    const list = approvals.filter((a) => !patientFilter || a.patient_id === patientFilter);
    return tab === "pending"
      ? [...list].sort((a, b) => urgencyRank(a) - urgencyRank(b) || a.created_at.localeCompare(b.created_at))
      : [...list].sort((a, b) => (b.resolved_at || "").localeCompare(a.resolved_at || ""));
  }, [approvals, patientFilter, tab]);

  const current = visible.find((a) => a.id === openId) || visible[0] || null;
  const open = (id: string) =>
    setParams(
      (p) => {
        p.set("id", id);
        return p;
      },
      { replace: true },
    );

  // J / K move through the queue.
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null;
      if (event.metaKey || event.ctrlKey || event.altKey) return;
      if (target && ["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName)) return;
      if (document.querySelector(".modal-overlay, .palette-overlay, .help-overlay, .tour-overlay")) return;
      const key = event.key.toLowerCase();
      if (key !== "j" && key !== "k") return;
      const i = current ? visible.findIndex((a) => a.id === current.id) : -1;
      const next = visible[Math.min(visible.length - 1, Math.max(0, i + (key === "j" ? 1 : -1)))];
      if (next) {
        event.preventDefault();
        open(next.id);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }); // eslint-disable-line react-hooks/exhaustive-deps

  const toggle = (id: string) =>
    setSelected((cur) => {
      const next = new Set(cur);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  const bulk = async (approve: boolean) => {
    setBulkBusy(true);
    try {
      const result = await api.bulkResolve([...selected], approve);
      const skipped = result.results.length - result.resolved;
      notify(`${approve ? "Approved" : "Rejected"} ${result.resolved}${skipped ? `, skipped ${skipped}` : ""}. Each decision is in the audit log.`);
      setSelected(new Set());
      await load();
      await refresh();
    } catch (err) {
      notify(err instanceof Error ? err.message : "Could not resolve the selection.");
    } finally {
      setBulkBusy(false);
    }
  };

  const pendingCount = tab === "pending" ? approvals.length : dashboard?.stats.pending_approvals ?? 0;
  const urgentCount = approvals.filter((a) => a.status === "pending" && urgencyRank(a) < 2).length;

  return (
    <div className="dq">
      <header className="dq-head">
        <div>
          <h1>Decisions</h1>
          <p>
            {isClinician
              ? "Clinical escalations waiting for a clinician."
              : "Changes that need a person before they apply: closing a follow-up, choosing between two dates, or acting on a patient's request."}
          </p>
        </div>
        <div className="dq-controls">
          <div className="seg" role="tablist">
            <button type="button" aria-pressed={tab === "pending"} onClick={() => setTab("pending")}>
              Waiting {tab === "pending" && pendingCount ? <b>{pendingCount}</b> : null}
            </button>
            <button type="button" aria-pressed={tab === "checks"} onClick={() => setTab("checks")}>
              Order and ID checks {ops?.review ? <b>{ops.review}</b> : null}
            </button>
            <button type="button" aria-pressed={tab === "resolved"} onClick={() => setTab("resolved")}>
              Decided
            </button>
          </div>
          <select aria-label="Patient" value={patientFilter} onChange={(e) => setPatientFilter(e.target.value)}>
            <option value="">All patients</option>
            {(dashboard?.patients ?? []).map((p) => (
              <option key={p.id} value={p.id}>
                {p.name}
              </option>
            ))}
          </select>
        </div>
      </header>

      {error ? <ErrorBanner message={error} onRetry={load} /> : null}

      {tab === "checks" ? (
        <ReviewPage embedded />
      ) : loading && approvals.length === 0 ? (
        <Spinner label="Loading decisions" />
      ) : visible.length === 0 ? (
        <div className="dq-empty">
          <h2>{tab === "pending" ? "Nothing is waiting for a decision." : "No decisions yet."}</h2>
          <p>When the assistant or a clinic record proposes a sensitive change, it appears here.</p>
        </div>
      ) : (
        <div className="dq-split">
          <section className="dq-queue" aria-label="Decision queue">
            {tab === "pending" && urgentCount ? (
              <p className="dq-urgent">
                <span className="dot dot-red" aria-hidden="true" /> {urgentCount} urgent, listed first
              </p>
            ) : null}
            <ul>
              {visible.map((a) => {
                const kind = approvalKind(a);
                const urgent = urgencyRank(a) < 2;
                const on = current?.id === a.id;
                return (
                  <li key={a.id} className={cx("dq-item", on && "on")}>
                    {a.status === "pending" && a.mode !== "interrupt" ? (
                      <input
                        type="checkbox"
                        className="dq-check"
                        checked={selected.has(a.id)}
                        onChange={() => toggle(a.id)}
                        aria-label={`Select ${a.title}`}
                      />
                    ) : (
                      <span className="dq-check" />
                    )}
                    <button type="button" className="dq-row" onClick={() => open(a.id)} aria-current={on}>
                      <span className={cx("dq-kind", `tone-${kind.tone}`)}>
                        <i className={cx("dot", `dot-${kind.tone}`)} />
                        {kind.label}
                      </span>
                      <span className={cx("dq-age", urgent && "tone-red")}>
                        {a.status === "pending" ? (urgent ? a.urgency : waited(a.created_at)) : a.status === "approved" ? "Approved" : "Rejected"}
                      </span>
                      <b className="dq-title">{a.title}</b>
                      <span className="dq-patient">{nameOf(a.patient_id)}</span>
                    </button>
                  </li>
                );
              })}
            </ul>
          </section>

          <section className="dq-focus" aria-label="Selected decision">
            {current ? (
              <>
                <div className="dq-focus-head">
                  <Link to={`/patients/${current.patient_id}`} className="dq-focus-patient">
                    {nameOf(current.patient_id)}
                  </Link>
                  <span className="muted">Raised {formatDateTime(current.created_at)}</span>
                </div>
                <ApprovalCard key={current.id} approval={current} onResolved={() => void load()} />
                <details className="dq-rules">
                  <summary>Why some changes wait for a person</summary>
                  <ul>
                    <li>A patient saying something happened is recorded, but it doesn't close the follow-up. The clinic has to confirm it.</li>
                    <li>Closing, cancelling or escalating needs evidence or a written reason.</li>
                    <li>When two dates disagree, both stay on record until someone picks one.</li>
                  </ul>
                </details>
              </>
            ) : null}
          </section>
        </div>
      )}

      {selected.size > 0 ? (
        <div className="bulk-bar" role="region" aria-label="Decide several at once">
          <strong>{selected.size} selected</strong>
          <div className="actions">
            <button className="btn" onClick={() => setSelected(new Set())} disabled={bulkBusy}>
              Clear
            </button>
            <button className="btn btn-danger-ghost" onClick={() => bulk(false)} disabled={bulkBusy}>
              Reject {selected.size}
            </button>
            <button className="btn btn-primary" onClick={() => bulk(true)} disabled={bulkBusy}>
              {bulkBusy ? "Applying…" : `Approve ${selected.size}`}
            </button>
          </div>
        </div>
      ) : null}
    </div>
  );
}
