import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";
import type { TcmRow, TcmWorklist as Worklist } from "../types";
import { Card, cx, formatDate } from "../ui";

const LANES: { key: keyof Worklist["lanes"]; title: string; detail: (r: TcmRow) => string; tone: string }[] = [
  { key: "contact_missed", title: "Contact window missed", detail: (r) => `Due ${formatDate(r.contact_due)} · ${r.contact_attempts} attempt(s)`, tone: "danger" },
  { key: "contact_due", title: "Contact due", detail: (r) => `By ${formatDate(r.contact_due)} · ${r.contact_attempts} of 2 attempts`, tone: "warn" },
  { key: "visit_missed", title: "Visit window missed", detail: (r) => `Was due ${formatDate(r.visit_due)} · ${r.code}`, tone: "danger" },
  { key: "visit_unbooked", title: "Visit not booked, due this week", detail: (r) => `By ${formatDate(r.visit_due)} · ${r.code}`, tone: "warn" },
];

/** The post-discharge deadlines a practice is held to, across the caseload. */
export function TcmWorklist({ refreshKey }: { refreshKey: number }) {
  const [data, setData] = useState<Worklist | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let live = true;
    api
      .tcmWorklist()
      .then((result) => live && (setData(result), setFailed(false)))
      .catch(() => live && setFailed(true));
    return () => {
      live = false;
    };
  }, [refreshKey]);

  const total = data ? LANES.reduce((sum, lane) => sum + data.lanes[lane.key].length, 0) : 0;

  return (
    <Card title="Transitional care deadlines" subtitle="Contact within 2 business days · visit within 7 or 14 days of discharge">
      {failed ? (
        <p className="empty empty-small">The TCM worklist could not be loaded.</p>
      ) : !data ? (
        <p className="empty empty-small">Loading…</p>
      ) : total === 0 ? (
        <p className="empty empty-small">Every recent discharge has its contact made and its visit booked.</p>
      ) : (
        <div className="tcm-lanes">
          {LANES.filter((lane) => data.lanes[lane.key].length).map((lane) => (
            <div key={lane.key} className={cx("tcm-lane", `tcm-lane-${lane.tone}`)}>
              <h3>
                {lane.title} <span className="count-badge">{data.lanes[lane.key].length}</span>
              </h3>
              <ul>
                {data.lanes[lane.key].slice(0, 5).map((row) => (
                  <li key={row.patient_id}>
                    <Link to={`/patients/${row.patient_id}`}>{row.name}</Link>
                    <small>{lane.detail(row)}</small>
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </div>
      )}
    </Card>
  );
}
