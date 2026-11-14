import { useCallback, useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { api } from "../api";
import { ACTION_STATUSES, ActionChip, ActionList } from "../components/ActionLedger";
import { useApp } from "../state";
import type { LedgerAction, LedgerSummary } from "../types";
import { Card, Empty, ErrorBanner, Spinner, cx } from "../ui";

type View = "attention" | "waiting" | "all" | (typeof ACTION_STATUSES)[number];

const VIEW_STATUS: Record<string, string | undefined> = {
  attention: "unconfirmed,unknown",
  waiting: "intended,sent",
  all: undefined,
};

export function ActionsPage() {
  const { dashboard, refreshKey } = useApp();
  const [params, setParams] = useSearchParams();
  const view = (params.get("view") as View) || "attention";
  const [actions, setActions] = useState<LedgerAction[] | null>(null);
  const [summary, setSummary] = useState<LedgerSummary | null>(null);
  const [error, setError] = useState<string | null>(null);

  const status = view in VIEW_STATUS ? VIEW_STATUS[view] : view;

  const load = useCallback(async () => {
    try {
      const data = await api.actions({ status, limit: 200 });
      setActions(data.actions);
      setSummary(data.summary);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not load the action ledger.");
    }
  }, [status]);

  useEffect(() => {
    void load();
  }, [load, refreshKey]);

  const names = useMemo(() => new Map((dashboard?.patients || []).map((p) => [p.id, p.name])), [dashboard]);
  const setView = (next: View) => setParams(next === "attention" ? {} : { view: next });

  if (!summary) return error ? <ErrorBanner message={error} onRetry={load} /> : <Spinner label="Loading the action ledger" />;

  const counts = summary.counts;
  const total = ACTION_STATUSES.reduce((sum, s) => sum + (counts[s] || 0), 0);

  return (
    <div className="stack">
      <header className="page-head">
        <div>
          <p className="eyebrow">
            <span className="eyebrow-dot" />
            Action ledger
          </p>
          <h1>What Continuum did, and what it can prove</h1>
          <p className="page-sub">
            Every outward action is recorded before it happens. “Sent” never counts as done: only the expected proof (a call
            transcript, a coordinator’s decision) confirms it.
          </p>
        </div>
      </header>

      {error ? <ErrorBanner message={error} onRetry={load} /> : null}

      <section className="ledger-states" aria-label="Actions by state">
        {ACTION_STATUSES.map((s) => (
          <button key={s} className={cx("ledger-state", view === s && "ledger-state-active")} onClick={() => setView(s)}>
            <ActionChip status={s} />
            <b>{counts[s] || 0}</b>
          </button>
        ))}
      </section>

      <Card
        title={view === "attention" ? "Needs attention" : view === "waiting" ? "Waiting for proof" : view === "all" ? "All actions" : `${view[0].toUpperCase()}${view.slice(1)} actions`}
        subtitle={
          view === "attention"
            ? "Unconfirmed (sent, no proof by the deadline) and unknown (interrupted mid-send). A human reconciles these; Continuum never blindly retries."
            : "Newest first. Open the history to see every state change."
        }
        action={
          <div className="tabs">
            {(["attention", "waiting", "all"] as View[]).map((item) => (
              <button key={item} className={cx("tab", view === item && "tab-active")} onClick={() => setView(item)} aria-pressed={view === item}>
                {item === "attention"
                  ? `Needs attention ${summary.needs_attention}`
                  : item === "waiting"
                    ? `Waiting ${(counts.intended || 0) + (counts.sent || 0)}`
                    : `All ${total}`}
              </button>
            ))}
          </div>
        }
      >
        {!actions ? (
          <Spinner />
        ) : actions.length === 0 ? (
          <Empty>{view === "attention" ? "Every action is either confirmed, failed or still inside its proof window." : "No actions in this view."}</Empty>
        ) : (
          <ActionList actions={actions} patientNames={names} showPatient />
        )}
      </Card>
    </div>
  );
}
