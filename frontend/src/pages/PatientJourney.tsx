import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { Link, useParams } from "react-router-dom";
import { api } from "../api";
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
import { approvalKind, eventKind, obligationTone, type Tone } from "../tones";
import type { Approval, Evidence, EventItem, Journey, LedgerAction, Obligation } from "../types";
import { Empty, ErrorBanner, Spinner, cx, formatDate } from "../ui";

/**
 * Patient page. Reading order: who this is, what is late, what needs a
 * person, what everyone else is waiting on. Color is a status signal only
 * (see src/tones.ts): red late, amber waiting on a person, green done,
 * blue a clinic record, violet a call.
 */

type Tab = "followups" | "history" | "ask";

const NEEDS = new Set(["overdue", "conflict", "patient_reported", "escalated", "unknown"]);

function firstName(name: string): string {
  return name.split(" ")[0] || name;
}

function sentence(text?: string | null, max = 150): string {
  if (!text) return "";
  const clean = text.replace(/\s+/g, " ").trim();
  if (clean.length <= max) return clean;
  const cut = clean.slice(0, max - 3);
  return `${cut.slice(0, Math.max(cut.lastIndexOf(" "), max - 50)).trimEnd()}…`;
}

const shortDate = (iso?: string | null) => formatDate(iso, { year: undefined });

function timeOf(iso: string): string {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? "" : new Intl.DateTimeFormat("en-CA", { hour: "numeric", minute: "2-digit" }).format(d);
}

function dayKey(iso: string): string {
  return iso.slice(0, 10);
}

function dayLabel(key: string, today?: string): string {
  if (today && key === today) return "Today";
  if (today) {
    const y = new Date(`${today}T12:00:00`);
    y.setDate(y.getDate() - 1);
    if (y.toISOString().slice(0, 10) === key) return "Yesterday";
  }
  return formatDate(key, { weekday: "long", month: "long", day: "numeric", year: undefined });
}

function waited(iso: string): string {
  const minutes = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 60000));
  if (minutes < 60) return `${minutes} min`;
  const hours = Math.round(minutes / 60);
  if (hours < 48) return `${hours} h`;
  return `${Math.round(hours / 24)} days`;
}

/** The status label on the right of a row, with its tone. */
function statusFor(o: Obligation, approval?: Approval): { text: string; tone: Tone; badge?: boolean } {
  if (approval) {
    const urgent = approval.urgency === "urgent" || approval.urgency === "emergency";
    const kind = approvalKind(approval);
    return urgent ? { text: `${approval.urgency}`, tone: "red", badge: true } : { text: kind.label, tone: kind.tone === "neutral" ? "amber" : kind.tone };
  }
  switch (o.status) {
    case "overdue":
      return { text: o.days_overdue ? `${o.days_overdue} day${o.days_overdue === 1 ? "" : "s"} overdue` : "Overdue", tone: "red" };
    case "conflict":
      return { text: "Two dates on record", tone: "amber" };
    case "patient_reported":
      return { text: "Needs proof", tone: "amber" };
    case "escalated":
      return { text: "With the clinician", tone: "red" };
    case "scheduled":
      return { text: o.confirmed_date ? `Booked ${shortDate(o.confirmed_date)}` : `Due ${shortDate(o.due_date)}`, tone: "blue" };
    case "verified":
      return { text: `Verified ${shortDate(o.clinic_completed_on || o.updated_at)}`, tone: "green" };
    case "cancelled":
      return { text: "Cancelled", tone: "neutral" };
    default:
      return { text: o.due_date ? `Due ${shortDate(o.due_date)}` : "No date", tone: "neutral" };
  }
}

function subFor(o: Obligation, approval?: Approval): string {
  if (approval) return sentence(approval.rationale || approval.title);
  const conflict = (o.conflicts || []).find((c) => !c.resolved);
  if (o.status === "conflict" && conflict) {
    return `Clinic record says ${conflict.recorded_value || "nothing"}. The patient says ${conflict.reported_value || "nothing"}.`;
  }
  if (o.status === "overdue" && o.next_action) return sentence(o.next_action);
  if (o.latest_evidence) return sentence(o.latest_evidence);
  if (o.next_action) return sentence(o.next_action);
  return `Owner: ${o.owner}.`;
}

function Status({ text, tone, badge }: { text: string; tone: Tone; badge?: boolean }) {
  return <span className={cx("fu-status", `tone-${tone}`, badge && "fu-badge")}>{text}</span>;
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
  const status = statusFor(obligation, approval);
  const tone = approval ? (status.tone === "red" ? "red" : "amber") : obligationTone(obligation);
  const done = !obligation.is_open;
  return (
    <div className={cx("fu-row", `fu-${tone}`, done && "fu-done", open && "fu-open")}>
      <button type="button" className="fu-main" onClick={onToggle} aria-expanded={open}>
        <span className={cx("dot", `dot-${tone}`)} aria-hidden="true" />
        <b className="fu-title">{obligation.description}</b>
        <Status {...status} />
        <span className="fu-sub">{subFor(obligation, approval)}</span>
        <span className="fu-chev" aria-hidden="true" />
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

function DecisionRow({ approval }: { approval: Approval }) {
  const kind = approvalKind(approval);
  const urgent = approval.urgency === "urgent" || approval.urgency === "emergency";
  return (
    <div className={cx("fu-row", `fu-${kind.tone}`)}>
      <Link to={`/approvals?id=${approval.id}`} className="fu-main">
        <span className={cx("dot", `dot-${urgent ? "red" : "amber"}`)} aria-hidden="true" />
        <b className="fu-title">{approval.title}</b>
        {urgent ? <Status text={`${approval.urgency}`} tone="red" badge /> : <Status text={kind.label} tone={kind.tone === "neutral" ? "amber" : kind.tone} />}
        <span className="fu-sub">{sentence(approval.rationale, 140)}</span>
        <span className="fu-chev" aria-hidden="true" />
      </Link>
    </div>
  );
}

function Group({ label, count, tone, hint, children }: { label: string; count: number; tone: Tone; hint: string; children: ReactNode }) {
  return (
    <section className={cx("fu-group", `fu-group-${tone}`)}>
      <header>
        <h3>{label}</h3>
        <span className="fu-count">{count}</span>
        <span className="fu-hint">{hint}</span>
      </header>
      {count === 0 ? <p className="fu-empty">Nothing here.</p> : children}
    </section>
  );
}

function HistoryItem({ event }: { event: EventItem }) {
  const [open, setOpen] = useState(false);
  const [expanded, setExpanded] = useState(false);
  const kind = eventKind(event);
  const lines = event.transcript || [];
  const long = (event.detail || "").length > 180;
  return (
    <li className={cx("hist-item", `hist-${kind.tone}`)}>
      <span className="hist-time">{timeOf(event.occurred_at)}</span>
      <span className={cx("hist-dot", `dot-${kind.tone}`)} aria-hidden="true" />
      <div className="hist-body">
        <div className="hist-head">
          <b>{event.title}</b>
          <span className={cx("tag-tone", `tone-${kind.tone}`)}>{kind.label}</span>
        </div>
        {event.detail ? (
          <p className={cx("hist-detail", long && !expanded && "clamp")}>
            {event.detail}
          </p>
        ) : null}
        <div className="hist-meta">
          <span>{event.actor || "Continuum"}</span>
          {long ? (
            <button type="button" className="hist-link" onClick={() => setExpanded((v) => !v)}>
              {expanded ? "Show less" : "Show all"}
            </button>
          ) : null}
          {lines.length ? (
            <button type="button" className="hist-link" onClick={() => setOpen((v) => !v)}>
              {open ? "Hide transcript" : `Transcript, ${lines.length} turns`}
            </button>
          ) : null}
        </div>
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
    </li>
  );
}

const ACTION_TONE: Record<string, Tone> = { confirmed: "green", sent: "blue", intended: "neutral", failed: "red", unconfirmed: "amber", unknown: "amber" };
const ACTION_LABEL: Record<string, string> = { confirmed: "Confirmed", sent: "Waiting for proof", intended: "Not sent yet", failed: "Failed", unconfirmed: "No proof", unknown: "Unknown" };

function ActionsCompact({ actions }: { actions: LedgerAction[] }) {
  const [all, setAll] = useState(false);
  const shown = all ? actions : actions.slice(0, 6);
  return (
    <>
      <ul className="act-list">
        {shown.map((a) => {
          const tone = ACTION_TONE[a.status] || "neutral";
          return (
            <li key={a.id}>
              <span className={cx("dot", `dot-${tone}`)} aria-hidden="true" />
              <span className="act-main">
                <b>{a.summary}</b>
                <small>
                  {a.status === "confirmed" ? a.confirmation || a.history.at(-1)?.detail : a.proof_expected ? `Waiting for ${a.proof_expected.toLowerCase()}` : a.history.at(-1)?.detail}
                </small>
              </span>
              <span className={cx("act-status", `tone-${tone}`)}>{ACTION_LABEL[a.status] || a.status}</span>
            </li>
          );
        })}
      </ul>
      {actions.length > 6 ? (
        <button type="button" className="hist-link act-more" onClick={() => setAll((v) => !v)}>
          {all ? "Show fewer" : `Show all ${actions.length}`}
        </button>
      ) : null}
    </>
  );
}

export function PatientJourney() {
  const { patientId = "" } = useParams();
  const { refreshKey, refresh, dashboard, config } = useApp();
  const [journey, setJourney] = useState<Journey | null>(null);
  const [actions, setActions] = useState<LedgerAction[] | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [tab, setTab] = useState<Tab>("followups");
  const [openRow, setOpenRow] = useState<string | null>(null);
  const moreRef = useRef<HTMLDetailsElement>(null);
  const [showAll, setShowAll] = useState<Set<string>>(new Set());

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

  const days = useMemo(() => {
    const groups = new Map<string, EventItem[]>();
    for (const e of journey?.events || []) {
      const key = dayKey(e.occurred_at);
      if (!groups.has(key)) groups.set(key, []);
      groups.get(key)!.push(e);
    }
    return [...groups.entries()];
  }, [journey?.events]);

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
  const needs = open
    .filter((o) => NEEDS.has(o.status) || decisionFor.has(o.id))
    .sort((a, b) => (a.status === "overdue" ? -1 : 0) - (b.status === "overdue" ? -1 : 0));
  const waiting = open.filter((o) => !NEEDS.has(o.status) && !decisionFor.has(o.id));
  const done = journey.obligations.filter((o) => !o.is_open);
  const identity = patient.identity_review?.status === "needed";
  const overdue = open.filter((o) => o.status === "overdue").length;
  const urgent = pending.filter((a) => a.urgency === "urgent" || a.urgency === "emergency").length;
  const needsCount = needs.length + otherDecisions.length + (identity ? 1 : 0);

  const changed = async () => {
    await load();
    await refresh();
  };
  const closeMore = () => moreRef.current?.removeAttribute("open");

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

  const day = tcm?.days_since_discharge;
  const highRisk = patient.risk === "High";

  return (
    <div className="pt">
      <nav className="breadcrumb" aria-label="Breadcrumb">
        <Link to="/patients">Patients</Link>
      </nav>

      <header className="pt-head">
        <div className="pt-id">
          <div className="pt-name">
            <h1>{patient.name}</h1>
            {patient.pronouns ? <span className="pt-pronouns">{patient.pronouns}</span> : null}
          </div>
          <p className="pt-condition">{episode?.reason || patient.condition}</p>
          <p className="pt-stay">
            {episode ? (
              <>
                Discharged {formatDate(episode.discharge_date)}
                {episode.admission_date ? ` after a stay from ${shortDate(episode.admission_date)}` : ""}.
                {day !== undefined && day !== null ? ` Day ${Math.max(0, day)} of 30.` : ""}
              </>
            ) : (
              "No active hospital stay."
            )}
          </p>
          <dl className="pt-facts">
            <div>
              <dt>Age</dt>
              <dd>{patient.age}</dd>
            </div>
            <div>
              <dt>Born</dt>
              <dd>{patient.birth_date ? formatDate(patient.birth_date) : "Not recorded"}</dd>
            </div>
            <div>
              <dt>MRN</dt>
              <dd className="pt-mrn">{patient.mrn}</dd>
            </div>
            <div>
              <dt>Readmission risk</dt>
              <dd className={cx(highRisk && "tone-red", patient.risk === "Medium" && "tone-amber")}>{patient.risk}</dd>
            </div>
            {tcm?.visit_window_days ? (
              <div>
                <dt>Visit window</dt>
                <dd>{tcm.visit_window_days} days</dd>
              </div>
            ) : null}
            {prefs.interpreter_needed ? (
              <div>
                <dt>Interpreter</dt>
                <dd className="tone-amber">{prefs.language || "Needed"}</dd>
              </div>
            ) : null}
          </dl>
          <div className="pt-flags" aria-label="What needs attention">
            {overdue ? <span className="flag tone-red">{overdue} overdue</span> : null}
            {urgent ? <span className="flag tone-red">{urgent} urgent</span> : null}
            {pending.length ? <span className="flag tone-amber">{pending.length} {pending.length === 1 ? "decision" : "decisions"} waiting</span> : null}
            {waiting.length ? <span className="flag tone-blue">{waiting.length} waiting on others</span> : null}
            {done.length ? <span className="flag tone-green">{done.length} done</span> : null}
            {!overdue && !pending.length && !waiting.length && !done.length ? <span className="flag tone-neutral">No follow-ups yet</span> : null}
          </div>
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
          <span className="dot dot-red" aria-hidden="true" />
          <div>
            <b>Is this the same {patient.name}?</b> {patient.identity_review?.reason || "Another record matched by name only."} The records have not
            been merged.
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
            ["followups", "Follow-ups", needsCount],
            ["history", "History", journey.events.length],
            ["ask", `Ask about ${first}`, null],
          ] as [Tab, string, number | null][]
        ).map(([key, label, n]) => (
          <button key={key} type="button" role="tab" aria-selected={tab === key} className={cx(tab === key && "on")} onClick={() => setTab(key)}>
            {label}
            {n ? <span className={cx("pt-tab-n", key === "followups" && "tone-amber")}>{n}</span> : null}
          </button>
        ))}
      </div>

      {tab === "followups" ? (
        <div className="pt-cols">
          <div className="pt-list" aria-label="Follow-ups">
            <Group label="Needs someone" count={needsCount} tone={overdue || urgent ? "red" : "amber"} hint="Late, disputed, or waiting for a decision">
              {identity ? (
                <div className="fu-row fu-red">
                  <Link to="/review#identity" className="fu-main">
                    <span className="dot dot-red" aria-hidden="true" />
                    <b className="fu-title">Is this the same {patient.name}?</b>
                    <Status text="Identity check" tone="red" badge />
                    <span className="fu-sub">{patient.identity_review?.reason || "Another record matched by name only."}</span>
                    <span className="fu-chev" aria-hidden="true" />
                  </Link>
                </div>
              ) : null}
              {needs.map(row)}
              {otherDecisions.map((a) => (
                <DecisionRow key={a.id} approval={a} />
              ))}
            </Group>
            <Group label="Waiting on others" count={waiting.length} tone="blue" hint="Booked or in progress with a clinic">
              {waiting.map(row)}
            </Group>
            <Group label="Done" count={done.length} tone="green" hint="Closed with proof">
              {done.map(row)}
            </Group>
          </div>

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
                <dd className="pt-phone">{patient.phone || <span className="tone-red">None on file</span>}</dd>
                {patient.email ? (
                  <>
                    <dt>Email</dt>
                    <dd>{patient.email}</dd>
                  </>
                ) : null}
                <dt>Best time</dt>
                <dd>
                  {prefs.best_time || "Any"}
                  {prefs.channel ? <span className="muted">, {prefs.channel.toLowerCase()}</span> : null}
                </dd>
                <dt>Language</dt>
                <dd>
                  {prefs.language || "English"}
                  {prefs.interpreter_needed ? <span className="tone-amber">, interpreter</span> : null}
                </dd>
                <dt>Caregiver</dt>
                <dd>
                  {caregiver?.name || patient.caregiver ? (
                    <>
                      {caregiver?.name || patient.caregiver}
                      {caregiver?.relationship ? <span className="muted">, {caregiver.relationship}</span> : null}
                      {caregiver?.phone ? <div className="pt-phone">{caregiver.phone}</div> : null}
                      {caregiver?.may_discuss_care ? <div className="tone-green small">May discuss care</div> : null}
                    </>
                  ) : (
                    <span className="muted">None</span>
                  )}
                </dd>
                <dt>Automated calls</dt>
                <dd className={prefs.consent_to_automated_calls ? "tone-green" : "tone-red"}>{prefs.consent_to_automated_calls ? "Allowed" : "Not allowed"}</dd>
                <dt>Recording</dt>
                <dd className={prefs.consent_to_recording ? "tone-green" : "muted"}>{prefs.consent_to_recording ? "Allowed" : "Not allowed"}</dd>
                {prefs.accessibility ? (
                  <>
                    <dt>Accessibility</dt>
                    <dd className="tone-amber">{prefs.accessibility}</dd>
                  </>
                ) : null}
                <dt>Coordinator</dt>
                <dd>{summary?.coordinator || <span className="muted">Unassigned</span>}</dd>
              </dl>
            </section>

            <section className="pt-panel">
              <div className="pt-panel-head">
                <h2>Hospital stay</h2>
              </div>
              {episode ? (
                <dl className="pt-kv">
                  <dt>Admitted</dt>
                  <dd>{formatDate(episode.admission_date)}</dd>
                  <dt>Discharged</dt>
                  <dd>{formatDate(episode.discharge_date)}</dd>
                  <dt>Reason</dt>
                  <dd>{episode.reason || patient.condition}</dd>
                </dl>
              ) : (
                <p className="muted pt-panel-body">No active hospital stay.</p>
              )}
              {episode?.discharge_summary ? (
                <details className="pt-doc">
                  <summary>Read the discharge summary</summary>
                  <pre className="document-text">{episode.discharge_summary}</pre>
                </details>
              ) : null}
              <div className="pt-panel-foot">
                <EpisodeActions patientId={patient.id} episode={episode} onChanged={changed} />
              </div>
            </section>
          </aside>
        </div>
      ) : null}

      {tab === "history" ? (
        <div className="pt-cols">
          <div className="pt-stack">
            <div className="hist-legend" aria-label="Legend">
              <span><i className="dot dot-violet" /> Call</span>
              <span><i className="dot dot-blue" /> Clinic or EHR record</span>
              <span><i className="dot dot-amber" /> Decision</span>
              <span><i className="dot dot-green" /> Verified</span>
              <span><i className="dot dot-red" /> Alert</span>
            </div>
            {days.length === 0 ? (
              <p className="fu-empty">No history yet.</p>
            ) : (
              days.map(([key, events]) => (
                <section key={key} className="hist-day">
                  <h3>
                    {dayLabel(key, config?.demo_today)} <span className="fu-count">{events.length}</span>
                  </h3>
                  <ol className="hist-list">
                    {(showAll.has(key) ? events : events.slice(0, 8)).map((event) => (
                      <HistoryItem key={event.id} event={event} />
                    ))}
                  </ol>
                  {events.length > 8 ? (
                    <button
                      type="button"
                      className="hist-link hist-more"
                      onClick={() =>
                        setShowAll((cur) => {
                          const next = new Set(cur);
                          if (next.has(key)) next.delete(key);
                          else next.add(key);
                          return next;
                        })
                      }
                    >
                      {showAll.has(key) ? "Show fewer" : `Show ${events.length - 8} earlier events from this day`}
                    </button>
                  ) : null}
                </section>
              ))
            )}
            <Link to="/audit" className="hist-link">
              Open the full audit log
            </Link>
          </div>
          <aside className="pt-side">
            {actions && actions.length ? (
              <section className="pt-panel">
                <div className="pt-panel-head">
                  <h2>Calls and tasks Continuum started</h2>
                </div>
                <ActionsCompact actions={actions} />
              </section>
            ) : null}
            {journey.checkins.length ? (
              <section className="pt-panel">
                <div className="pt-panel-head">
                  <h2>Scheduled check-ins</h2>
                </div>
                <ul className="act-list">
                  {journey.checkins.map((c) => (
                    <li key={c.id}>
                      <span className={cx("dot", c.status.includes("review") || c.status.includes("manual") ? "dot-amber" : "dot-blue")} aria-hidden="true" />
                      <span className="act-main">
                        <b>{sentence(c.reason, 90)}</b>
                        <small>{shortDate(c.scheduled_for)}</small>
                      </span>
                      <span className="act-status tone-neutral">{c.status.replace(/_/g, " ")}</span>
                    </li>
                  ))}
                </ul>
              </section>
            ) : null}
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
                Every answer is checked against the clinic record. A patient's word is recorded but never treated as proof, and sensitive changes wait for a
                person. <Link to={`/patients/${patient.id}/agent`}>See the last run</Link>
              </p>
            </section>
          </aside>
        </div>
      ) : null}
    </div>
  );
}
