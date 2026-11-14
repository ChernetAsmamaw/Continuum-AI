import { useCallback, useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api } from "../api";
import { ActionList } from "../components/ActionLedger";
import { ApprovalCard } from "../components/ApprovalCard";
import { Icon } from "../components/Icon";
import { MemoryPanel } from "../components/MemoryPanel";
import { ObligationCard } from "../components/ObligationCard";
import { EditPatient } from "../components/EditPatient";
import { EpisodeActions } from "../components/EpisodeActions";
import { RecordActions } from "../components/RecordActions";
import { TcmPanel } from "../components/TcmPanel";
import { Timeline } from "../components/Timeline";
import { useApp } from "../state";
import type { Journey, LedgerAction } from "../types";
import {
  Card,
  Empty,
  ErrorBanner,
  formatDate,
  formatDateTime,
  initials,
  RiskBadge,
  Spinner,
  StatusPill,
  cx,
} from "../ui";

type ObligationTab = "open" | "all" | "closed";

export function PatientJourney() {
  const { patientId = "" } = useParams();
  const { refreshKey, refresh, config } = useApp();
  const [journey, setJourney] = useState<Journey | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [tab, setTab] = useState<ObligationTab>("open");
  const [openCall, setOpenCall] = useState<string | null>(null);
  const [actions, setActions] = useState<LedgerAction[] | null>(null);
  const [actionView, setActionView] = useState<"all" | "attention">("all");

  const load = useCallback(async () => {
    if (!patientId) return;
    try {
      const [data, ledger] = await Promise.all([
        api.journey(patientId),
        api.actions({ patient_id: patientId, limit: 100 }).catch(() => null),
      ]);
      setJourney(data);
      setActions(ledger ? ledger.actions : null);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not load this patient.");
    } finally {
      setLoading(false);
    }
  }, [patientId]);

  useEffect(() => {
    setLoading(true);
    void load();
  }, [load, refreshKey]);

  if (loading && !journey) return <Spinner label="Loading patient journey" />;
  if (error && !journey) return <ErrorBanner message={error} onRetry={load} />;
  if (!journey) return <Empty>Patient not found.</Empty>;

  const { patient, episode } = journey;
  const obligations = journey.obligations;
  const openObligations = obligations.filter((obligation) => obligation.is_open);
  const closedObligations = obligations.filter((obligation) => !obligation.is_open);
  const filtered =
    tab === "open" ? openObligations : tab === "closed" ? closedObligations : obligations;
  const pendingApprovals = journey.approvals.filter((approval) => approval.status === "pending");
  const resolvedApprovals = journey.approvals.filter((approval) => approval.status !== "pending");
  const counts = {
    overdue: openObligations.filter((obligation) => obligation.status === "overdue").length,
    conflict: openObligations.filter((obligation) => obligation.status === "conflict").length,
    verify: openObligations.filter((obligation) => obligation.status === "patient_reported").length,
    escalated: openObligations.filter((obligation) => obligation.status === "escalated").length,
    verified: obligations.filter((obligation) => obligation.status === "verified").length,
  };

  const patientChanged = async () => {
    await load();
    await refresh();
  };

  return (
    <div className="stack">
      <nav className="breadcrumb" aria-label="Breadcrumb">
        <Link to="/patients">Patients</Link>
        <span aria-hidden="true">/</span>
        <span>{patient.name}</span>
      </nav>

      <header className="patient-hero">
        <div className="patient-hero-main">
          <span className={`avatar avatar-xl ${patient.risk === "High" ? "avatar-high" : ""}`}>
            {patient.initials || initials(patient.name)}
          </span>
          <div>
            <p className="eyebrow">
              <span className="eyebrow-dot" />
              {episode?.reason || patient.condition}
            </p>
            <h1>{patient.name}</h1>
            <p className="patient-hero-meta">
              {patient.age} years{patient.birth_date ? ` (born ${formatDate(patient.birth_date)})` : ""} · {patient.pronouns || "—"} · MRN {patient.mrn} ·{" "}
              {patient.condition}
              {patient.preferences?.interpreter_needed ? ` · Interpreter: ${patient.preferences.language || "needed"}` : ""}
            </p>
            <div className="tag-row">
              <RiskBadge risk={patient.risk} />
              <span className="tag tag-muted">{patient.phone || "No phone on file"}</span>
              <span className="tag tag-muted">Caregiver: {patient.caregiver || "—"}</span>
              {patient.synthetic ? <span className="tag tag-blue">Synthetic record</span> : null}
            </div>
          </div>
        </div>
        <div className="actions">
          <EditPatient patient={patient} onChanged={patientChanged} />
          <RecordActions patientId={patient.id} dischargeDate={episode?.discharge_date} onChanged={patientChanged} />
          <a className="btn" href="#voice">
            Voice channel
          </a>
          <Link className="btn btn-primary" to={`/patients/${patient.id}/agent`}>
            Ask Continuum
          </Link>
        </div>
      </header>

      {error ? <ErrorBanner message={error} onRetry={load} /> : null}

      {patient.identity_review?.status === "needed" ? (
        <div className="banner banner-warn identity-banner" role="status">
          <div>
            <strong>Possible duplicate patient.</strong>
            <p>
              {patient.identity_review.reason || "This record matched another patient by name only."} Records have not been
              merged.
            </p>
          </div>
          <Link className="btn btn-small btn-primary" to="/review#identity">
            Review identity
          </Link>
        </div>
      ) : null}

      <section className="loop-summary" aria-label="Loop summary">
        <div className="loop-lead">
          <span className="loop-lead-label">Open loops</span>
          <strong>{openObligations.length}</strong>
          <span className="loop-lead-note">{counts.verified} verified in this episode</span>
        </div>
        <div className="loop-facts">
          <div className={cx("loop-fact", counts.overdue > 0 && "loop-fact-danger")}>
            <span>Overdue</span>
            <b>{counts.overdue}</b>
          </div>
          <div className={cx("loop-fact", counts.conflict > 0 && "loop-fact-amber")}>
            <span>Conflicts</span>
            <b>{counts.conflict}</b>
          </div>
          <div className={cx("loop-fact", counts.verify > 0 && "loop-fact-blue")}>
            <span>Awaiting verification</span>
            <b>{counts.verify}</b>
          </div>
          <div className={cx("loop-fact", counts.escalated > 0 && "loop-fact-purple")}>
            <span>Escalated</span>
            <b>{counts.escalated}</b>
          </div>
        </div>
        <div className="loop-episode">
          <span>Active episode</span>
          <strong>{episode ? `${formatDate(episode.discharge_date)} discharge` : "No active episode"}</strong>
          {episode ? (
            <small>
              {episode.reason} · {episode.id}
            </small>
          ) : null}
          <EpisodeActions patientId={patient.id} episode={episode} onChanged={patientChanged} />
        </div>
      </section>

      {journey.tcm ? <TcmPanel patientId={patient.id} tcm={journey.tcm} today={config?.demo_today} onChanged={patientChanged} /> : null}

      <div className="journey-grid">
        <div className="stack">
          <Card
            title="Obligations"
            subtitle="Open loops stay visible until evidence closes them. History is never overwritten."
            action={
              <div className="tabs">
                {(["open", "all", "closed"] as ObligationTab[]).map((item) => (
                  <button
                    key={item}
                    className={cx("tab", tab === item && "tab-active")}
                    onClick={() => setTab(item)}
                    aria-pressed={tab === item}
                  >
                    {item === "open" ? `Open ${openObligations.length}` : item === "closed" ? `Closed ${closedObligations.length}` : `All ${obligations.length}`}
                  </button>
                ))}
              </div>
            }
          >
            {filtered.length === 0 ? (
              <Empty>
                {tab === "open" ? "No open obligations for this patient." : "Nothing in this view."}
              </Empty>
            ) : (
              <div className="obligation-list">
                {filtered.map((obligation) => (
                  <ObligationCard
                    key={obligation.id}
                    obligation={obligation}
                    evidence={journey.evidence[obligation.id] || []}
                    onChanged={patientChanged}
                  />
                ))}
              </div>
            )}
          </Card>

          <Card
            title="Action ledger"
            subtitle="Calls and tasks Continuum started, and the proof each one is waiting for"
            action={
              actions && actions.length > 0 ? (
                <div className="tabs">
                  {(["all", "attention"] as const).map((item) => {
                    const n = item === "all" ? actions.length : actions.filter((a) => a.status === "unconfirmed" || a.status === "unknown").length;
                    return (
                      <button key={item} className={cx("tab", actionView === item && "tab-active")} onClick={() => setActionView(item)} aria-pressed={actionView === item}>
                        {item === "all" ? `All ${n}` : `Needs attention ${n}`}
                      </button>
                    );
                  })}
                </div>
              ) : null
            }
          >
            {actions === null ? (
              <Empty>Action ledger unavailable.</Empty>
            ) : actions.length === 0 ? (
              <Empty>No outward actions recorded for this patient yet.</Empty>
            ) : (
              (() => {
                const shown = actionView === "attention" ? actions.filter((a) => a.status === "unconfirmed" || a.status === "unknown") : actions;
                return shown.length === 0 ? <Empty>Nothing needs attention.</Empty> : <ActionList actions={shown} />;
              })()
            )}
          </Card>

          <Card title="Care timeline" subtitle="Documents, voice calls, coordinator actions and agent runs">
            <Timeline events={journey.events} />
          </Card>

          <Card
            title="Voice calls"
            subtitle="ElevenLabs transcripts and post-call extraction"
            action={
              <a className="link-inline" href="#voice">
                Start a call →
              </a>
            }
          >
            {journey.voice_calls.length === 0 ? (
              <Empty>No calls recorded.</Empty>
            ) : (
              <div className="call-list">
                {journey.voice_calls.map((call) => {
                  const open = openCall === call.id;
                  return (
                    <article key={call.id} className="call-card">
                      <button className="call-head" onClick={() => setOpenCall(open ? null : call.id)}>
                        <span className={cx("call-icon", `call-${call.direction}`)}>
                          <Icon name="arrow-up-right" />
                        </span>
                        <span className="call-main">
                          <strong>
                            {call.direction === "outbound" ? "Outbound check-in" : "Inbound call"} · {call.outcome}
                          </strong>
                          <small>
                            {formatDateTime(call.started_at)}
                            {call.duration_secs ? ` · ${call.duration_secs}s` : ""}
                            {call.elevenlabs_conversation_id ? ` · ${call.elevenlabs_conversation_id}` : ""}
                          </small>
                        </span>
                        <span className="call-toggle">{open ? "−" : "+"}</span>
                      </button>
                      {call.agent_summary ? <p className="call-summary">{call.agent_summary}</p> : null}
                      {open && call.transcript && call.transcript.length > 0 ? (
                        <div className="transcript">
                          {call.transcript.map((line, index) => (
                            <div key={index} className={cx("transcript-line", `transcript-${line.role}`)}>
                              <span className="transcript-role">
                                {line.role === "agent" ? "Continuum" : "Patient"}
                              </span>
                              <p>{line.message}</p>
                              {line.time_in_call_secs !== undefined ? (
                                <small>{line.time_in_call_secs}s</small>
                              ) : null}
                            </div>
                          ))}
                        </div>
                      ) : null}
                    </article>
                  );
                })}
              </div>
            )}
          </Card>

          {journey.checkins.length > 0 ? (
            <Card title="Scheduled check-ins" subtitle="Coordinator-requested follow-up calls">
              <ul className="checkin-list">
                {journey.checkins.map((checkin) => (
                  <li key={checkin.id}>
                    <span className={cx(
                        "dot",
                        checkin.status === "scheduled" || checkin.status === "dispatched"
                          ? "dot-blue"
                          : checkin.status === "awaiting_manual_call" || checkin.status === "needs_review"
                            ? "dot-amber"
                            : "dot-neutral",
                      )} />
                    <div>
                      <strong>{formatDate(checkin.scheduled_for)}</strong>
                      <p>{checkin.reason}</p>
                    </div>
                    <span className="tag tag-muted">{checkin.status.replace(/_/g, " ")}</span>
                  </li>
                ))}
              </ul>
            </Card>
          ) : null}
        </div>

        <aside className="stack journey-side">
          <Card
            title="Approvals"
            subtitle="Sensitive changes wait for a coordinator"
            action={<Link to="/approvals" className="link-inline">All →</Link>}
          >
            {pendingApprovals.length === 0 && resolvedApprovals.length === 0 ? (
              <Empty>No approvals recorded.</Empty>
            ) : (
              <div className="stack-tight">
                {pendingApprovals.map((approval) => (
                  <ApprovalCard key={approval.id} approval={approval} compact onResolved={patientChanged} />
                ))}
                {resolvedApprovals.slice(0, 3).map((approval) => (
                  <ApprovalCard key={approval.id} approval={approval} compact onResolved={patientChanged} />
                ))}
              </div>
            )}
          </Card>

          <Card title="Preferences & context" subtitle="Stored across sessions">
            <dl className="kv">
              <div>
                <dt>Channel</dt>
                <dd>{patient.preferences?.channel || "—"}</dd>
              </div>
              <div>
                <dt>Best time</dt>
                <dd>{patient.preferences?.best_time || "—"}</dd>
              </div>
              <div>
                <dt>Language</dt>
                <dd>{patient.preferences?.language || "—"}</dd>
              </div>
              <div>
                <dt>Automated calls</dt>
                <dd>{patient.preferences?.consent_to_automated_calls ? "Consented" : "Not recorded"}</dd>
              </div>
              <div>
                <dt>Recording</dt>
                <dd>{patient.preferences?.consent_to_recording ? "Consented" : "Not recorded"}</dd>
              </div>
              <div>
                <dt>Accessibility</dt>
                <dd>{patient.preferences?.accessibility || "None recorded"}</dd>
              </div>
            </dl>
          </Card>

          <div id="voice">
            <Card title="Voice check-in">
              <div className="call-cta">
                <p>
                  Call {patient.name.split(" ")[0]} about open follow-ups. The call room shows the agent's brief, the
                  conversation, and what each answer changes.
                </p>
                <Link className="btn btn-primary" to={`/patients/${patient.id}/call`}>
                  Open call room
                </Link>
              </div>
            </Card>
          </div>

          <MemoryPanel patientId={patient.id} memories={journey.memories} />
        </aside>
      </div>

      {episode?.discharge_summary ? (
        <Card title="Source discharge summary" subtitle={`Discharge ${formatDate(episode.discharge_date)} · synthetic`}>
          <pre className="document-text">{episode.discharge_summary}</pre>
        </Card>
      ) : null}

      <section className="audit-preview">
        <div className="audit-preview-head">
          <h2>Recent audit entries</h2>
          <Link to="/audit" className="link-inline">
            Full audit log →
          </Link>
        </div>
        <div className="audit-list">
          {journey.audit.slice(0, 6).map((entry) => (
            <div key={entry.id} className="audit-row">
              <span className="audit-time">{formatDateTime(entry.created_at)}</span>
              <span className="audit-actor">{entry.actor}</span>
              <span className="audit-action">{entry.action}</span>
              <span className="audit-detail">{entry.detail}</span>
            </div>
          ))}
        </div>
      </section>

      <div className="status-legend">
        <span className="muted">Status reference</span>
        {["pending", "scheduled", "patient_reported", "conflict", "overdue", "escalated", "verified"].map((status) => (
          <StatusPill key={status} status={status} />
        ))}
      </div>
    </div>
  );
}
