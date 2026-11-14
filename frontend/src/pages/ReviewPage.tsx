import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";
import { Modal } from "../components/Modal";
import { categoryLabel, methodLabel, OrderTypeForm, ScoreBar } from "../components/Provenance";
import { useApp } from "../state";
import type { IdentityCase, IdentityView, ReviewQueue } from "../types";
import { Card, Empty, ErrorBanner, formatDate, Spinner, cx } from "../ui";

const ROWS: { key: keyof IdentityView | "ehr"; label: string; render?: (p: IdentityView) => string }[] = [
  { key: "name", label: "Name" },
  { key: "birth_date", label: "Birth date", render: (p) => (p.birth_date ? formatDate(p.birth_date) : "—") },
  { key: "mrn", label: "MRN" },
  { key: "ehr", label: "EHR identity", render: (p) => p.ehr_fhir_id || (p.ehr_identifiers[0]?.value ?? "—") },
  { key: "phone", label: "Phone" },
  { key: "condition", label: "Condition" },
  { key: "latest_discharge", label: "Latest discharge", render: (p) => (p.latest_discharge ? formatDate(p.latest_discharge) : "—") },
  { key: "facility", label: "Facility" },
  { key: "follow_ups", label: "Follow-ups", render: (p) => `${p.follow_ups} (${p.open_follow_ups} open)` },
  { key: "episodes", label: "Episodes" },
];

function cell(p: IdentityView, row: (typeof ROWS)[number]): string {
  if (row.render) return row.render(p);
  const value = p[row.key as keyof IdentityView];
  return value === undefined || value === null || value === "" ? "—" : String(value);
}

function IdentityCaseCard({ item, onResolved }: { item: IdentityCase; onResolved: (message: string) => void }) {
  const [confirm, setConfirm] = useState<{ kind: "merge"; into: IdentityView } | { kind: "dismiss" } | null>(null);
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const columns = [item.patient, ...item.candidates];

  const submit = async () => {
    if (!confirm) return;
    setBusy(true);
    setError(null);
    try {
      if (confirm.kind === "merge") {
        const result = await api.mergeIdentity(item.patient.id, confirm.into.id, note.trim() || undefined);
        const total = Object.values(result.moved).reduce((sum, n) => sum + n, 0);
        onResolved(`Merged into ${confirm.into.name}: ${total} records moved.`);
      } else {
        await api.dismissIdentity(item.patient.id, note.trim() || undefined);
        onResolved("Kept as different people.");
      }
      setConfirm(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not resolve the review.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <article className="identity-case">
      <p className="identity-reason">{item.reason || "Possible duplicate patient."}</p>
      <div className="identity-compare" style={{ gridTemplateColumns: `minmax(110px, 0.7fr) repeat(${columns.length}, minmax(0, 1fr))` }}>
        <div className="identity-head identity-head-blank" />
        {columns.map((p, index) => (
          <div key={p.id} className={cx("identity-head", index === 0 && "identity-head-new")}>
            <small>{index === 0 ? "New record from the EHR" : "Existing patient"}</small>
            <Link to={`/patients/${p.id}`}>{p.name}</Link>
            <code>{p.id}</code>
          </div>
        ))}
        {ROWS.map((row) => {
          const values = columns.map((p) => cell(p, row));
          const differs = new Set(values.filter((v) => v !== "—")).size > 1;
          return (
            <div key={row.key} className="identity-row" role="row">
              <span className="identity-label">{row.label}</span>
              {values.map((value, index) => (
                <span key={index} className={cx("identity-value", differs && row.key !== "follow_ups" && row.key !== "episodes" && "identity-diff", value === "—" && "muted")}>
                  {value}
                </span>
              ))}
            </div>
          );
        })}
      </div>
      <div className="identity-actions">
        {item.candidates.map((candidate) => (
          <button key={candidate.id} className="btn btn-small btn-primary" onClick={() => { setNote(""); setError(null); setConfirm({ kind: "merge", into: candidate }); }}>
            Same person: merge into {candidate.name} ({candidate.mrn || candidate.id})
          </button>
        ))}
        <button className="btn btn-small" onClick={() => { setNote(""); setError(null); setConfirm({ kind: "dismiss" }); }}>
          Different people: dismiss
        </button>
      </div>

      {confirm ? (
        <Modal
          title={confirm.kind === "merge" ? "Merge duplicate record" : "Keep as different people"}
          subtitle={confirm.kind === "merge" ? `${item.patient.name} → ${confirm.into.name}` : item.patient.name}
          onClose={() => setConfirm(null)}
          footer={
            <>
              <button className="btn" onClick={() => setConfirm(null)}>
                Cancel
              </button>
              <button className={cx("btn", confirm.kind === "merge" ? "btn-danger btn-primary" : "btn-primary")} onClick={submit} disabled={busy}>
                {busy ? "Saving…" : confirm.kind === "merge" ? "Merge records" : "Dismiss review"}
              </button>
            </>
          }
        >
          {confirm.kind === "merge" ? (
            <p className="form-hint form-hint-warn">
              Moves <b>{item.patient.follow_ups} follow-ups</b>, <b>{item.patient.episodes} episode(s)</b>, their evidence, timeline,
              memories, approvals and actions from <b>{item.patient.id}</b> onto <b>{confirm.into.name}</b> ({confirm.into.id}), and
              links the EHR identity so the next discharge matches exactly. The duplicate record is removed from the caseload; a full
              copy is kept and the merge is written to the audit log.
            </p>
          ) : (
            <p className="form-hint">
              Both records stay as they are and the flag is cleared. The decision is written to the audit log.
            </p>
          )}
          <label className="field">
            <span>Note (optional, saved in the audit log)</span>
            <textarea rows={2} value={note} onChange={(event) => setNote(event.target.value)} placeholder="How you confirmed it, e.g. birth date on the referral letter" />
          </label>
          {error ? <p className="form-error">{error}</p> : null}
        </Modal>
      ) : null}
    </article>
  );
}

export function ReviewPage() {
  const { refreshKey, refresh, notify } = useApp();
  const [queue, setQueue] = useState<ReviewQueue | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setQueue(await api.review());
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not load the review queue.");
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load, refreshKey]);

  const resolved = async (message: string) => {
    notify(message);
    await load();
    await refresh();
  };

  if (!queue) return error ? <ErrorBanner message={error} onRetry={load} /> : <Spinner label="Loading the review queue" />;

  return (
    <div className="stack">
      <header className="page-head">
        <div>
          <p className="eyebrow">
            <span className="eyebrow-dot" />
            Coordinator review
          </p>
          <h1>Decisions Continuum won’t guess</h1>
          <p className="page-sub">
            {queue.counts.total === 0
              ? "Nothing waiting. Every EHR order type and patient identity is settled."
              : `${queue.counts.orders} order type${queue.counts.orders === 1 ? "" : "s"} to confirm and ${queue.counts.identities} possible duplicate patient${queue.counts.identities === 1 ? "" : "s"}.`}
          </p>
        </div>
      </header>

      {error ? <ErrorBanner message={error} onRetry={load} /> : null}

      <Card
        title="Confirm order types"
        subtitle="EHR orders the rules didn't recognize, or that were placed by similarity to a past order. Your answer teaches Continuum the next one."
      >
        {queue.orders.length === 0 ? (
          <Empty>No inferred or unrecognized orders waiting.</Empty>
        ) : (
          <div className="review-orders">
            {queue.orders.map((item) => (
              <article key={item.id} className="review-order">
                <div className="review-order-head">
                  <div>
                    <strong>{item.description}</strong>
                    <small>
                      <Link to={`/patients/${item.patient_id}`} className="link-inline">
                        {item.patient_name || item.patient_id}
                      </Link>
                      {" · "}
                      {item.source_order?.ref || item.fhir_refs?.[0]}
                    </small>
                  </div>
                  <span className={cx("tag", item.classified_by?.method === "unrecognized" ? "tag-amber" : "tag-blue")}>
                    {methodLabel(item.classified_by?.method)}
                  </span>
                </div>
                <dl className="kv review-order-kv">
                  <div>
                    <dt>EHR order</dt>
                    <dd>
                      {item.source_order?.display || item.source?.quote || "—"}
                      {item.source_order?.code ? <code> {item.source_order.code}</code> : null}
                    </dd>
                  </div>
                  <div>
                    <dt>Filed as</dt>
                    <dd>{categoryLabel(item.category)}</dd>
                  </div>
                  {item.suggested_category && item.suggested_category !== item.category ? (
                    <div>
                      <dt>Narrative suggests</dt>
                      <dd>{categoryLabel(item.suggested_category)}</dd>
                    </div>
                  ) : null}
                  {item.classified_by?.matched ? (
                    <div>
                      <dt>Matched past order</dt>
                      <dd>“{item.classified_by.matched}”</dd>
                    </div>
                  ) : null}
                  {item.classified_by?.score !== undefined ? (
                    <div>
                      <dt>Similarity</dt>
                      <dd>
                        <ScoreBar score={item.classified_by.score} />
                      </dd>
                    </div>
                  ) : null}
                </dl>
                <OrderTypeForm
                  obligation={item}
                  sourceOrder={item.source_order}
                  duplicates={item.possible_duplicates}
                  categories={queue.categories}
                  compact
                  onDone={(outcome, n) =>
                    void resolved(
                      (outcome === "corrected" ? `Corrected and learned: ${item.description}.` : `Confirmed and learned: ${item.description}.`) +
                        (n ? ` ${n === 1 ? "The AI copy was" : `${n} AI copies were`} cancelled as a duplicate.` : ""),
                    )
                  }
                />
              </article>
            ))}
          </div>
        )}
      </Card>

      <Card
        title="Identity review"
        subtitle="A discharge matched an existing patient by name only. Records are never merged automatically."
      >
        <div id="identity" />
        {queue.identities.length === 0 ? (
          <Empty>No possible duplicates waiting.</Empty>
        ) : (
          <div className="stack">
            {queue.identities.map((item) => (
              <IdentityCaseCard key={item.patient.id} item={item} onResolved={(message) => void resolved(message)} />
            ))}
          </div>
        )}
      </Card>
    </div>
  );
}
