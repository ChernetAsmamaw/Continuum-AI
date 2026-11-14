import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { Icon } from "../components/Icon";
import "../landing.css";

/* ── Hero: a discharge summary becomes tracked follow-ups ──────────────── */

type Status = "hidden" | "pending" | "reported" | "verified" | "overdue";

const SUMMARY: { text: string; item?: number }[] = [
  { text: "Michael Chen, 64, discharged after a heart-failure admission. " },
  { text: "See his family physician within 7 days.", item: 0 },
  { text: " " },
  { text: "Repeat bloodwork (creatinine, electrolytes) in 1 week.", item: 1 },
  { text: " " },
  { text: "Home-care nurse to assess daily weights.", item: 2 },
  { text: " " },
  { text: "Cardiology follow-up in 3 weeks.", item: 3 },
  { text: " Continue furosemide 40 mg daily." },
];

const ITEMS = [
  { title: "Family physician visit", due: "due Nov 11" },
  { title: "Bloodwork", due: "due Nov 18" },
  { title: "Home-care assessment", due: "due Nov 12" },
  { title: "Cardiology follow-up", due: "Dec 2" },
];

const STATUS_LABEL: Record<Status, string> = {
  hidden: "",
  pending: "Open",
  reported: "Patient says done",
  verified: "Verified by lab result",
  overdue: "Overdue",
};

type Frame = { states: Status[]; lit: number | null; note: string };

const FRAMES: Frame[] = [
  { states: ["hidden", "hidden", "hidden", "hidden"], lit: null, note: "The hospital sends Michael's discharge." },
  { states: ["pending", "hidden", "hidden", "hidden"], lit: 0, note: "Continuum reads the plan and finds each follow-up." },
  { states: ["pending", "pending", "hidden", "hidden"], lit: 1, note: "Continuum reads the plan and finds each follow-up." },
  { states: ["pending", "pending", "pending", "hidden"], lit: 2, note: "Continuum reads the plan and finds each follow-up." },
  { states: ["pending", "pending", "pending", "pending"], lit: 3, note: "Four follow-ups, each with an owner and a deadline." },
  { states: ["pending", "reported", "pending", "pending"], lit: null, note: "Day 3: on a check-in call Michael says his bloodwork is done. Noted, not closed." },
  { states: ["pending", "reported", "overdue", "pending"], lit: null, note: "Day 13: nobody has come for home care. It goes to the top of the queue." },
  { states: ["pending", "verified", "overdue", "pending"], lit: null, note: "The lab result arrives from the EHR. Now the bloodwork is verified." },
];

function useReducedMotion() {
  const [reduced, setReduced] = useState(false);
  useEffect(() => {
    const m = window.matchMedia?.("(prefers-reduced-motion: reduce)");
    if (!m) return;
    setReduced(m.matches);
    const on = () => setReduced(m.matches);
    m.addEventListener?.("change", on);
    return () => m.removeEventListener?.("change", on);
  }, []);
  return reduced;
}

function HeroDemo() {
  const reduced = useReducedMotion();
  const [frame, setFrame] = useState(FRAMES.length - 1);
  const [playing, setPlaying] = useState(false);
  const timer = useRef<number | undefined>(undefined);

  const play = () => {
    window.clearTimeout(timer.current);
    setFrame(0);
    setPlaying(true);
  };

  useEffect(() => {
    if (!reduced) play();
    return () => window.clearTimeout(timer.current);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [reduced]);

  useEffect(() => {
    if (!playing) return;
    if (frame >= FRAMES.length - 1) {
      setPlaying(false);
      return;
    }
    timer.current = window.setTimeout(() => setFrame((f) => f + 1), frame < 4 ? 900 : 2200);
    return () => window.clearTimeout(timer.current);
  }, [frame, playing]);

  const f = FRAMES[frame];
  return (
    <div className="lp-demo" aria-label="Example: a discharge plan turned into follow-ups">
      <div className="lp-paper">
        <p className="lp-paper-head">Discharge summary</p>
        <p className="lp-paper-body">
          {SUMMARY.map((s, i) =>
            s.item === undefined ? (
              <span key={i}>{s.text}</span>
            ) : (
              <mark key={i} className={f.lit === s.item ? "is-lit" : f.states[s.item] !== "hidden" ? "is-found" : ""}>
                {s.text}
              </mark>
            ),
          )}
        </p>
      </div>
      <div className="lp-board">
        <ul className="lp-items">
          {ITEMS.map((item, i) => (
            <li key={item.title} className={`lp-item is-${f.states[i]}`} aria-hidden={f.states[i] === "hidden"}>
              <span className="lp-item-title">{item.title}</span>
              <span className="lp-item-due">{item.due}</span>
              <span className="lp-item-status">{STATUS_LABEL[f.states[i]]}</span>
            </li>
          ))}
        </ul>
        <div className="lp-demo-foot">
          <p aria-live="polite">{f.note}</p>
          <button type="button" className="lp-replay" onClick={play} disabled={playing}>
            {playing ? "Playing" : "Play again"}
          </button>
        </div>
      </div>
    </div>
  );
}

/* ── Information flow: step through one discharge ─────────────────────── */

type NodeId = "ehr" | "ingest" | "atlas" | "agent" | "auto" | "voice" | "patient" | "console";

const NODES: { id: NodeId; x: number; y: number; w: number; label: string; sub: string }[] = [
  { id: "ehr", x: 20, y: 150, w: 150, label: "Hospital EHR", sub: "HL7 + FHIR" },
  { id: "ingest", x: 230, y: 60, w: 160, label: "Ingestion", sub: "pull and build" },
  { id: "auto", x: 230, y: 240, w: 160, label: "Automations", sub: "every 5 minutes" },
  { id: "atlas", x: 450, y: 150, w: 170, label: "MongoDB Atlas", sub: "the shared record" },
  { id: "agent", x: 450, y: 10, w: 170, label: "AI agent", sub: "Gemini + LangGraph" },
  { id: "console", x: 690, y: 10, w: 170, label: "Care team console", sub: "updates live" },
  { id: "voice", x: 690, y: 150, w: 170, label: "Voice agent", sub: "ElevenLabs" },
  { id: "patient", x: 690, y: 290, w: 170, label: "Patient", sub: "phone, text, portal" },
];

const EDGES: [NodeId, NodeId][] = [
  ["ehr", "ingest"], ["ingest", "atlas"], ["atlas", "agent"], ["atlas", "auto"], ["auto", "ehr"],
  ["atlas", "console"], ["atlas", "voice"], ["voice", "patient"],
];

const STEPS: { title: string; body: string; nodes: NodeId[]; edges: number[] }[] = [
  {
    title: "The hospital discharges Michael",
    body: "Its record system sends a standard discharge message. Nobody has to type anything in.",
    nodes: ["ehr", "ingest"],
    edges: [0],
  },
  {
    title: "The record becomes follow-ups",
    body: "Continuum pulls orders, appointments, medications and the summary, then saves each follow-up with an owner and a deadline.",
    nodes: ["ingest", "atlas"],
    edges: [1],
  },
  {
    title: "The agent decides what's next",
    body: "It reads the patient's history and memory from Atlas, proposes the next step, and asks a person before anything sensitive.",
    nodes: ["atlas", "agent"],
    edges: [2],
  },
  {
    title: "Michael gets a call",
    body: "When a check-in is due, the voice agent calls him. Everything he says is saved as his own report, not as fact.",
    nodes: ["atlas", "voice", "patient"],
    edges: [6, 7],
  },
  {
    title: "The care team sees it instantly",
    body: "Every change in Atlas reaches the console the moment it happens, sorted by what needs a person first.",
    nodes: ["atlas", "console"],
    edges: [5],
  },
  {
    title: "Proof closes the loop",
    body: "Automations ask the EHR for lab results and visit notes. Only real evidence turns a follow-up into verified.",
    nodes: ["auto", "ehr", "atlas"],
    edges: [3, 4],
  },
];

function center(id: NodeId) {
  const n = NODES.find((x) => x.id === id)!;
  return { x: n.x + n.w / 2, y: n.y + 32 };
}

function FlowMap() {
  const [step, setStep] = useState(0);
  const s = STEPS[step];
  return (
    <div className="lp-flow">
      <ol className="lp-flow-steps">
        {STEPS.map((item, i) => (
          <li key={item.title}>
            <button type="button" className={i === step ? "is-on" : ""} aria-pressed={i === step} onClick={() => setStep(i)}>
              <span className="lp-flow-n">{i + 1}</span>
              {item.title}
            </button>
          </li>
        ))}
      </ol>
      <div className="lp-flow-stage">
        <div className="lp-map">
          <svg viewBox="0 0 880 360" role="img" aria-label={`Step ${step + 1}: ${s.title}`}>
            {EDGES.map(([a, b], i) => {
              const p = center(a);
              const q = center(b);
              return (
                <line key={i} x1={p.x} y1={p.y} x2={q.x} y2={q.y} className={s.edges.includes(i) ? "is-on" : ""} />
              );
            })}
            {NODES.map((n) => (
              <g key={n.id} className={s.nodes.includes(n.id) ? "is-on" : ""} transform={`translate(${n.x} ${n.y})`}>
                <rect width={n.w} height={64} rx={10} />
                <text x={n.w / 2} y={28} className="lp-map-label">
                  {n.label}
                </text>
                <text x={n.w / 2} y={47} className="lp-map-sub">
                  {n.sub}
                </text>
              </g>
            ))}
          </svg>
        </div>
        <div className="lp-flow-text">
          <h3>{s.title}</h3>
          <p>{s.body}</p>
          <div className="lp-flow-nav">
            <button type="button" onClick={() => setStep((n) => Math.max(0, n - 1))} disabled={step === 0}>
              Back
            </button>
            <button type="button" onClick={() => setStep((n) => Math.min(STEPS.length - 1, n + 1))} disabled={step === STEPS.length - 1}>
              Next step
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

/* ── Finishing the job: three small working answers ───────────────────── */

function SentVsDone() {
  const [stage, setStage] = useState(1);
  const labels = ["Intended", "Sent", "Confirmed"];
  return (
    <div className="lp-mini">
      <ol className="lp-track">
        {labels.map((l, i) => (
          <li key={l} className={i < stage ? "is-done" : i === stage ? "is-now" : ""}>
            {l}
          </li>
        ))}
      </ol>
      <p>
        {stage < 2
          ? "The call was placed. Continuum treats that as sent, not done, until the transcript comes back."
          : "The transcript arrived, so the call is confirmed. A call with no transcript gets flagged instead."}
      </p>
      <button type="button" className="lp-ghost" onClick={() => setStage((n) => (n < 2 ? 2 : 1))}>
        {stage < 2 ? "Transcript arrives" : "Reset"}
      </button>
    </div>
  );
}

function CrashResume() {
  const steps = ["Pull", "Identify", "Structured", "Narrative", "Verify"];
  const [phase, setPhase] = useState<"crashed" | "resumed">("crashed");
  return (
    <div className="lp-mini">
      <ol className="lp-track">
        {steps.map((l, i) => (
          <li
            key={l}
            className={i < 3 ? "is-done" : phase === "resumed" ? (i === 3 ? "is-now is-done" : "is-done") : i === 3 ? "is-stop" : ""}
          >
            {l}
          </li>
        ))}
      </ol>
      <p>
        {phase === "crashed"
          ? "The server stopped after step 3. Steps 1 to 3 are saved in Atlas."
          : "On restart Continuum found the job, skipped what was saved, and finished from step 4. Nothing was pulled twice."}
      </p>
      <button type="button" className="lp-ghost" onClick={() => setPhase((p) => (p === "crashed" ? "resumed" : "crashed"))}>
        {phase === "crashed" ? "Restart the server" : "Crash it again"}
      </button>
    </div>
  );
}

function CodeChange() {
  const [matched, setMatched] = useState(false);
  return (
    <div className="lp-mini">
      <div className="lp-codes">
        <div>
          <span>Known order</span>
          <b>Basic metabolic panel</b>
          <small>LOINC 51990-0</small>
        </div>
        <div className={matched ? "is-match" : ""}>
          <span>New order from the hospital</span>
          <b>Renal function + lytes</b>
          <small>local code RFL-22</small>
        </div>
      </div>
      <p>
        {matched
          ? "Vector Search matched it by meaning (score 0.90) and tracked it as the same bloodwork. A coordinator's correction is remembered next time."
          : "The hospital changed the code and the wording. A rule that only knows exact codes would miss this order."}
      </p>
      <button type="button" className="lp-ghost" onClick={() => setMatched((m) => !m)}>
        {matched ? "Reset" : "Match with Vector Search"}
      </button>
    </div>
  );
}

const ANSWERS = [
  { id: "sent", q: "Did it actually happen?", render: () => <SentVsDone /> },
  { id: "crash", q: "What if the server crashes?", render: () => <CrashResume /> },
  { id: "codes", q: "What if the hospital changes its codes?", render: () => <CodeChange /> },
];

function Answers() {
  const [tab, setTab] = useState(0);
  return (
    <div className="lp-answers">
      <div className="lp-tabs" role="tablist" aria-label="How Continuum finishes the job">
        {ANSWERS.map((a, i) => (
          <button
            key={a.id}
            id={`lp-tab-${a.id}`}
            role="tab"
            aria-selected={tab === i}
            aria-controls={`lp-panel-${a.id}`}
            className={tab === i ? "is-on" : ""}
            onClick={() => setTab(i)}
          >
            {a.q}
          </button>
        ))}
      </div>
      <div className="lp-panel" role="tabpanel" id={`lp-panel-${ANSWERS[tab].id}`} aria-labelledby={`lp-tab-${ANSWERS[tab].id}`}>
        {ANSWERS[tab].render()}
      </div>
    </div>
  );
}

/* ── The problem, in numbers ─────────────────────────────────────────── */

const STATS: { n: string; what: string; src: string; href: string }[] = [
  {
    n: "1 in 5",
    what: "Medicare patients return to hospital within 30 days of discharge.",
    src: "Jencks et al., NEJM 2009",
    href: "https://pubmed.ncbi.nlm.nih.gov/19339721/",
  },
  {
    n: "Half",
    what: "of those readmitted after a medical stay never saw a doctor in between.",
    src: "Jencks et al., NEJM 2009",
    href: "https://pubmed.ncbi.nlm.nih.gov/19339721/",
  },
  {
    n: "41%",
    what: "of patients leave the hospital with test results still pending.",
    src: "Roy et al., Annals of Internal Medicine 2005",
    href: "https://pubmed.ncbi.nlm.nih.gov/16027454/",
  },
];

/* ── How it meets the brief ────────────────────────────────────────────── */

const BRIEF: { ask: string; built: string }[] = [
  {
    ask: "Complete a long, multi-step task across several systems",
    built:
      "A 30-day transition of care: the hospital's EHR (HL7 and FHIR), phone calls through ElevenLabs, texts and a patient portal, and the care team's console.",
  },
  {
    ask: "Use MongoDB Atlas to persist verified task state",
    built:
      "Every follow-up, its evidence, every action and its outcome, the audit trail and the agent's checkpoints live in Atlas, with schema validation and change streams.",
  },
  {
    ask: "Use Vector Search to retrieve relevant past experience",
    built:
      "Patient memory is searched by meaning, per patient, and new hospital orders are matched to ones Continuum has seen before.",
  },
  {
    ask: "Survive crashes and system changes, and know what actually happened",
    built: "The three answers below. Each one is working code you can try.",
  },
];

const STACK: { name: string; role: string }[] = [
  { name: "MongoDB Atlas", role: "The shared record: follow-ups, evidence, every action, the audit trail, and the agent's checkpoints." },
  { name: "Atlas Vector Search", role: "Patient memory and order matching by meaning, kept separate per patient." },
  { name: "Change streams", role: "Push every change to the care team console the moment it is written." },
  { name: "LangGraph + Gemini", role: "The agent that reads documents and reports and proposes the next step." },
  { name: "ElevenLabs", role: "The voice agent that calls patients and records what they say through tools." },
  { name: "HL7 and FHIR", role: "How discharges and lab results arrive from the hospital's record system." },
  { name: "LangSmith", role: "A trace of every agent run, so a reviewer can see what it read and why." },
];

export function Landing() {
  return (
    <div className="lp">
      <header className="lp-nav">
        <Link to="/" className="lp-brand" aria-label="Continuum home">
          <span className="lp-mark">
            <Icon name="loop" />
          </span>
          Continuum
        </Link>
        <nav className="lp-links" aria-label="Page sections">
          <a href="#problem">The problem</a>
          <a href="#solution">What it does</a>
          <a href="#flow">How it works</a>
          <a href="#brief">How it's built</a>
        </nav>
        <Link to="/demo" className="lp-btn lp-btn-small">
          Open the demo
        </Link>
      </header>

      <section className="lp-hero lp-hero-story">
        <div className="lp-hero-copy">
          <h1>Leaving the hospital is when care falls through the cracks.</h1>
          <p className="lp-lede">
            Every discharge comes with a plan: see your doctor in a week, repeat the bloodwork, a nurse will visit. Nobody owns
            making sure it happens. Continuum is an AI agent that does, for all 30 days.
          </p>
          <div className="lp-cta">
            <Link to="/demo" className="lp-btn">
              Open the live demo
            </Link>
            <span className="lp-cta-note">One click, no sign-up. Synthetic patients only.</span>
          </div>
        </div>
      </section>

      <main>
        <section className="lp-section" id="problem">
          <h2>The plan is written. Then it stalls.</h2>
          <ul className="lp-stats">
            {STATS.map((s) => (
              <li key={s.n}>
                <b>{s.n}</b>
                <p>{s.what}</p>
                <a href={s.href} target="_blank" rel="noreferrer">
                  {s.src}
                </a>
              </li>
            ))}
          </ul>
          <p className="lp-section-lede">
            It isn't that nobody cares. The work is spread across the hospital, clinics, labs, pharmacies and phone calls, and
            every one of them assumes someone else is following up. Results come back to nobody. Appointments never get booked.
            The patient says "I'm fine" and nothing checks.
          </p>
        </section>

        <section className="lp-section" id="solution">
          <h2>Continuum finishes the plan</h2>
          <p className="lp-section-lede">
            It reads the discharge, turns it into follow-ups with owners and deadlines, calls the patient, chases what's late,
            and closes each item only when there's proof. The care team only sees what needs a person. Watch one patient:
          </p>
          <HeroDemo />
        </section>

        <section className="lp-section" id="flow">
          <h2>How it works</h2>
          <p className="lp-section-lede">Step through one discharge, from the hospital's record to a verified follow-up.</p>
          <FlowMap />
        </section>

        <section className="lp-section" id="brief">
          <h2>Built for "Finish the Job"</h2>
          <p className="lp-section-lede">
            Continuum answers Statement Three of the MongoDB Agents in Context hackathon. Here is each requirement and what we
            built for it.
          </p>
          <dl className="lp-brief">
            {BRIEF.map((b) => (
              <div key={b.ask}>
                <dt>{b.ask}</dt>
                <dd>{b.built}</dd>
              </div>
            ))}
          </dl>
          <Answers />
        </section>

        <section className="lp-section" id="built">
          <h2>Built with</h2>
          <dl className="lp-stack">
            {STACK.map((s) => (
              <div key={s.name}>
                <dt>{s.name}</dt>
                <dd>{s.role}</dd>
              </div>
            ))}
          </dl>
        </section>

        <section className="lp-final">
          <h2>See it working</h2>
          <p>The console opens with a full synthetic caseload: overdue follow-ups, a live call room and an assistant you can ask.</p>
          <Link to="/demo" className="lp-btn">
            Open the live demo
          </Link>
        </section>
      </main>

      <footer className="lp-footer">
        <span>Continuum coordinates care after discharge. It does not diagnose, prescribe or change treatment.</span>
        <span>Built for MongoDB Agents in Context, Toronto 2026</span>
      </footer>
    </div>
  );
}
