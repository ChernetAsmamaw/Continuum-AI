import { useMemo } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useApp } from "../state";
import { Card, Empty, ErrorBanner, formatDate, initials, RiskBadge, Spinner, StatusPill } from "../ui";

type Filter = "all" | "overdue" | "conflict" | "verification" | "escalated" | "high";

const FILTERS: { id: Filter; label: string }[] = [
  { id: "all", label: "All patients" },
  { id: "overdue", label: "Overdue" },
  { id: "conflict", label: "Conflicts" },
  { id: "verification", label: "Awaiting verification" },
  { id: "escalated", label: "Escalated" },
  { id: "high", label: "High risk" },
];

export function PatientsPage() {
  const { dashboard, loading, error, refresh } = useApp();
  const [params, setParams] = useSearchParams();
  const query = params.get("q") || "";
  const rawFilter = params.get("filter");
  const filter: Filter = FILTERS.some((item) => item.id === rawFilter) ? (rawFilter as Filter) : "all";

  const setParam = (key: string, value: string) => {
    const next = new URLSearchParams(params);
    if (value) next.set(key, value);
    else next.delete(key);
    setParams(next, { replace: true });
  };

  const patients = useMemo(() => {
    const all = dashboard?.patients ?? [];
    const needle = query.trim().toLowerCase();
    return all.filter((patient) => {
      if (filter === "overdue" && patient.overdue_count === 0) return false;
      if (filter === "conflict" && patient.conflict_count === 0) return false;
      if (filter === "verification" && patient.pending_verification === 0) return false;
      if (filter === "escalated" && patient.escalation_count === 0) return false;
      if (filter === "high" && patient.risk !== "High") return false;
      if (!needle) return true;
      const haystack = [
        patient.name,
        patient.condition,
        patient.mrn,
        patient.caregiver || "",
        ...patient.priority.map((obligation) => obligation.description),
      ]
        .join(" ")
        .toLowerCase();
      return haystack.includes(needle);
    });
  }, [dashboard, filter, query]);

  if (!dashboard) {
    return loading ? <Spinner /> : <ErrorBanner message={error || "No patient data."} onRetry={refresh} />;
  }

  const stats = dashboard.stats;

  return (
    <div className="stack">
      <header className="page-head">
        <div>
          <p className="eyebrow">
            <span className="eyebrow-dot" />
            Care panel
          </p>
          <h1>Patients</h1>
          <p className="page-sub">
            {dashboard.patients.length} patients · {stats.open_obligations} open loops ·{" "}
            {stats.overdue > 0 ? <strong>{stats.overdue} overdue</strong> : "nothing overdue"}
          </p>
        </div>
        <div className="actions">
          <Link className="btn btn-primary" to="/patients/new">
            + New patient
          </Link>
          <Link className="btn" to="/audit">
            Audit log
          </Link>
          <Link className="btn btn-primary" to="/approvals">
            Approvals{stats.pending_approvals ? ` · ${stats.pending_approvals}` : ""}
          </Link>
        </div>
      </header>

      <Card className="filter-card">
        <div className="filter-bar">
          <div className="filter-search">
            <input
              value={query}
              onChange={(event) => setParam("q", event.target.value)}
              placeholder="Search by name, condition, MRN or obligation…"
              aria-label="Filter patients"
            />
            {query ? (
              <button className="link-button" onClick={() => setParam("q", "")}>
                Clear
              </button>
            ) : null}
          </div>
          <div className="filter-chips">
            {FILTERS.map((item) => (
              <button
                key={item.id}
                className={`chip chip-button ${filter === item.id ? "chip-active" : ""}`}
                onClick={() => setParam("filter", item.id === "all" ? "" : item.id)}
                aria-pressed={filter === item.id}
              >
                {item.label}
              </button>
            ))}
          </div>
        </div>
      </Card>

      {patients.length === 0 ? (
        <Empty>No patients match this filter.</Empty>
      ) : (
        <div className="patient-grid">
          {patients.map((patient) => (
            <article key={patient.id} className="patient-card">
              <div className="patient-card-head">
                <span className={`avatar avatar-large ${patient.risk === "High" ? "avatar-high" : ""}`}>
                  {patient.initials || initials(patient.name)}
                </span>
                <div className="patient-card-title">
                  <h2>
                    <Link to={`/patients/${patient.id}`}>{patient.name}</Link>
                  </h2>
                  <p>
                    {patient.age} years · {patient.condition} · MRN {patient.mrn}
                  </p>
                </div>
                <RiskBadge risk={patient.risk} />
              </div>

              <div className="patient-stats">
                <span>
                  <b>{patient.open_count}</b> open
                </span>
                <span className={patient.overdue_count ? "text-danger" : ""}>
                  <b>{patient.overdue_count}</b> overdue
                </span>
                <span className={patient.conflict_count ? "text-amber" : ""}>
                  <b>{patient.conflict_count}</b> conflict
                </span>
                <span>
                  <b>{patient.pending_verification}</b> to verify
                </span>
                <span>
                  <b>{patient.escalation_count}</b> escalated
                </span>
              </div>

              {patient.priority.length > 0 ? (
                <ul className="patient-priority">
                  {patient.priority.map((obligation) => (
                    <li key={obligation.id}>
                      <StatusPill status={obligation.status} derived={obligation.status_derived} />
                      <span>{obligation.description}</span>
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="empty empty-small">No open obligations.</p>
              )}

              <div className="patient-card-foot">
                <span className="patient-next">
                  <span className="muted">Next due</span>
                  <strong>{formatDate(patient.next_due)}</strong>
                </span>
                <div className="patient-card-actions">
                  <Link className="btn btn-small" to={`/patients/${patient.id}`}>
                    Open journey
                  </Link>
                  <Link className="btn btn-small btn-primary" to={`/patients/${patient.id}/agent`}>
                    Ask agent
                  </Link>
                </div>
              </div>
            </article>
          ))}
        </div>
      )}
    </div>
  );
}
