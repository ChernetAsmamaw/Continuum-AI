import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { api, getAuthToken, setAuthToken } from "./api";

export interface AuthUser {
  id: string;
  email: string;
  name: string;
  role: string;
}

const TOKEN_KEY = "continuum.token";

export type DemoRole = "coordinator" | "clinician";

interface AuthState {
  user: AuthUser | null;
  ready: boolean;
  login: (email: string, password: string) => Promise<void>;
  loginDemo: (role: DemoRole) => Promise<void>;
  logout: () => void;
}

const AuthContext = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<AuthUser | null>(null);
  const [ready, setReady] = useState(false);

  // Attach any stored token during first render, before children mount and
  // start fetching, so the very first console request is already authorized.
  const booted = useRef(false);
  if (!booted.current) {
    booted.current = true;
    const stored = localStorage.getItem(TOKEN_KEY);
    if (stored) setAuthToken(stored);
  }

  useEffect(() => {
    let cancelled = false;
    async function validate() {
      if (!getAuthToken()) {
        setReady(true);
        return;
      }
      try {
        const { user } = await api.auth.me();
        if (!cancelled) setUser(user);
      } catch {
        setAuthToken(null);
        localStorage.removeItem(TOKEN_KEY);
        if (!cancelled) setUser(null);
      } finally {
        if (!cancelled) setReady(true);
      }
    }
    void validate();
    return () => {
      cancelled = true;
    };
  }, []);

  const applySession = useCallback((token: string, nextUser: AuthUser) => {
    localStorage.setItem(TOKEN_KEY, token);
    setAuthToken(token);
    setUser(nextUser);
  }, []);

  const login = useCallback(
    async (email: string, password: string) => {
      const { token, user } = await api.auth.login(email, password);
      applySession(token, user);
    },
    [applySession],
  );

  const loginDemo = useCallback(
    async (role: DemoRole) => {
      const creds = await api.auth.demo();
      const cred = creds[role];
      const { token, user } = await api.auth.login(cred.email, cred.password);
      applySession(token, user);
    },
    [applySession],
  );

  const logout = useCallback(() => {
    localStorage.removeItem(TOKEN_KEY);
    setAuthToken(null);
    setUser(null);
    void api.auth.logout().catch(() => undefined);
  }, []);

  const value = useMemo(
    () => ({ user, ready, login, loginDemo, logout }),
    [user, ready, login, loginDemo, logout],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthState {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within AuthProvider");
  return ctx;
}
