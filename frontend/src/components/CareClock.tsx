import type { TcmState } from "../types";
import { cx, formatDate as formatDateLong, formatDateTime } from "../ui";
import { OUTCOME_LABEL, VisitWindowControl } from "./TcmPanel";

/** Short dates on the clock: the year is in the heading's context already. */
const formatDate = (value?: string | null) => formatDateLong(value, { year: undefined });

/**
 * The 30 days after discharge on one line: discharged, first contact, the
 * face-to-face visit window, today, day 30. Red only where a deadline was
 * missed. On narrow screens the same points become a list.
 */

type Mark = { key: string; day: number; label: string; sub: string; state: "done" | "late" | "todo"; side: "up" | "down" };

function dayOf(iso: string | null | undefined, discharge: string): number | null {
  if (!iso) return null;
  const a = Date.parse(`${iso.slice(0, 10)}T12:00:00`);
  const b = Date.parse(`${discharge}T12:00:00`);
  if (Number.isNaN(a) || Number.isNaN(b)) return null;
  return Math.round((a - b) / 86_400_000);
}

function plusDays(iso: string, days: number): string {
  const d = new Date(`${iso}T12:00:00`);
  d.setDate(d.getDate() + days);
  return d.toISOString().slice(0, 10);
}

export function CareClock({ tcm, patientId, onChanged }: { tcm: TcmState; patientId: string; onChanged: () => void | Promise<void> }) {
  if (!tcm.eligible || !tcm.discharge_date) {
    return tcm.reason ? <p className="clock-none muted">{tcm.reason}</p> : null;
  }
  const discharge = tcm.discharge_date;
  const today = Math.max(0, tcm.days_since_discharge ?? 0);
  const pos = (day: number) => `${Math.min(100, Math.max(0, (day / 30) * 100))}%`;
  const windowDays = tcm.visit_window_days ?? 7;

  const marks: Mark[] = [{ key: "discharged", day: 0, label: "Discharged", sub: formatDate(discharge), state: "done", side: "up" }];

  const contactMet = tcm.contact_status === "met" || tcm.contact_status === "attempts_documented";
  const contactDay = (contactMet ? dayOf(tcm.contact_made_at, discharge) : null) ?? dayOf(tcm.contact_due, discharge) ?? 2;
  marks.push({
    key: "contact",
    day: contactDay,
    label: "First contact",
    sub: contactMet
      ? `${formatDate(tcm.contact_made_at || tcm.contact_due)} ✓`
      : tcm.contact_status === "missed"
        ? `due ${formatDate(tcm.contact_due)} · missed`
        : `due ${formatDate(tcm.contact_due)}`,
    state: contactMet ? "done" : tcm.contact_status === "missed" ? "late" : "todo",
    side: "down",
  });

  const visitMet = tcm.visit_status === "met";
  const visitDay = dayOf(tcm.visit_due, discharge) ?? windowDays;
  const visitLate = !visitMet && (tcm.visit_status === "missed" || today > visitDay);
  marks.push({
    key: "visit",
    day: visitDay,
    label: `Visit (${windowDays}-day)`,
    sub: visitMet
      ? `by ${formatDate(tcm.visit_due)} ✓`
      : tcm.visit_status === "booked"
        ? `booked ${formatDate(tcm.visit_date)}`
        : tcm.visit_status === "booked_late"
          ? `booked late · ${formatDate(tcm.visit_date)}`
          : visitLate
            ? `due ${formatDate(tcm.visit_due)} · missed`
            : `due ${formatDate(tcm.visit_due)}`,
    state: visitMet ? "done" : visitLate ? "late" : "todo",
    side: "up",
  });
  if (visitMet && tcm.visit_date) {
    marks.push({ key: "seen", day: dayOf(tcm.visit_date, discharge) ?? visitDay, label: "Seen", sub: formatDate(tcm.visit_date), state: "done", side: "down" });
  }
  marks.push({ key: "day30", day: 30, label: "Day 30", sub: formatDate(plusDays(discharge, 30)), state: today >= 30 ? "done" : "todo", side: today >= 30 ? "up" : "down" });

  const attempts = tcm.contacts || [];
  const last = attempts[attempts.length - 1];

  return (
    <section className="clock-panel" aria-label="30 days after discharge">
      <div className="clock-head">
        <h2>30 days after discharge</h2>
        <span className="clock-rule muted">
          <span>Contact within 2 business days</span>
          <span>Visit within {windowDays} days</span>
          {tcm.med_rec_due ? <span>Medications reconciled by {formatDate(tcm.med_rec_due)}</span> : null}
        </span>
      </div>

      <div className="clock" aria-hidden="true">
        <div className="clock-bar" />
        <div className="clock-elapsed" style={{ width: pos(today) }} />
        {marks.map((m) => (
          <div key={m.key} className={cx("clock-m", `clock-${m.state}`, `clock-${m.side}`)} style={{ left: pos(m.day) }}>
            <i />
            <span>
              <b>{m.label}</b>
              <em>{m.sub}</em>
            </span>
          </div>
        ))}
        <div className="clock-today" style={{ left: pos(today) }}>
          <span>Today · day {today}</span>
        </div>
      </div>

      <ul className="clock-list">
        {marks.map((m) => (
          <li key={m.key} className={cx(`clock-${m.state}`)}>
            <i />
            <span>{m.label}</span>
            <small>{m.sub}</small>
          </li>
        ))}
        <li className="clock-list-today">
          <i />
          <span>Today</span>
          <small>day {today}</small>
        </li>
      </ul>

      <div className="clock-foot">
        {attempts.length ? (
          <details className="clock-attempts">
            <summary>
              {attempts.length} contact attempt{attempts.length === 1 ? "" : "s"}
              {last ? `. Last on ${formatDateTime(last.at)}: ${(OUTCOME_LABEL[last.outcome] || last.outcome).toLowerCase()}` : ""}
            </summary>
            <ol>
              {attempts.map((c) => (
                <li key={c.id}>
                  <span className="mono muted">{formatDateTime(c.at)}</span>
                  <span>
                    {c.channel} · <b>{OUTCOME_LABEL[c.outcome] || c.outcome}</b>
                    {c.note ? ` · ${c.note}` : ""}
                  </span>
                  <small className="muted">{c.actor}</small>
                </li>
              ))}
            </ol>
          </details>
        ) : (
          <span className="muted">No contact attempts logged yet.</span>
        )}
        <VisitWindowControl patientId={patientId} tcm={tcm} onChanged={onChanged} />
      </div>
    </section>
  );
}
