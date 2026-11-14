import { useEffect } from "react";
import { createPortal } from "react-dom";
import { Icon, type IconName } from "./Icon";

const TOUR_ITEMS: { icon: IconName; title: string; body: string }[] = [
  { icon: "check", title: "Needs you", body: "One list of what needs a person, overdue first. Open an item, read why, take the main action." },
  { icon: "users", title: "Patients", body: "Each patient's follow-ups, evidence and timeline in one place." },
  { icon: "overview", title: "Add a discharge", body: "Import from the hospital record or enter one by hand." },
  { icon: "document", title: "Activity", body: "Calls, imports, past decisions and the audit log. A place to check, not to work." },
];

const TOUR_KEYS: { label: string; keys: string[] }[] = [
  { label: "Needs you", keys: ["g", "d"] },
  { label: "Patients", keys: ["g", "p"] },
  { label: "Next / previous item", keys: ["j", "k"] },
  { label: "Main action", keys: ["↵"] },
  { label: "Search", keys: ["/"] },
  { label: "Ask Continuum", keys: ["a"] },
  { label: "Audit log", keys: ["g", "l"] },
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
        <h2>Everything that needs you, in one list.</h2>
        <p>
          Every follow-up stays open until evidence closes it. Here is where things live:
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
