import { useCallback, useEffect, useState } from "react";
import TopBar from "./components/TopBar";
import ProjectCard from "./components/ProjectCard";
import StatsStrip from "./components/StatsStrip";
import NewProjectModal from "./components/NewProjectModal";
import VideoRow from "./components/VideoRow";
import { createProject, getStats, listProjects } from "./projects";
import type { Project, Stats, TranscriptSummary } from "./types";

export interface DashboardProps {
  onMenuToggle?: () => void;
  onAdd: () => void;
}

/** Adapt a StatsVideo (trimmed shape from /api/stats) into the TranscriptSummary
 *  shape VideoRow expects. Fields not supplied by /api/stats get safe defaults. */
function statsVideoToSummary(v: Stats["latest_videos"][number]): TranscriptSummary {
  return {
    id: v.id,
    title: v.title ?? v.id,
    duration_sec: v.duration_sec,
    language: null,
    diarized: false,
    model: null,
    segment_count: 0,
    channel: v.channel,
  };
}

export default function Dashboard({ onMenuToggle, onAdd }: DashboardProps) {
  const [projects, setProjects] = useState<Project[]>([]);
  const [stats, setStats] = useState<Stats | null>(null);
  const [modalOpen, setModalOpen] = useState(false);

  const reload = useCallback(() => {
    listProjects().then(setProjects).catch(console.error);
    getStats().then(setStats).catch(console.error);
  }, []);

  useEffect(() => {
    reload();
  }, [reload]);

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

        <section className="dash-latest">
          <h2>Latest videos</h2>
          {stats && stats.latest_videos.length > 0 ? (
            <div className="latest-list">
              {stats.latest_videos.map((v) => (
                <VideoRow key={v.id} v={statsVideoToSummary(v)} />
              ))}
            </div>
          ) : (
            <div className="empty">No videos yet. Paste a URL to get started.</div>
          )}
        </section>

        {stats && <StatsStrip stats={stats} />}
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
