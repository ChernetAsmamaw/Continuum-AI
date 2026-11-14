import { useState } from "react";
import { api } from "../api";
import { useApp } from "../state";
import { cx, Spinner } from "../ui";
import { Icon, type IconName } from "./Icon";

function channelIcon(channel: string): IconName {
  const value = channel.toLowerCase();
  if (value.includes("elevenlabs") || value.includes("call") || value.includes("voice")) return "phone";
  if (value.includes("document")) return "document";
  if (value.includes("chat") || value.includes("message")) return "chat";
  return "spark";
}

export function DemoRunner({
  onDone,
  showReset = false,
  compact = false,
}: {
  onDone?: () => void;
  showReset?: boolean;
  compact?: boolean;
}) {
  const { config, refresh, notify } = useApp();
  const [running, setRunning] = useState<number | null>(null);
  const [resetting, setResetting] = useState(false);
  const [narration, setNarration] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const scenario = config?.scenario ?? [];

  const run = async (day: number) => {
    setRunning(day);
    setError(null);
    setNarration(null);
    try {
      const result = await api.demoDay(day);
      if (result.ok === false) {
        setError((result as unknown as { detail?: string }).detail || "The demo day could not run.");
      } else {
        setNarration(result.narration || result.reply || "Scenario day completed.");
        notify(`Day ${day}: ${result.narration || "scenario updated"}`);
        onDone?.();
        await refresh();
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not run the demo day.");
    } finally {
      setRunning(null);
    }
  };

  const [sweeping, setSweeping] = useState(false);
  const [daysAhead, setDaysAhead] = useState(14);

  const LABELS: Record<string, string> = {
    overdue_checkin: "overdue item(s) chased with a check-in",
    overdue_escalated: "owner outreach task(s) queued",
    stale_report: "verification request(s) for stale patient reports",
    checkin: "due check-in(s) dispatched",
    checkin_needs_review: "check-in(s) with an unclear time flagged",
  };

  const sweep = async () => {
    setSweeping(true);
    setError(null);
    setNarration(null);
    try {
      const result = await api.sweep(daysAhead);
      const parts = Object.entries(result.counts).map(([k, n]) => `${n} ${LABELS[k] ?? k}`);
      const text = parts.length
        ? `As of ${result.today}: ${parts.join("; ")}.${result.telephony ? "" : " Voice calling is not configured, so due calls became coordinator tasks."}`
        : `As of ${result.today}: nothing needed follow-through.`;
      setNarration(text);
      notify("Follow-through sweep complete.");
      onDone?.();
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not run the sweep.");
    } finally {
      setSweeping(false);
    }
  };

  const [sendingLab, setSendingLab] = useState(false);
  const sendLab = async () => {
    setSendingLab(true);
    setError(null);
    setNarration(null);
    try {
      const result = await api.fhir({
        resourceType: "DiagnosticReport",
        id: `demo-lab-${Date.now()}`,
        status: "final",
        category: [{ coding: [{ display: "Laboratory" }] }],
        code: { text: "Creatinine and electrolytes panel" },
        subject: { reference: "Patient/pat_michael" },
        effectiveDateTime: config?.demo_today ?? new Date().toISOString().slice(0, 10),
      });
      const first = result.results[0];
      setNarration(
        first
          ? `Lab system sent a final result for Michael (FHIR DiagnosticReport). ${first.detail}`
          : "Lab record received.",
      );
      notify("Clinic record received.");
      onDone?.();
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not send the lab record.");
    } finally {
      setSendingLab(false);
    }
  };

  const reset = async () => {
    if (!window.confirm("Reset every synthetic patient and replay from a clean seed?")) return;
    setResetting(true);
    setError(null);
    try {
      await api.seed(true);
      setNarration("Synthetic dataset reset. Michael is seeded at the Day-7 baseline.");
      notify("Demo data reset.");
      onDone?.();
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not reset the demo.");
    } finally {
      setResetting(false);
    }
  };

  return (
    <div className={cx("demo-runner", compact && "demo-runner-compact")}>
      <div className="demo-head">
        <div>
          <h3>30-day scenario</h3>
          <p className="muted">Run the timeline in order, or jump to any day. All data is synthetic.</p>
        </div>
        {showReset ? (
          <button className="btn btn-small btn-ghost" onClick={reset} disabled={resetting || running !== null}>
            {resetting ? "Resetting…" : "Reset demo"}
          </button>
        ) : null}
      </div>

      <div className="demo-steps">
        {scenario.map((item) => (
          <div key={item.day} className={cx("demo-step", running === item.day && "demo-step-running")} title={item.channel}>
            <div className="demo-step-head">
              <span className="day-chip">Day {item.day}</span>
              <span className="demo-channel" title={item.channel} aria-hidden="true">
                <Icon name={channelIcon(item.channel)} />
              </span>
            </div>
            <h4>{item.title}</h4>
            {!compact ? <p className="demo-detail">{item.detail}</p> : null}
            <small className="demo-capability">{item.capability}</small>
            <button
              className={cx("btn btn-small", item.day === 0 ? "btn-primary" : "btn-outline")}
              onClick={() => run(item.day)}
              disabled={running !== null || resetting || !config?.demo_mode}
            >
              {running === item.day ? <Spinner label="Running" /> : `Run day ${item.day}`}
            </button>
          </div>
        ))}
      </div>

      <div className="demo-sweep">
        <div>
          <strong>Follow-through sweep</strong>
          <p className="muted">
            Runs automatically every few minutes. Fast-forward to see what Continuum does on its own: place due
            check-ins, chase overdue follow-ups and request proof for stale patient reports.
          </p>
        </div>
        <label className="demo-sweep-controls">
          <span>Fast-forward</span>
          <select value={daysAhead} onChange={(e) => setDaysAhead(Number(e.target.value))} disabled={sweeping}>
            {[0, 3, 7, 14, 30, 45].map((d) => (
              <option key={d} value={d}>
                {d === 0 ? "Today" : `+${d} days`}
              </option>
            ))}
          </select>
          <button
            className="btn btn-small btn-outline"
            onClick={sweep}
            disabled={sweeping || running !== null || resetting || !config?.demo_mode}
          >
            {sweeping ? <Spinner label="Sweeping" /> : "Run sweep"}
          </button>
        </label>
      </div>

      <div className="demo-sweep">
        <div>
          <strong>Clinic record arrives</strong>
          <p className="muted">
            Simulate the lab system posting Michael&apos;s bloodwork result (FHIR). Continuum matches it to the open
            follow-up and queues a one-click, proof-backed closure.
          </p>
        </div>
        <button
          className="btn btn-small btn-outline"
          onClick={sendLab}
          disabled={sendingLab || sweeping || running !== null || resetting}
        >
          {sendingLab ? <Spinner label="Sending" /> : "Send lab result"}
        </button>
      </div>

      {narration ? (
        <div className="demo-narration">
          <strong>Agent narration</strong>
          <p>{narration}</p>
        </div>
      ) : null}
      {error ? <p className="form-error">{error}</p> : null}
      {config && !config.demo_mode ? (
        <p className="form-hint form-hint-warn">Demo endpoints are disabled (DEMO_MODE=false).</p>
      ) : null}
    </div>
  );
}
