import { BrowserRouter, Navigate, Route, Routes, useLocation } from "react-router-dom";
import { AuthProvider, useAuth } from "./auth";
import { AppProvider } from "./state";
import { RequireAuth } from "./components/RequireAuth";
import { Layout } from "./components/Layout";
import { ActionsPage } from "./pages/ActionsPage";
import { AuditPage } from "./pages/AuditPage";
import { CallRoom } from "./pages/CallRoom";
import { CheckinPage } from "./pages/CheckinPage";
import { ApprovalsPage } from "./pages/ApprovalsPage";
import { Dashboard } from "./pages/Dashboard";
import { IngestionPage } from "./pages/IngestionPage";
import { Inspector } from "./pages/Inspector";
import { Landing } from "./pages/Landing";
import { Login } from "./pages/Login";
import { PatientIntake } from "./pages/PatientIntake";
import { PatientJourney } from "./pages/PatientJourney";
import { PatientsPage } from "./pages/PatientsPage";
import { QueuePage } from "./pages/QueuePage";
import { ReviewPage } from "./pages/ReviewPage";
import { WorkPage } from "./pages/WorkPage";

function HomeRedirect() {
  const { user, ready } = useAuth();
  const location = useLocation();
  if (!ready) return null;
  // Unknown paths land on the console when signed in, otherwise the landing page.
  return <Navigate to={user ? "/console" : "/"} replace state={{ from: location.pathname }} />;
}

export default function App() {
  return (
    <AuthProvider>
      <BrowserRouter>
        <Routes>
          <Route path="/" element={<Landing />} />
          <Route path="/login" element={<Login />} />
          <Route path="/checkin/:token" element={<CheckinPage />} />
          <Route element={<RequireAuth />}>
            <Route element={<AppProvider><Layout /></AppProvider>}>
              <Route path="/console" element={<QueuePage />} />
              <Route path="/overview" element={<Dashboard />} />
              <Route path="/work" element={<WorkPage />} />
              <Route path="/patients" element={<PatientsPage />} />
              <Route path="/patients/new" element={<PatientIntake />} />
              <Route path="/patients/:patientId" element={<PatientJourney />} />
              <Route path="/patients/:patientId/agent" element={<Inspector />} />
              <Route path="/patients/:patientId/call" element={<CallRoom />} />
              <Route path="/approvals" element={<ApprovalsPage />} />
              <Route path="/review" element={<ReviewPage />} />
              <Route path="/ingestion" element={<IngestionPage />} />
              <Route path="/actions" element={<ActionsPage />} />
              <Route path="/audit" element={<AuditPage />} />
            </Route>
          </Route>
          <Route path="*" element={<HomeRedirect />} />
        </Routes>
      </BrowserRouter>
    </AuthProvider>
  );
}
