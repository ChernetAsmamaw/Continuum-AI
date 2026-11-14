import { Link } from "react-router-dom";
import { DemoRunner } from "../components/DemoRunner";
import { useApp } from "../state";

/**
 * Demo: the scripted scenario and the simulation controls, nothing else.
 * Day-to-day work lives in Needs you; this page exists to show the product.
 */
export function Dashboard() {
  const { config, refresh } = useApp();

  return (
    <div className="dq demo">
      <header className="dq-head">
        <div>
          <h1>Demo</h1>
          <p>
            Replay one synthetic patient's first two weeks home, one moment at a time, and watch what Continuum does. Every patient and number here is
            made up.
          </p>
        </div>
        <Link className="btn btn-primary" to="/console">
          Go to Needs you
        </Link>
      </header>

      <div className="im-summary">
        <span className={config?.llm_enabled ? "flag tone-green" : "flag tone-neutral"}>
          {config?.llm_enabled ? `Language model: ${config.model}` : "No language model key: using rule-based extraction"}
        </span>
        <span className={config?.voice_enabled ? "flag tone-violet" : "flag tone-neutral"}>
          {config?.voice_enabled ? "Voice agent connected" : "Voice agent not configured"}
        </span>
      </div>

      <section className="demo-panel">
        <DemoRunner onDone={refresh} showReset />
      </section>
    </div>
  );
}
