import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";
import { ActionList } from "../components/ActionLedger";
import { ApprovalCard } from "../components/ApprovalCard";
import { DemoRunner } from "../components/DemoRunner";
import { ObligationCard } from "../components/ObligationCard";
import { TcmWorklist } from "../components/TcmWorklist";
import { Timeline } from "../components/Timeline";
import { useApp } from "../state";
import type { LedgerAction } from "../types";
import { Card, Empty, ErrorBanner, formatDate, initials, RiskBadge, Spinner, cx } from "../ui";

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
          <h1>Today’s follow-ups</h1>
          <p className="page-sub">
            {stats.open_obligations} open loops across {stats.patients} patients.{" "}
            {stats.overdue > 0 ? (
              <>
                <strong>{stats.overdue} overdue.</strong>{" "}
              </>
            ) : null}
            {stats.pending_approvals > 0 ? (
              <>
                <strong>
                  {stats.pending_approvals} waiting
                </strong>{" "}
                for a coordinator decision.
              </>
            ) : (
              "No sensitive changes are waiting for review."
            )}
          </p>
        </div>
        <div className="actions">
          <Link className="btn" to="/patients">
            View caseload
          </Link>
          <Link className="btn btn-primary" to="/approvals">
            Review approvals{stats.pending_approvals ? ` · ${stats.pending_approvals}` : ""}
          </Link>
        </div>
      </header>

      {error ? <ErrorBanner message={error} onRetry={refresh} /> : null}

      <section className="metric-row" aria-label="Key indicators">
        <Link to="/patients" className="metric">
          <span className="metric-label">Open loops</span>
          <strong className="metric-value">{stats.open_obligations}</strong>
          <span className="metric-note">Across {stats.patients} synthetic patients</span>
        </Link>
        <Link to="/patients?filter=overdue" className={cx("metric", stats.overdue > 0 && "metric-danger")}>
          <span className="metric-label">Overdue follow-ups</span>
          <strong className="metric-value">{stats.overdue}</strong>
          <span className="metric-note">{stats.overdue > 0 ? "Needs outreach today" : "Nothing past its due date"}</span>
        </Link>
        <Link to="/approvals" className={cx("metric", stats.pending_approvals > 0 && "metric-warn")}>
          <span className="metric-label">Awaiting decision</span>
          <strong className="metric-value">{stats.pending_approvals}</strong>
          <span className="metric-note">Human review before closure</span>
        </Link>
        <div className="metric-minor">
          <Link to="/patients?filter=conflict">
            <span>Conflicts</span>
            <b>{stats.conflicts}</b>
          </Link>
          <Link to="/patients?filter=verification">
            <span>Verification</span>
            <b>{stats.awaiting_verification}</b>
          </Link>
          <Link to="/patients?filter=escalated">
            <span>Escalated</span>
            <b>{stats.escalated}</b>
          </Link>
        </div>
      </section>

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
            title="Awaiting your decision"
            subtitle="Sensitive changes pause until a coordinator reviews them"
            action={
              <Link to="/approvals" className="link-inline">
                View all →
              </Link>
            }
          >
            {dashboard.pending_approvals.length === 0 ? (
              <Empty>Nothing waiting. All sensitive changes have been reviewed.</Empty>
            ) : (
              <div className="stack-tight">
                {dashboard.pending_approvals.slice(0, 2).map((approval) => (
                  <ApprovalCard key={approval.id} approval={approval} compact />
                ))}
              </div>
            )}
          </Card>

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

          <Card title="Caseload" subtitle="Ranked by overdue and conflicting loops">
            <div className="mini-patient-list">
              {dashboard.patients.map((patient) => (
                <Link key={patient.id} to={`/patients/${patient.id}`} className="mini-patient">
                  <span className={`avatar ${patient.risk === "High" ? "avatar-high" : ""}`}>
                    {patient.initials || initials(patient.name)}
                  </span>
                  <span className="mini-patient-main">
                    <strong>{patient.name}</strong>
                    <small>
                      {patient.condition} · {patient.open_count} open
                    </small>
                  </span>
                  <span className="mini-patient-badges">
                    {patient.overdue_count > 0 ? <em className="badge badge-danger">{patient.overdue_count} overdue</em> : null}
                    {patient.conflict_count > 0 ? <em className="badge badge-amber">{patient.conflict_count} conflict</em> : null}
                    <RiskBadge risk={patient.risk} />
                  </span>
                </Link>
              ))}
            </div>
          </Card>
        </div>
      </div>

      <Card title="Walk through the scenario" subtitle="One synthetic patient, five decisive moments. All data is synthetic.">
        <DemoRunner onDone={refresh} showReset compact />
      </Card>

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
        <Timeline events={dashboard.recent_events} limit={8} />
      </Card>

      {config ? (
        <section className="capability-strip" aria-label="System capabilities">
          <div>
            <strong>Memory</strong>
            <span>Atlas Vector Search + lexical fallback</span>
          </div>
          <div>
            <strong>Reasoning</strong>
            <span>{config.llm_enabled ? config.model : "Deterministic extraction fallback"}</span>
          </div>
          <div>
            <strong>Workflow</strong>
            <span>LangGraph · checkpointed · interruptible</span>
          </div>
          <div>
            <strong>Voice</strong>
            <span>{config.voice_enabled ? "ElevenLabs agent connected" : "ElevenLabs not configured"}</span>
          </div>
        </section>
      ) : null}
    </div>
  );
}
