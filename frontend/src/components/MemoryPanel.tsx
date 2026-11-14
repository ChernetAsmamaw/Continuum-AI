import { useEffect, useState } from "react";
import { api } from "../api";
import type { Memory } from "../types";
import { cx, formatDateTime, pct } from "../ui";

const KIND_LABELS: Record<string, string> = {
  patient_context: "Patient context",
  preference: "Preference",
  care_obligation: "Care obligation",
  interaction: "Interaction",
  change: "Change",
  uncertainty: "Uncertainty",
};

export function MemoryPanel({
  patientId,
  memories,
  title = "Persistent memory",
  compact = false,
}: {
  patientId: string;
  memories: Memory[];
  title?: string;
  compact?: boolean;
}) {
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<Memory[] | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setResults(null);
    setQuery("");
  }, [patientId, memories]);

  const items = results ?? memories;

  const search = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!query.trim()) {
      setResults(null);
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const data = await api.memories(patientId, query.trim());
      setResults(data.memories || []);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not search memory.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className={cx("memory-panel", compact && "memory-panel-compact")}>
      <div className="memory-head">
        <div>
          <h3>{title}</h3>
          <p className="muted">
            {results ? `${items.length} recalled for “${query}”` : `${items.length} memories stored in Atlas`}
          </p>
        </div>
      </div>
      <form className="memory-search" onSubmit={search}>
        <input
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          placeholder="Semantic recall — e.g. caregiver, cardiology date"
          aria-label="Search memory"
        />
        <button className="btn btn-small" type="submit" disabled={busy}>
          {busy ? "Recalling…" : "Recall"}
        </button>
        {results ? (
          <button
            className="btn btn-small btn-ghost"
            type="button"
            onClick={() => {
              setResults(null);
              setQuery("");
            }}
          >
            Clear
          </button>
        ) : null}
      </form>
      {error ? <p className="form-error">{error}</p> : null}

      {items.length === 0 ? (
        <p className="empty">No memories matched.</p>
      ) : (
        <ul className="memory-list">
          {items.map((memory) => (
            <li key={memory.id} className={cx("memory-item", memory.retrieval && `memory-${memory.retrieval}`)}>
              <div className="memory-meta">
                <span className="tag tag-muted">{KIND_LABELS[memory.kind] || memory.kind.replace(/_/g, " ")}</span>
                {memory.retrieval ? (
                  <span className={cx("tag", memory.retrieval === "vector" ? "tag-blue" : "tag-amber")}>
                    {memory.retrieval}
                  </span>
                ) : null}
                {memory.score !== undefined ? <span className="memory-score">{pct(memory.score)} match</span> : null}
                <small>{formatDateTime(memory.created_at)}</small>
              </div>
              <p className="memory-summary">{memory.summary}</p>
              {!compact && memory.content && memory.content !== memory.summary ? (
                <p className="memory-content">{memory.content}</p>
              ) : null}
              {memory.tags && memory.tags.length > 0 ? (
                <div className="tag-row">
                  {memory.tags.slice(0, 6).map((tag) => (
                    <span key={tag} className="chip">
                      {tag}
                    </span>
                  ))}
                </div>
              ) : null}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
