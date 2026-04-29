import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import TopBar from "./components/TopBar";
import VideoRow from "./components/VideoRow";
import ConfirmDialog from "./components/ConfirmDialog";
import AddVideosModal from "./components/AddVideosModal";
import IngestCard from "./components/IngestCard";
import SearchBar from "./components/SearchBar";
import Toast, { type ToastKind } from "./components/Toast";
import { type IngestState, listIngests, listTranscripts } from "./api";
import { getProject } from "./projects";
import { useProjects } from "./ProjectsContext";
import type {
  BulkIngestResponse,
  ProjectDetail,
  ProjectVideoEntry,
  TranscriptSummary,
} from "./types";

export interface ProjectPageProps {
  onMenuToggle?: () => void;
}

/** Adapt a ProjectVideoEntry (shape returned by /api/projects/:id) into the
 *  TranscriptSummary shape VideoRow expects. When a global library summary
 *  is available for this id, prefer its fields — that's where source +
 *  image_url + diarized + segment_count etc. live. The project endpoint
 *  itself doesn't carry those today. */
function entryToSummary(
  v: ProjectVideoEntry,
  fromLibrary?: TranscriptSummary,
): TranscriptSummary {
  if (fromLibrary) {
    // Library summary is authoritative; only fall back to entry fields
    // when something's actually missing (channel/duration drift).
    return {
      ...fromLibrary,
      title: fromLibrary.title || v.title || v.id,
      duration_sec: fromLibrary.duration_sec ?? v.duration_sec,
      channel: fromLibrary.channel ?? v.channel,
    };
  }
  return {
    id: v.id,
    title: v.title ?? v.id,
    duration_sec: v.duration_sec,
    language: null,
    diarized: false,
    model: null,
    segment_count: 0,
    channel: v.channel,
    source: "youtube",
  };
}

function tokenize(s: string): string {
  return s.toLowerCase().normalize("NFKD").replace(/\s+/g, " ").trim();
}

interface ToastState {
  open: boolean;
  kind: ToastKind;
  message: string;
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
  const [addVideosOpen, setAddVideosOpen] = useState(false);
  const [toast, setToast] = useState<ToastState>({
    open: false,
    kind: "info",
    message: "",
  });
  const [projectIngests, setProjectIngests] = useState<IngestState[]>([]);
  const [librarySummaries, setLibrarySummaries] = useState<TranscriptSummary[]>([]);
  const [search, setSearch] = useState("");

  // Mutations funnel through the context so the sidebar/library stay in sync.
  const {
    rename: renameProject,
    remove: deleteProjectCtx,
    removeVideo: removeProjectVideo,
    refresh: refreshProjects,
  } = useProjects();
  // Track video IDs we just submitted so we can keep polling even before the
  // server has fully reflected them in the project's video list.
  const pendingIdsRef = useRef<Set<string>>(new Set());
  // IDs of ingests we've already observed as done — prevents a reload storm
  // when `data` updates cause this polling effect to re-run.
  const handledDoneRef = useRef<Set<string>>(new Set());

  const reload = useCallback(() => {
    if (!projectId) return;
    getProject(projectId)
      .then((d) => {
        setData(d);
        setNotFound(false);
        // Clear any pending IDs that now show up in the project — they're
        // no longer "pending" from our perspective.
        if (pendingIdsRef.current.size > 0) {
          const known = new Set(d.videos.map((v) => v.id));
          for (const id of Array.from(pendingIdsRef.current)) {
            if (known.has(id)) pendingIdsRef.current.delete(id);
          }
        }
      })
      .catch(() => {
        setNotFound(true);
        setData(null);
      });
  }, [projectId]);

  useEffect(() => {
    reload();
  }, [reload]);

  // Pull the global library so we can layer real source + image_url + diarized
  // counts onto each project video. Re-fetched whenever project membership
  // changes (data update) — listTranscripts is cheap on localhost.
  useEffect(() => {
    let cancelled = false;
    listTranscripts()
      .then((rows) => { if (!cancelled) setLibrarySummaries(rows); })
      .catch(() => { /* swallow */ });
    return () => { cancelled = true; };
  }, [data?.videos.length]);

  const summaryById = useMemo(() => {
    const m = new Map<string, TranscriptSummary>();
    for (const s of librarySummaries) m.set(s.id, s);
    return m;
  }, [librarySummaries]);

  // Poll /api/ingests for in-flight jobs that belong to this project. Filters
  // locally by video ID — backend doesn't yet scope /api/ingests by project.
  useEffect(() => {
    if (!projectId) return;
    let cancelled = false;

    const tick = async () => {
      try {
        const all = await listIngests();
        if (cancelled) return;
        const projectVideoIds = new Set<string>(
          (data?.videos ?? []).map((v) => v.id),
        );
        // Keep anything whose id matches a video already in the project OR
        // one we just submitted (pending-ingest state).
        const mine = all.filter(
          (ing) =>
            projectVideoIds.has(ing.id) || pendingIdsRef.current.has(ing.id),
        );
        setProjectIngests(mine);
        // Pull a fresh project detail the first time we see each done ingest
        // so the new row shows up with its final metadata. Guarding on
        // handledDoneRef prevents a feedback loop where every data update
        // reruns this effect and retriggers reload().
        let triggeredReload = false;
        for (const i of mine) {
          if (i.done && !handledDoneRef.current.has(i.id)) {
            handledDoneRef.current.add(i.id);
            if (!triggeredReload) {
              reload();
              // Newly-finished ingest = new project membership; bump the
              // shared cache so sidebar/library video_counts reflect it.
              refreshProjects();
              triggeredReload = true;
            }
          }
        }
      } catch {
        /* swallow — polling is best-effort */
      }
    };

    tick();
    const id = window.setInterval(tick, 2500);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, [projectId, data, reload, refreshProjects]);

  const saveName = useCallback(async () => {
    if (!projectId || !data) return;
    const next = nameDraft.trim();
    if (next && next !== data.project.name) {
      try {
        // Returns the updated detail; use it directly so the title flips
        // without a follow-up GET. Context refresh handles sidebar + library.
        const updated = await renameProject(projectId, next);
        setData(updated);
      } catch (e) {
        alert(`Rename failed: ${e}`);
      }
    }
    setEditingName(false);
  }, [projectId, nameDraft, data, renameProject]);

  const handleRemove = useCallback(
    async (vid: string) => {
      try {
        await removeProjectVideo(projectId, vid);
      } catch (e) {
        alert(`Remove failed: ${e}`);
        return;
      }
      reload();
    },
    [projectId, reload, removeProjectVideo],
  );

  const handleDelete = useCallback(async () => {
    setDeleteBusy(true);
    try {
      await deleteProjectCtx(projectId);
      navigate("/");
    } catch (e) {
      alert(`Delete failed: ${e}`);
      setDeleteBusy(false);
    }
  }, [projectId, navigate, deleteProjectCtx]);

  const handleAddSuccess = useCallback(
    (resp: BulkIngestResponse) => {
      setAddVideosOpen(false);
      // Track the newly-submitted jobs so polling recognises them even before
      // the project detail payload lists them.
      for (const jid of resp.job_ids) pendingIdsRef.current.add(jid);
      reload();
      // Already-transcribed videos are added to the project synchronously,
      // bumping video_count. Refresh the projects context so sidebar +
      // library see the updated count immediately.
      refreshProjects();
      const started = resp.job_ids.length;
      const alreadyDone = resp.skipped.filter(
        (s) => s.reason === "already_transcribed",
      ).length;
      const archived = resp.skipped.filter((s) => s.reason === "archived")
        .length;

      // Compose a message that reflects the three outcomes.
      const parts: string[] = [];
      if (started > 0) {
        parts.push(
          `Started ${started} ingest${started === 1 ? "" : "s"}.`,
        );
      }
      if (alreadyDone > 0) {
        parts.push(
          `${alreadyDone} already transcribed (added to project).`,
        );
      }
      if (archived > 0) {
        parts.push(
          `${archived} skipped (archived).`,
        );
      }
      const message =
        parts.length > 0
          ? parts.join(" ")
          : "Nothing to do — all videos already handled.";
      const kind: ToastKind =
        started === 0 && alreadyDone === 0 && archived === 0
          ? "info"
          : started === 0 && alreadyDone > 0
            ? "success"
            : "info";
      setToast({ open: true, kind, message });
    },
    [reload, refreshProjects],
  );

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
          crumbs={[{ label: "dashboard", to: "/" }, "Project"]}
          leading={leading}
        />
        <div className="project-body">
          <div className="empty">
            project not found. <Link to="/">back to dashboard</Link>.
          </div>
        </div>
      </div>
    );
  }
  if (data === null) {
    return (
      <div className="main project-page">
        <TopBar
          crumbs={[{ label: "dashboard", to: "/" }, "Project"]}
          leading={leading}
        />
        <div className="project-body">
          <div className="empty">loading...</div>
        </div>
      </div>
    );
  }

  const { project, videos } = data;
  const activeIngests = projectIngests.filter((i) => !i.done);

  return (
    <div className="main project-page">
      <TopBar
        crumbs={[{ label: "dashboard", to: "/" }, project.name]}
        leading={leading}
        actions={
          <>
            <button
              className="btn btn-accent"
              onClick={() => setAddVideosOpen(true)}
            >
              <PlusIcon /> add videos
            </button>
            <button
              className="btn btn-destructive"
              onClick={() => setConfirmDelete(true)}
            >
              delete project
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

        {projectIngests.length > 0 && (
          <section className="ingest-strip">
            <header>
              <span className="label">in progress</span>
              <span className="count">
                {activeIngests.length} active &middot;{" "}
                {projectIngests.filter((i) => i.done).length} just finished
              </span>
            </header>
            <div className="ingest-cards">
              {projectIngests.map((ing) => (
                <IngestCard key={ing.id} ing={ing} />
              ))}
            </div>
          </section>
        )}

        {videos.length > 0 && (
          <div className="project-search">
            <SearchBar
              value={search}
              onChange={setSearch}
              placeholder={`search inside ${project.name.toLowerCase()}…`}
            />
          </div>
        )}

        <section className="project-videos">
          {videos.length === 0 && projectIngests.length === 0 ? (
            <div className="empty">
              no videos yet. click <span className="accent">add videos</span> to
              import from a playlist or paste urls.
            </div>
          ) : (() => {
            const q = tokenize(search);
            const matches = q
              ? videos.filter((v) => {
                  const summary = summaryById.get(v.id);
                  const hay = [
                    summary?.title ?? v.title ?? "",
                    summary?.channel ?? v.channel ?? "",
                  ].map(tokenize).join(" ");
                  return hay.includes(q);
                })
              : videos;
            if (matches.length === 0) {
              return (
                <div className="empty">
                  no videos match &ldquo;{search}&rdquo;.
                </div>
              );
            }
            return matches.map((v) => (
              <div key={v.id} className="project-video-row">
                <VideoRow v={entryToSummary(v, summaryById.get(v.id))} />
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
            ));
          })()}
        </section>
      </div>

      <AddVideosModal
        open={addVideosOpen}
        projectId={projectId}
        onClose={() => setAddVideosOpen(false)}
        onSuccess={handleAddSuccess}
      />

      <ConfirmDialog
        open={confirmDelete}
        title="delete project?"
        body={
          <>
            <strong>{project.name}</strong> will be removed. videos themselves
            are not affected &mdash; only the project grouping goes away.
          </>
        }
        confirmLabel="delete"
        destructive
        busy={deleteBusy}
        onCancel={() => setConfirmDelete(false)}
        onConfirm={handleDelete}
      />

      <Toast
        open={toast.open}
        kind={toast.kind}
        message={toast.message}
        onClose={() => setToast((t) => ({ ...t, open: false }))}
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

function PlusIcon() {
  return (
    <svg
      width="13"
      height="13"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      strokeLinecap="round"
    >
      <path d="M12 5v14M5 12h14" />
    </svg>
  );
}
