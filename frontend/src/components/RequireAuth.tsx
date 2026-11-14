import { Navigate, Outlet, useLocation } from "react-router-dom";
import { useAuth } from "../auth";
import { Spinner } from "../ui";

export function RequireAuth() {
  const { user, ready } = useAuth();
  const location = useLocation();

  if (!ready) {
    return <Spinner label="Checking session" />;
  }
  if (!user) {
    return <Navigate to="/login" replace state={{ from: location.pathname }} />;
  }
  return <Outlet />;
}
