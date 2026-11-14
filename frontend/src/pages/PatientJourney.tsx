import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { Link, useParams } from "react-router-dom";
import { api } from "../api";
import { ActionList } from "../components/ActionLedger";
import { ApprovalCard } from "../components/ApprovalCard";
import { CareClock } from "../components/CareClock";
import { ChatPanel } from "../components/ChatPanel";
import { EditPatient } from "../components/EditPatient";
import { EpisodeActions } from "../components/EpisodeActions";
import { MemoryPanel } from "../components/MemoryPanel";
import { ObligationCard } from "../components/ObligationCard";
import { RecordActions } from "../components/RecordActions";
import { LogContactButton } from "../components/TcmPanel";
import { useApp } from "../state";
import type { Approval, Evidence, EventItem, Journey, LedgerAction, Obligation } from "../types";
import { Empty, ErrorBanner, Spinner, cx, formatDate, formatDateTime } from "../ui";

/**
 * Patient page, after the design review: name and the facts that identify
 * the person, the 30-day clock, then one list of follow-ups grouped by who
 * is waiting on whom. A row opens in place to show its evidence and actions.
 * Contact details sit beside the list. History and the assistant are tabs.
 */

type Tab = "followups" | "history" | "ask";

const NEEDS = new Set(["overdue", "conflict", "patient_reported", "escalated", "unknown"]);
const DONE = new Set(["verified", "cancelled"]);

function firstName(name: string): string {
  return name.split(" ")[0] || name;
}

function sentence(text?: string | null): string {
  if (!text) return "";
  const clean = text.replace(/\s+/g, " ").trim();
  if (clean.length <= 150) return clean;
  const cut = clean.slice(0, 147);
  return `${cut.slice(0, Math.max(cut.lastIndexOf(" "), 100)).trimEnd()}…`;
}

function whenFor(o: Obligation, approval?: Approval): { text: string; red?: boolean; strong?: boolean } {
  if (approval) {
    const urgent = approval.urgency === "urgent" || approval.urgency === "emergency";
    return urgent ? { text: approval.urgency as string, red: true } : { text: "needs your decision", strong: true };
  }
  switch (o.status) {
    case "overdue":
      return { text: o.days_overdue ? `${o.days_overdue} day${o.days_overdue === 1 ? "" : "s"} overdue` : "overdue", red: true };
    case "conflict":
      return { text: "two dates on record" };
    case "patient_reported":
      return { text: "needs proof" };
    case "escalated":
      return { text: "with the clinician" };
    case "scheduled":
      return { text: o.confirmed_date ? `booked ${formatDate(o.confirmed_date)}` : `due ${formatDate(o.due_date)}` };
    case "verified":
      return { text: `verified ${formatDate(o.clinic_completed_on || o.updated_at)}` };
    case "cancelled":
      return { text: "cancelled" };
    default:
      return { text: o.due_date ? `due ${formatDate(o.due_date)}` : "no date" };
  }
}

function subFor(o: Obligation, approval?: Approval): string {
  if (approval) return sentence(approval.rationale || approval.title);
  const conflict = (o.conflicts || []).find((c) => !c.resolved);
  if (o.status === "conflict" && conflict) {
    return `Clinic record says ${conflict.recorded_value || "—"}; the patient says ${conflict.reported_value || "—"}.`;
  }
  if (o.status === "overdue" && o.next_action) return sentence(o.next_action);
  if (o.latest_evidence) return sentence(o.latest_evidence);
  if (o.next_action) return sentence(o.next_action);
  if (o.source?.label) return `From ${o.source.label}.`;
  return `Owner: ${o.owner}.`;
}

function FollowUpRow({
  obligation,
  evidence,
  approval,
  open,
  onToggle,
  onChanged,
}: {
  obligation: Obligation;
  evidence: Evidence[];
  approval?: Approval;
  open: boolean;
  onToggle: () => void;
  onChanged: () => void;
}) {
  const when = whenFor(obligation, approval);
  const done = DONE.has(obligation.status);
  const overdue = obligation.status === "overdue";
  return (
    <div className={cx("fu-row", overdue && "fu-overdue", done && "fu-done", open && "fu-open")}>
      <button type="button" className="fu-main" onClick={onToggle} aria-expanded={open}>
        <span className={cx("fu-dot", (overdue || when.red) && "fu-dot-red", done && "fu-dot-done")} aria-hidden="true" />
        <b className="fu-title">{obligation.description}</b>
        <span className={cx("fu-when", when.red && "fu-red", when.strong && "fu-strong")}>{when.text}</span>
        <span className="fu-sub">
          {subFor(obligation, approval)} <u>{open ? "Close" : approval ? "Decide →" : "Open →"}</u>
        </span>
      </button>
      {open ? (
        <div className="fu-detail">
          {approval ? <ApprovalCard approval={approval} compact onResolved={onChanged} /> : null}
          <ObligationCard obligation={obligation} evidence={evidence} onChanged={onChanged} />
        </div>
      ) : null}
    </div>
  );
}

function waited(iso: string): string {
  const minutes = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 60000));
  if (minutes < 60) return `${minutes} min ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 48) return `${hours} h ago`;
  return `${Math.round(hours / 24)} days ago`;
}

function DecisionRow({ approval }: { approval: Approval }) {
  const urgent = approval.urgency === "urgent" || approval.urgency === "emergency";
  return (
    <Link to="/approvals" className={cx("fu-row fu-link", urgent && "fu-overdue")}>
      <span className={cx("fu-dot", urgent && "fu-dot-red")} aria-hidden="true" />
      <b className="fu-title">{approval.title}</b>
      <span className={cx("fu-when", urgent && "fu-red")}>{urgent ? approval.urgency : waited(approval.created_at)}</span>
      <span className="fu-sub">
        {sentence(approval.rationale)} <u>Decide →</u>
      </span>
    </Link>
  );
}

function Group({ label, count, children }: { label: string; count: number; children: ReactNode }) {
  return (
    <>
      <div className="fu-group">
        {label} · {count}
      </div>
      {count === 0 ? <div className="fu-empty muted">Nothing.</div> : children}
    </>
  );
}

function HistoryRow({ event }: { event: EventItem }) {
  const [open, setOpen] = useState(false);
  const lines = event.transcript || [];
  return (
    <div className="hist-row">
      <span className="mono muted hist-time">{formatDateTime(event.occurred_at)}</span>
      <div className="hist-body">
        <b>{event.title}</b>
        {event.detail ? <p>{event.detail}</p> : null}
        <small className="muted">
          {event.actor || "Continuum"}
          {event.source_ref ? ` · ${event.source_ref}` : ""}
          {lines.length ? (
            <>
              {" · "}
              <button type="button" className="link-button" onClick={() => setOpen((v) => !v)}>
                {open ? "Hide transcript" : `Transcript (${lines.length})`}
              </button>
            </>
          ) : null}
        </small>
        {open ? (
          <div className="transcript">
            {lines.map((line, index) => (
              <div key={index} className={cx("transcript-line", `transcript-${line.role}`)}>
                <span className="transcript-role">{line.role === "agent" ? "Continuum" : "Patient"}</span>
                <p>{line.message}</p>
              </div>
            ))}
          </div>
        ) : null}
      </div>
    </div>
  );
}

export function PatientJourney() {
  const { patientId = "" } = useParams();
  const { refreshKey, refresh, dashboard } = useApp();
  const [journey, setJourney] = useState<Journey | null>(null);
  const [actions, setActions] = useState<LedgerAction[] | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [tab, setTab] = useState<Tab>("followups");
  const [openRow, setOpenRow] = useState<string | null>(null);
  const moreRef = useRef<HTMLDetailsElement>(null);

  const load = useCallback(async () => {
    if (!patientId) return;
    try {
      const [data, ledger] = await Promise.all([api.journey(patientId), api.actions({ patient_id: patientId, limit: 100 }).catch(() => null)]);
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

  useEffect(() => {
    setTab("followups");
    setOpenRow(null);
  }, [patientId]);

  if (loading && !journey) return <Spinner label="Loading patient" />;
  if (error && !journey) return <ErrorBanner message={error} onRetry={load} />;
  if (!journey) return <Empty>Patient not found.</Empty>;

  const { patient, episode, tcm } = journey;
  const first = firstName(patient.name);
  const prefs = patient.preferences || {};
  const caregiver = patient.caregiver_contact;
  const summary = dashboard?.patients.find((p) => p.id === patient.id);

  const pending = journey.approvals.filter((a) => a.status === "pending");
  // A decision about a follow-up is shown on that follow-up's row, not twice.
  const decisionFor = new Map<string, Approval>();
  for (const a of pending) if (a.obligation_id && !decisionFor.has(a.obligation_id)) decisionFor.set(a.obligation_id, a);
  const otherDecisions = pending.filter((a) => !a.obligation_id || decisionFor.get(a.obligation_id) !== a);
  const open = journey.obligations.filter((o) => o.is_open);
  const needs = open.filter((o) => NEEDS.has(o.status) || decisionFor.has(o.id));
  const waiting = open.filter((o) => !NEEDS.has(o.status) && !decisionFor.has(o.id));
  const done = journey.obligations.filter((o) => !o.is_open);
  const identity = patient.identity_review?.status === "needed";
  const needsCount = needs.length + otherDecisions.length + (identity ? 1 : 0);

  const changed = async () => {
    await load();
    await refresh();
  };
  const closeMore = () => moreRef.current?.removeAttribute("open");

  const windowDays = tcm?.visit_window_days;
  const meta = [
    patient.age ? `${patient.age}` : null,
    patient.birth_date ? `DOB ${patient.birth_date}` : null,
    `MRN ${patient.mrn}`,
    patient.pronouns || null,
    windowDays ? `${windowDays}-day visit window` : null,
  ]
    .filter(Boolean)
    .join(" · ");

  const stay = episode
    ? `${episode.admission_date ? `Admitted ${formatDate(episode.admission_date)}, ` : ""}${episode.admission_date ? "d" : "D"}ischarged ${formatDate(episode.discharge_date)}. ${episode.reason || patient.condition}.`
    : `${patient.condition}. No active episode.`;

  const evidenceFor = (o: Obligation) => journey.evidence[o.id] || [];
  const row = (o: Obligation) => (
    <FollowUpRow
      key={o.id}
      obligation={o}
      evidence={evidenceFor(o)}
      approval={decisionFor.get(o.id)}
      open={openRow === o.id}
      onToggle={() => setOpenRow((current) => (current === o.id ? null : o.id))}
      onChanged={() => void changed()}
    />
  );

  return (
    <div className="pt">
      <nav className="breadcrumb" aria-label="Breadcrumb">
        <Link to="/patients">Patients</Link>
      </nav>

      <header className="pt-head">
        <div className="pt-id">
          <h1>{patient.name}</h1>
          <p className="pt-meta mono">{meta}</p>
          <p className="pt-stay">
            {stay}
            {patient.risk === "High" ? <span className="pt-risk"> High readmission risk.</span> : null}
            {prefs.interpreter_needed ? ` Interpreter needed (${prefs.language || "language on file"}).` : ""}
          </p>
        </div>
        <div className="pt-actions">
          <details className="pt-more" ref={moreRef}>
            <summary className="btn">More</summary>
            <div className="pt-menu" onClick={closeMore}>
              <EditPatient patient={patient} onChanged={changed} />
              <RecordActions patientId={patient.id} dischargeDate={episode?.discharge_date} onChanged={changed} />
              <EpisodeActions patientId={patient.id} episode={episode} onChanged={changed} />
              <Link className="btn" to={`/patients/${patient.id}/agent`}>
                How decisions were made
              </Link>
            </div>
          </details>
          <LogContactButton patientId={patient.id} onChanged={changed} />
          <Link className="btn btn-primary" to={`/patients/${patient.id}/call`}>
            Call {first}
          </Link>
        </div>
      </header>

      {error ? <ErrorBanner message={error} onRetry={load} /> : null}

      {identity ? (
        <div className="pt-warn" role="status">
          <span className="fu-dot fu-dot-red" aria-hidden="true" />
          <div>
            <b>Is this the same {patient.name}?</b>{" "}
            {patient.identity_review?.reason || "Another record matched by name only."} Records have not been merged.
          </div>
          <Link className="btn btn-small" to="/review#identity">
            Review
          </Link>
        </div>
      ) : null}

      {tcm ? <CareClock tcm={tcm} patientId={patient.id} onChanged={changed} /> : null}

      <div className="pt-tabs" role="tablist">
        {(
          [
            ["followups", `Follow-ups`],
            ["history", "History"],
            ["ask", `Ask about ${first}`],
          ] as [Tab, string][]
        ).map(([key, label]) => (
          <button key={key} type="button" role="tab" aria-selected={tab === key} className={cx(tab === key && "on")} onClick={() => setTab(key)}>
            {label}
          </button>
        ))}
      </div>

      {tab === "followups" ? (
        <div className="pt-cols">
          <section className="pt-panel pt-list" aria-label="Follow-ups">
            <Group label="Needs someone" count={needsCount}>
              {identity ? (
                <Link to="/review#identity" className="fu-row fu-link fu-overdue">
                  <span className="fu-dot fu-dot-red" aria-hidden="true" />
                  <b className="fu-title">Is this the same {patient.name}?</b>
                  <span className="fu-when fu-red">identity</span>
                  <span className="fu-sub">
                    {patient.identity_review?.reason || "Another record matched by name only."} <u>Review →</u>
                  </span>
                </Link>
              ) : null}
              {needs.map(row)}
              {otherDecisions.map((a) => (
                <DecisionRow key={a.id} approval={a} />
              ))}
            </Group>
            <Group label="Waiting on others" count={waiting.length}>
              {waiting.map(row)}
            </Group>
            <Group label="Done" count={done.length}>
              {done.map(row)}
            </Group>
          </section>

          <aside className="pt-side">
            <section className="pt-panel">
              <div className="pt-panel-head">
                <h2>Contact and consent</h2>
                <span className="pt-edit">
                  <EditPatient patient={patient} onChanged={changed} />
                </span>
              </div>
              <dl className="pt-kv">
                <dt>Phone</dt>
                <dd className="mono-num">{patient.phone || <span className="pt-risk">None on file</span>}</dd>
                {patient.email ? (
                  <>
                    <dt>Email</dt>
                    <dd>{patient.email}</dd>
                  </>
                ) : null}
                <dt>Best time</dt>
                <dd>
                  {prefs.best_time || "Any"}
                  {prefs.channel ? `, ${prefs.channel.toLowerCase()}` : ""}
                </dd>
                <dt>Language</dt>
                <dd>
                  {prefs.language || "English"}
                  {prefs.interpreter_needed ? " · interpreter" : ""}
                </dd>
                <dt>Caregiver</dt>
                <dd>
                  {caregiver?.name || patient.caregiver ? (
                    <>
                      {caregiver?.name || patient.caregiver}
                      {caregiver?.relationship ? `, ${caregiver.relationship}` : ""}
                      {caregiver?.phone ? ` · ${caregiver.phone}` : ""}
                      {caregiver?.may_discuss_care ? <small className="muted"> · may discuss care</small> : null}
                    </>
                  ) : (
                    "None"
                  )}
                </dd>
                <dt>Automated calls</dt>
                <dd>{prefs.consent_to_automated_calls ? "Allowed" : <span className="pt-risk">Not allowed</span>}</dd>
                <dt>Call recording</dt>
                <dd>{prefs.consent_to_recording ? "Allowed" : "Not allowed"}</dd>
                {prefs.accessibility ? (
                  <>
                    <dt>Accessibility</dt>
                    <dd>{prefs.accessibility}</dd>
                  </>
                ) : null}
                <dt>Coordinator</dt>
                <dd>{summary?.coordinator || "Unassigned"}</dd>
              </dl>
            </section>

            <section className="pt-panel">
              <div className="pt-panel-head">
                <h2>Stay</h2>
              </div>
              {episode ? (
                <dl className="pt-kv">
                  <dt>Admitted</dt>
                  <dd>{formatDate(episode.admission_date)}</dd>
                  <dt>Discharged</dt>
                  <dd>{formatDate(episode.discharge_date)}</dd>
                  <dt>Reason</dt>
                  <dd>{episode.reason || patient.condition}</dd>
                  <dt>Episode</dt>
                  <dd className="mono muted">{episode.id}</dd>
                </dl>
              ) : (
                <p className="muted pt-panel-body">No active episode.</p>
              )}
              <div className="pt-panel-foot">
                <EpisodeActions patientId={patient.id} episode={episode} onChanged={changed} />
              </div>
              {episode?.discharge_summary ? (
                <details className="pt-doc">
                  <summary>Discharge summary</summary>
                  <pre className="document-text">{episode.discharge_summary}</pre>
                </details>
              ) : null}
            </section>
          </aside>
        </div>
      ) : null}

      {tab === "history" ? (
        <div className="pt-cols">
          <div className="pt-stack">
            {journey.checkins.length ? (
              <section className="pt-panel">
                <div className="pt-panel-head">
                  <h2>Scheduled check-ins</h2>
                </div>
                {journey.checkins.map((c) => (
                  <div key={c.id} className="hist-row">
                    <span className="mono muted hist-time">{formatDate(c.scheduled_for)}</span>
                    <div className="hist-body">
                      <b>{c.reason}</b>
                      <small className="muted">{c.status.replace(/_/g, " ")}</small>
                    </div>
                  </div>
                ))}
              </section>
            ) : null}
            {actions && actions.length ? (
              <section className="pt-panel">
                <div className="pt-panel-head">
                  <h2>Calls and tasks Continuum started</h2>
                  <span className="muted">{actions.length}</span>
                </div>
                <div className="pt-panel-body">
                  <ActionList actions={actions} compact />
                </div>
              </section>
            ) : null}
            <section className="pt-panel" aria-label="History">
              <div className="pt-panel-head">
                <h2>History</h2>
                <Link to={`/audit?patient=${patient.id}`} className="muted">
                  Full audit log →
                </Link>
              </div>
              {journey.events.length === 0 ? (
                <p className="muted pt-panel-body">No history yet.</p>
              ) : (
                journey.events.map((event) => <HistoryRow key={event.id} event={event} />)
              )}
            </section>
          </div>
          <aside className="pt-side">
            <MemoryPanel patientId={patient.id} memories={journey.memories} title="What Continuum remembers" compact />
          </aside>
        </div>
      ) : null}

      {tab === "ask" ? (
        <div className="pt-cols">
          <ChatPanel patientId={patient.id} patientName={patient.name} title={`Ask about ${first}`} subtitle="Answers use this patient's records only" onResult={() => void load()} />
          <aside className="pt-side">
            <section className="pt-panel">
              <div className="pt-panel-head">
                <h2>How answers are made</h2>
              </div>
              <p className="pt-panel-body muted">
                Every answer is checked against the clinic record. A patient's word is recorded, never treated as proof. Sensitive changes wait for a
                person. <Link to={`/patients/${patient.id}/agent`}>See the last run →</Link>
              </p>
            </section>
            <MemoryPanel patientId={patient.id} memories={journey.memories} title="What Continuum remembers" compact />
          </aside>
        </div>
      ) : null}
    </div>
  );
}
