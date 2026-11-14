import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api } from "../api";
import { ApprovalCard } from "../components/ApprovalCard";
import { useApp } from "../state";
import type { Approval, ContactOutcome, LedgerAction, Obligation, PatientSummary, ReviewQueue } from "../types";
import { cx, ErrorBanner, formatDate, formatDateTime, Spinner, statusLabel } from "../ui";
import "../queue.css";

/* "Needs you": one queue, one item in focus, one main action.
   Pulls together what used to be spread over Overview, Approvals, Review and
   the action ledger. Everything here is built on the existing API. */

type Filter = "all" | "overdue" | "verify" | "system";

type Kind =
  | "approval-verify"
  | "approval-contact"
  | "approval-other"
  | "approval-special"
  | "overdue"
  | "conflict"
  | "order"
  | "identity"
  | "action";

interface Item {
  key: string;
  kind: Kind;
  filter: Exclude<Filter, "all">;
  title: string;
  patientId?: string;
  when: string;
  overdueDays: number;
  sortAt: string;
  approval?: Approval;
  obligation?: Obligation;
  order?: ReviewQueue["orders"][number];
  identity?: ReviewQueue["identities"][number];
  action?: LedgerAction;
}

const FILTERS: { id: Filter; label: string }[] = [
  { id: "all", label: "All" },
  { id: "overdue", label: "Overdue" },
  { id: "verify", label: "To verify" },
  { id: "system", label: "System" },
];

const OUTCOMES: { value: ContactOutcome; label: string }[] = [
  { value: "reached", label: "Reached the patient" },
  { value: "reached_caregiver", label: "Reached the caregiver" },
  { value: "voicemail", label: "Left a voicemail" },
  { value: "no_answer", label: "No answer" },
  { value: "wrong_number", label: "Wrong number" },
  { value: "declined", label: "Declined" },
  { value: "message_sent", label: "Sent a message" },
];

function daysSince(value: string | undefined, today: Date): number {
  if (!value) return 0;
  const date = new Date(value.length === 10 ? `${value}T12:00:00` : value);
  return Math.max(0, Math.round((today.getTime() - date.getTime()) / 86400000));
}

function overdueText(days: number) {
  return days === 1 ? "1 day overdue" : `${days} days overdue`;
}

function firstName(name?: string) {
  return name ? name.split(" ")[0] : "patient";
}

function buildItems(
  approvals: Approval[],
  obligations: Obligation[],
  review: ReviewQueue | null,
  actions: LedgerAction[],
  today: Date,
): Item[] {
  const items: Item[] = [];
  const covered = new Set<string>();

  for (const approval of approvals) {
    if (approval.obligation_id) covered.add(approval.obligation_id);
    const special = approval.kind === "resolve_conflict" || Boolean(approval.urgency);
    const changes = approval.proposed_changes || {};
    const contactDue = typeof changes.contact_due === "string" ? changes.contact_due : undefined;
    const overdueDays = contactDue ? daysSince(contactDue, today) : 0;
    const kind: Kind = special
      ? "approval-special"
      : approval.kind === "verify_completion"
        ? "approval-verify"
        : approval.kind === "follow_up" && contactDue
          ? "approval-contact"
          : "approval-other";
    items.push({
      key: `ap:${approval.id}`,
      kind,
      filter: overdueDays > 0 ? "overdue" : kind === "approval-verify" ? "verify" : special ? "verify" : "system",
      title: kind === "approval-contact" ? "Post-discharge call" : approval.title,
      patientId: approval.patient_id,
      when: overdueDays > 0 ? overdueText(overdueDays) : kind === "approval-verify" ? "to verify" : formatDate(approval.created_at),
      overdueDays: approval.urgency === "emergency" ? 999 : approval.urgency === "urgent" ? 998 : overdueDays,
      sortAt: approval.created_at,
      approval,
    });
  }

  for (const obligation of obligations) {
    if (!obligation.is_open || covered.has(obligation.id)) continue;
    if (obligation.status !== "overdue" && obligation.status !== "conflict") continue;
    const days = obligation.days_overdue ?? 0;
    items.push({
      key: `ob:${obligation.id}`,
      kind: obligation.status === "conflict" ? "conflict" : "overdue",
      filter: obligation.status === "conflict" ? "verify" : "overdue",
      title: obligation.description,
      patientId: obligation.patient_id,
      when: obligation.status === "conflict" ? "dates disagree" : overdueText(days),
      overdueDays: obligation.status === "overdue" ? days : 0,
      sortAt: obligation.due_date || "",
      obligation,
    });
  }

  for (const order of review?.orders ?? []) {
    items.push({
      key: `or:${order.id}`,
      kind: "order",
      filter: "system",
      title: "Confirm the order type",
      patientId: order.patient_id,
      when: "unknown code",
      overdueDays: 0,
      sortAt: order.created_at || "",
      order,
    });
  }

  for (const identity of review?.identities ?? []) {
    items.push({
      key: `id:${identity.patient.id}`,
      kind: "identity",
      filter: "system",
      title: "Is this the same person?",
      patientId: identity.patient.id,
      when: "identity check",
      overdueDays: 0,
      sortAt: identity.patient.created_at || "",
      identity,
    });
  }

  for (const action of actions) {
    items.push({
      key: `ac:${action.id}`,
      kind: "action",
      filter: "system",
      title: action.status === "unknown" ? "We don't know if this went out" : "Sent, but never confirmed",
      patientId: action.patient_id,
      when: formatDateTime(action.intended_at).split(",").pop()?.trim() || "",
      overdueDays: 0,
      sortAt: action.intended_at,
      action,
    });
  }

  // Overdue first (most overdue at the top), then oldest first.
  return items.sort((a, b) => b.overdueDays - a.overdueDays || a.sortAt.localeCompare(b.sortAt));
}

export function QueuePage() {
  const { dashboard, config, refresh, refreshKey, notify, error: appError, loading } = useApp();
  const navigate = useNavigate();
  const [approvals, setApprovals] = useState<Approval[] | null>(null);
  const [review, setReview] = useState<ReviewQueue | null>(null);
  const [actions, setActions] = useState<LedgerAction[]>([]);
  const [filter, setFilter] = useState<Filter>("all");
  const [selectedKey, setSelectedKey] = useState<string | null>(null);
  const [snoozed, setSnoozed] = useState<Set<string>>(() => new Set());
  const [done, setDone] = useState<{ key: string; title: string; patient?: string; note: string }[]>([]);
  const [leaving, setLeaving] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const focusRef = useRef<HTMLDivElement>(null);

  const load = useCallback(async () => {
    try {
      const [ap, rv, ac] = await Promise.all([
        api.approvals({ status: "pending" }),
        api.review().catch(() => null),
        api.actions({ status: "unconfirmed,unknown", limit: 20 }).catch(() => null),
      ]);
      setApprovals(ap.approvals);
      setReview(rv);
      setActions(ac?.actions ?? []);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not load the queue.");
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load, refreshKey]);

  const today = useMemo(
    () => (config?.demo_today ? new Date(`${config.demo_today}T12:00:00`) : new Date()),
    [config?.demo_today],
  );

  const patients = useMemo(
    () => new Map<string, PatientSummary>((dashboard?.patients ?? []).map((p) => [p.id, p])),
    [dashboard],
  );

  const all = useMemo(
    () =>
      approvals
        ? buildItems(approvals, dashboard?.priority_obligations ?? [], review, actions, today).filter(
            (item) => !snoozed.has(item.key),
          )
        : [],
    [approvals, dashboard, review, actions, today, snoozed],
  );

  const counts = useMemo(() => {
    const result: Record<Filter, number> = { all: all.length, overdue: 0, verify: 0, system: 0 };
    for (const item of all) result[item.filter] += 1;
    return result;
  }, [all]);

  const visible = filter === "all" ? all : all.filter((item) => item.filter === filter);
  const selected = visible.find((item) => item.key === selectedKey) ?? visible[0] ?? null;

  const comingUp = useMemo(
    () =>
      (dashboard?.priority_obligations ?? [])
        .filter((o) => o.is_open && o.status !== "overdue" && o.status !== "conflict" && !all.some((i) => i.obligation?.id === o.id || i.approval?.obligation_id === o.id))
        .sort((a, b) => (a.due_date || "").localeCompare(b.due_date || "")),
    [dashboard, all],
  );

  const move = useCallback(
    (step: number) => {
      if (!visible.length) return;
      const index = selected ? visible.findIndex((i) => i.key === selected.key) : -1;
      const next = visible[Math.min(visible.length - 1, Math.max(0, index + step))];
      setSelectedKey(next.key);
    },
    [visible, selected],
  );

  /** Run an action, then let the item leave the queue and open the next one. */
  const complete = useCallback(
    async (item: Item, run: () => Promise<unknown>, message: string) => {
      await run();
      const index = visible.findIndex((i) => i.key === item.key);
      const next = visible[index + 1] ?? visible[index - 1];
      setLeaving(item.key);
      window.setTimeout(() => {
        setLeaving(null);
        setSelectedKey(next?.key ?? null);
        setDone((list) => [{ key: item.key, title: item.title, patient: patients.get(item.patientId || "")?.name, note: message }, ...list]);
      }, 220);
      notify(message);
      await Promise.all([refresh(), load()]);
    },
    [visible, notify, refresh, load, patients],
  );

  const snooze = (item: Item) => {
    const index = visible.findIndex((i) => i.key === item.key);
    const next = visible[index + 1] ?? visible[index - 1];
    setSnoozed((set) => new Set(set).add(item.key));
    setSelectedKey(next?.key ?? null);
    notify("Snoozed until you reload the page.");
  };

  // J/K move through the queue. Enter is handled by the focus pane's main button.
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.metaKey || event.ctrlKey || event.altKey) return;
      const el = event.target as HTMLElement | null;
      if (el && (["INPUT", "TEXTAREA", "SELECT"].includes(el.tagName) || el.isContentEditable)) return;
      if (document.querySelector(".modal-overlay, .palette-overlay, .help-overlay, .tour-overlay")) return;
      const key = event.key.toLowerCase();
      if (key === "j") {
        event.preventDefault();
        move(1);
      } else if (key === "k" && event.shiftKey === false && visible.length) {
        // k is also the global command palette key; inside the queue it moves up.
        event.preventDefault();
        event.stopImmediatePropagation();
        move(-1);
      } else if (key === "p" && selected?.patientId) {
        event.preventDefault();
        navigate(`/patients/${selected.patientId}`);
      } else if (event.key === "Enter" && !el?.closest(".nq-focus, a, .modal-overlay")) {
        // Enter runs the main action from anywhere except inside the focus pane,
        // where buttons and links handle Enter themselves.
        const primary = focusRef.current?.querySelector<HTMLButtonElement>("[data-primary]");
        if (primary && !primary.disabled) {
          event.preventDefault();
          primary.click();
        }
      }
    };
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  }, [move, navigate, selected, visible.length]);

  if (!approvals || !dashboard) {
    if (error || (!loading && appError)) return <ErrorBanner message={error || appError || "No data"} onRetry={load} />;
    return <Spinner label="Loading what needs you" />;
  }

  const overdueCount = all.filter((i) => i.overdueDays > 0).length;

  return (
    <div className="nq">
      <section className="nq-queue" aria-label="Needs you">
        <header className="nq-queue-head">
          <h1>Needs you</h1>
          <p>
            {all.length} item{all.length === 1 ? "" : "s"}
            {overdueCount ? (
              <>
                {" · "}
                <span className="nq-red">{overdueCount} overdue</span>
              </>
            ) : null}
            {" · overdue first, then oldest"}
          </p>
        </header>
        <div className="nq-filters" role="tablist" aria-label="Filter the queue">
          {FILTERS.map((f) => (
            <button
              key={f.id}
              role="tab"
              aria-selected={filter === f.id}
              className={cx("nq-filter", filter === f.id && "nq-filter-on")}
              onClick={() => setFilter(f.id)}
            >
              {f.label} <span>{counts[f.id]}</span>
            </button>
          ))}
        </div>

        {visible.length === 0 ? (
          <p className="nq-empty-row">Nothing here.</p>
        ) : (
          <ul className="nq-list">
            {visible.map((item) => {
              const patient = patients.get(item.patientId || "");
              return (
                <li key={item.key}>
                  <button
                    className={cx(
                      "nq-row",
                      selected?.key === item.key && "nq-row-on",
                      leaving === item.key && "nq-row-leaving",
                    )}
                    onClick={() => {
                      setSelectedKey(item.key);
                      if (window.matchMedia("(max-width: 760px)").matches) {
                        window.setTimeout(() => focusRef.current?.scrollIntoView({ behavior: "smooth", block: "start" }), 0);
                      }
                    }}
                    aria-current={selected?.key === item.key}
                  >
                    <span className={cx("nq-dot", item.overdueDays > 0 && "nq-dot-red")} aria-hidden />
                    <span className="nq-row-main">
                      <strong>{item.title}</strong>
                      <small>{patient?.name || item.identity?.patient.name || "Unknown patient"}</small>
                    </span>
                    <span className={cx("nq-when", item.overdueDays > 0 && "nq-red")}>{item.when}</span>
                  </button>
                </li>
              );
            })}
          </ul>
        )}

        <details className="nq-group">
          <summary>
            Coming up <span>{comingUp.length}</span>
          </summary>
          <ul>
            {comingUp.map((o) => (
              <li key={o.id}>
                <Link to={`/patients/${o.patient_id}`}>
                  <strong>{o.description}</strong>
                  <small>
                    {patients.get(o.patient_id)?.name} · {statusLabel(o.status).toLowerCase()} · due {formatDate(o.due_date)}
                  </small>
                </Link>
              </li>
            ))}
          </ul>
        </details>
        <details className="nq-group">
          <summary>
            Done this session <span>{done.length}</span>
          </summary>
          <ul>
            {done.map((d) => (
              <li key={d.key}>
                <div>
                  <strong>{d.title}</strong>
                  <small>
                    {d.patient ? `${d.patient} · ` : ""}
                    {d.note}
                  </small>
                </div>
              </li>
            ))}
          </ul>
        </details>
      </section>

      <section className="nq-focus" ref={focusRef} aria-live="polite">
        {selected ? (
          <Focus
            key={selected.key}
            item={selected}
            patient={patients.get(selected.patientId || "")}
            onComplete={complete}
            onSnooze={snooze}
          />
        ) : (
          <EmptyFocus next={comingUp[0]} patientName={comingUp[0] ? patients.get(comingUp[0].patient_id)?.name : undefined} />
        )}
      </section>
    </div>
  );
}

function EmptyFocus({ next, patientName }: { next?: Obligation; patientName?: string }) {
  return (
    <div className="nq-empty">
      <h2>Nothing needs you right now.</h2>
      {next ? (
        <p>
          Next due: <strong>{next.description}</strong> for {patientName} on {formatDate(next.due_date)}.
        </p>
      ) : (
        <p>No open follow-ups are due.</p>
      )}
      <Link className="btn" to="/patients">
        See all patients
      </Link>
    </div>
  );
}

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="nq-kv">
      <span>{label}</span>
      <div>{children}</div>
    </div>
  );
}

function Focus({
  item,
  patient,
  onComplete,
  onSnooze,
}: {
  item: Item;
  patient?: PatientSummary;
  onComplete: (item: Item, run: () => Promise<unknown>, message: string) => Promise<void>;
  onSnooze: (item: Item) => void;
}) {
  const navigate = useNavigate();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [noteMode, setNoteMode] = useState<null | "log" | "reject" | "contact">(null);
  const [note, setNote] = useState("");
  const [outcome, setOutcome] = useState<ContactOutcome>("reached");
  const name = patient?.name || item.identity?.patient.name;
  const first = firstName(name);

  const run = async (fn: () => Promise<unknown>, message: string) => {
    setBusy(true);
    setError(null);
    try {
      await onComplete(item, fn, message);
    } catch (err) {
      setError(err instanceof Error ? err.message : "That didn't work.");
      setBusy(false);
    }
  };

  const callPatient = () => navigate(`/patients/${item.patientId}/call`);
  const openPatient = () => navigate(`/patients/${item.patientId}`);

  let why: ReactNode = null;
  let evidence: ReactNode = null;
  let primary: { label: string; onClick: () => void } | null = null;
  const secondary: { label: string; onClick: () => void }[] = [];
  let sources: ReactNode = null;

  const a = item.approval;
  const o = item.obligation;

  switch (item.kind) {
    case "approval-verify": {
      why = a!.rationale;
      evidence = (
        <>
          <Field label="What we heard">{a!.evidence_summary || "Nothing recorded."}</Field>
          <Field label="Proof">
            {a!.clinic_evidence ? (
              <>
                Clinic record received <code>{a!.clinic_evidence.source_ref}</code>
              </>
            ) : (
              "No clinic record yet. Only verify if you confirmed it with the clinic."
            )}
          </Field>
        </>
      );
      primary = {
        label: "Verify and close",
        onClick: () => run(() => api.resolveApproval(a!.id, true, note.trim() || undefined), "Verified and closed."),
      };
      secondary.push({ label: "Not enough proof", onClick: () => setNoteMode("reject") });
      break;
    }
    case "approval-contact": {
      const contactDue = String(a!.proposed_changes.contact_due);
      const attempts = Number(a!.evidence_summary?.match(/\d+/)?.[0] ?? 0);
      why = (
        <>
          Nobody has reached {first} since discharge. The 2-business-day contact window closed on {formatDate(contactDue)}
          {attempts > 0 ? `, and only ${attempts} attempt${attempts === 1 ? " is" : "s are"} documented.` : " with no attempts documented."}
        </>
      );
      evidence = (
        <>
          <Field label="Contact due">{formatDate(contactDue)}</Field>
          <Field label="Phone">
            <code>{patient?.phone || "Not on file"}</code>
          </Field>
          {patient?.preferences?.best_time ? <Field label="Best time">{patient.preferences.best_time}</Field> : null}
          {patient?.caregiver ? <Field label="Caregiver">{patient.caregiver}</Field> : null}
        </>
      );
      primary = { label: `Call ${first}`, onClick: callPatient };
      secondary.push({ label: "Log a contact attempt", onClick: () => setNoteMode("contact") });
      break;
    }
    case "approval-other": {
      why = a!.rationale;
      evidence = a!.evidence_summary ? <Field label="Evidence">{a!.evidence_summary}</Field> : null;
      primary = {
        label: "Approve",
        onClick: () => run(() => api.resolveApproval(a!.id, true, note.trim() || undefined), "Approved."),
      };
      secondary.push({ label: "Reject", onClick: () => setNoteMode("reject") });
      break;
    }
    case "overdue": {
      why = (
        <>
          {o!.description} was due {formatDate(o!.due_date)} and nobody has confirmed it.{" "}
          {o!.latest_evidence ? `Last we heard: ${o!.latest_evidence}` : "We have no update yet."}
        </>
      );
      evidence = (
        <>
          <Field label="Who">{o!.owner}</Field>
          <Field label="By when">{formatDate(o!.due_date)}</Field>
          {patient?.phone ? (
            <Field label="Patient phone">
              <code>{patient.phone}</code>
            </Field>
          ) : null}
          {o!.next_action ? <Field label="Next step">{o!.next_action}</Field> : null}
        </>
      );
      primary = { label: `Call ${first}`, onClick: callPatient };
      secondary.push({ label: "Log what happened", onClick: () => setNoteMode("log") });
      break;
    }
    case "conflict": {
      why = "Two sources disagree about this follow-up. Pick the value that is right on the patient's page; the other is kept as history.";
      evidence = (
        <>
          {(o!.conflicts ?? [])
            .filter((c) => !c.resolved)
            .map((c, i) => (
              <div className="nq-compare" key={i}>
                <div>
                  <span>On record</span>
                  <strong>{c.recorded_value}</strong>
                </div>
                <div>
                  <span>Reported{c.reported_by ? ` by ${c.reported_by}` : ""}</span>
                  <strong>{c.reported_value}</strong>
                </div>
              </div>
            ))}
        </>
      );
      primary = { label: "Resolve on patient page", onClick: openPatient };
      break;
    }
    case "order": {
      const order = item.order!;
      const suggested = order.suggested_category || order.classified_by?.category || order.category;
      why = "The hospital sent an order code we don't recognise. Confirm what kind of follow-up it is so the right team picks it up.";
      evidence = (
        <>
          <Field label="Order">{order.source_order?.display || order.description}</Field>
          {order.source_order?.code ? (
            <Field label="Code">
              <code>{order.source_order.code}</code>
            </Field>
          ) : null}
          <Field label="Best guess">{suggested ? suggested.replace(/_/g, " ") : "None"}</Field>
        </>
      );
      if (suggested) {
        primary = {
          label: `Confirm as ${suggested.replace(/_/g, " ")}`,
          onClick: () => run(() => api.reviewOrder(order.id, suggested), "Order type confirmed."),
        };
      }
      secondary.push({ label: "Choose another type", onClick: () => navigate("/review") });
      break;
    }
    case "identity": {
      const id = item.identity!;
      const other = id.candidates[0];
      why = id.reason || "A new record looks like a patient we already have. Check before anything is merged.";
      evidence = other ? (
        <div className="nq-compare">
          {[id.patient, other].map((p) => (
            <div key={p.id}>
              <span>{p === id.patient ? "New record" : "Existing patient"}</span>
              <strong>{p.name}</strong>
              <small>
                Born {formatDate(p.birth_date)} · MRN <code>{p.mrn || "—"}</code>
                <br />
                {p.phone || "no phone"} · {p.open_follow_ups} open follow-ups
              </small>
            </div>
          ))}
        </div>
      ) : null;
      if (other) {
        secondary.push(
          {
            label: "Same person, merge",
            onClick: () => run(() => api.mergeIdentity(id.patient.id, other.id), "Records merged."),
          },
          {
            label: "Different people",
            onClick: () => run(() => api.dismissIdentity(id.patient.id), "Kept as separate patients."),
          },
        );
      }
      break;
    }
    case "action": {
      const action = item.action!;
      why = `${action.summary}. ${action.status === "unknown" ? "It was interrupted mid-send, so we can't tell if it reached anyone." : `We expected ${action.proof_expected || "proof"} and it never came.`}`;
      evidence = (
        <>
          <Field label="Started">{formatDateTime(action.intended_at)}</Field>
          {action.confirm_by ? <Field label="Proof due by">{formatDateTime(action.confirm_by)}</Field> : null}
        </>
      );
      primary = { label: "Open in calls and tasks", onClick: () => navigate("/actions") };
      sources = (
        <ol className="nq-track">
          {action.history.map((h, i) => (
            <li key={i}>
              <code>{formatDateTime(h.at)}</code> {h.status}: {h.detail}
            </li>
          ))}
        </ol>
      );
      break;
    }
    default:
      break;
  }

  if (item.patientId && item.kind !== "conflict") secondary.push({ label: "Open patient", onClick: openPatient });

  const submitNote = () => {
    if (noteMode === "reject") {
      void run(() => api.resolveApproval(a!.id, false, note.trim() || undefined), "Sent back. The record is unchanged.");
    } else if (noteMode === "log") {
      if (!note.trim()) return setError("Write what you heard and who told you.");
      void run(
        () => api.changeStatus(o!.id, { status: "patient_reported", evidence_summary: note.trim() }),
        "Logged as a report. It still needs proof to close.",
      );
    } else if (noteMode === "contact") {
      void run(async () => {
        await api.logContact(item.patientId!, { outcome, channel: "Voice call", note: note.trim() || undefined });
        await api.resolveApproval(a!.id, true, `Contact attempt logged: ${outcome.replace(/_/g, " ")}`);
      }, "Contact attempt logged.");
    }
  };

  return (
    <article className="nq-item">
      <p className="nq-context">
        {item.patientId && name ? <Link to={`/patients/${item.patientId}`}>{name}</Link> : null}
        {patient ? ` · ${patient.age} · MRN ${patient.mrn} · ${patient.condition}` : null}
      </p>
      <h2>{item.kind === "approval-contact" ? `Post-discharge call to ${first}` : item.title}</h2>
      {item.overdueDays > 0 && item.overdueDays < 900 ? <p className="nq-status nq-red">{overdueText(item.overdueDays)}</p> : null}
      {a?.urgency ? <p className="nq-status nq-red">{a.urgency === "emergency" ? "Emergency" : "Urgent"}</p> : null}
      {o?.assignee ? <p className="nq-assignee">Assigned to {o.assignee.name}</p> : null}

      {item.kind === "approval-special" ? (
        <div className="nq-embed">
          <ApprovalCard approval={a!} />
        </div>
      ) : (
        <>
          {why ? <p className="nq-why">{why}</p> : null}
          {evidence ? <div className="nq-evidence">{evidence}</div> : null}
        </>
      )}

      {(sources || o?.history?.length || o?.source?.quote || a) && item.kind !== "approval-special" ? (
        <details className="nq-sources">
          <summary>Sources and history</summary>
          {sources}
          {o?.source?.quote ? <blockquote>“{o.source.quote}”</blockquote> : null}
          {a ? (
            <p>
              Raised {formatDateTime(a.created_at)} by {a.source || "Continuum"} · {a.kind.replace(/_/g, " ")}
              {a.confidence ? ` · confidence ${Math.round(a.confidence * 100)}%` : ""}
            </p>
          ) : null}
          {o?.history?.length ? (
            <ol className="nq-track">
              {o.history.map((h, i) => (
                <li key={i}>
                  <code>{formatDateTime(h.at)}</code> {statusLabel(h.status)} · {h.actor}: {h.reason}
                </li>
              ))}
            </ol>
          ) : null}
        </details>
      ) : null}

      {noteMode ? (
        <div className="nq-note">
          {noteMode === "contact" ? (
            <label>
              What happened
              <select value={outcome} onChange={(e) => setOutcome(e.target.value as ContactOutcome)}>
                {OUTCOMES.map((x) => (
                  <option key={x.value} value={x.value}>
                    {x.label}
                  </option>
                ))}
              </select>
            </label>
          ) : null}
          <label>
            {noteMode === "log" ? "Who told you, and what did they say?" : noteMode === "reject" ? "Why? (optional)" : "Note (optional)"}
            <textarea
              autoFocus
              rows={3}
              value={note}
              onChange={(e) => setNote(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Escape") setNoteMode(null);
              }}
            />
          </label>
          {noteMode === "log" ? <p className="nq-hint">Saved as a report, not proof. The follow-up stays open until the clinic confirms.</p> : null}
          <div className="nq-note-actions">
            <button className="btn btn-primary" onClick={submitNote} disabled={busy}>
              Save
            </button>
            <button className="btn" onClick={() => setNoteMode(null)} disabled={busy}>
              Cancel
            </button>
          </div>
        </div>
      ) : null}

      {error ? <p className="nq-error">{error}</p> : null}

      {item.kind !== "approval-special" && !noteMode ? (
        <div className="nq-actions">
          {primary ? (
            <button className="btn btn-primary" data-primary onClick={primary.onClick} disabled={busy}>
              {busy ? "Working…" : primary.label}
              <kbd>↵</kbd>
            </button>
          ) : null}
          {secondary.map((s) => (
            <button key={s.label} className="btn" onClick={s.onClick} disabled={busy}>
              {s.label}
            </button>
          ))}
          <button className="btn btn-ghost" onClick={() => onSnooze(item)} disabled={busy}>
            Snooze
          </button>
          <span className="nq-keys">
            <kbd>J</kbd>
            <kbd>K</kbd> move · <kbd>P</kbd> patient
          </span>
        </div>
      ) : item.kind === "approval-special" ? (
        <div className="nq-actions">
          <button className="btn" onClick={openPatient}>
            Open patient
          </button>
          <button className="btn btn-ghost" onClick={() => onSnooze(item)}>
            Snooze
          </button>
        </div>
      ) : null}
    </article>
  );
}
