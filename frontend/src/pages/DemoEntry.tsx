import { useEffect, useRef, useState } from "react";
import { Link, Navigate } from "react-router-dom";
import { useAuth } from "../auth";

/** One-click entry: /demo signs in as the demo coordinator and opens the console. */
export function DemoEntry() {
  const { user, loginDemo } = useAuth();
  const [failed, setFailed] = useState(false);
  const started = useRef(false);

  useEffect(() => {
    if (user || started.current) return;
    started.current = true;
    loginDemo("coordinator").catch(() => setFailed(true));
  }, [user, loginDemo]);

  if (user) return <Navigate to="/console" replace />;
  if (failed) return <Navigate to="/login" replace />;
  return (
    <div className="auth-screen" aria-busy="true">
      <p className="auth-sub" style={{ margin: "auto" }}>
        Opening the demo workspace… <Link to="/login">Sign in instead</Link>
      </p>
    </div>
  );
}
