import { useMemo } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { useAuth } from "../auth";
import { useApp } from "../state";
import type { PatientSummary } from "../types";
import { ErrorBanner, Spinner, cx } from "../ui";
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

  const showCoordinator = (dashboard?.patients ?? []).some((p) => p.coordinator);
  const count = needsYou;

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
        <div className={cx("pt-table pl-table", showCoordinator && "pl-with-coord")} role="table" aria-label="Patients">
          <div className="pt-row pt-row-head" role="row">
            <span role="columnheader">Patient</span>
            <span role="columnheader">Since discharge</span>
            <span role="columnheader">Next deadline</span>
            <span role="columnheader">Needs you</span>
            {showCoordinator ? <span role="columnheader">Coordinator</span> : null}
          </div>
          {rows.map((p) => {
            const overdue = p.overdue_count > 0;
            const day = p.days_since_discharge;
            const decide = p.conflict_count + p.pending_verification;
            const mrn = p.mrn && /^[A-Z]{1,3}-?\d{3,}$/.test(p.mrn) ? p.mrn : null;
            return (
              <div
                key={p.id}
                className={cx("pt-row", overdue && "pl-overdue")}
                role="row"
                tabIndex={0}
                onClick={() => navigate(`/patients/${p.id}`)}
                onKeyDown={(e) => e.key === "Enter" && navigate(`/patients/${p.id}`)}
              >
                <span role="cell" className="pl-who">
                  <i className={cx("dot", overdue ? "dot-red" : count(p) ? "dot-amber" : "dot-neutral")} aria-hidden />
                  <span>
                    <b>{p.name}</b>
                    <small>
                      {p.condition}
                      {mrn ? <span className="pl-mrn">{mrn}</span> : null}
                    </small>
                  </span>
                </span>
                <span role="cell" className="pl-day">
                  {day != null ? (
                    <>
                      <span>Day {Math.max(0, day)} of 30</span>
                      <i className="pl-track" aria-hidden>
                        <i style={{ width: `${Math.min(100, (Math.max(0, day) / 30) * 100)}%` }} />
                      </i>
                    </>
                  ) : (
                    <span className="pt-muted">No stay</span>
                  )}
                </span>
                <span role="cell" className="pl-next">
                  {p.next_deadline ? (
                    <>
                      <span>{p.next_deadline.description}</span>
                      <small className={p.next_deadline.overdue ? "tone-red" : ""}>
                        {p.next_deadline.overdue ? "Was due" : "Due"} {shortDate(p.next_deadline.due_date)}
                      </small>
                    </>
                  ) : (
                    <span className="pt-muted">Nothing open</span>
                  )}
                </span>
                <span role="cell" className="pl-needs">
                  {overdue ? <span className="tag-tone tone-red">{p.overdue_count} overdue</span> : null}
                  {decide ? <span className="tag-tone tone-amber">{decide} to check</span> : null}
                  {p.escalation_count ? <span className="tag-tone tone-red">{p.escalation_count} escalated</span> : null}
                  {!overdue && !decide && !p.escalation_count ? <span className="pt-muted">Nothing</span> : null}
                </span>
                {showCoordinator ? <span role="cell" className="pt-muted">{p.coordinator || "Unassigned"}</span> : null}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
