import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";
import { useAuth } from "../auth";
import { ObligationCard } from "../components/ObligationCard";
import { useApp } from "../state";
import type { MyWork } from "../types";
import { Card, Empty, ErrorBanner, formatDate, Spinner } from "../ui";

/** One queue for the signed-in person: theirs, then what their role picks up. */
export function WorkPage() {
  const { user } = useAuth();
  const { refreshKey } = useApp();
  const [work, setWork] = useState<MyWork | null>(null);
  const [error, setError] = useState<string | null>(null);

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

  const contactDue = [...work.tcm.contact_missed, ...work.tcm.contact_due];
  const clinician = work.role === "clinician";

  return (
    <div className="stack">
      <header className="page-head">
        <div>
          <p className="eyebrow">
            <span className="eyebrow-dot" />
            {formatDate(work.today)} · {user?.name}
          </p>
          <h1>My work</h1>
          <p className="page-sub">
            {work.assigned.length} assigned to you · {work.unassigned.length} unassigned {clinician ? "clinical" : ""} item(s) due this week ·{" "}
            <Link to="/approvals">{work.approvals_pending} decision(s) waiting</Link>
          </p>
        </div>
      </header>

      {!clinician && contactDue.length ? (
        <Card title="Post-discharge calls to make" subtitle="TCM: interactive contact within 2 business days of discharge">
          <ul className="work-calls">
            {contactDue.map((row) => (
              <li key={row.patient_id}>
                <Link to={`/patients/${row.patient_id}`}>{row.name}</Link>
                <span className="muted">
                  discharged {formatDate(row.discharge_date)} · contact by {formatDate(row.contact_due)} · {row.contact_attempts} of 2 attempts
                </span>
              </li>
            ))}
          </ul>
        </Card>
      ) : null}

      <Card title="Assigned to you" subtitle="Overdue first, then by due date">
        {work.assigned.length === 0 ? (
          <Empty>Nothing is assigned to you. Pick up an item below or assign one from a patient's follow-ups.</Empty>
        ) : (
          <div className="obligation-list">
            {work.assigned.map((o) => (
              <ObligationCard key={o.id} obligation={o} patientName={o.patient_name} dense onChanged={load} />
            ))}
          </div>
        )}
      </Card>

      <Card
        title={clinician ? "Unassigned clinical work" : "Unassigned, due within 7 days"}
        subtitle={clinician ? "Medication reconciliations waiting for a clinician" : "Follow-ups nobody on the team has picked up yet"}
      >
        {work.unassigned.length === 0 ? (
          <Empty>Everything due this week has someone on it.</Empty>
        ) : (
          <div className="obligation-list">
            {work.unassigned.map((o) => (
              <ObligationCard key={o.id} obligation={o} patientName={o.patient_name} dense onChanged={load} />
            ))}
          </div>
        )}
      </Card>
    </div>
  );
}
