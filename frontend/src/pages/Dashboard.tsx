import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";
import { ActionList } from "../components/ActionLedger";
import { DemoRunner } from "../components/DemoRunner";
import { ObligationCard } from "../components/ObligationCard";
import { TcmWorklist } from "../components/TcmWorklist";
import { Timeline } from "../components/Timeline";
import { useApp } from "../state";
import type { LedgerAction } from "../types";
import { Card, Empty, ErrorBanner, formatDate, Spinner } from "../ui";

/**
 * Overview: the summary and demo page. The working queue is "Needs you"
 * (/console). Following the design review, this page no longer repeats the
 * KPI tiles, the approvals column or the caseload (both live in the queue and
 * the rail); one sentence with linked counts says the same thing.
 */

export function Dashboard() {
  const { dashboard, config, loading, error, refresh, refreshKey, ops } = useApp();
  const [attention, setAttention] = useState<LedgerAction[] | null>(null);

  useEffect(() => {
    let live = true;
    api
      .actions({ status: "unconfirmed,unknown", limit: 5 })
      .then((data) => live && setAttention(data.actions))
      .catch(() => live && setAttention(null));
    return () => {
      live = false;
    };
  }, [refreshKey]);

  if (!dashboard) {
    return loading ? <Spinner label="Connecting to Continuum" /> : <ErrorBanner message={error || "No dashboard data."} onRetry={refresh} />;
  }

  const patientById = new Map(dashboard.patients.map((patient) => [patient.id, patient]));
  const stats = dashboard.stats;

  return (
    <div className="stack">
      <header className="page-head">
        <div>
          <p className="eyebrow">
            <span className="eyebrow-dot" />
            {config?.demo_today ? formatDate(config.demo_today) : "Today"} · Synthetic care panel
          </p>
          <h1>Overview</h1>
          <p className="page-sub overview-summary">
            <Link to="/patients">{stats.open_obligations} open loops</Link> across {stats.patients} patients.{" "}
            {stats.overdue > 0 ? (
              <>
                <Link to="/patients?filter=overdue" className="overview-danger">
                  {stats.overdue} overdue
                </Link>
                .{" "}
              </>
            ) : null}
            {stats.pending_approvals > 0 ? (
              <>
                <Link to="/approvals">{stats.pending_approvals} waiting</Link> for a decision.{" "}
              </>
            ) : null}
            {stats.conflicts > 0 ? (
              <>
                <Link to="/patients?filter=conflict">{stats.conflicts} with conflicting dates</Link>.{" "}
              </>
            ) : null}
            {stats.awaiting_verification > 0 ? (
              <>
                <Link to="/patients?filter=verification">{stats.awaiting_verification} to verify</Link>.{" "}
              </>
            ) : null}
            {stats.escalated > 0 ? (
              <>
                <Link to="/patients?filter=escalated">{stats.escalated} escalated</Link>.
              </>
            ) : null}
            {stats.overdue === 0 && stats.pending_approvals === 0 ? "Nothing is overdue or waiting on you." : null}
          </p>
        </div>
        <div className="actions">
          <Link className="btn" to="/patients/new">
            Add a discharge
          </Link>
          <Link className="btn btn-primary" to="/console">
            Open Needs you{stats.overdue + stats.pending_approvals ? ` · ${stats.overdue + stats.pending_approvals}` : ""}
          </Link>
        </div>
      </header>

      {error ? <ErrorBanner message={error} onRetry={refresh} /> : null}

      <TcmWorklist refreshKey={refreshKey} />

      <div className="dashboard-grid">
        <Card
          title="Needs attention"
          subtitle="Open loops ranked by overdue status, conflict and due date"
          action={
            <Link to="/patients" className="link-inline">
              All patients →
            </Link>
          }
        >
          {dashboard.priority_obligations.length === 0 ? (
            <Empty>No open obligations. Run the scenario to generate them.</Empty>
          ) : (
            <div className="obligation-list">
              {dashboard.priority_obligations.slice(0, 6).map((obligation) => (
                <ObligationCard
                  key={obligation.id}
                  obligation={obligation}
                  patientName={patientById.get(obligation.patient_id)?.name}
                  dense
                />
              ))}
            </div>
          )}
        </Card>

        <div className="stack">
          <Card
            title="Actions needing attention"
            subtitle="Sent but never confirmed, or interrupted mid-send"
            action={
              <Link to="/actions" className="link-inline">
                Action ledger →
              </Link>
            }
          >
            {attention === null ? (
              <Empty>Action ledger unavailable.</Empty>
            ) : attention.length === 0 ? (
              <Empty>Every outward action is confirmed, failed, or still inside its proof window.</Empty>
            ) : (
              <>
                <ActionList actions={attention} patientNames={new Map(dashboard.patients.map((p) => [p.id, p.name]))} showPatient compact />
                {(ops?.attention ?? 0) > attention.length ? (
                  <Link to="/actions" className="link-inline">
                    {(ops?.attention ?? 0) - attention.length} more →
                  </Link>
                ) : null}
              </>
            )}
            {ops && ops.review > 0 ? (
              <Link to="/review" className="review-callout">
                <strong>{ops.review} waiting for review</strong>
                <span>
                  {ops.reviewOrders} order type{ops.reviewOrders === 1 ? "" : "s"} to confirm · {ops.reviewIdentities} possible duplicate
                  {ops.reviewIdentities === 1 ? "" : "s"}
                </span>
              </Link>
            ) : null}
          </Card>
        </div>
      </div>

      <Card
        title="Recent activity"
        subtitle="Append-only event trail"
        className="card-activity"
        action={
          <Link to="/audit" className="link-inline">
            Audit log →
          </Link>
        }
      >
        <Timeline events={dashboard.recent_events} limit={6} />
      </Card>

      <Card
        title="Walk through the scenario"
        subtitle={`One synthetic patient, five decisive moments. ${config?.llm_enabled ? `Model: ${config.model}.` : "Deterministic extraction, no model key."} ${config?.voice_enabled ? "Voice agent connected." : "Voice agent not configured."}`}
      >
        <DemoRunner onDone={refresh} showReset compact />
      </Card>
    </div>
  );
}
