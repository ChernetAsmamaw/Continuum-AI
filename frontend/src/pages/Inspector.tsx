import { useCallback, useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api } from "../api";
import { ChatPanel } from "../components/ChatPanel";
import { MemoryPanel } from "../components/MemoryPanel";
import { useApp } from "../state";
import type { AgentResult, Journey, ThreadState } from "../types";
import { Card, Empty, ErrorBanner, formatDateTime, initials, RiskBadge, Spinner, cx } from "../ui";

function asArray(value: unknown): Record<string, unknown>[] {
  return Array.isArray(value) ? (value as Record<string, unknown>[]) : [];
}

function text(value: unknown): string {
  if (value === undefined || value === null || value === "") return "—";
  if (typeof value === "string") return value;
  return JSON.stringify(value);
}

export function Inspector() {
  const { patientId = "" } = useParams();
  const { refreshKey, refresh } = useApp();
  const [journey, setJourney] = useState<Journey | null>(null);
  const [thread, setThread] = useState<ThreadState | null>(null);
  const [result, setResult] = useState<AgentResult | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    if (!patientId) return;
    try {
      const [journeyData, threadData] = await Promise.all([
        api.journey(patientId),
        api.thread(patientId).catch(() => null),
      ]);
      setJourney(journeyData);
      setThread(threadData);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not load the agent workspace.");
    } finally {
      setLoading(false);
    }
  }, [patientId]);

  useEffect(() => {
    setLoading(true);
    void load();
  }, [load, refreshKey]);

  if (loading && !journey) return <Spinner label="Loading agent workspace" />;
  if (error && !journey) return <ErrorBanner message={error} onRetry={load} />;
  if (!journey) return <Empty>Patient not found.</Empty>;

  const { patient } = journey;
  const extraction = (result?.extraction || {}) as Record<string, unknown>;
  const safety = (extraction.safety || {}) as Record<string, unknown>;
  const followUps = asArray(extraction.follow_ups);
  const reports = asArray(extraction.patient_reports);
  const actions = asArray(extraction.requested_actions);
  const uncertainty = Array.isArray(extraction.uncertainty) ? (extraction.uncertainty as unknown[]) : [];
  const safetyFlags = Object.entries(safety).filter(
    ([key, value]) => key !== "summary" && value === true,
  );
  const retrievalMemories = result?.retrieval?.memories || journey.memories;
  const pending = thread?.pending_approvals || journey.approvals.filter((approval) => approval.status === "pending");

  return (
    <div className="stack">
      <nav className="breadcrumb" aria-label="Breadcrumb">
        <Link to="/patients">Patients</Link>
        <span aria-hidden="true">/</span>
        <Link to={`/patients/${patient.id}`}>{patient.name}</Link>
        <span aria-hidden="true">/</span>
        <span>Agent workspace</span>
      </nav>

      <header className="page-head inspector-head">
        <div>
          <p className="eyebrow">
            <span className="eyebrow-dot" />
            Agent workspace
          </p>
          <h1>
            <Link to={`/patients/${patient.id}`} className="heading-link">
              {patient.name}
            </Link>{" "}
            · coordination inspector
          </h1>
          <p className="page-sub">
            Inspect the LangGraph run, retrieval, extraction and human-in-the-loop checkpoints behind every
            reply.
          </p>
          <div className="tag-row">
            <RiskBadge risk={patient.risk} />
            <span className="tag tag-muted">{patient.condition}</span>
            <span className="tag tag-muted">MRN {patient.mrn}</span>
            <span className="tag tag-muted">
              {journey.obligations.filter((obligation) => obligation.is_open).length} open loops
            </span>
          </div>
        </div>
        <div className="actions">
          <Link className="btn" to={`/patients/${patient.id}`}>
            Patient journey
          </Link>
          <Link className="btn btn-primary" to="/approvals">
            Approvals{pending.length ? ` (${pending.length})` : ""}
          </Link>
        </div>
      </header>

      {error ? <ErrorBanner message={error} onRetry={load} /> : null}

      <div className="inspector-grid">
        <ChatPanel
          patientId={patient.id}
          patientName={patient.name}
          onResult={(nextResult, nextThread) => {
            setResult(nextResult);
            if (nextThread) setThread(nextThread);
            void load();
          }}
        />

        <aside className="stack">
          <Card title="LangGraph checkpoint" subtitle="Persistent state per patient thread">
            <div className="thread-status">
              <span className={cx("tag", thread?.paused ? "tag-amber" : "tag-green")}>
                <span className={cx("dot", thread?.paused ? "dot-amber" : "dot-green")} />
                {thread?.paused ? "Interrupted" : "Idle / ready"}
              </span>
              <div>
                <span className="muted">Thread</span>
                <code>{thread?.thread_id || patient.id}</code>
              </div>
              <div>
                <span className="muted">Next node</span>
                <code>{thread?.next?.length ? thread.next.join(", ") : "—"}</code>
              </div>
              <div>
                <span className="muted">Checkpoint</span>
                <code className="checkpoint-id">{thread?.checkpoint_id || "—"}</code>
              </div>
              <div>
                <span className="muted">Pending approvals</span>
                <code>{pending.length}</code>
              </div>
            </div>
            <p className="form-hint">
              Checkpoints are stored in MongoDB via <code>MongoDBSaver</code>. A paused run resumes from the
              exact node after a coordinator decision.
            </p>
          </Card>

          {result ? (
            <Card title="Extraction trace" subtitle={`Run ${result.run_id || "—"}`}>
              <div className="trace-meta">
                <span className={cx("tag", result.status === "awaiting_approval" ? "tag-amber" : "tag-green")}>
                  {result.status.replace(/_/g, " ")}
                </span>
                <span className="tag tag-muted">{formatDateTime(new Date().toISOString())}</span>
                {result.applied && result.applied.length > 0 ? (
                  <span className="tag tag-blue">{result.applied.length} applied</span>
                ) : null}
                {result.proposals && result.proposals.length > 0 ? (
                  <span className="tag tag-purple">{result.proposals.length} proposals</span>
                ) : null}
              </div>

              {safetyFlags.length > 0 || safety.summary ? (
                <div className="trace-block trace-safety">
                  <h4>Safety flags</h4>
                  <div className="tag-row">
                    {safetyFlags.map(([key]) => (
                      <span key={key} className="tag tag-purple">
                        {key.replace(/_/g, " ")}
                      </span>
                    ))}
                    {safety.summary ? <span className="tag tag-muted">{text(safety.summary)}</span> : null}
                  </div>
                </div>
              ) : null}

              <div className="trace-block">
                <h4>Follow-ups extracted ({followUps.length})</h4>
                {followUps.length === 0 ? (
                  <p className="muted">None in this input.</p>
                ) : (
                  <ul className="trace-list">
                    {followUps.map((item, index) => (
                      <li key={index}>
                        <strong>{text(item.description)}</strong>
                        <small>
                          {text(item.category)} · due in {text(item.due_in_days)} days · {text(item.owner_hint)}
                        </small>
                      </li>
                    ))}
                  </ul>
                )}
              </div>

              <div className="trace-block">
                <h4>Patient reports ({reports.length})</h4>
                {reports.length === 0 ? (
                  <p className="muted">None in this input.</p>
                ) : (
                  <ul className="trace-list">
                    {reports.map((item, index) => (
                      <li key={index}>
                        <strong>{text(item.obligation_hint)}</strong>
                        <small>
                          {text(item.reported_status)} · {text(item.reported_date)}
                        </small>
                        {item.details ? <p>{text(item.details)}</p> : null}
                      </li>
                    ))}
                  </ul>
                )}
              </div>

              <div className="trace-block">
                <h4>Requested actions ({actions.length})</h4>
                {actions.length === 0 ? (
                  <p className="muted">None in this input.</p>
                ) : (
                  <ul className="trace-list">
                    {actions.map((item, index) => (
                      <li key={index}>
                        <strong>{text(item.action)}</strong>
                        <small>{text(item.obligation_hint)}</small>
                        {item.rationale ? <p>{text(item.rationale)}</p> : null}
                      </li>
                    ))}
                  </ul>
                )}
              </div>

              {uncertainty.length > 0 ? (
                <div className="trace-block">
                  <h4>Uncertainty</h4>
                  <ul className="trace-list">
                    {uncertainty.map((item, index) => (
                      <li key={index}>{text(item)}</li>
                    ))}
                  </ul>
                </div>
              ) : null}

              {result.notes && result.notes.length > 0 ? (
                <div className="trace-block">
                  <h4>Validation notes</h4>
                  <ul className="trace-list">
                    {result.notes.map((note, index) => (
                      <li key={index}>{note}</li>
                    ))}
                  </ul>
                </div>
              ) : null}
            </Card>
          ) : (
            <Card title="Extraction trace" subtitle="Inspect what the agent understood">
              <p className="empty">
                Send a message to see the extraction, validation notes and retrieved memory behind the reply.
              </p>
            </Card>
          )}

          <Card
            title="Retrieved context"
            subtitle={result?.retrieval?.counts ? `${result.retrieval.counts.memories ?? 0} memories · ${result.retrieval.counts.evidence ?? 0} evidence` : "Atlas memory"}
          >
            {retrievalMemories.length === 0 ? (
              <Empty>No memory recalled yet.</Empty>
            ) : (
              <ul className="mini-memory">
                {retrievalMemories.slice(0, 6).map((memory) => (
                  <li key={memory.id}>
                    <div>
                      <strong>{memory.summary}</strong>
                      <small>
                        {memory.kind.replace(/_/g, " ")}
                        {memory.score !== undefined ? ` · ${Math.round(memory.score * 100)}% match` : ""}
                      </small>
                    </div>
                    {memory.retrieval ? (
                      <span className={cx("tag", memory.retrieval === "vector" ? "tag-blue" : "tag-amber")}>
                        {memory.retrieval}
                      </span>
                    ) : null}
                  </li>
                ))}
              </ul>
            )}
          </Card>

          <MemoryPanel patientId={patient.id} memories={journey.memories} compact title="All patient memory" />
        </aside>
      </div>

      <Card
        title="How this run works"
        subtitle="The same six-stage graph powers document, chat and voice inputs"
      >
        <div className="pipeline">
          {[
            ["Ingest", "Append event + memory"],
            ["Extract", "LangChain structured output or deterministic rules"],
            ["Retrieve", "Vector + lexical memory and evidence"],
            ["Validate", "Unclosed Loop Engine rules"],
            ["Propose", "Queue or interrupt for sensitive changes"],
            ["Apply / Compose", "Administrative updates, then reply"],
          ].map(([step, detail], index) => (
            <div key={step} className="pipeline-step">
              <span>{index + 1}</span>
              <strong>{step}</strong>
              <small>{detail}</small>
            </div>
          ))}
        </div>
        <p className="form-hint">
          Patient-reported completion never closes a loop by itself. Verified completion, cancellation,
          escalation and conflict resolution require evidence or an authorized coordinator decision — all
          recorded in the audit log.
        </p>
      </Card>
    </div>
  );
}
