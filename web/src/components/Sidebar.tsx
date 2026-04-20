import { Link, useLocation } from "react-router-dom";

export interface SidebarProps {
  libraryCount: number;
  onSearchFocus?: () => void;
  onNewIngest: () => void;
}

export default function Sidebar({ libraryCount, onSearchFocus, onNewIngest }: SidebarProps) {
  const loc = useLocation();
  const onLibrary = loc.pathname === "/" || loc.pathname.startsWith("/library");

  return (
    <aside className="sidebar">
      <div className="sidebar-top">
        <span className="brand-mark">a</span>
        <div>
          <div className="brand-name">Edu Center</div>
          <div className="brand-sub">Local · v0.3</div>
        </div>
      </div>

      <div className="sidebar-search" onClick={onSearchFocus}>
        <SearchIcon />
        <input placeholder="Search library…" />
        <span className="kbd">Ctrl K</span>
      </div>

      <div className="nav-group">
        <div className="nav-group-title">Library</div>
        <Link to="/" className={`nav-item ${onLibrary ? "active" : ""}`}>
          <LibIcon />
          <span>All videos</span>
          <span className="count">{libraryCount}</span>
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
      </div>
    </aside>
  );
}

function SearchIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <circle cx="11" cy="11" r="7" /><path d="m21 21-4.3-4.3" />
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
