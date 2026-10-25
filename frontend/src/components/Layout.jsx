import { useEffect } from "react";
import { Link, NavLink, Outlet, useLocation } from "react-router-dom";
import { USE_MOCK, API_BASE_URL } from "../config";
import { MockBadge } from "./Badges";
import { Icon } from "./Icon";

const NAV = [
  { to: "/", label: "Ask", end: true },
  { to: "/lab", label: "Hallucination Lab" },
  { to: "/why", label: "Why this model?" },
  { to: "/dashboard", label: "Benchmark" },
];

function ScrollToTop() {
  const { pathname } = useLocation();
  useEffect(() => {
    window.scrollTo({ top: 0, behavior: "instant" });
  }, [pathname]);
  return null;
}

export function Layout() {
  return (
    <div className="app-shell">
      <a className="skip-link" href="#main">
        Skip to content
      </a>
      <ScrollToTop />
      <header className="site-header">
        <div className="shell header-inner">
          <Link to="/" className="brand" aria-label="TrustRouter home">
            <span className="brand-mark">
              <Icon name="route" size={20} />
            </span>
            <span className="brand-copy">
              <strong>TrustRouter</strong>
              <small>Nebius Token Factory</small>
            </span>
          </Link>

          <nav className="nav" aria-label="Primary">
            {NAV.map((item) => (
              <NavLink
                key={item.to}
                to={item.to}
                end={item.end}
                className={({ isActive }) => `nav-link ${isActive ? "nav-active" : ""}`}
              >
                {item.label}
              </NavLink>
            ))}
          </nav>

          <div className="header-right">
            <MockBadge live={!USE_MOCK} />
            {!USE_MOCK && (
              <a
                className="header-api"
                href={`${API_BASE_URL}/docs`}
                target="_blank"
                rel="noreferrer"
                title="Open the gateway API docs"
              >
                API docs <Icon name="link" size={12} />
              </a>
            )}
          </div>
        </div>
      </header>

      <main id="main" className="shell main">
        <Outlet />
      </main>

      <footer className="site-footer">
        <div className="shell footer-inner">
          <div className="footer-brand">
            <Icon name="route" size={15} />
            <span>Route. Verify. Repair.</span>
          </div>
          <p>
            Verified means claims were checked against retrieved sources. It is evidence, not a
            guarantee of truth.
          </p>
          <span className="footer-build">Demo build · schema v1.1</span>
        </div>
      </footer>
    </div>
  );
}
