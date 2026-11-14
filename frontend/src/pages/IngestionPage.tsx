import { useCallback, useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";
import { useApp } from "../state";
import type { IngestJob, IngestStepName } from "../types";
import { ErrorBanner, Spinner, cx, formatDateTime, relativeDate } from "../ui";

/**
 * Imports: discharges that arrived from the hospital record. One row per
 * import in plain words; the five steps are a small bar. Technical detail
 * (job ids, workers, HL7) is one click away, not on the surface.
 */

const STEPS: IngestStepName[] = ["pull", "identify", "structured", "narrative", "verify"];

const STEP_NAME: Record<IngestStepName, string> = {
  pull: "Pulled from the hospital record",
  identify: "Matched to a patient",
  structured: "Follow-ups from orders",
  narrative: "Read the discharge summary",
  verify: "Checked everything was saved",
};

/** Which step a resumed job restarted from. */
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

function stepState(job: IngestJob, step: IngestStepName): "done" | "failed" | "running" | "pending" {
  const s = job.steps[step]?.status || "pending";
  return s === "running" && job.status !== "running" ? "failed" : s;
}

/** Step summaries are written for logs; say the same thing in plain words. */
function plainStep(step: IngestStepName, text?: string): string {
  if (!text) return "";
  let m: RegExpMatchArray | null;
  if (step === "pull" && (m = text.match(/(\d+) orders?, (\d+) appointments?, (\d+) pending results?, (\d+) meds?/)))
    return `${m[1]} orders, ${m[2]} appointments, ${m[3]} pending results and ${m[4]} medications`;
  if (step === "identify") {
    if (text.startsWith("new_patient")) return "No match found, so a new patient record was created";
    if (text.startsWith("matched") || text.includes("existing")) return "Matched an existing patient";
    if (text.includes("review")) return "Possible duplicate: held for an identity check";
  }
  if (step === "structured" && (m = text.match(/(\d+) created, (\d+) already tracked/)))
    return `${m[1]} follow-ups created${Number(m[2]) ? `, ${m[2]} were already tracked` : ""}`;
  if (step === "narrative" && (m = text.match(/(\d+) from narrative/)))
    return Number(m[1]) ? `${m[1]} more follow-up${m[1] === "1" ? "" : "s"} found that the orders missed` : "Nothing new beyond the orders";
  if (step === "verify") return "Every follow-up was read back from the database";
  return text.replace(/_/g, " ");
}

function plainSource(source: string): string {
  const s = (source || "").toLowerCase();
  if (s.includes("hl7")) return "Discharge message from the hospital";
  if (s.includes("fhir") || s.includes("pull")) return "Pulled from the hospital record";
  if (s.includes("manual")) return "Started by a coordinator";
  return source || "Import";
}

function outcome(job: IngestJob): { text: string; tone: string } {
  if (job.status === "failed") return { text: "Stopped", tone: "red" };
  if (job.status === "running") return canResume(job) ? { text: "Stalled", tone: "red" } : { text: "Running", tone: "blue" };
  if (job.status === "queued") return { text: "Queued", tone: "neutral" };
  if (job.result?.identity_review) return { text: "Check identity", tone: "amber" };
  return { text: "Imported", tone: "green" };
}

function StepBar({ job, resumed }: { job: IngestJob; resumed: IngestStepName | null }) {
  return (
    <span className="im-bar" role="img" aria-label={STEPS.map((s) => `${STEP_NAME[s]}: ${stepState(job, s)}`).join(", ")}>
      {STEPS.map((s) => (
        <i key={s} className={cx(`im-seg-${stepState(job, s)}`, resumed === s && "im-seg-resumed")} />
      ))}
    </span>
  );
}

function ImportRow({ job, name, onResumed }: { job: IngestJob; name?: string; onResumed: () => void }) {
  const { notify } = useApp();
  const [open, setOpen] = useState(job.status === "failed");
  const [busy, setBusy] = useState(false);
  const [hint, setHint] = useState<string | null>(null);
  const resumed = resumedFrom(job, hint);
  const failed = STEPS.find((s) => stepState(job, s) === "failed");
  const state = outcome(job);
  const patientId = job.patient_id || job.result?.patient_id;
  const known = Boolean(name);
  const label = name || (patientId ? "Patient record removed" : String(job.params?.patient_fhir_id || "Unidentified patient"));
  const orders = Array.isArray(job.result?.created) ? (job.result!.created as unknown[]).length : null;
  const fromSummary = Array.isArray(job.result?.ai_created) ? (job.result!.ai_created as unknown[]).length : 0;
  const resultText =
    orders !== null
      ? `${orders + fromSummary} follow-ups: ${orders} from orders${fromSummary ? `, ${fromSummary} found in the discharge summary` : ""}`
      : job.result?.summary || "In progress";

  const resume = async () => {
    setBusy(true);
    try {
      const result = await api.resumeJob(job.id);
      setHint(result.resumed_from || null);
      if (result.status === "done") notify(`Import finished${result.resumed_from ? `, picked up at "${STEP_NAME[result.resumed_from as IngestStepName] || result.resumed_from}"` : ""}.`);
      else if (result.status === "failed") notify(`Still stopping at ${result.failed_step}: ${result.error}`);
      else notify(result.detail || `Import is ${result.status}.`);
      onResumed();
    } catch (err) {
      notify(err instanceof Error ? err.message : "Could not resume the import.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <li className={cx("im-row", `im-${state.tone}`, open && "im-open")}>
      <button type="button" className="im-main" onClick={() => setOpen((v) => !v)} aria-expanded={open}>
        <span className={cx("dot", `dot-${state.tone}`)} aria-hidden="true" />
        <span className="im-who">
          <b className={cx(!known && "muted")}>{label}</b>
          <small>{plainSource(job.source)}</small>
        </span>
        <span className="im-result">{failed ? <span className="tone-red">Stopped at: {STEP_NAME[failed].toLowerCase()}</span> : resultText}</span>
        <StepBar job={job} resumed={resumed} />
        <span className="im-when">{formatDateTime(job.created_at)}</span>
        <span className={cx("im-state", `tone-${state.tone}`)}>{state.text}</span>
      </button>

      {resumed && !open ? <p className="im-note tone-violet">Recovered after a restart. It picked up at "{STEP_NAME[resumed].toLowerCase()}" without redoing earlier steps.</p> : null}

      {open ? (
        <div className="im-detail">
          {(job.status === "failed" || failed || canResume(job)) && job.status !== "done" ? (
            <div className="im-alert">
              <div>
                <b>
                  {failed ? `Stopped at "${STEP_NAME[failed].toLowerCase()}"` : job.status === "queued" ? "Waiting for a worker" : "The worker stopped responding"}
                </b>
                <p>
                  {(failed && job.steps[failed].error) ||
                    job.error ||
                    (job.status === "running" ? `Its lease expired ${relativeDate(job.lease_until)}.` : "No error was recorded.")}
                </p>
                <small>Resuming starts at the first unfinished step. Finished steps are not repeated.</small>
              </div>
              {canResume(job) ? (
                <button className="btn btn-primary" onClick={resume} disabled={busy}>
                  {busy ? "Resuming…" : "Resume import"}
                </button>
              ) : null}
            </div>
          ) : null}
          <ol className="im-steps">
            {STEPS.map((s) => {
              const st = stepState(job, s);
              const info = job.steps[s];
              const tone = st === "done" ? "green" : st === "failed" ? "red" : st === "running" ? "blue" : "neutral";
              return (
                <li key={s}>
                  <span className={cx("dot", `dot-${tone}`)} aria-hidden="true" />
                  <span className="im-step-name">
                    {STEP_NAME[s]}
                    {resumed === s ? <span className="tag-tone tone-violet">Picked up here</span> : null}
                    {info?.attempts > 1 ? <span className="tag-tone tone-amber">Tried {info.attempts} times</span> : null}
                  </span>
                  <span className={cx("im-step-info", st === "failed" && "tone-red")}>{info?.error || plainStep(s, info?.summary) || (st === "pending" ? "Not started" : "")}</span>
                  <span className="im-step-time">{info?.finished_at ? formatDateTime(info.finished_at) : ""}</span>
                </li>
              );
            })}
          </ol>
          <div className="im-foot">
            {patientId && known ? <Link to={`/patients/${patientId}`}>Open {name}</Link> : null}
            {job.result?.identity_review ? <Link to="/approvals?tab=checks" className="tone-amber">Check the identity match</Link> : null}
            <details className="im-tech">
              <summary>Technical details</summary>
              <dl>
                <dt>Import</dt>
                <dd>{job.id}</dd>
                <dt>Source</dt>
                <dd>{job.source}</dd>
                <dt>Attempts</dt>
                <dd>{job.attempts}</dd>
                {STEPS.filter((s) => job.steps[s]?.worker).map((s) => (
                  <div key={s} className="im-tech-row">
                    <dt>{STEP_NAME[s]}</dt>
                    <dd>{job.steps[s].worker}</dd>
                  </div>
                ))}
              </dl>
            </details>
          </div>
        </div>
      ) : null}
    </li>
  );
}

type Filter = "all" | "attention" | "done";

export function IngestionPage() {
  const { dashboard, refreshKey } = useApp();
  const [jobs, setJobs] = useState<IngestJob[] | null>(null);
  const [names, setNames] = useState<Map<string, string>>(new Map());
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState<Filter>("all");

  const load = useCallback(async () => {
    try {
      const [data, patients] = await Promise.all([api.ingestJobs(), api.patients().catch(() => null)]);
      setJobs(data.jobs);
      if (patients) setNames(new Map(patients.patients.map((p) => [p.id, p.name])));
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not load imports.");
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load, refreshKey]);

  const nameOf = useMemo(() => {
    const fromDash = new Map((dashboard?.patients || []).map((p) => [p.id, p.name]));
    return (id?: string) => (id ? names.get(id) || fromDash.get(id) : undefined);
  }, [names, dashboard]);

  if (!jobs) return error ? <ErrorBanner message={error} onRetry={load} /> : <Spinner label="Loading imports" />;

  const attention = jobs.filter((j) => j.status !== "done" || j.result?.identity_review);
  const done = jobs.filter((j) => j.status === "done");
  const recovered = jobs.filter((j) => resumedFrom(j)).length;
  const visible = filter === "attention" ? attention : filter === "done" ? done : jobs;

  return (
    <div className="im">
      <header className="dq-head">
        <div>
          <h1>Imports</h1>
          <p>Discharges that arrive from the hospital record. Each one is matched to a patient, turned into follow-ups, and checked before it counts.</p>
        </div>
      </header>

      {error ? <ErrorBanner message={error} onRetry={load} /> : null}

      <div className="im-summary">
        <span className="flag tone-green">{done.length} imported</span>
        <span className={cx("flag", attention.length ? "tone-red" : "tone-neutral")}>{attention.length ? `${attention.length} need attention` : "Nothing stuck"}</span>
        {recovered ? <span className="flag tone-violet">{recovered} recovered after a restart</span> : null}
        <div className="seg im-filter" role="tablist">
          {(["all", "attention", "done"] as Filter[]).map((f) => (
            <button key={f} type="button" aria-pressed={filter === f} onClick={() => setFilter(f)}>
              {f === "all" ? "All" : f === "attention" ? "Needs attention" : "Imported"}
            </button>
          ))}
        </div>
      </div>

      {visible.length === 0 ? (
        <div className="dq-empty">
          <h2>{jobs.length === 0 ? "No discharges have arrived yet." : "Nothing in this view."}</h2>
          <p>{jobs.length === 0 ? "When the hospital sends a discharge, it appears here within a few seconds." : "Switch to All to see every import."}</p>
        </div>
      ) : (
        <section className="im-panel">
          <div className="im-cols" aria-hidden="true">
            <span />
            <span>Patient</span>
            <span>Result</span>
            <span>Steps</span>
            <span>Arrived</span>
            <span />
          </div>
          <ul className="im-list">
            {visible.map((job) => (
              <ImportRow key={job.id} job={job} name={nameOf(job.patient_id || job.result?.patient_id)} onResumed={load} />
            ))}
          </ul>
        </section>
      )}
    </div>
  );
}
