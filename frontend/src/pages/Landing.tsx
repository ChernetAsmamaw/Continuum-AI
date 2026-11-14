import { Link } from "react-router-dom";
import { Icon } from "../components/Icon";
import consoleHero from "../assets/console-hero.png";

const PRINCIPLES = [
  { n: "01", title: "Turn plans into owned work", body: "Continuum extracts the follow-ups hidden in discharge plans and makes the owner, deadline, and evidence requirement explicit." },
  { n: "02", title: "Keep every loop visible", body: "Patient reports, clinic confirmations, calls, and changes stay in one longitudinal record—without silently overwriting history." },
  { n: "03", title: "Put people in control", body: "Sensitive changes stop for review. Coordinators see what changed, why it changed, and the evidence behind the recommendation." },
];
const STEPS = [
  { day: "Day 0", title: "Plan received", body: "Four follow-up obligations are identified from Michael’s discharge summary." },
  { day: "Day 3", title: "Patient checks in", body: "A voice update is recorded as patient-reported—not verified completion." },
  { day: "Day 7", title: "Evidence arrives", body: "The coordinator attaches clinic confirmation and advances the obligation." },
  { day: "Day 10", title: "Conflict caught", body: "Two appointment dates are preserved and routed for human review." },
];

export function Landing() {
  return <div className="landing landing-v2">
    <div className="announce-v2"><span>Built for the MongoDB Agents in Context Hackathon</span><Link to="/demo">Open the working demo <Icon name="arrow-right" /></Link></div>
    <header className="landing-nav landing-nav-v2"><div className="landing-nav-inner">
      <Link to="/" className="auth-brand-mark" aria-label="Continuum home"><span className="brandmark"><Icon name="loop" /></span><span className="brand-word">Continuum<small>Care coordination</small></span></Link>
      <nav className="landing-nav-links" aria-label="Landing navigation"><a href="#product">Product</a><a href="#workflow">How it works</a><a href="#safety">Safety</a></nav>
      <div className="landing-nav-actions"><Link to="/login" className="landing-signin">Sign in</Link><Link to="/demo" className="btn btn-nav-cta">Explore the demo</Link></div>
    </div></header>
    <main>
      <section className="yc-hero">
        <div className="yc-hero-copy"><p className="yc-kicker"><span /> Operational memory for transitional care</p><h1>Care doesn’t stop at discharge.<br /><em>Neither should follow-up.</em></h1><p>Continuum turns post-discharge plans into tracked, evidence-backed obligations—so care teams know what happened, what is still open, and what needs attention next.</p><div className="yc-actions"><Link to="/demo" className="yc-button yc-button-dark">Explore the live demo <Icon name="arrow-right" /></Link><a href="#product" className="yc-button yc-button-light">See how it works</a></div><p className="yc-note"><Icon name="shield" /> Synthetic patient data only · Human review for sensitive actions</p></div>
        <div className="yc-product-frame" id="product"><div className="yc-browser"><i /><i /><i /><span>Continuum coordinator workspace</span></div><img src={consoleHero} alt="Continuum coordinator dashboard showing open care obligations" /><div className="yc-float yc-float-left"><span className="yc-pulse" /><div><b>Conflict detected</b><small>Cardiology date needs review</small></div></div><div className="yc-float yc-float-right"><Icon name="check" /><div><b>Evidence attached</b><small>Clinic confirmation · 9:42 AM</small></div></div></div>
      </section>
      <section className="yc-trust"><p>One coordination layer across the tools care teams already use</p><div><span>MongoDB Atlas</span><span>LangGraph</span><span>ElevenLabs</span><span>FastAPI</span><span>React</span></div></section>
      <section className="yc-problem"><p className="yc-kicker"><span /> The unclosed loop</p><h2>The riskiest follow-up is the one everyone assumes someone else owns.</h2><p>Discharge plans cross hospitals, clinics, labs, pharmacies, caregivers, and phone calls. Continuum gives the care team one reliable view of every commitment—without replacing the systems they already use.</p></section>
      <section className="yc-principles">{PRINCIPLES.map(item => <article key={item.n}><span>{item.n}</span><h3>{item.title}</h3><p>{item.body}</p></article>)}</section>
      <section className="yc-workflow" id="workflow"><div className="yc-workflow-head"><div><p className="yc-kicker"><span /> One patient, one continuous record</p><h2>Follow the work, not just the conversation.</h2></div><p>Every document, call, status change, and approval advances the same care journey.</p></div><div className="yc-steps">{STEPS.map((step,i) => <article key={step.day}><span>{step.day}</span><i>{i+1}</i><h3>{step.title}</h3><p>{step.body}</p></article>)}</div></section>
      <section className="yc-safety" id="safety"><div><p className="yc-kicker"><span /> Designed for accountable use</p><h2>Automation where it helps.<br />Human judgment where it matters.</h2></div><div className="yc-safety-list"><p><Icon name="check" /><span><b>Evidence before closure</b>Patient reports remain unverified until supporting evidence arrives.</span></p><p><Icon name="check" /><span><b>History stays intact</b>Conflicts and superseded facts remain visible in an append-only audit trail.</span></p><p><Icon name="check" /><span><b>Clinical boundaries</b>Continuum coordinates care. It does not diagnose, prescribe, or alter treatment.</span></p></div></section>
      <section className="yc-final"><span className="brandmark"><Icon name="loop" /></span><h2>Close the loop on post-discharge care.</h2><p>Explore the working coordinator console with a complete synthetic patient journey.</p><Link to="/demo" className="yc-button yc-button-dark">Launch the demo <Icon name="arrow-right" /></Link></section>
    </main>
    <footer className="yc-footer"><span>Continuum</span><span>Care coordination that remembers what still needs to happen.</span><span>Synthetic demo · 2026</span></footer>
  </div>;
}
