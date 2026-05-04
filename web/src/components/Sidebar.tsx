import { useCallback, useEffect, useState } from "react";
import { Link, useLocation } from "react-router-dom";
import { useProjects } from "../ProjectsContext";
import NewProjectModal from "./NewProjectModal";

export interface SidebarProps {
  libraryCount: number;
  /** Currently-running ingests. Drives the "in progress" pill. */
  activeIngestCount?: number;
  /** Failed ingests (done && error && !cancelled). Drives the "failed"
   *  surface so the user can jump straight to /queue and bulk-retry. */
  failedIngestCount?: number;
  onNewIngest: () => void;
}

type Theme = "dark" | "light";

function readTheme(): Theme {
  try {
    return (localStorage.getItem("vvi.theme") as Theme) || "light";
  } catch { return "light"; }
}

export default function Sidebar({
  libraryCount,
  activeIngestCount = 0,
  failedIngestCount = 0,
  onNewIngest,
}: SidebarProps) {
  const loc = useLocation();
  const onDashboard = loc.pathname === "/";
  const onLibrary = loc.pathname.startsWith("/library");
  const onArchive = loc.pathname.startsWith("/archive");
  const onQueue = loc.pathname.startsWith("/queue");
  const [theme, setTheme] = useState<Theme>(readTheme);
  const [modalOpen, setModalOpen] = useState(false);

  const { projects: allProjects, create } = useProjects();
  const projects = (allProjects ?? []).slice(0, 8);

  useEffect(() => {
    document.documentElement.classList.toggle("theme-light", theme === "light");
    try { localStorage.setItem("vvi.theme", theme); } catch {}
  }, [theme]);

  const handleCreate = useCallback(
    async (name: string, description: string) => {
      await create(name, description || undefined);
    },
    [create],
  );

  const toggleTheme = () => setTheme((t) => t === "dark" ? "light" : "dark");

  // Surface the queue link only when there's something to do there.
  // Failed jobs win over active for visual emphasis (tangerine accent).
  const queueVisible = activeIngestCount > 0 || failedIngestCount > 0;
  const queueHasFailures = failedIngestCount > 0;

  return (
    <aside className="sidebar">
      <div className="sidebar-top">
        <span className="sidebar-wordmark" aria-label="vidan.">vidan</span>
        <span className="sidebar-tagline">
          for humans who don&rsquo;t have 90 min
        </span>
      </div>

      {/* Primary CTA — the one action that wears the tangerine. */}
      <div className="sidebar-cta">
        <button type="button" className="btn btn-accent btn-cta-block" onClick={onNewIngest}>
          <PlusIcon /> add video
          <span className="btn-cta-kbd">CTRL N</span>
        </button>
      </div>

      <div className="nav-group nav-group-flush">
        <Link to="/" className={`nav-item ${onDashboard ? "active" : ""}`}>
          <DashIcon />
          <span>dashboard</span>
        </Link>
        <Link to="/library" className={`nav-item ${onLibrary ? "active" : ""}`}>
          <LibIcon />
          <span>all videos</span>
          <span className="count">{libraryCount}</span>
        </Link>
        <Link
          to="/archive"
          className={`nav-item ${onArchive ? "active" : ""}`}
        >
          <ArchiveIcon />
          <span>archive</span>
        </Link>
        {queueVisible && (
          <Link
            to="/queue"
            className={[
              "nav-item",
              "nav-item-queue",
              onQueue ? "active" : "",
              queueHasFailures ? "has-failures" : "",
            ].join(" ").trim()}
            title={queueHasFailures
              ? `${failedIngestCount} failed · ${activeIngestCount} active`
              : "View live progress"}
          >
            {queueHasFailures ? (
              <span className="queue-bang" aria-hidden>!</span>
            ) : (
              <span className="nav-pulse" aria-hidden>
                <span className="pulse-core" />
                <span className="pulse-ring" />
              </span>
            )}
            <span>queue</span>
            <span className="count count-stack">
              {activeIngestCount > 0 && (
                <span className="count-active">{activeIngestCount}</span>
              )}
              {queueHasFailures && (
                <span className="count-failed">
                  {activeIngestCount > 0 ? " · " : ""}
                  {failedIngestCount} failed
                </span>
              )}
            </span>
          </Link>
        )}
      </div>

      <div className="nav-group">
        <div className="nav-group-title">projects</div>
        {projects.map((p) => {
          const active = loc.pathname === `/p/${p.id}`;
          const isInbox = p.system_kind === "inbox";
          return (
            <Link
              key={p.id}
              to={`/p/${p.id}`}
              className={`nav-item ${active ? "active" : ""}`}
              title={isInbox ? "Unprojected videos land here" : undefined}
            >
              {isInbox ? <InboxIcon /> : <FolderIcon />}
              <span>{p.name}</span>
              <span className="count">{p.video_count}</span>
            </Link>
          );
        })}
        <button className="nav-item nav-item-muted" onClick={() => setModalOpen(true)}>
          <PlusIcon />
          <span>new project</span>
        </button>
      </div>

      <div className="sidebar-bottom">
        <span className="avatar">K</span>
        <div className="user">
          <div className="uname">you</div>
          <div className="ustack">local stack</div>
        </div>
        <Link
          to="/settings"
          className="sidebar-icon-btn"
          title="Settings"
          aria-label="Settings"
        >
          <GearIcon />
        </Link>
        <button
          type="button"
          className="sidebar-icon-btn theme-toggle"
          onClick={toggleTheme}
          title={theme === "dark" ? "Switch to light mode" : "Switch to dark mode"}
          aria-label={theme === "dark" ? "Switch to light mode" : "Switch to dark mode"}
        >
          {theme === "dark" ? <SunIcon /> : <MoonIcon />}
        </button>
      </div>

      <NewProjectModal
        open={modalOpen}
        onClose={() => setModalOpen(false)}
        onSubmit={handleCreate}
      />
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

function DashIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <path d="M3 13a9 9 0 0 1 18 0" />
      <path d="M12 13l4-4" />
      <circle cx="12" cy="13" r="1" />
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

function GearIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <circle cx="12" cy="12" r="3" />
      <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 1 1-4 0v-.09a1.65 1.65 0 0 0-1-1.51 1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 1 1 0-4h.09a1.65 1.65 0 0 0 1.51-1 1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 1 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 1 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z" />
    </svg>
  );
}

function FolderIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z" />
    </svg>
  );
}

/** Inbox glyph: tray with inbound arrow. Distinguishes the system
 *  project from user-created folders in the sidebar. */
function InboxIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <path d="M22 12h-6l-2 3h-4l-2-3H2" />
      <path d="M5.45 5.11 2 12v6a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-6l-3.45-6.89A2 2 0 0 0 16.76 4H7.24a2 2 0 0 0-1.79 1.11z" />
    </svg>
  );
}
