import { NavLink } from "react-router-dom";
import type { Health } from "../api";
import { useApi } from "../useApi";

const NAV = [
  { to: "/", label: "Dashboard", end: true },
  { to: "/forecasting", label: "Forecasting" },
  { to: "/route-planning", label: "Route Planning" },
  { to: "/reports", label: "Reports & Verification" },
  { to: "/audit", label: "Audit Log" },
];

// Greyed out until the roles and settings work exists (Day 8 greys them deliberately).
const DISABLED = ["User Management", "Settings"];

export default function Sidebar() {
  const { data: health } = useApi<Health>("/health");
  const offline = health?.offline_mode ?? true;

  return (
    <aside className="sidebar">
      <div className="brand">
        <span className="brand-mark" aria-hidden="true" />
        <div>
          <div className="brand-name">RASAD</div>
          <div className="brand-sub">Ladakh sector</div>
        </div>
      </div>

      <nav aria-label="Main">
        {NAV.map((item) => (
          <NavLink
            key={item.to}
            to={item.to}
            end={item.end}
            className={({ isActive }) => `nav-item${isActive ? " active" : ""}`}
          >
            {item.label}
          </NavLink>
        ))}
        <div className="nav-divider" />
        {DISABLED.map((label) => (
          <span key={label} className="nav-item disabled" aria-disabled="true">
            {label}
          </span>
        ))}
      </nav>

      <div className="sidebar-foot">
        <span className={`badge ${offline ? "badge-ok" : "badge-warn"}`}>
          <span className="dot" />
          {offline ? "Offline mode" : "Online"}
        </span>
        {health?.data && (
          <div className="provenance">
            <div>Weather: {health.data.weather_source === "open-meteo" ? "Open-Meteo" : "synthetic"}</div>
            <div>Consumption: {health.data.consumption_source}</div>
          </div>
        )}
      </div>
    </aside>
  );
}
