import { useEffect } from "react";
import { createPortal } from "react-dom";

const GLOBAL: { label: string; keys: string[] }[] = [
  { label: "Go to Needs you", keys: ["g", "d"] },
  { label: "Go to Patients", keys: ["g", "p"] },
  { label: "Go to Decisions", keys: ["g", "a"] },
  { label: "Go to Audit log", keys: ["g", "l"] },
  { label: "Focus search", keys: ["/"] },
  { label: "Ask Continuum", keys: ["a"] },
  { label: "Command palette", keys: ["k"] },
  { label: "Keyboard shortcuts", keys: ["?"] },
];

const QUEUE: { label: string; keys: string[] }[] = [
  { label: "Next item", keys: ["j"] },
  { label: "Previous item", keys: ["k"] },
  { label: "Run the main action", keys: ["enter"] },
  { label: "Open the patient", keys: ["p"] },
];

const TASK: { label: string; keys: string[] }[] = [
  { label: "Obligation: add evidence (hover or focus a card)", keys: ["e"] },
  { label: "Obligation: log update", keys: ["u"] },
  { label: "Obligation: confirm appointment date", keys: ["c"] },
  { label: "Approval: approve & apply", keys: ["y"] },
  { label: "Approval: reject", keys: ["x"] },
  { label: "Close dialogs, palette and tour", keys: ["esc"] },
];

function Rows({ rows }: { rows: { label: string; keys: string[] }[] }) {
  return (
    <>
      {rows.map((row) => (
        <div className="help-row" key={row.label}>
          <span>{row.label}</span>
          <span className="help-keys">
            {row.keys.map((key) => (
              <kbd key={key} className="kbd">
                {key}
              </kbd>
            ))}
          </span>
        </div>
      ))}
    </>
  );
}

export function HotkeyHelp({ open, onClose }: { open: boolean; onClose: () => void }) {
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
    <div className="help-overlay" role="presentation" onMouseDown={(event) => event.target === event.currentTarget && onClose()}>
      <div className="help-card" role="dialog" aria-modal="true" aria-label="Keyboard shortcuts">
        <div className="help-head">
          <h2>Keyboard shortcuts</h2>
          <button className="icon-button" onClick={onClose} aria-label="Close">
            ×
          </button>
        </div>
        <div className="help-section">
          <h3>Navigation</h3>
          <Rows rows={GLOBAL} />
        </div>
        <div className="help-section">
          <h3>Needs you</h3>
          <Rows rows={QUEUE} />
        </div>
        <div className="help-section">
          <h3>On the cards</h3>
          <Rows rows={TASK} />
        </div>
        <p className="help-foot">
          Task keys work while a card is hovered or focused. Hotkeys pause while you are typing in a field.
        </p>
      </div>
    </div>,
    document.body,
  );
}
