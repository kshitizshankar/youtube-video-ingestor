import { useCallback, useEffect, useRef, useState } from "react";
import TopBar from "./components/TopBar";
import IngestStrip from "./components/IngestStrip";
import ProjectCard from "./components/ProjectCard";
import StatsStrip from "./components/StatsStrip";
import NewProjectModal from "./components/NewProjectModal";
import TopChannelsCard from "./components/TopChannelsCard";
import LanguageBreakdownCard from "./components/LanguageBreakdownCard";
import TopTagsCard from "./components/TopTagsCard";
import LongestVideosCard from "./components/LongestVideosCard";
import RecentlyAnalyzedCard from "./components/RecentlyAnalyzedCard";
import { createProject, getStats, listProjects } from "./projects";
import { useActiveIngests } from "./useActiveIngests";
import type { Project, Stats } from "./types";

export interface DashboardProps {
  onMenuToggle?: () => void;
  onAdd: () => void;
}

/** Stats + projects refresh cadence for the Dashboard. Short enough that
 *  a fresh ingest's counts show up without a manual reload, long enough
 *  that it doesn't saturate the connection. */
const POLL_MS = 3000;

export default function Dashboard({ onMenuToggle, onAdd }: DashboardProps) {
  const [projects, setProjects] = useState<Project[]>([]);
  const [stats, setStats] = useState<Stats | null>(null);
  const [modalOpen, setModalOpen] = useState(false);
  const ingests = useActiveIngests();
  const reloadRef = useRef<() => void>(() => {});

  const reload = useCallback(() => {
    listProjects().then(setProjects).catch(console.error);
    getStats().then(setStats).catch(console.error);
  }, []);
  reloadRef.current = reload;

  useEffect(() => {
    reload();
  }, [reload]);

  // Poll while the tab is visible; pause while hidden to avoid pointless
  // work. Refresh immediately on tab focus so switching back shows fresh
  // numbers without waiting for the next tick.
  useEffect(() => {
    let timer: number | null = null;
    const start = () => {
      if (timer != null) return;
      timer = window.setInterval(() => {
        if (document.visibilityState === "visible") reloadRef.current();
      }, POLL_MS);
    };
    const stop = () => {
      if (timer != null) {
        window.clearInterval(timer);
        timer = null;
      }
    };
    const onVis = () => {
      if (document.visibilityState === "visible") {
        reloadRef.current();
        start();
      } else {
        stop();
      }
    };
    const onFocus = () => reloadRef.current();

    start();
    document.addEventListener("visibilitychange", onVis);
    window.addEventListener("focus", onFocus);
    return () => {
      stop();
      document.removeEventListener("visibilitychange", onVis);
      window.removeEventListener("focus", onFocus);
    };
  }, []);

  const handleCreate = useCallback(
    async (name: string, description: string) => {
      await createProject(name, description || undefined);
      reload();
    },
    [reload],
  );

  return (
    <div className="main dashboard">
      <TopBar
        title="Dashboard"
        leading={
          onMenuToggle && (
            <button
              type="button"
              className="btn-hamburger"
              onClick={onMenuToggle}
              aria-label="Open menu"
            >
              <HamburgerIcon />
            </button>
          )
        }
        actions={
          <button className="btn btn-primary" onClick={onAdd}>
            <PlusIcon /> Add
          </button>
        }
      />
      <div className="dash-body">
        <IngestStrip ingests={ingests} />
        <section className="dash-hero">
          <h1>Your projects</h1>
          <div className="dash-hero-actions">
            <button className="primary" onClick={() => setModalOpen(true)}>
              New project
            </button>
            <button onClick={onAdd}>Quick ingest</button>
          </div>
        </section>

        <section className="dash-projects">
          {projects.length === 0 ? (
            <div className="empty">
              No projects yet. Create one to start grouping videos.
            </div>
          ) : (
            <div className="project-grid">
              {projects.map((p) => (
                <ProjectCard key={p.id} p={p} />
              ))}
            </div>
          )}
        </section>

        {stats && <StatsStrip stats={stats} />}

        {stats && (
          <section className="dash-analytics">
            <div className="dash-analytics-head">
              <span className="dash-analytics-kicker">Readings</span>
              <h2>What&rsquo;s in your library</h2>
            </div>
            <div className="analytics-grid">
              <TopChannelsCard channels={stats.by_channel} />
              <LanguageBreakdownCard languages={stats.by_language} />
              <TopTagsCard tags={stats.top_tags} />
              <LongestVideosCard videos={stats.longest_videos} />
              <RecentlyAnalyzedCard items={stats.recently_analyzed} />
            </div>
          </section>
        )}
      </div>
      <NewProjectModal
        open={modalOpen}
        onClose={() => setModalOpen(false)}
        onSubmit={handleCreate}
      />
    </div>
  );
}

function HamburgerIcon() {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round">
      <path d="M3 6h18M3 12h18M3 18h18" />
    </svg>
  );
}

function PlusIcon() {
  return (
    <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round">
      <path d="M12 5v14M5 12h14" />
    </svg>
  );
}
