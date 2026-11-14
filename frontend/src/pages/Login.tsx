import { useEffect, useState, type FormEvent } from "react";
import { Link, Navigate, useLocation, useNavigate } from "react-router-dom";
import { useAuth } from "../auth";
import { api } from "../api";
import { Icon } from "../components/Icon";

const BULLETS = [
  { icon: "check" as const, label: "Track every follow-up from plan to proof" },
  { icon: "shield" as const, label: "Pause sensitive changes for human review" },
  { icon: "list" as const, label: "Keep a complete, append-only audit history" },
];

export function Login() {
  const { user, login, loginDemo } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const from = (location.state as { from?: string } | null)?.from || "/console";

  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState<"form" | "demo-coordinator" | "demo-clinician" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [demoCreds, setDemoCreds] = useState<{
    coordinator: { email: string; password: string; name: string; role: string };
    clinician: { email: string; password: string; name: string; role: string };
  } | null>(null);

  useEffect(() => {
    let cancelled = false;
    api.auth
      .demo()
      .then((creds) => !cancelled && setDemoCreds(creds))
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, []);

  if (user) return <Navigate to={from} replace />;

  const go = () => navigate(from, { replace: true });

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (busy) return;
    setBusy("form");
    setError(null);
    try {
      await login(email.trim(), password);
      go();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Sign-in failed. Try again.");
    } finally {
      setBusy(null);
    }
  };

  const runDemo = async (role: "coordinator" | "clinician") => {
    if (busy) return;
    setBusy(role === "clinician" ? "demo-clinician" : "demo-coordinator");
    setError(null);
    try {
      await loginDemo(role);
      go();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Demo sign-in failed. Try again.");
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className="auth-screen">
      <div className="auth-brand">
        <Link to="/" className="auth-brand-mark" aria-label="Continuum home">
          <span className="brandmark"><Icon name="loop" /></span>
          <span className="brand-word">
            Continuum
            <small>Care coordination</small>
          </span>
        </Link>
        <div className="auth-brand-body">
          <p className="auth-eyebrow">Coordinator workspace</p>
          <h1>Every care commitment,<br /><em>accounted for.</em></h1>
          <p>
            A shared operational view of post-discharge obligations, evidence, patient updates, and approvals.
          </p>
          <ul className="auth-bullets">
            {BULLETS.map((item) => (
              <li key={item.label}>
                <Icon name={item.icon} />
                {item.label}
              </li>
            ))}
          </ul>
        </div>
        <div className="auth-proof">
          <span className="auth-proof-avatar">MC</span>
          <div><b>Michael Chen · Day 10</b><small>4 obligations · 1 conflict · 2 awaiting evidence</small></div>
          <span className="auth-proof-state">Needs review</span>
        </div>
        <p className="auth-brand-foot"><Icon name="shield" /> Synthetic demo · No real patient data is used.</p>
      </div>

      <div className="auth-form-panel">
        <div className="auth-card">
          <p className="auth-card-eyebrow">Secure access</p>
          <h2>Welcome back</h2>
          <p className="auth-sub">Sign in to continue to the coordinator workspace.</p>
          <form onSubmit={submit} noValidate>
            <label className="field">
              <span>Email</span>
              <input
                type="email"
                value={email}
                onChange={(event) => setEmail(event.target.value)}
                placeholder="you@clinic.org"
                autoComplete="username"
                autoFocus
                disabled={busy !== null}
              />
            </label>
            <label className="field">
              <span>Password</span>
              <input
                type="password"
                value={password}
                onChange={(event) => setPassword(event.target.value)}
                placeholder="••••••••"
                autoComplete="current-password"
                disabled={busy !== null}
              />
            </label>
            {error ? <p className="form-error">{error}</p> : null}
            <button className="btn btn-primary auth-submit" type="submit" disabled={busy !== null || !email || !password}>
              {busy === "form" ? "Signing in…" : "Sign in"}
              <Icon name="arrow-right" />
            </button>
          </form>

          <div className="auth-divider"><span>or</span></div>

          <div className="stack auth-demo-stack">
            <button className="btn auth-demo-btn" onClick={() => runDemo("coordinator")} disabled={busy !== null}>
              <Icon name="spark" />
              {busy === "demo-coordinator" ? "Opening workspace…" : "Explore as care coordinator"}
            </button>
            <button className="btn auth-demo-btn auth-demo-btn-secondary" onClick={() => runDemo("clinician")} disabled={busy !== null}>
              <Icon name="shield" />
              {busy === "demo-clinician" ? "Opening workspace…" : "Explore as clinician"}
            </button>
          </div>

          {demoCreds ? (
            <div className="stack">
              <p className="auth-hint">
                Coordinator — <code>{demoCreds.coordinator.email}</code> / <code>{demoCreds.coordinator.password}</code>
              </p>
              <p className="auth-hint">
                Clinician — <code>{demoCreds.clinician.email}</code> / <code>{demoCreds.clinician.password}</code>
              </p>
            </div>
          ) : null}

          <Link to="/" className="auth-back">
            <Icon name="arrow-right" className="auth-back-arrow" />
            Back to home
          </Link>
        </div>
      </div>
    </div>
  );
}
