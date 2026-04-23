import { useEffect, useState } from "react";
import { Link, useLocation } from "react-router-dom";

export interface SidebarProps {
  libraryCount: number;
  onNewIngest: () => void;
}

type Theme = "dark" | "light";

function readTheme(): Theme {
  try {
    return (localStorage.getItem("vvi.theme") as Theme) || "dark";
  } catch { return "dark"; }
}

export default function Sidebar({ libraryCount, onNewIngest }: SidebarProps) {
  const loc = useLocation();
  const onLibrary = loc.pathname === "/" || loc.pathname.startsWith("/library");
  const [theme, setTheme] = useState<Theme>(readTheme);

  useEffect(() => {
    document.documentElement.classList.toggle("theme-light", theme === "light");
    try { localStorage.setItem("vvi.theme", theme); } catch {}
  }, [theme]);

  const toggleTheme = () => setTheme((t) => t === "dark" ? "light" : "dark");

  return (
    <aside className="sidebar">
      <div className="sidebar-top">
        <span className="brand-mark">V</span>
        <div>
          <div className="brand-name">Vidan</div>
          <div className="brand-sub">Video Analyzer</div>
        </div>
      </div>

      <div className="nav-group">
        <div className="nav-group-title">Library</div>
        <Link to="/" className={`nav-item ${onLibrary ? "active" : ""}`}>
          <LibIcon />
          <span>All videos</span>
          <span className="count">{libraryCount}</span>
        </Link>
        <Link
          to="/archive"
          className={`nav-item ${loc.pathname.startsWith("/archive") ? "active" : ""}`}
        >
          <ArchiveIcon />
          <span>Archive</span>
        </Link>
        <button className="nav-item" onClick={onNewIngest}>
          <PlusIcon />
          <span>Add video</span>
          <span className="count">Ctrl N</span>
        </button>
      </div>

      <div className="nav-group">
        <div className="nav-group-title">Collections</div>
        <button className="nav-item">
          <span className="tag-dot" style={{ background: "var(--speaker-1)" }} />
          <span>AI research</span>
          <span className="count">—</span>
        </button>
        <button className="nav-item">
          <span className="tag-dot" style={{ background: "var(--speaker-3)" }} />
          <span>Interviews</span>
          <span className="count">—</span>
        </button>
      </div>

      <div className="sidebar-bottom">
        <span className="avatar">K</span>
        <div className="user">
          <div className="uname">You</div>
          <div className="ustack">Local stack</div>
        </div>
        <button
          type="button"
          className="theme-toggle"
          onClick={toggleTheme}
          title={theme === "dark" ? "Switch to light mode" : "Switch to dark mode"}
          aria-label={theme === "dark" ? "Switch to light mode" : "Switch to dark mode"}
        >
          {theme === "dark" ? <SunIcon /> : <MoonIcon />}
        </button>
      </div>
    </aside>
  );
}

function SunIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <circle cx="12" cy="12" r="4" />
      <path d="M12 2v2M12 20v2M4.93 4.93l1.41 1.41M17.66 17.66l1.41 1.41M2 12h2M20 12h2M4.93 19.07l1.41-1.41M17.66 6.34l1.41-1.41" />
    </svg>
  );
}
function MoonIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <path d="M21 12.79A9 9 0 1 1 11.21 3a7 7 0 0 0 9.79 9.79z" />
    </svg>
  );
}

function LibIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <rect x="3" y="3" width="7" height="7" rx="1" />
      <rect x="14" y="3" width="7" height="7" rx="1" />
      <rect x="3" y="14" width="7" height="7" rx="1" />
      <rect x="14" y="14" width="7" height="7" rx="1" />
    </svg>
  );
}

function PlusIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round">
      <path d="M12 5v14M5 12h14" />
    </svg>
  );
}

function ArchiveIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <rect x="3" y="4" width="18" height="5" rx="1" />
      <path d="M5 9v10a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1V9M10 13h4" />
    </svg>
  );
}
