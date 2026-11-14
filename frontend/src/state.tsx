import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { api, API_BASE, getAuthToken } from "./api";
import type { AppConfig, Dashboard } from "./types";

interface AppState {
  config: AppConfig | null;
  dashboard: Dashboard | null;
  loading: boolean;
  error: string | null;
  refreshKey: number;
  /** Counts for the review queue and the action ledger (null until loaded / if unavailable). */
  ops: { review: number; reviewOrders: number; reviewIdentities: number; attention: number } | null;
  refresh: () => Promise<void>;
  toast: string | null;
  notify: (message: string) => void;
  apiBase: string;
}

const AppContext = createContext<AppState | null>(null);

export function AppProvider({ children }: { children: ReactNode }) {
  const [config, setConfig] = useState<AppConfig | null>(null);
  const [dashboard, setDashboard] = useState<Dashboard | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [refreshKey, setRefreshKey] = useState(0);
  const [toast, setToast] = useState<string | null>(null);
  const [ops, setOps] = useState<AppState["ops"]>(null);

  const refresh = useCallback(async () => {
    try {
      const [nextDashboard, review, ledger] = await Promise.all([
        api.dashboard(),
        api.review().catch(() => null),
        api.actions({ limit: 1 }).catch(() => null),
      ]);
      setDashboard(nextDashboard);
      setOps(
        review || ledger
          ? {
              review: review?.counts.total ?? 0,
              reviewOrders: review?.counts.orders ?? 0,
              reviewIdentities: review?.counts.identities ?? 0,
              attention: ledger?.summary.needs_attention ?? 0,
            }
          : null,
      );
      setError(null);
      setRefreshKey((key) => key + 1);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unable to reach the Continuum API");
    } finally {
      setLoading(false);
    }
  }, []);

  const notify = useCallback((message: string) => {
    setToast(message);
    window.setTimeout(() => setToast(null), 4200);
  }, []);

  useEffect(() => {
    api
      .config()
      .then(setConfig)
      .catch(() => setConfig(null));
    void refresh();
    const timer = window.setInterval(() => {
      void refresh();
    }, 30000);

    // MongoDB change streams → server-sent events: refresh as soon as task
    // state changes anywhere (EHR ingestion, approvals, ledger), debounced.
    let source: EventSource | null = null;
    let pending: number | undefined;
    const token = getAuthToken();
    if (token && typeof EventSource !== "undefined") {
      source = new EventSource(`${API_BASE}/api/stream?token=${encodeURIComponent(token)}`);
      source.addEventListener("change", () => {
        window.clearTimeout(pending);
        pending = window.setTimeout(() => void refresh(), 500);
      });
      source.onerror = () => {
        // The browser retries on its own; the 30s timer still covers us.
      };
    }
    return () => {
      window.clearInterval(timer);
      window.clearTimeout(pending);
      source?.close();
    };
  }, [refresh]);

  const value = useMemo(
    () => ({
      config,
      dashboard,
      loading,
      error,
      refreshKey,
      ops,
      refresh,
      toast,
      notify,
      apiBase: API_BASE,
    }),
    [config, dashboard, loading, error, refreshKey, ops, refresh, toast, notify],
  );

  return <AppContext.Provider value={value}>{children}</AppContext.Provider>;
}

export function useApp(): AppState {
  const context = useContext(AppContext);
  if (!context) throw new Error("useApp must be used within AppProvider");
  return context;
}
