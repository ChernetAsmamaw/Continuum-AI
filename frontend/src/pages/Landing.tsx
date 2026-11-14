import { Link } from "react-router-dom";
import { Icon } from "../components/Icon";
import consoleHero from "../assets/console-hero.png";
import "../landing.css";

const PRINCIPLES = [
  {
    title: "Plans become owned work",
    body: "Continuum pulls the follow-ups out of a discharge plan and gives each one an owner, a deadline and the proof it needs.",
  },
  {
    title: "One list of what needs you",
    body: "Overdue follow-ups, things to verify and missed calls land in one queue, most overdue first. Open one, read why, act.",
  },
  {
    title: "People make the sensitive calls",
    body: "Closing a follow-up or escalating to a clinician stops for a person, with the evidence shown beside the decision.",
  },
];

const STEPS = [
  { day: "Day 0", title: "Plan received", body: "Four follow-ups are found in Michael's discharge summary." },
  { day: "Day 3", title: "Patient checks in", body: "On a voice call he says he saw his doctor. It is saved as a report, not as proof." },
  { day: "Day 7", title: "Evidence arrives", body: "The clinic confirms the appointment, and the coordinator closes that follow-up." },
  { day: "Day 10", title: "Conflict caught", body: "Two different cardiology dates are kept side by side for a person to settle." },
];

const SAFETY = [
  { title: "Evidence before closure", body: "A patient's word keeps a follow-up open until a clinic record or a coordinator confirms it." },
  { title: "History stays intact", body: "Conflicting and replaced facts stay visible in an append-only audit log." },
  { title: "Clinical boundaries", body: "Continuum coordinates care. It does not diagnose, prescribe or change treatment." },
];

const STACK = ["MongoDB Atlas", "LangGraph", "ElevenLabs", "FastAPI", "React"];

export function Landing() {
  return (
    <div className="lp">
      <div className="lp-dark lp-top">
        <header className="lp-nav">
          <Link to="/" className="lp-brand" aria-label="Continuum home">
            <span className="lp-mark">
              <Icon name="loop" />
            </span>
            <span>
              Continuum
              <small>Care coordination</small>
            </span>
          </Link>
          <nav className="lp-links" aria-label="Page sections">
            <a href="#product">Product</a>
            <a href="#how">How it works</a>
            <a href="#safety">Safety</a>
          </nav>
          <div className="lp-nav-actions">
            <Link to="/login" className="lp-signin">
              Sign in
            </Link>
            <Link to="/demo" className="lp-btn lp-btn-mint lp-btn-small">
              Open the demo
            </Link>
          </div>
        </header>

        <section className="lp-hero">
          <p className="lp-kicker">Follow-up after hospital discharge</p>
          <h1>
            Care doesn't stop at discharge. <em>Neither should follow-up.</em>
          </h1>
          <p className="lp-lede">
            Continuum turns a discharge plan into tracked follow-ups, each with an owner, a deadline and proof. Care teams see
            what happened, what is still open and what needs them next.
          </p>
          <div className="lp-actions">
            <Link to="/demo" className="lp-btn lp-btn-mint">
              Open the live demo <Icon name="arrow-right" />
            </Link>
            <a href="#how" className="lp-btn lp-btn-outline">
              See how it works
            </a>
          </div>
          <p className="lp-note">
            <Icon name="shield" /> Synthetic patient data only. A person reviews every sensitive action.
          </p>
        </section>

        <figure className="lp-shot" id="product">
          <div className="lp-shot-bar" aria-hidden="true">
            <i />
            <i />
            <i />
            <span>Continuum · Needs you</span>
          </div>
          <div className="lp-shot-view">
            <img
              src={consoleHero}
              width={1920}
              height={1140}
              alt="The Needs you queue: overdue follow-ups on the left, one item open on the right with its main action"
            />
          </div>
        </figure>
      </div>

      <main>
        <section className="lp-section">
          <p className="lp-kicker lp-kicker-light">The unclosed loop</p>
          <h2>The riskiest follow-up is the one everyone assumes someone else owns.</h2>
          <p className="lp-section-lede">
            Discharge plans cross hospitals, clinics, labs, pharmacies, caregivers and phone calls. Continuum gives the care team
            one reliable view of every commitment, alongside the systems they already use.
          </p>
          <div className="lp-cards">
            {PRINCIPLES.map((item, index) => (
              <article key={item.title}>
                <span>{String(index + 1).padStart(2, "0")}</span>
                <h3>{item.title}</h3>
                <p>{item.body}</p>
              </article>
            ))}
          </div>
        </section>

        <section className="lp-band" id="how">
          <div className="lp-section">
            <p className="lp-kicker lp-kicker-light">One patient, one continuous record</p>
            <h2>Follow the work, not just the conversation.</h2>
            <p className="lp-section-lede">
              Every document, call, status change and decision moves the same care journey forward. Here are ten days of one
              synthetic patient.
            </p>
            <ol className="lp-steps">
              {STEPS.map((step) => (
                <li key={step.day}>
                  <span>{step.day}</span>
                  <h3>{step.title}</h3>
                  <p>{step.body}</p>
                </li>
              ))}
            </ol>
          </div>
        </section>

        <section className="lp-dark" id="safety">
          <div className="lp-section lp-split">
            <div>
              <p className="lp-kicker">Designed for accountable use</p>
              <h2>Automation where it helps. Human judgment where it matters.</h2>
            </div>
            <ul className="lp-safety">
              {SAFETY.map((item) => (
                <li key={item.title}>
                  <Icon name="check" />
                  <div>
                    <h3>{item.title}</h3>
                    <p>{item.body}</p>
                  </div>
                </li>
              ))}
            </ul>
          </div>
        </section>

        <section className="lp-stack" aria-label="Built with">
          <p>Built with</p>
          <ul>
            {STACK.map((name) => (
              <li key={name}>{name}</li>
            ))}
          </ul>
        </section>

        <section className="lp-dark lp-final">
          <span className="lp-mark">
            <Icon name="loop" />
          </span>
          <h2>Close the loop on post-discharge care.</h2>
          <p>Try the working coordinator console with a complete synthetic patient journey.</p>
          <Link to="/demo" className="lp-btn lp-btn-mint">
            Open the demo <Icon name="arrow-right" />
          </Link>
        </section>
      </main>

      <footer className="lp-footer">
        <span>Continuum</span>
        <span>Built for the MongoDB Agents in Context Hackathon</span>
        <span>Synthetic demo · 2026</span>
      </footer>
    </div>
  );
}
