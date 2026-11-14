import { useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { Icon, type IconName } from "./Icon";
import { cx } from "../ui";

export interface PaletteAction {
  id: string;
  label: string;
  icon: IconName;
  keys?: string[];
  run: () => void;
}

export function CommandPalette({
  open,
  actions,
  onClose,
}: {
  open: boolean;
  actions: PaletteAction[];
  onClose: () => void;
}) {
  const [query, setQuery] = useState("");
  const [active, setActive] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);

  const filtered = useMemo(() => {
    const needle = query.trim().toLowerCase();
    if (!needle) return actions;
    return actions.filter((action) => action.label.toLowerCase().includes(needle));
  }, [actions, query]);

  useEffect(() => {
    if (open) {
      setQuery("");
      setActive(0);
    }
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const id = window.setTimeout(() => inputRef.current?.focus(), 0);
    return () => window.clearTimeout(id);
  }, [open]);

  useEffect(() => {
    setActive(0);
  }, [query]);

  if (!open || typeof document === "undefined") return null;

  const pick = (action: PaletteAction | undefined) => {
    if (!action) return;
    onClose();
    action.run();
  };

  const onKeyDown = (event: React.KeyboardEvent) => {
    if (event.key === "ArrowDown") {
      event.preventDefault();
      setActive((index) => Math.min(index + 1, filtered.length - 1));
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      setActive((index) => Math.max(index - 1, 0));
    } else if (event.key === "Enter") {
      event.preventDefault();
      pick(filtered[active]);
    } else if (event.key === "Escape") {
      event.preventDefault();
      onClose();
    }
  };

  return createPortal(
    <div className="palette-overlay" role="presentation" onMouseDown={(event) => event.target === event.currentTarget && onClose()}>
      <div className="palette" role="dialog" aria-modal="true" aria-label="Command palette">
        <div className="palette-input-row">
          <Icon name="command" />
          <input
            ref={inputRef}
            className="palette-input"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            onKeyDown={onKeyDown}
            placeholder="Type a command…"
            aria-label="Search commands"
          />
        </div>
        <div className="palette-list">
          {filtered.length === 0 ? <p className="palette-empty">No matching commands.</p> : null}
          {filtered.map((action, index) => (
            <button
              key={action.id}
              type="button"
              className={cx("palette-item", index === active && "active")}
              onMouseEnter={() => setActive(index)}
              onClick={() => pick(action)}
            >
              <Icon name={action.icon} />
              {action.label}
              {action.keys ? (
                <span className="help-keys">
                  {action.keys.map((key) => (
                    <kbd key={key} className="kbd">
                      {key}
                    </kbd>
                  ))}
                </span>
              ) : null}
            </button>
          ))}
        </div>
        <div className="palette-foot">
          <span><b>↑↓</b> navigate</span>
          <span><b>Enter</b> run</span>
          <span><b>Esc</b> close</span>
        </div>
      </div>
    </div>,
    document.body,
  );
}
