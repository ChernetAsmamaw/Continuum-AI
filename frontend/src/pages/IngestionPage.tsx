import { useCallback, useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";
import { useApp } from "../state";
import type { IngestJob, IngestStepName } from "../types";
import { Card, Empty, ErrorBanner, formatDateTime, relativeDate, Spinner, cx } from "../ui";

const STEPS: IngestStepName[] = ["pull", "identify", "structured", "narrative", "verify"];

const STEP_LABELS: Record<IngestStepName, string> = {
  pull: "Pull",
  identify: "Identify",
  structured: "Structured",
  narrative: "Narrative",
  verify: "Verify",
};

const STEP_HELP: Record<IngestStepName, string> = {
  pull: "Fetch the stay from the EHR (FHIR)",
  identify: "Match or create the patient and episode",
  structured: "Follow-ups from orders, bookings, pending results",
  narrative: "AI extraction from the discharge summary",
  verify: "Re-read every write from the database",
};

const JOB_TONE: Record<string, string> = { done: "green", failed: "danger", running: "blue", queued: "neutral" };
const JOB_LABEL: Record<string, string> = { done: "Done", failed: "Failed", running: "Running", queued: "Queued" };

/**
 * Which step a resumed job restarted from. Steps only run once unless retried,
 * so a retried step has attempts > 1; a crash-resume shows up as a later step
 * run by a different worker process than the one that pulled.
 */
export function resumedFrom(job: IngestJob, hint?: string | null): IngestStepName | null {
  if (hint && STEPS.includes(hint as IngestStepName)) return hint as IngestStepName;
  if ((job.attempts || 0) <= 1) return null;
  const retried = STEPS.find((step) => (job.steps[step]?.attempts || 0) > 1);
  if (retried) return retried;
  const firstWorker = job.steps.pull?.worker;
  return STEPS.find((step) => job.steps[step]?.worker && firstWorker && job.steps[step].worker !== firstWorker) || null;
}

function leaseExpired(job: IngestJob): boolean {
  return !job.lease_until || new Date(job.lease_until).getTime() < Date.now();
}

export function canResume(job: IngestJob): boolean {
  if (job.status === "failed" || job.status === "queued") return true;
  return job.status === "running" && leaseExpired(job);
}

export function StepStrip({ job, resumed }: { job: IngestJob; resumed?: IngestStepName | null }) {
  return (
    <ol className="step-strip" aria-label="Ingestion steps">
      {STEPS.map((step, index) => {
        const state = job.steps[step] || { status: "pending", attempts: 0 };
        const status = state.status === "running" && job.status !== "running" ? "failed" : state.status;
        return (
          <li
            key={step}
            className={cx("step", `step-${status}`, resumed === step && "step-resumed")}
            title={`${STEP_HELP[step]}${state.summary ? `\n${state.summary}` : ""}${state.error ? `\nError: ${state.error}` : ""}`}
          >
            <span className="step-bar" />
            <span className="step-label">
              <b>
                {index + 1}. {STEP_LABELS[step]}
              </b>
              {state.attempts > 1 ? <em className="step-attempts">×{state.attempts}</em> : null}
            </span>
            <small className="step-state">
              {status === "failed"
                ? state.attempts > 1 ? "Failed again" : "Failed"
                : resumed === step
                  ? status === "done" ? "Resumed here · done" : "Resumed here"
                  : status === "done" ? "Done" : status === "running" ? "Running" : "Pending"}
            </small>
          </li>
        );
      })}
    </ol>
  );
}

function JobCard({ job, patientName, onResumed }: { job: IngestJob; patientName?: string; onResumed: () => void }) {
  const { notify } = useApp();
  const [busy, setBusy] = useState(false);
  const [open, setOpen] = useState(job.status === "failed");
  const [hint, setHint] = useState<string | null>(null);
  const resumed = resumedFrom(job, hint);
  const failedStep = STEPS.find((step) => job.steps[step]?.status === "failed");
  const patientId = job.patient_id || job.result?.patient_id;
  const source = (job.source || "").toLowerCase().includes("hl7")
    ? "HL7 ADT^A03 → FHIR pull"
    : (job.source || "").toLowerCase() === "fhir"
      ? "FHIR pull"
      : job.source;

  const resume = async () => {
    setBusy(true);
    try {
      const result = await api.resumeJob(job.id);
      setHint(result.resumed_from || null);
      if (result.status === "done") notify(`Job finished${result.resumed_from ? `, resumed from ${result.resumed_from}` : ""}.`);
      else if (result.status === "failed") notify(`Still failing at ${result.failed_step}: ${result.error}`);
      else notify(result.detail || `Job is ${result.status}.`);
      onResumed();
    } catch (err) {
      notify(err instanceof Error ? err.message : "Could not resume the job.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <article className={cx("job-card", `job-${job.status}`)}>
      <header className="job-head">
        <div className="job-title">
          <strong>
            {patientId ? (
              <Link to={`/patients/${patientId}`}>{patientName || patientId}</Link>
            ) : (
              <span>{String(job.params?.patient_fhir_id || (job.params?.patient_identifiers as string[] | undefined)?.[0] || "Unidentified patient")}</span>
            )}
          </strong>
          <small>
            {source} · {formatDateTime(job.created_at)} · {job.id}
          </small>
        </div>
        <div className="job-meta">
          <span className="tag tag-muted" title="Times a worker claimed this job">
            {job.attempts} {job.attempts === 1 ? "attempt" : "attempts"}
          </span>
          {resumed ? <span className="tag tag-blue">Resumed from {STEP_LABELS[resumed]}</span> : null}
          {job.result?.identity_review ? (
            <Link to="/review" className="tag tag-amber">
              Identity review
            </Link>
          ) : null}
          <span className={cx("pill", `pill-${JOB_TONE[job.status] || "neutral"}`)}>
            <span className="pill-dot" />
            {JOB_LABEL[job.status] || job.status}
          </span>
        </div>
      </header>

      <StepStrip job={job} resumed={resumed} />

      {job.status === "failed" || failedStep ? (
        <div className="job-failure">
          <div>
            <strong>Failed at {failedStep ? STEP_LABELS[failedStep] : job.current_step || "a step"}</strong>
            <p>{(failedStep && job.steps[failedStep].error) || job.error || "No error recorded."}</p>
            <small>Finished steps are checkpointed; resuming continues from the first unfinished step without re-fetching.</small>
          </div>
          {canResume(job) ? (
            <button className="btn btn-small btn-primary" onClick={resume} disabled={busy}>
              {busy ? "Resuming…" : "Resume"}
            </button>
          ) : null}
        </div>
      ) : canResume(job) ? (
        <div className="job-failure job-stalled">
          <div>
            <strong>{job.status === "queued" ? "Queued" : "Stalled"}</strong>
            <p>
              {job.status === "queued"
                ? "Waiting for a worker."
                : `The worker's lease expired ${relativeDate(job.lease_until)}; it likely crashed mid-job.`}
            </p>
          </div>
          <button className="btn btn-small btn-primary" onClick={resume} disabled={busy}>
            {busy ? "Resuming…" : "Resume"}
          </button>
        </div>
      ) : null}

      <div className="job-foot">
        <button className="link-button" onClick={() => setOpen((value) => !value)} aria-expanded={open}>
          {open ? "Hide step details" : "Step details"}
        </button>
        {job.result?.summary ? <span className="job-summary">{job.result.summary}</span> : null}
      </div>

      {open ? (
        <dl className="kv job-steps">
          {STEPS.map((step) => {
            const state = job.steps[step];
            return (
              <div key={step}>
                <dt>
                  {STEP_LABELS[step]}
                  {state?.attempts ? <small> · {state.attempts}×</small> : null}
                </dt>
                <dd>
                  {state?.error ? <span className="text-danger">{state.error}</span> : state?.summary || <span className="muted">{STEP_HELP[step]}</span>}
                  {state?.finished_at ? <small> · {formatDateTime(state.finished_at)}</small> : null}
                  {state?.worker ? <small> · {state.worker}</small> : null}
                </dd>
              </div>
            );
          })}
        </dl>
      ) : null}
    </article>
  );
}

type JobFilter = "all" | "attention" | "done";

export function IngestionPage() {
  const { dashboard, refreshKey } = useApp();
  const [jobs, setJobs] = useState<IngestJob[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState<JobFilter>("all");

  const load = useCallback(async () => {
    try {
      const data = await api.ingestJobs();
      setJobs(data.jobs);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not load ingestion jobs.");
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load, refreshKey]);

  const names = useMemo(() => new Map((dashboard?.patients || []).map((p) => [p.id, p.name])), [dashboard]);

  if (!jobs) return error ? <ErrorBanner message={error} onRetry={load} /> : <Spinner label="Loading ingestion jobs" />;

  const attention = jobs.filter((job) => job.status !== "done");
  const visible = filter === "attention" ? attention : filter === "done" ? jobs.filter((job) => job.status === "done") : jobs;
  const resumedCount = jobs.filter((job) => resumedFrom(job)).length;

  return (
    <div className="stack">
      <header className="page-head">
        <div>
          <p className="eyebrow">
            <span className="eyebrow-dot" />
            EHR ingestion
          </p>
          <h1>Discharges from the EHR</h1>
          <p className="page-sub">
            Each discharge runs as a checkpointed job: pull → identify → structured → narrative → verify. A crash resumes from the
            first unfinished step; nothing finished is redone.
          </p>
        </div>
      </header>

      {error ? <ErrorBanner message={error} onRetry={load} /> : null}

      <section className="metric-row metric-row-3" aria-label="Ingestion summary">
        <div className="metric">
          <span className="metric-label">Recent jobs</span>
          <strong className="metric-value">{jobs.length}</strong>
          <span className="metric-note">{jobs.filter((job) => job.status === "done").length} finished and verified</span>
        </div>
        <button className={cx("metric", attention.length > 0 && "metric-danger")} onClick={() => setFilter("attention")}>
          <span className="metric-label">Failed or unfinished</span>
          <strong className="metric-value">{attention.length}</strong>
          <span className="metric-note">{attention.length ? "Resume to continue from the checkpoint" : "Nothing stuck"}</span>
        </button>
        <div className="metric">
          <span className="metric-label">Resumed or retried</span>
          <strong className="metric-value">{resumedCount}</strong>
          <span className="metric-note">Picked up from the checkpoint, completed steps not replayed</span>
        </div>
      </section>

      <Card
        title="Ingestion jobs"
        subtitle="Newest first. Hover a step for what it did."
        action={
          <div className="tabs">
            {(["all", "attention", "done"] as JobFilter[]).map((item) => (
              <button key={item} className={cx("tab", filter === item && "tab-active")} onClick={() => setFilter(item)} aria-pressed={filter === item}>
                {item === "all" ? `All ${jobs.length}` : item === "attention" ? `Needs attention ${attention.length}` : "Done"}
              </button>
            ))}
          </div>
        }
      >
        {visible.length === 0 ? (
          <Empty>{jobs.length === 0 ? "No discharges have arrived from the EHR yet." : "Nothing in this view."}</Empty>
        ) : (
          <div className="job-list">
            {visible.map((job) => (
              <JobCard key={job.id} job={job} patientName={names.get(job.patient_id || "")} onResumed={load} />
            ))}
          </div>
        )}
      </Card>
    </div>
  );
}
