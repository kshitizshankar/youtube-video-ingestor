import { useCallback, useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import TopBar from "./components/TopBar";
import VideoRow from "./components/VideoRow";
import ConfirmDialog from "./components/ConfirmDialog";
import {
  deleteProject,
  getProject,
  removeVideoFromProject,
  updateProject,
} from "./projects";
import type { ProjectDetail, ProjectVideoEntry, TranscriptSummary } from "./types";

export interface ProjectPageProps {
  onMenuToggle?: () => void;
}

/** Adapt a ProjectVideoEntry (shape returned by /api/projects/:id) into the
 *  TranscriptSummary shape VideoRow expects. Fields not supplied by the
 *  project endpoint get safe defaults. */
function entryToSummary(v: ProjectVideoEntry): TranscriptSummary {
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

export default function Project({ onMenuToggle }: ProjectPageProps) {
  const { projectId = "" } = useParams();
  const navigate = useNavigate();
  const [data, setData] = useState<ProjectDetail | null>(null);
  const [notFound, setNotFound] = useState(false);
  const [editingName, setEditingName] = useState(false);
  const [nameDraft, setNameDraft] = useState("");
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [deleteBusy, setDeleteBusy] = useState(false);

  const reload = useCallback(() => {
    if (!projectId) return;
    getProject(projectId)
      .then((d) => {
        setData(d);
        setNotFound(false);
      })
      .catch(() => {
        setNotFound(true);
        setData(null);
      });
  }, [projectId]);

  useEffect(() => {
    reload();
  }, [reload]);

  const saveName = useCallback(async () => {
    if (!projectId || !data) return;
    const next = nameDraft.trim();
    if (next && next !== data.project.name) {
      try {
        await updateProject(projectId, { name: next });
      } catch (e) {
        alert(`Rename failed: ${e}`);
      }
    }
    setEditingName(false);
    reload();
  }, [projectId, nameDraft, data, reload]);

  const handleRemove = useCallback(
    async (vid: string) => {
      try {
        await removeVideoFromProject(projectId, vid);
      } catch (e) {
        alert(`Remove failed: ${e}`);
        return;
      }
      reload();
    },
    [projectId, reload],
  );

  const handleDelete = useCallback(async () => {
    setDeleteBusy(true);
    try {
      await deleteProject(projectId);
      navigate("/");
    } catch (e) {
      alert(`Delete failed: ${e}`);
      setDeleteBusy(false);
    }
  }, [projectId, navigate]);

  if (!projectId) return null;

  const leading = onMenuToggle ? (
    <button
      type="button"
      className="btn-hamburger"
      onClick={onMenuToggle}
      aria-label="Open menu"
    >
      <HamburgerIcon />
    </button>
  ) : undefined;

  if (notFound) {
    return (
      <div className="main project-page">
        <TopBar
          crumbs={[{ label: "Dashboard", to: "/" }, "Project"]}
          leading={leading}
        />
        <div className="project-body">
          <div className="empty">
            Project not found. <Link to="/">Back to dashboard</Link>.
          </div>
        </div>
      </div>
    );
  }
  if (data === null) {
    return (
      <div className="main project-page">
        <TopBar
          crumbs={[{ label: "Dashboard", to: "/" }, "Project"]}
          leading={leading}
        />
        <div className="project-body">
          <div className="empty">Loading…</div>
        </div>
      </div>
    );
  }

  const { project, videos } = data;

  return (
    <div className="main project-page">
      <TopBar
        crumbs={[{ label: "Dashboard", to: "/" }, project.name]}
        leading={leading}
        actions={
          <>
            <button
              className="btn"
              disabled
              title="Coming in the next slice"
            >
              Add videos
            </button>
            <button
              className="btn btn-destructive"
              onClick={() => setConfirmDelete(true)}
            >
              Delete project
            </button>
          </>
        }
      />
      <div className="project-body">
        <header className="project-header">
          {editingName ? (
            <input
              className="project-name-input"
              autoFocus
              value={nameDraft}
              onChange={(e) => setNameDraft(e.target.value)}
              onBlur={saveName}
              onKeyDown={(e) => {
                if (e.key === "Enter") saveName();
                if (e.key === "Escape") setEditingName(false);
              }}
            />
          ) : (
            <h1
              className="project-title"
              onClick={() => {
                setEditingName(true);
                setNameDraft(project.name);
              }}
              title="Click to rename"
            >
              {project.name}
            </h1>
          )}
          {project.description && (
            <p className="project-desc">{project.description}</p>
          )}
          <div className="project-meta">
            <span>
              {project.video_count} video{project.video_count === 1 ? "" : "s"}
            </span>
          </div>
        </header>

        <section className="project-videos">
          {videos.length === 0 ? (
            <div className="empty">
              No videos yet. Adding videos lands in the next slice.
            </div>
          ) : (
            videos.map((v) => (
              <div key={v.id} className="project-video-row">
                <VideoRow v={entryToSummary(v)} />
                <button
                  type="button"
                  className="row-action row-action-danger project-row-remove"
                  title="Remove from project (video itself is not deleted)"
                  aria-label="Remove from project"
                  onClick={() => handleRemove(v.id)}
                >
                  Remove
                </button>
              </div>
            ))
          )}
        </section>
      </div>

      <ConfirmDialog
        open={confirmDelete}
        title="Delete project?"
        body={
          <>
            <strong>{project.name}</strong> will be removed. Videos themselves
            are not affected — only the project grouping goes away.
          </>
        }
        confirmLabel="Delete"
        destructive
        busy={deleteBusy}
        onCancel={() => setConfirmDelete(false)}
        onConfirm={handleDelete}
      />
    </div>
  );
}

function HamburgerIcon() {
  return (
    <svg
      width="18"
      height="18"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      strokeLinecap="round"
    >
      <path d="M3 6h18M3 12h18M3 18h18" />
    </svg>
  );
}
