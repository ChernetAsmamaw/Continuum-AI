import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";
import { FollowUpRow } from "../components/FollowUpRow";
import { useApp } from "../state";
import type { MyWork, TcmRow } from "../types";
import { ErrorBanner, Spinner, cx, formatDate } from "../ui";

/**
 * Assigned to me: the calls the 2-day rule says to make, then what is on
 * your desk, then what nobody has picked up. Every follow-up is one row.
 */

function CallRow({ row, missed }: { row: TcmRow; missed: boolean }) {
  return (
    <li className={cx("wk-call", missed && "wk-missed")}>
      <span className={cx("dot", missed ? "dot-red" : "dot-amber")} aria-hidden="true" />
      <span className="wk-call-main">
        <Link to={`/patients/${row.patient_id}`}>{row.name}</Link>
        <small>
          Discharged {formatDate(row.discharge_date, { year: undefined })}. {row.contact_attempts} of 2 attempts logged.
        </small>
      </span>
      <span className={cx("wk-due", missed ? "tone-red" : "tone-amber")}>
        {missed ? "Missed" : "Due"} {formatDate(row.contact_due, { year: undefined })}
      </span>
      <Link className="btn btn-small btn-primary" to={`/patients/${row.patient_id}/call`}>
        Call
      </Link>
    </li>
  );
}

export function WorkPage() {
  const { refreshKey } = useApp();
  const [work, setWork] = useState<MyWork | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [open, setOpen] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setWork(await api.myWork());
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not load your work.");
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load, refreshKey]);

  if (!work) return error ? <ErrorBanner message={error} onRetry={load} /> : <Spinner label="Loading your work" />;

  const clinician = work.role === "clinician";
  const missed = clinician ? [] : work.tcm.contact_missed;
  const due = clinician ? [] : work.tcm.contact_due;
  const calls = missed.length + due.length;
  const overdue = [...work.assigned, ...work.unassigned].filter((o) => o.status === "overdue").length;

  const rows = (list: MyWork["assigned"]) =>
    list.map((o) => (
      <FollowUpRow
        key={o.id}
        obligation={o}
        patientName={o.patient_name}
        open={open === o.id}
        onToggle={() => setOpen((cur) => (cur === o.id ? null : o.id))}
        onChanged={() => void load()}
      />
    ));

  return (
    <div className="dq">
      <header className="dq-head">
        <div>
          <h1>Assigned to me</h1>
          <p>Calls to make first, then the follow-ups on your desk, then what nobody has picked up yet.</p>
        </div>
      </header>

      <div className="im-summary">
        {calls ? <span className={cx("flag", missed.length ? "tone-red" : "tone-amber")}>{calls} {calls === 1 ? "call" : "calls"} to make</span> : null}
        {overdue ? <span className="flag tone-red">{overdue} overdue</span> : null}
        <span className="flag tone-blue">{work.assigned.length} assigned to you</span>
        <span className="flag tone-neutral">{work.unassigned.length} unclaimed this week</span>
        {work.approvals_pending ? (
          <Link to="/approvals" className="flag tone-amber">
            {work.approvals_pending} decisions waiting
          </Link>
        ) : null}
      </div>

      {calls ? (
        <section className={cx("fu-group", missed.length ? "fu-group-red" : "fu-group-amber")}>
          <header>
            <h3>Post-discharge calls</h3>
            <span className="fu-count">{calls}</span>
            <span className="fu-hint">Each patient needs a real conversation within 2 business days of going home</span>
          </header>
          <ul className="wk-calls">
            {missed.map((r) => (
              <CallRow key={r.patient_id} row={r} missed />
            ))}
            {due.map((r) => (
              <CallRow key={r.patient_id} row={r} missed={false} />
            ))}
          </ul>
        </section>
      ) : null}

      <section className="fu-group fu-group-blue">
        <header>
          <h3>On your desk</h3>
          <span className="fu-count">{work.assigned.length}</span>
          <span className="fu-hint">Overdue first, then by due date</span>
        </header>
        {work.assigned.length ? (
          rows(work.assigned)
        ) : (
          <p className="fu-empty">Nothing is assigned to you. Pick one up below, or assign yourself from a patient's page.</p>
        )}
      </section>

      <section className="fu-group">
        <header>
          <h3>{clinician ? "Clinical work nobody has picked up" : "Nobody has picked these up"}</h3>
          <span className="fu-count">{work.unassigned.length}</span>
          <span className="fu-hint">Due within 7 days. Open one and choose "Assign to someone".</span>
        </header>
        {work.unassigned.length ? rows(work.unassigned) : <p className="fu-empty">Everything due this week has someone on it.</p>}
      </section>
    </div>
  );
}
