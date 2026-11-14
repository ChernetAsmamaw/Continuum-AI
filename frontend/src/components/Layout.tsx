import { useEffect, useMemo, useRef, useState } from "react";
import { Link, NavLink, Outlet, useLocation, useNavigate } from "react-router-dom";
import { useAuth } from "../auth";
import { CallBar, useCall } from "../calls";
import { useApp } from "../state";
import { cx, initials } from "../ui";
import { CommandPalette, type PaletteAction } from "./CommandPalette";
import { AssistantPanel } from "../assistant/Assistant";
import { HotkeyHelp } from "./HotkeyHelp";
import { Icon as GlyphIcon } from "./Icon";
import { WelcomeTour } from "./WelcomeTour";

const TOUR_KEY = "continuum.tour.v1";

type IconName = "overview" | "work" | "patients" | "approvals" | "audit" | "search" | "menu" | "loop" | "shield" | "ledger" | "database";

function Icon({ name, className }: { name: IconName; className?: string }) {
  const common = {
    className,
    viewBox: "0 0 24 24",
    fill: "none",
    stroke: "currentColor",
    strokeWidth: 1.8,
    strokeLinecap: "round" as const,
    strokeLinejoin: "round" as const,
    "aria-hidden": true,
  };
  switch (name) {
    case "overview":
      return (
        <svg {...common}>
          <rect x="3" y="3" width="7.5" height="7.5" rx="2" />
          <rect x="13.5" y="3" width="7.5" height="7.5" rx="2" />
          <rect x="3" y="13.5" width="7.5" height="7.5" rx="2" />
          <rect x="13.5" y="13.5" width="7.5" height="7.5" rx="2" />
        </svg>
      );
    case "patients":
      return (
        <svg {...common}>
          <circle cx="9" cy="8" r="3.2" />
          <path d="M3.5 20c0-3.2 2.5-5.4 5.5-5.4s5.5 2.2 5.5 5.4" />
          <path d="M16 5.4a3.2 3.2 0 0 1 0 6.2M17.4 14.9c2 .5 3.3 2.2 3.3 4.4" />
        </svg>
      );
    case "work":
      return (
        <svg {...common}>
          <path d="M4.5 13.5 6.8 6h10.4l2.3 7.5v4.5h-15z" />
          <path d="M4.5 13.5h4.2l1.3 2h4l1.3-2h4.2" />
        </svg>
      );
    case "approvals":
      return (
        <svg {...common}>
          <circle cx="12" cy="12" r="8.5" />
          <path d="m8.4 12.2 2.4 2.4 4.8-5" />
        </svg>
      );
    case "audit":
      return (
        <svg {...common}>
          <path d="M4.5 5.5h15M4.5 12h15M4.5 18.5h9" />
          <circle cx="17.5" cy="18.5" r="2.2" />
        </svg>
      );
    case "search":
      return (
        <svg {...common}>
          <circle cx="10.8" cy="10.8" r="6.3" />
          <path d="m15.5 15.5 4 4" />
        </svg>
      );
    case "menu":
      return (
        <svg {...common}>
          <path d="M4 7h16M4 12h16M4 17h16" />
        </svg>
      );
    case "loop":
      return (
        <svg {...common} strokeWidth={2.2}>
          <path d="M20 11.5a8 8 0 1 1-2.3-5.1" />
          <path d="M20 4v5h-5" />
          <circle cx="12" cy="12" r="1.4" fill="currentColor" stroke="none" />
        </svg>
      );
    case "shield":
      return (
        <svg {...common}>
          <path d="M12 3.2 5 6v5.6c0 4.3 3 7.6 7 9.2 4-1.6 7-4.9 7-9.2V6l-7-2.8Z" />
          <path d="M12 8.5v4M12 15.6v.1" />
        </svg>
      );
    case "ledger":
      return (
        <svg {...common}>
          <circle cx="6" cy="6.5" r="1.6" />
          <circle cx="6" cy="12" r="1.6" />
          <circle cx="6" cy="17.5" r="1.6" />
          <path d="M10.5 6.5h9M10.5 12h9M10.5 17.5h5.5" />
        </svg>
      );
    case "database":
      return (
        <svg {...common}>
          <ellipse cx="12" cy="6" rx="7" ry="2.8" />
          <path d="M5 6v6c0 1.5 3.1 2.8 7 2.8s7-1.3 7-2.8V6M5 12v6c0 1.5 3.1 2.8 7 2.8s7-1.3 7-2.8v-6" />
        </svg>
      );
    default:
      return null;
  }
}

type NavItem = { to: string; label: string; icon: IconName; end?: boolean; badge?: "queue" | "approvals" | "review" | "attention" | "decisions" };

const NAV: NavItem[] = [
  { to: "/console", label: "Needs you", icon: "work", end: true, badge: "queue" },
  { to: "/patients", label: "Patients", icon: "patients" },
  { to: "/patients/new", label: "Add a discharge", icon: "overview", end: true },
  { to: "/work", label: "Assigned to me", icon: "approvals" },
];

const ACTIVITY: NavItem[] = [
  { to: "/actions", label: "Calls and tasks", icon: "ledger", badge: "attention" },
  { to: "/ingestion", label: "Imports", icon: "database" },
  { to: "/approvals", label: "Decisions", icon: "approvals", badge: "decisions" },
  { to: "/audit", label: "Audit log", icon: "audit" },
  { to: "/overview", label: "Demo", icon: "overview" },
];

function sectionLabel(pathname: string): string {
  if (pathname === "/console") return "Needs you";
  if (pathname === "/overview") return "Demo";
  if (pathname === "/patients/new") return "Add a discharge";
  if (pathname.startsWith("/patients")) return "Patients";
  if (pathname.startsWith("/work")) return "Assigned to me";
  if (pathname.startsWith("/approvals")) return "Decisions";
  if (pathname.startsWith("/actions")) return "Calls and tasks";
  if (pathname.startsWith("/ingestion")) return "Imports";
  if (pathname.startsWith("/audit")) return "Audit log";
  return "Workspace";
}

export function Layout() {
  const { config, dashboard, error, toast, loading, ops } = useApp();
  const { user, logout } = useAuth();
  const { live: onCall } = useCall();
  const [menuOpen, setMenuOpen] = useState(false);
  const [search, setSearch] = useState("");
  const [paletteOpen, setPaletteOpen] = useState(false);
  const [assistantOpen, setAssistantOpen] = useState(false);
  const [helpOpen, setHelpOpen] = useState(false);
  const [tourSeen, setTourSeen] = useState(() => typeof localStorage !== "undefined" && localStorage.getItem(TOUR_KEY) === "1");
  const searchRef = useRef<HTMLInputElement>(null);
  const location = useLocation();
  const navigate = useNavigate();

  const patientMatch = location.pathname.match(/^\/patients\/([^/]+)/);
  const agentHref = patientMatch && patientMatch[1] !== "" ? `/patients/${patientMatch[1]}/agent` : "/patients/pat_michael/agent";

  useEffect(() => {
    setMenuOpen(false);
    window.scrollTo(0, 0);
  }, [location.pathname]);

  const closeTour = () => {
    try {
      localStorage.setItem(TOUR_KEY, "1");
    } catch {
      /* private mode */
    }
    setTourSeen(true);
  };

  const showTour = Boolean(user) && !tourSeen;

  // Global hotkeys: g-sequences, /, a, k, ?, Esc. Task-level keys (e/u/c, y/x)
  // are handled by the cards themselves when hovered or focused.
  useEffect(() => {
    let gAt = 0;
    const isTyping = (target: EventTarget | null) => {
      const el = target as HTMLElement | null;
      if (!el) return false;
      return (
        el.tagName === "INPUT" ||
        el.tagName === "TEXTAREA" ||
        el.tagName === "SELECT" ||
        el.isContentEditable
      );
    };
    const overlayOpen = () =>
      document.querySelector(".modal-overlay, .palette-overlay, .help-overlay, .tour-overlay") !== null;

    const onKey = (event: KeyboardEvent) => {
      if (event.metaKey || event.ctrlKey || event.altKey) return;
      if (event.key === "Escape") {
        setPaletteOpen(false);
        setHelpOpen(false);
        return;
      }
      if (isTyping(event.target) || overlayOpen()) return;
      if (event.key === "?") {
        event.preventDefault();
        setHelpOpen(true);
        return;
      }
      const key = event.key.toLowerCase();
      if (key === "g") {
        gAt = Date.now();
        return;
      }
      // Keep the g-prefix window short (vim-style ~500ms) so it can't
      // swallow standalone hotkeys like "a" pressed right after a g-sequence.
      if (Date.now() - gAt < 500) {
        const target =
          key === "d" ? "/console"
          : key === "p" ? "/patients"
          : key === "a" ? "/approvals"
          : key === "l" ? "/audit"
          : null;
        if (target) {
          event.preventDefault();
          gAt = 0;
          navigate(target);
          return;
        }
      }
      if (event.key === "/") {
        event.preventDefault();
        searchRef.current?.focus();
        return;
      }
      if (key === "a") {
        event.preventDefault();
        setAssistantOpen(true);
        return;
      }
      if (key === "k") {
        event.preventDefault();
        setPaletteOpen(true);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [navigate, agentHref]);

  const signOut = () => {
    logout();
    navigate("/login");
  };

  const paletteActions = useMemo<PaletteAction[]>(
    () => [
      { id: "queue", label: "Go to Needs you", icon: "overview", keys: ["g", "d"], run: () => navigate("/console") },
      { id: "overview", label: "Go to Demo", icon: "overview", run: () => navigate("/overview") },
      { id: "patients", label: "Go to Patients", icon: "users", keys: ["g", "p"], run: () => navigate("/patients") },
      { id: "approvals", label: "Go to Decisions", icon: "check", keys: ["g", "a"], run: () => navigate("/approvals") },
      { id: "audit", label: "Go to Audit log", icon: "document", keys: ["g", "l"], run: () => navigate("/audit") },
      { id: "ask", label: "Ask Continuum", icon: "chat", keys: ["a"], run: () => setAssistantOpen(true) },
      { id: "search", label: "Focus search", icon: "search", keys: ["/"], run: () => window.setTimeout(() => searchRef.current?.focus(), 0) },
      { id: "tour", label: "Show welcome tour", icon: "sparkle", run: () => setTourSeen(false) },
      { id: "help", label: "Show keyboard shortcuts", icon: "command", keys: ["?"], run: () => setHelpOpen(true) },
      { id: "signout", label: "Sign out", icon: "logout", run: signOut },
    ],
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [navigate, agentHref],
  );

  const pending = dashboard?.stats.pending_approvals ?? 0;
  const pageLabel = sectionLabel(location.pathname);
  const queueCount = (dashboard?.stats.overdue ?? 0) + pending + (ops?.review ?? 0) + (ops?.attention ?? 0);

  const badgeFor = (badge: NavItem["badge"]) => {
    if (badge === "queue") return queueCount > 0 ? <b>{queueCount}</b> : null;
    if (badge === "review") return (ops?.review ?? 0) > 0 ? <b>{ops?.review}</b> : null;
    if (badge === "decisions") return pending + (ops?.review ?? 0) > 0 ? <b className="nav-amber">{pending + (ops?.review ?? 0)}</b> : null;
    if (badge === "attention") return (ops?.attention ?? 0) > 0 ? <b>{ops?.attention}</b> : null;
    return null;
  };

  const renderNav = (item: NavItem) => {
    // "Patients" stays lit on a patient's pages but not on "Add a discharge".
    const active =
      item.to === "/patients"
        ? location.pathname.startsWith("/patients") && location.pathname !== "/patients/new"
        : item.end
          ? location.pathname === item.to
          : location.pathname.startsWith(item.to);
    return (
      <NavLink key={item.to} to={item.to} end={item.end} className={cx("nav", active && "nav-active")} aria-current={active ? "page" : undefined}>
        <span className="nav-icon">
          <Icon name={item.icon} />
        </span>
        <span>{item.label}</span>
        {badgeFor(item.badge)}
      </NavLink>
    );
  };

  const submitSearch = (event: React.FormEvent) => {
    event.preventDefault();
    const needle = search.trim();
    navigate(needle ? `/patients?q=${encodeURIComponent(needle)}` : "/patients");
    setSearch("");
  };

  return (
    <div className="app-shell">
      <aside className={cx("sidebar", menuOpen && "sidebar-open")}>
        <Link to="/console" className="brand" aria-label="Continuum home">
          <span className="brandmark">
            <Icon name="loop" />
          </span>
          <span className="brand-word">
            Continuum
            <small>Care coordination</small>
          </span>
        </Link>

        <nav className="nav-list" aria-label="Main navigation">
          {NAV.map(renderNav)}
          <p className="sidebar-label nav-group-label">Activity</p>
          {ACTIVITY.map(renderNav)}
        </nav>

        <div className="sidebar-section">
          <div className="sidebar-section-head">
            <p className="sidebar-label">Caseload</p>
            <Link to="/patients" className="sidebar-link">
              All
            </Link>
          </div>
          {(dashboard?.patients ?? []).slice(0, 4).map((patient) => (
            <Link
              key={patient.id}
              to={`/patients/${patient.id}`}
              className={cx(
                "patient-link",
                location.pathname === `/patients/${patient.id}` && "patient-link-active",
              )}
            >
              <span className={cx("avatar avatar-sidebar", patient.risk === "High" && "avatar-high")}>
                {patient.initials || initials(patient.name)}
              </span>
              <span className="patient-link-name">
                {patient.name}
                <small>{patient.condition}</small>
              </span>
              {patient.overdue_count > 0 ? <em>{patient.overdue_count}</em> : null}
            </Link>
          ))}
        </div>

        <div className="sidebar-bottom">
          <div className="privacy">
            <span className="privacy-dot" />
            <div>
              <strong>Synthetic demo</strong>
              <small>No real patient data</small>
            </div>
          </div>
          <div className="user-card">
            <span className="avatar avatar-user">{initials(user?.name || "Jordan Davis")}</span>
            <span className="user-card-text">
              <strong>{user?.name || "Coordinator"}</strong>
              <small>
                {user?.role === "clinician"
                  ? "Clinician"
                  : user?.role === "coordinator"
                    ? "Care coordinator"
                    : user?.role || "Coordinator"}
              </small>
            </span>
            <button className="signout" onClick={signOut} title="Sign out" aria-label="Sign out">
              <GlyphIcon name="logout" />
            </button>
          </div>
        </div>
      </aside>

      <div className={cx("main", onCall && "main-on-call")}>
        <header className="topbar">
          <button
            className="menu-button"
            onClick={() => setMenuOpen((value) => !value)}
            aria-label="Toggle navigation"
            aria-expanded={menuOpen}
          >
            <Icon name="menu" />
          </button>

          <span className="topbar-context">{pageLabel}</span>

          <form className="search" onSubmit={submitSearch} role="search">
            <Icon name="search" className="search-icon" />
            <input
              ref={searchRef}
              value={search}
              onChange={(event) => setSearch(event.target.value)}
              placeholder="Search patients, conditions, MRN…"
              aria-label="Search patients"
            />
            <kbd>/</kbd>
          </form>

          <div className="top-actions">
            <span
              className={cx("sync", error && "sync-offline")}
              title={error || (loading ? "Connecting to Continuum" : "Continuum API connected")}
            >
              <i />
              {error ? "Offline" : loading ? "Connecting" : "Live"}
            </span>
            {config?.demo_mode ? <span className="demo-chip">Demo</span> : null}
            <button
              className="top-help"
              onClick={() => setHelpOpen(true)}
              title="Keyboard shortcuts (?)"
              aria-label="Keyboard shortcuts"
            >
              ?
            </button>
            <button type="button" className="btn top-ask" onClick={() => setAssistantOpen((o) => !o)} aria-expanded={assistantOpen}>
              Ask Continuum
            </button>
          </div>
        </header>

        <main className={cx("page", location.pathname === "/console" && "page-flush")} key={location.pathname}>
          {error ? (
            <div className="banner banner-error">
              <div>
                <strong>Continuum API is not responding.</strong>
                <p>
                  Start the backend on <code>:8000</code> or set <code>VITE_API_URL</code>. {error}
                </p>
              </div>
            </div>
          ) : null}
          <Outlet />
        </main>

        <footer className="app-footer">
          <span>Continuum</span>
          <span>Administrative coordination only. No diagnosis, prescribing or treatment changes.</span>
        </footer>
      </div>

      {menuOpen ? <button className="scrim" aria-label="Close navigation" onClick={() => setMenuOpen(false)} /> : null}
      <CallBar />
      {toast ? (
        <div className="toast" role="status">
          {toast}
        </div>
      ) : null}

      <WelcomeTour
        open={showTour}
        onClose={closeTour}
        onShowHelp={() => {
          closeTour();
          setHelpOpen(true);
        }}
      />
      <AssistantPanel open={assistantOpen} onClose={() => setAssistantOpen(false)} />
      <CommandPalette open={paletteOpen} actions={paletteActions} onClose={() => setPaletteOpen(false)} />
      <HotkeyHelp open={helpOpen} onClose={() => setHelpOpen(false)} />
    </div>
  );
}
