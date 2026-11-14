import { useState } from "react";
import type { EventItem } from "../types";
import { cx, formatDateTime } from "../ui";
import { Icon, type IconName } from "./Icon";

const ICONS: Record<string, IconName> = {
  document: "document",
  chat: "chat",
  voice_call: "phone",
  coordinator: "check",
  voice_report: "phone",
  voice_transcript: "phone",
};

const TONES: Record<string, string> = {
  document: "blue",
  chat: "neutral",
  voice_call: "purple",
  coordinator: "green",
  voice_report: "purple",
  voice_transcript: "purple",
};

export function Timeline({ events, limit }: { events: EventItem[]; limit?: number }) {
  const [openId, setOpenId] = useState<string | null>(null);
  const items = limit ? events.slice(0, limit) : events;

  if (items.length === 0) {
    return <p className="empty">No activity recorded yet.</p>;
  }

  return (
    <ol className="timeline">
      {items.map((event) => {
        const hasTranscript = (event.transcript?.length || 0) > 0;
        const open = openId === event.id;
        return (
          <li key={event.id} className={cx("timeline-item", open && "timeline-item-open")}>
            <span className={cx("timeline-icon", `timeline-icon-${TONES[event.type] || "neutral"}`)}>
              <Icon name={ICONS[event.type] || "spark"} />
            </span>
            <div className="timeline-body">
              <div className="timeline-head">
                <strong>{event.title}</strong>
                <small>{formatDateTime(event.occurred_at)}</small>
              </div>
              <p>{event.detail}</p>
              <div className="timeline-meta">
                <span>{event.actor || "Continuum"}</span>
                <span className="tag tag-muted">{event.type.replace(/_/g, " ")}</span>
                {event.source_ref ? <span>Ref {event.source_ref}</span> : null}
                {hasTranscript ? (
                  <button className="link-button" onClick={() => setOpenId(open ? null : event.id)}>
                    {open ? "Hide transcript" : `View transcript (${event.transcript?.length})`}
                  </button>
                ) : null}
              </div>
              {open && hasTranscript ? (
                <div className="transcript">
                  {event.transcript?.map((line, index) => (
                    <div key={index} className={cx("transcript-line", `transcript-${line.role}`)}>
                      <span className="transcript-role">{line.role === "agent" ? "Continuum" : "Patient"}</span>
                      <p>{line.message}</p>
                      {line.time_in_call_secs !== undefined ? <small>{line.time_in_call_secs}s</small> : null}
                    </div>
                  ))}
                </div>
              ) : null}
            </div>
          </li>
        );
      })}
    </ol>
  );
}
