import { Navigate, Route, Routes } from "react-router-dom";
import Sidebar from "./components/Sidebar";
import AuditLog from "./pages/AuditLog";
import Dashboard from "./pages/Dashboard";
import Forecasting from "./pages/Forecasting";
import ReportsVerification from "./pages/ReportsVerification";
import RoutePlanning from "./pages/RoutePlanning";

export default function App() {
  return (
    <div className="shell">
      <Sidebar />
      <main className="main">
        <Routes>
          <Route path="/" element={<Dashboard />} />
          <Route path="/forecasting" element={<Forecasting />} />
          <Route path="/route-planning" element={<RoutePlanning />} />
          <Route path="/reports" element={<ReportsVerification />} />
          <Route path="/audit" element={<AuditLog />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </main>
    </div>
  );
}
