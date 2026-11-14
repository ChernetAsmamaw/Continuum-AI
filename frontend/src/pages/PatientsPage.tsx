import { useMemo } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { useAuth } from "../auth";
import { useApp } from "../state";
import type { PatientSummary } from "../types";
import { ErrorBanner, Spinner } from "../ui";
import "./patients.css";

type Filter = "all" | "mine" | "needs" | "overdue";

const FILTERS: { id: Filter; label: string }[] = [
  { id: "all", label: "All" },
  { id: "mine", label: "Mine" },
  { id: "needs", label: "Needs you" },
  { id: "overdue", label: "Overdue" },
];

const needsYou = (p: PatientSummary) =>
  p.overdue_count + p.conflict_count + p.pending_verification + p.escalation_count;

function shortDate(iso?: string | null) {
  if (!iso) return "";
  const d = new Date(`${iso.slice(0, 10)}T12:00:00`);
  return d.toLocaleDateString("en-US", { month: "short", day: "numeric" });
}

export function PatientsPage() {
  const { dashboard, loading, error, refresh } = useApp();
  const { user } = useAuth();
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  const query = params.get("q") || "";
  const rawFilter = params.get("filter");
  const filter: Filter = FILTERS.some((f) => f.id === rawFilter) ? (rawFilter as Filter) : "all";

  const setParam = (key: string, value: string) => {
    const next = new URLSearchParams(params);
    if (value) next.set(key, value);
    else next.delete(key);
    setParams(next, { replace: true });
  };

  const rows = useMemo(() => {
    const needle = query.trim().toLowerCase();
    return (dashboard?.patients ?? [])
      .filter((p) => {
        if (filter === "mine" && p.coordinator !== user?.name) return false;
        if (filter === "needs" && needsYou(p) === 0) return false;
        if (filter === "overdue" && p.overdue_count === 0) return false;
        if (!needle) return true;
        return [p.name, p.mrn, p.condition].join(" ").toLowerCase().includes(needle);
      })
      .sort((a, b) => {
        // Overdue first, then by the next deadline, then by name.
        if (!!a.overdue_count !== !!b.overdue_count) return a.overdue_count ? -1 : 1;
        const ad = a.next_deadline?.due_date || "9999";
        const bd = b.next_deadline?.due_date || "9999";
        return ad === bd ? a.name.localeCompare(b.name) : ad.localeCompare(bd);
      });
  }, [dashboard, filter, query, user]);

  if (!dashboard) {
    return loading ? <Spinner /> : <ErrorBanner message={error || "No patient data."} onRetry={refresh} />;
  }

  return (
    <div className="pt-page">
      <header className="pt-head">
        <div>
          <h1>Patients</h1>
          <p>Everyone in the 30-day window after discharge. Sorted by the next deadline.</p>
        </div>
        <Link className="pt-add" to="/patients/new">Add a discharge</Link>
      </header>

      <div className="pt-tools">
        <input
          className="pt-search"
          value={query}
          onChange={(e) => setParam("q", e.target.value)}
          placeholder="Search name, MRN or condition"
          aria-label="Search patients"
        />
        <div className="pt-filters" role="tablist" aria-label="Filter patients">
          {FILTERS.map((f) => (
            <button
              key={f.id}
              role="tab"
              aria-selected={filter === f.id}
              className={filter === f.id ? "is-on" : ""}
              onClick={() => setParam("filter", f.id === "all" ? "" : f.id)}
            >
              {f.label}
            </button>
          ))}
        </div>
      </div>

      {rows.length === 0 ? (
        <p className="pt-empty">No patients match.</p>
      ) : (
        <div className="pt-table" role="table" aria-label="Patients">
          <div className="pt-row pt-row-head" role="row">
            <span role="columnheader">Patient</span>
            <span role="columnheader">Condition</span>
            <span role="columnheader">Since discharge</span>
            <span role="columnheader">Next deadline</span>
            <span role="columnheader">Needs you</span>
            <span role="columnheader">Coordinator</span>
          </div>
          {rows.map((p) => {
            const count = needsYou(p);
            const overdue = p.overdue_count > 0;
            return (
              <div
                key={p.id}
                className="pt-row"
                role="row"
                tabIndex={0}
                onClick={() => navigate(`/patients/${p.id}`)}
                onKeyDown={(e) => e.key === "Enter" && navigate(`/patients/${p.id}`)}
              >
                <span role="cell" className="pt-name">
                  <i className={overdue ? "pt-dot is-red" : "pt-dot"} aria-hidden />
                  <b>{p.name}</b>
                  <code title={p.mrn}>{p.mrn && p.mrn.length > 12 ? `${p.mrn.slice(0, 8)}…` : p.mrn}</code>
                </span>
                <span role="cell">{p.condition}</span>
                <span role="cell"><code>{p.days_since_discharge != null ? `day ${p.days_since_discharge}` : "—"}</code></span>
                <span role="cell" className={p.next_deadline?.overdue ? "pt-red" : ""}>
                  {p.next_deadline ? `${p.next_deadline.description} · due ${shortDate(p.next_deadline.due_date)}` : "—"}
                </span>
                <span role="cell" className={overdue ? "pt-red pt-strong" : ""}>
                  {count === 0 ? "—" : overdue ? `${count} · ${p.overdue_count} overdue` : count}
                </span>
                <span role="cell" className="pt-muted">{p.coordinator || "Unassigned"}</span>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
