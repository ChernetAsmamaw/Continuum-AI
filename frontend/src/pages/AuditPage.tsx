import { useCallback, useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";
import type { Tone } from "../tones";
import { useApp } from "../state";
import type { AuditEntry } from "../types";
import { Card, Empty, ErrorBanner, formatDateTime, Spinner, cx } from "../ui";

/** Same color vocabulary as the rest of the console (src/tones.ts). */
function auditTone(action: string): Tone {
  const a = action.toLowerCase();
  if (/reject|fail|escalat|overdue|missed|delete|merge/.test(a)) return "red";
  if (/prepared|request|queued|conflict|override|review|pending/.test(a)) return "amber";
  if (/approv|verif|confirm|resolved/.test(a)) return "green";
  if (/conflict|override|request|review|pending/.test(a)) return "amber";
  if (/voice|call|sms|message|contact/.test(a)) return "violet";
  if (/ehr|ingest|fhir|hl7|document|intake|extract/.test(a)) return "blue";
  return "neutral";
}

function pretty(value: unknown): string {
  if (value === undefined || value === null) return "—";
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}

export function AuditPage() {
  const { dashboard, refreshKey } = useApp();
  const [entries, setEntries] = useState<AuditEntry[]>([]);
  const [patientFilter, setPatientFilter] = useState("");
  const [actionFilter, setActionFilter] = useState("");
  const [query, setQuery] = useState("");
  const [expanded, setExpanded] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const data = await api.audit(patientFilter || undefined, 300);
      setEntries(data.audit);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not load the audit log.");
    } finally {
      setLoading(false);
    }
  }, [patientFilter]);

  useEffect(() => {
    void load();
  }, [load, refreshKey]);

  const actions = useMemo(
    () => Array.from(new Set(entries.map((entry) => entry.action))).sort(),
    [entries],
  );

  const visible = useMemo(() => {
    const needle = query.trim().toLowerCase();
    return entries.filter((entry) => {
      if (actionFilter && entry.action !== actionFilter) return false;
      if (!needle) return true;
      return [entry.actor, entry.action, entry.detail, entry.patient_id, entry.run_id || ""]
        .join(" ")
        .toLowerCase()
        .includes(needle);
    });
  }, [entries, actionFilter, query]);

  const [allNames, setAllNames] = useState<Map<string, string>>(new Map());
  useEffect(() => {
    api.patients().then((r) => setAllNames(new Map(r.patients.map((p) => [p.id, p.name])))).catch(() => undefined);
  }, []);
  const patientName = (id: string) =>
    id === "system" ? "System" : allNames.get(id) || dashboard?.patients.find((patient) => patient.id === id)?.name || "Removed patient";

  return (
    <div className="stack">
      <header className="dq-head">
        <div>
          <h1>Audit log</h1>
          <p>Every change anyone made, person or assistant, with the values before and after. Nothing here can be edited or deleted.</p>
        </div>
        <button className="btn" onClick={load}>
          Refresh
        </button>
      </header>

      {error ? <ErrorBanner message={error} onRetry={load} /> : null}

      <Card className="filter-card">
        <div className="filter-bar">
          <div className="filter-chips">
            <label className="select-inline">
              <span>Patient</span>
              <select value={patientFilter} onChange={(event) => setPatientFilter(event.target.value)}>
                <option value="">All patients</option>
                {(dashboard?.patients ?? []).map((patient) => (
                  <option key={patient.id} value={patient.id}>
                    {patient.name}
                  </option>
                ))}
              </select>
            </label>
            <label className="select-inline">
              <span>Action</span>
              <select value={actionFilter} onChange={(event) => setActionFilter(event.target.value)}>
                <option value="">All actions</option>
                {actions.map((action) => (
                  <option key={action} value={action}>
                    {action}
                  </option>
                ))}
              </select>
            </label>
          </div>
          <div className="filter-search">
            <input
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="Search actor, detail, run id…"
            />
            {query ? (
              <button className="link-button" onClick={() => setQuery("")}>
                Clear
              </button>
            ) : null}
          </div>
        </div>
      </Card>

      <div className="audit-summary">
        <span>
          <b>{visible.length}</b> entries
        </span>
        <span>
          <b>{new Set(visible.map((entry) => entry.actor)).size}</b> actors
        </span>
        <span>
          <b>{new Set(visible.map((entry) => entry.patient_id)).size}</b> patients
        </span>
        {loading ? <span className="muted">Refreshing…</span> : null}
      </div>

      {loading && entries.length === 0 ? (
        <Spinner label="Loading audit trail" />
      ) : visible.length === 0 ? (
        <Empty>No audit entries match these filters.</Empty>
      ) : (
        <div className="audit-table">
          <div className="audit-table-head">
            <span>Time</span>
            <span>Patient</span>
            <span>Actor</span>
            <span>Action</span>
            <span>Detail</span>
          </div>
          {visible.map((entry) => {
            const open = expanded === entry.id;
            return (
              <div key={entry.id} className={cx("audit-entry", open && "audit-entry-open")}>
                <button
                  className="audit-table-row"
                  onClick={() => setExpanded(open ? null : entry.id)}
                  aria-expanded={open}
                >
                  <span className="audit-time">{formatDateTime(entry.created_at)}</span>
                  <span>{patientName(entry.patient_id)}</span>
                  <span className="audit-actor">{entry.actor}</span>
                  <span>
                    <span className={cx("tag-tone", `tone-${auditTone(entry.action)}`)}>{entry.action.replace(/[_.]/g, " ")}</span>
                  </span>
                  <span className="audit-detail">{entry.detail}</span>
                </button>
                {open ? (
                  <div className="audit-expanded">
                    <div className="audit-ids">
                      <span>
                        <small>Audit id</small>
                        <code>{entry.id}</code>
                      </span>
                      <span>
                        <small>Thread</small>
                        <code>{entry.thread_id || "—"}</code>
                      </span>
                      <span>
                        <small>Run</small>
                        <code>{entry.run_id || "—"}</code>
                      </span>
                      <span>
                        <small>Evidence</small>
                        <code>{entry.evidence_id || "—"}</code>
                      </span>
                    </div>
                    <div className="audit-diff">
                      <div>
                        <h4>Before</h4>
                        <pre>{pretty(entry.before)}</pre>
                      </div>
                      <div>
                        <h4>After</h4>
                        <pre>{pretty(entry.after)}</pre>
                      </div>
                    </div>
                  </div>
                ) : null}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
