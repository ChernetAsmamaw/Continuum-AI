import { useCallback, useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";
import { useAuth } from "../auth";
import { ApprovalCard } from "../components/ApprovalCard";
import { useApp } from "../state";
import type { Approval } from "../types";
import { Card, Empty, ErrorBanner, Spinner, cx } from "../ui";

type Tab = "pending" | "resolved" | "all";

const TABS: { id: Tab; label: string }[] = [
  { id: "pending", label: "Pending" },
  { id: "resolved", label: "Resolved" },
  { id: "all", label: "All" },
];

export function ApprovalsPage() {
  const { user } = useAuth();
  const { dashboard, refreshKey, refresh, notify } = useApp();
  const isClinician = user?.role === "clinician";
  const [approvals, setApprovals] = useState<Approval[]>([]);
  const [tab, setTab] = useState<Tab>("pending");
  const [patientFilter, setPatientFilter] = useState("");
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [bulkBusy, setBulkBusy] = useState(false);

  const toggle = (id: string) =>
    setSelected((current) => {
      const next = new Set(current);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  const bulk = async (approve: boolean) => {
    setBulkBusy(true);
    try {
      const result = await api.bulkResolve([...selected], approve);
      const skipped = result.results.length - result.resolved;
      notify(`${approve ? "Approved" : "Rejected"} ${result.resolved}${skipped ? `; ${skipped} skipped` : ""}. Each decision is in the audit log.`);
      setSelected(new Set());
      await load();
      await refresh();
    } catch (err) {
      notify(err instanceof Error ? err.message : "Could not resolve the selection.");
    } finally {
      setBulkBusy(false);
    }
  };
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const status = tab === "pending" ? "pending" : "";
      const data = await api.approvals({ status });
      const filtered =
        tab === "resolved" ? data.approvals.filter((approval) => approval.status !== "pending") : data.approvals;
      setApprovals(filtered);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not load approvals.");
    } finally {
      setLoading(false);
    }
  }, [tab]);

  useEffect(() => {
    void load();
  }, [load, refreshKey]);

  const visible = useMemo(
    () => approvals.filter((approval) => !patientFilter || approval.patient_id === patientFilter),
    [approvals, patientFilter],
  );

  const pendingCount = approvals.filter((approval) => approval.status === "pending").length;
  const totalPending = dashboard?.stats.pending_approvals ?? pendingCount;
  const patientName = (id: string) => dashboard?.patients.find((patient) => patient.id === id)?.name || id;

  return (
    <div className="stack">
      <header className="page-head">
        <div>
          <p className="eyebrow">
            <span className="eyebrow-dot" />
            Human in the loop
          </p>
          <h1>
            Approvals
            {totalPending > 0 ? <span className="count-badge">{totalPending}</span> : null}
          </h1>
          <p className="page-sub">
            {isClinician
              ? "Clinical escalations from the voice agent and coordinator land here for clinician review. Every decision is written to the audit log."
              : "Sensitive closures, cancellations, overrides and conflict resolutions require an explicit coordinator decision. Clinical escalations are routed to the clinician queue. Every decision is written to the audit log."}
          </p>
        </div>
        <div className="actions">
          <button
            className="btn"
            onClick={async () => {
              await load();
              await refresh();
            }}
          >
            Refresh
          </button>
        </div>
      </header>

      {error ? <ErrorBanner message={error} onRetry={load} /> : null}

      <Card className="filter-card">
        <div className="filter-bar">
          <div className="filter-chips">
            {TABS.map((item) => (
              <button
                key={item.id}
                className={cx("chip chip-button", tab === item.id && "chip-active")}
                onClick={() => setTab(item.id)}
                aria-pressed={tab === item.id}
              >
                {item.label}
                {item.id === "pending" && pendingCount > 0 ? ` · ${pendingCount}` : ""}
              </button>
            ))}
          </div>
          <label className="select-inline">
            <span>Patient</span>
            <select value={patientFilter} onChange={(event) => setPatientFilter(event.target.value)}>
              <option value="">All patients</option>
              {(dashboard?.patients ?? []).map((patient) => (
                <option key={patient.id} value={patient.id}>
                  {patient.name}
                </option>
              ))}
            </select>
          </label>
        </div>
      </Card>

      {loading && approvals.length === 0 ? (
        <Spinner label="Loading approvals" />
      ) : visible.length === 0 ? (
        <Empty>
          {tab === "pending"
            ? "No approvals waiting. Sensitive changes requested by the agent appear here."
            : "No approvals in this view."}
        </Empty>
      ) : (
        <div className="approval-grid">
          {visible.map((approval) => (
            <div key={approval.id} className="approval-wrap">
              <div className="approval-patient">
                {approval.status === "pending" && approval.mode !== "interrupt" ? (
                  <label className="approval-select">
                    <input
                      type="checkbox"
                      id={`select-${approval.id}`}
                      checked={selected.has(approval.id)}
                      onChange={() => toggle(approval.id)}
                      aria-label={`Select ${approval.title}`}
                    />
                  </label>
                ) : null}
                <span className="muted">Patient</span>
                <Link to={`/patients/${approval.patient_id}`}>{patientName(approval.patient_id)}</Link>
                <span className="approval-patient-meta">{approval.kind.replace(/_/g, " ")}</span>
              </div>
              <ApprovalCard
                approval={approval}
                onResolved={() => {
                  void load();
                }}
              />
            </div>
          ))}
        </div>
      )}

      {selected.size > 0 ? (
        <div className="bulk-bar" role="region" aria-label="Bulk decision">
          <strong>{selected.size} selected</strong>
          <div className="actions">
            <button className="btn" onClick={() => setSelected(new Set())} disabled={bulkBusy}>
              Clear
            </button>
            <button className="btn btn-danger-ghost" onClick={() => bulk(false)} disabled={bulkBusy}>
              Reject selected
            </button>
            <button className="btn btn-primary" onClick={() => bulk(true)} disabled={bulkBusy}>
              {bulkBusy ? "Applying…" : "Approve selected"}
            </button>
          </div>
        </div>
      ) : null}

      <Card title="Decision rules" subtitle="Why some changes pause for a human">
        <div className="rule-grid">
          <div>
            <strong>Patient report ≠ completion</strong>
            <p>A patient saying a visit happened records evidence and keeps the loop open until the clinic confirms.</p>
          </div>
          <div>
            <strong>Evidence or override</strong>
            <p>Verified, cancelled and escalated statuses require evidence or an explicit override reason.</p>
          </div>
          <div>
            <strong>Conflicts are preserved</strong>
            <p>Both the recorded clinic value and the patient report stay on the obligation, even after resolution.</p>
          </div>
          <div>
            <strong>Interrupts resume runs</strong>
            <p>Approving an interrupt-mode request resumes the checkpointed LangGraph run at the awaiting node.</p>
          </div>
        </div>
      </Card>
    </div>
  );
}
