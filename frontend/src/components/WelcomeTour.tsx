import { useEffect } from "react";
import { createPortal } from "react-dom";
import { Icon, type IconName } from "./Icon";

const TOUR_ITEMS: { icon: IconName; title: string; body: string }[] = [
  { icon: "overview", title: "Overview", body: "Your caseload at a glance — open loops, overdue and awaiting verification, plus the demo day runner." },
  { icon: "users", title: "Patient journey", body: "Each patient's timeline, obligations and evidence trail in one place." },
  { icon: "check", title: "Approvals", body: "Sensitive changes pause here for your decision. Approve or reject with Y / X." },
  { icon: "document", title: "Audit log", body: "Append-only trail of every change. Nothing in Continuum is ever overwritten." },
];

const TOUR_KEYS: { label: string; keys: string[] }[] = [
  { label: "Overview", keys: ["g", "d"] },
  { label: "Patients", keys: ["g", "p"] },
  { label: "Approvals", keys: ["g", "a"] },
  { label: "Audit log", keys: ["g", "l"] },
  { label: "Search", keys: ["/"] },
  { label: "Ask Continuum", keys: ["a"] },
  { label: "Command palette", keys: ["k"] },
  { label: "All shortcuts", keys: ["?"] },
];

export function WelcomeTour({
  open,
  onClose,
  onShowHelp,
}: {
  open: boolean;
  onClose: () => void;
  onShowHelp: () => void;
}) {
  useEffect(() => {
    if (!open) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  if (!open || typeof document === "undefined") return null;

  return createPortal(
    <div className="tour-overlay" role="presentation" onMouseDown={(event) => event.target === event.currentTarget && onClose()}>
      <div className="tour-card" role="dialog" aria-modal="true" aria-label="Welcome to Continuum">
        <p className="eyebrow">
          <span className="eyebrow-dot" />
          Welcome to Continuum
        </p>
        <h2>Your post-discharge caseload, in one calm place.</h2>
        <p>
          Every follow-up stays an open loop until evidence closes it. Here is where things live:
        </p>
        <div className="tour-grid">
          {TOUR_ITEMS.map((item) => (
            <div className="tour-item" key={item.title}>
              <span className="tour-icon">
                <Icon name={item.icon} />
              </span>
              <div>
                <b>{item.title}</b>
                <span>{item.body}</span>
              </div>
            </div>
          ))}
        </div>
        <div className="tour-keys">
          {TOUR_KEYS.map((row) => (
            <div key={row.label}>
              {row.label}
              <span className="help-keys">
                {row.keys.map((key) => (
                  <kbd key={key} className="kbd">
                    {key}
                  </kbd>
                ))}
              </span>
            </div>
          ))}
        </div>
        <div className="tour-actions">
          <button className="link-button" onClick={onShowHelp}>
            Show all shortcuts
          </button>
          <button className="btn btn-primary" onClick={onClose}>
            Start using Continuum
          </button>
        </div>
      </div>
    </div>,
    document.body,
  );
}
