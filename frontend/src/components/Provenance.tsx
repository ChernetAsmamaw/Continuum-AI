import { useEffect, useState } from "react";
import { api } from "../api";
import type { ClassifiedBy, Obligation, PossibleDuplicate, ReviewCategory } from "../types";
import { cx, formatDateTime, pct } from "../ui";

export const METHOD_LABELS: Record<string, string> = {
  known_code: "Known code",
  similar_order: "Similar past order (vector search)",
  similar_order_lexical: "Similar past order (word overlap)",
  unrecognized: "Unrecognized",
};

export function methodLabel(method?: string): string {
  return (method && METHOD_LABELS[method]) || (method || "").replace(/_/g, " ");
}

export function categoryLabel(category?: string): string {
  return (category || "—").replace(/_/g, " ");
}

/** Does this follow-up's type still need a coordinator's confirmation? */
export function needsTypeReview(obligation: Obligation): boolean {
  return !!obligation.classified_by && !obligation.classified_by.confirmed;
}

/** Small tags for the obligation meta row: where it came from and whether its type is settled. */
export function ProvenanceTags({ obligation }: { obligation: Obligation }) {
  const cb = obligation.classified_by;
  return (
    <>
      {obligation.origin === "ehr_structured" ? (
        <span className="tag tag-blue" title={`Structured EHR record${obligation.fhir_refs?.length ? `: ${obligation.fhir_refs[0]}` : ""}`}>
          EHR order
        </span>
      ) : obligation.origin === "ai_extracted" ? (
        <span className="tag tag-purple" title="Extracted by AI from the discharge narrative">
          AI-extracted
        </span>
      ) : null}
      {obligation.possible_duplicate_of ? (
        <span className="tag tag-amber" title="May be the same as an EHR order whose type is unconfirmed">
          Possible duplicate
        </span>
      ) : null}
      {cb && !cb.confirmed ? (
        <span className="tag tag-amber" title={classificationTitle(cb)}>
          {cb.method === "unrecognized" ? "Type unrecognized" : "Type inferred"}
        </span>
      ) : null}
      {cb?.confirmed ? (
        <span
          className="tag tag-green"
          title={cb.outcome === "learned" ? `Code taught earlier by ${cb.confirmed_by}` : `${cb.outcome === "corrected" ? "Corrected" : "Confirmed"} by ${cb.confirmed_by}`}
        >
          Type {cb.outcome === "corrected" ? "corrected" : cb.outcome === "learned" ? "learned" : "confirmed"}
        </span>
      ) : null}
    </>
  );
}

function classificationTitle(cb: ClassifiedBy): string {
  if (cb.method === "unrecognized") return "No rule or past order matched this EHR order";
  return `${methodLabel(cb.method)}${cb.matched ? ` · matched "${cb.matched}"` : ""}${cb.score !== undefined ? ` · ${pct(cb.score)}` : ""}`;
}

/** Detail rows for the expanded obligation card. */
export function ProvenanceDetails({ obligation }: { obligation: Obligation }) {
  const cb = obligation.classified_by;
  if (!obligation.origin && !cb && !obligation.fhir_refs?.length) return null;
  return (
    <div className="detail-block">
      <h4>Provenance</h4>
      <dl className="kv prov-kv">
        <div>
          <dt>Origin</dt>
          <dd>
            {obligation.origin === "ehr_structured"
              ? `Structured EHR record${obligation.ehr_kind ? ` · ${obligation.ehr_kind.replace("order:", "")} order` : ""}`
              : obligation.origin === "ai_extracted"
                ? "AI-extracted from the discharge narrative"
                : obligation.origin || "Care interaction"}
          </dd>
        </div>
        {obligation.fhir_refs?.length ? (
          <div>
            <dt>EHR reference</dt>
            <dd className="prov-refs">
              {obligation.fhir_refs.map((ref) => (
                <code key={ref}>{ref}</code>
              ))}
            </dd>
          </div>
        ) : null}
        {cb ? (
          <>
            <div>
              <dt>Order type</dt>
              <dd>{methodLabel(cb.method)}</dd>
            </div>
            {cb.matched ? (
              <div>
                <dt>Matched past order</dt>
                <dd>
                  “{cb.matched}”{cb.source_ref ? <small> · {cb.source_ref}</small> : null}
                </dd>
              </div>
            ) : null}
            {cb.score !== undefined ? (
              <div>
                <dt>Similarity</dt>
                <dd>
                  <ScoreBar score={cb.score} />
                </dd>
              </div>
            ) : null}
            {cb.confirmed ? (
              <div>
                <dt>{cb.outcome === "corrected" ? "Corrected" : cb.outcome === "learned" ? "Learned from" : "Confirmed"}</dt>
                <dd>
                  {cb.outcome === "corrected" && cb.original_category
                    ? `${categoryLabel(cb.original_category)} → ${categoryLabel(obligation.category)}`
                    : categoryLabel(obligation.category)}
                  <small>
                    {" "}
                    · {cb.confirmed_by} · {formatDateTime(cb.confirmed_at)}
                  </small>
                </dd>
              </div>
            ) : null}
          </>
        ) : null}
      </dl>
    </div>
  );
}

export function ScoreBar({ score }: { score: number }) {
  const tone = score >= 0.9 ? "green" : score >= 0.7 ? "blue" : "amber";
  return (
    <span className="score-bar" title={`${pct(score)} similarity`}>
      <span className="score-track">
        <span className={cx("score-fill", `score-${tone}`)} style={{ width: `${Math.round(Math.min(1, score) * 100)}%` }} />
      </span>
      <b>{pct(score)}</b>
    </span>
  );
}

let categoryCache: ReviewCategory[] | null = null;

export function useReviewCategories(initial?: ReviewCategory[]): ReviewCategory[] {
  const [categories, setCategories] = useState<ReviewCategory[]>(initial || categoryCache || []);
  useEffect(() => {
    if (initial?.length) {
      categoryCache = initial;
      setCategories(initial);
      return;
    }
    if (categoryCache) return;
    let live = true;
    api
      .review()
      .then((queue) => {
        categoryCache = queue.categories;
        if (live) setCategories(queue.categories);
      })
      .catch(() => undefined);
    return () => {
      live = false;
    };
  }, [initial]);
  return categories;
}

/**
 * Confirm or correct what an inferred/unrecognized EHR order is. The answer is
 * taught back to Continuum's order memory so the next identical order is exact.
 */
export function OrderTypeForm({
  obligation,
  sourceOrder,
  duplicates,
  categories: given,
  onDone,
  onCancel,
  compact = false,
}: {
  obligation: Obligation;
  duplicates?: PossibleDuplicate[];
  sourceOrder?: { code?: string | null; display: string; performer?: string | null } | null;
  categories?: ReviewCategory[];
  onDone: (outcome: "confirmed" | "corrected", cancelledDuplicates: number) => void;
  onCancel?: () => void;
  compact?: boolean;
}) {
  const categories = useReviewCategories(given);
  const cb = obligation.classified_by;
  const [category, setCategory] = useState(obligation.suggested_category || obligation.category || "follow_up");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const changed = category !== obligation.category;

  const submit = async () => {
    setBusy(true);
    setError(null);
    try {
      const result = await api.reviewOrder(obligation.id, category, note.trim() || undefined);
      onDone(result.outcome, (result.duplicates || []).filter((d) => d.resolution === "cancelled_duplicate").length);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not save the order type.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className={cx("order-type-form", compact && "order-type-form-compact")}>
      {!compact ? (
        <div className="order-type-evidence">
          <div>
            <small>EHR order</small>
            <b>{sourceOrder?.display || obligation.source?.quote || obligation.description}</b>
            {sourceOrder?.code ? <code>{sourceOrder.code}</code> : null}
          </div>
          <div>
            <small>How Continuum placed it</small>
            <b>{methodLabel(cb?.method)}</b>
            {cb?.matched ? <span>like “{cb.matched}”</span> : null}
            {cb?.score !== undefined ? <ScoreBar score={cb.score} /> : null}
          </div>
        </div>
      ) : null}
      {duplicates?.length ? (
        <div className="order-type-duplicates">
          {duplicates.map((dup) => (
            <p key={dup.id}>
              <span className="tag tag-amber">Possible duplicate</span> The discharge narrative also produced{" "}
              <b>“{dup.description}”</b> ({categoryLabel(dup.category)}){dup.quote ? <> from “{dup.quote.replace(/[.\s]+$/, "")}”</> : null}.{" "}
              {category === dup.category
                ? "Saving this type treats them as one follow-up and cancels the AI copy."
                : "With a different type they stay as two follow-ups."}
            </p>
          ))}
        </div>
      ) : null}
      <div className="order-type-controls">
        <label className="field">
          <span>Follow-up type</span>
          <select value={category} onChange={(event) => setCategory(event.target.value)} aria-label="Follow-up type">
            {!categories.some((c) => c.category === category) ? <option value={category}>{categoryLabel(category)}</option> : null}
            {categories.map((c) => (
              <option key={c.category} value={c.category}>
                {c.label}
                {c.category === obligation.category ? " (current)" : ""}
                {c.category === obligation.suggested_category && c.category !== obligation.category ? " (suggested by narrative)" : ""}
              </option>
            ))}
          </select>
        </label>
        {!compact ? (
          <label className="field">
            <span>Note (optional)</span>
            <input value={note} onChange={(event) => setNote(event.target.value)} placeholder="e.g. Wound nurse home visit" />
          </label>
        ) : null}
        <div className="inline-actions">
          {onCancel ? (
            <button className="btn btn-small" onClick={onCancel} disabled={busy}>
              Cancel
            </button>
          ) : null}
          <button className="btn btn-small btn-primary" onClick={submit} disabled={busy}>
            {busy ? "Saving…" : changed ? `Correct to ${categoryLabel(category)}` : `Confirm ${categoryLabel(category)}`}
          </button>
        </div>
      </div>
      <p className="form-hint">
        {sourceOrder?.code
          ? `Continuum will remember code ${sourceOrder.code} as ${categoryLabel(category)}, so the next identical order is recognized exactly.`
          : `Continuum will remember this order text as ${categoryLabel(category)} for future discharges.`}{" "}
        The change is recorded in the audit log.
      </p>
      {error ? <p className="form-error">{error}</p> : null}
    </div>
  );
}
