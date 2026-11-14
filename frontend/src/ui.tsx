import type { ReactNode } from "react";
import type { ObligationStatus } from "./types";

export const STATUS_LABELS: Record<string, string> = {
  pending: "Pending",
  scheduled: "Scheduled",
  attempted: "Attempted",
  patient_reported: "Patient reported",
  verified: "Verified",
  overdue: "Overdue",
  cancelled: "Cancelled",
  escalated: "Escalated",
  unknown: "Unknown",
  conflict: "Conflict",
};

export const STATUS_ORDER: ObligationStatus[] = [
  "pending",
  "scheduled",
  "attempted",
  "patient_reported",
  "conflict",
  "overdue",
  "escalated",
  "verified",
  "cancelled",
  "unknown",
];

export function statusLabel(status: string): string {
  return STATUS_LABELS[status] || status.replace(/_/g, " ");
}

export function statusTone(status: string): string {
  switch (status) {
    case "overdue":
    case "conflict":
      return "danger";
    case "escalated":
      return "purple";
    case "patient_reported":
    case "attempted":
      return "amber";
    case "verified":
      return "green";
    case "scheduled":
      return "blue";
    case "cancelled":
      return "muted";
    default:
      return "neutral";
  }
}

export function formatDate(value?: string | null, opts?: Intl.DateTimeFormatOptions): string {
  if (!value) return "—";
  const date = new Date(value.length === 10 ? `${value}T12:00:00` : value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat("en-CA", {
    month: "short",
    day: "numeric",
    year: "numeric",
    ...opts,
  }).format(date);
}

export function formatDateTime(value?: string | null): string {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat("en-CA", {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  }).format(date);
}

export function relativeDate(value?: string | null): string {
  if (!value) return "No date";
  const date = new Date(value.length === 10 ? `${value}T12:00:00` : value);
  if (Number.isNaN(date.getTime())) return value;
  const today = new Date();
  const oneDay = 24 * 60 * 60 * 1000;
  const start = new Date(today.getFullYear(), today.getMonth(), today.getDate()).getTime();
  const target = new Date(date.getFullYear(), date.getMonth(), date.getDate()).getTime();
  const days = Math.round((target - start) / oneDay);
  if (days === 0) return "Today";
  if (days === 1) return "Tomorrow";
  if (days === -1) return "Yesterday";
  if (days > 1) return `In ${days} days`;
  return `${Math.abs(days)} days overdue`;
}

export function dueLabel(days?: number | null): string {
  if (days === null || days === undefined) return "No due date";
  if (days === 0) return "Due today";
  if (days === 1) return "Due tomorrow";
  if (days > 0) return `Due in ${days} days`;
  return `${Math.abs(days)}d overdue`;
}

export function initials(name: string): string {
  return name
    .split(" ")
    .map((part) => part[0])
    .join("")
    .slice(0, 2)
    .toUpperCase();
}

export function pct(value?: number): string {
  if (value === undefined || value === null) return "—";
  return `${Math.round(value * 100)}%`;
}

export function cx(...values: (string | false | null | undefined)[]): string {
  return values.filter(Boolean).join(" ");
}

export function StatusPill({ status, derived }: { status: string; derived?: boolean }) {
  return (
    <span className={cx("pill", `pill-${statusTone(status)}`)} title={derived ? "Derived from the due date" : undefined}>
      <span className="pill-dot" />
      {statusLabel(status)}
    </span>
  );
}

export function Card({
  title,
  action,
  children,
  className,
  subtitle,
}: {
  title?: ReactNode;
  subtitle?: ReactNode;
  action?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section className={cx("card", className)}>
      {(title || action) && (
        <header className="card-head">
          <div>
            {title ? <h2>{title}</h2> : null}
            {subtitle ? <p className="card-sub">{subtitle}</p> : null}
          </div>
          {action}
        </header>
      )}
      {children}
    </section>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="empty">{children}</div>;
}

export function ErrorBanner({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return (
    <div className="banner banner-error" role="alert">
      <div>
        <strong>Something went wrong.</strong>
        <p>{message}</p>
      </div>
      {onRetry ? (
        <button className="btn btn-ghost" onClick={onRetry}>
          Retry
        </button>
      ) : null}
    </div>
  );
}

export function Spinner({ label = "Loading" }: { label?: string }) {
  return (
    <div className="loading">
      <span className="spinner" />
      {label}
    </div>
  );
}

export function RiskBadge({ risk }: { risk: string }) {
  const tone = risk === "High" ? "danger" : risk === "Medium" ? "amber" : "green";
  return <span className={cx("tag", `tag-${tone}`)}>{risk} risk</span>;
}
