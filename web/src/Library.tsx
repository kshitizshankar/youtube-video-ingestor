import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import {
  archiveVideo,
  type IngestState,
  listIngests,
  listTranscripts,
  type SearchHit,
  searchTranscripts,
} from "./api";
import { useProjects } from "./ProjectsContext";
import IngestStrip from "./components/IngestStrip";
import SearchBar from "./components/SearchBar";
import SearchResults from "./components/SearchResults";
import TopBar from "./components/TopBar";
import VideoRow from "./components/VideoRow";
import type { TranscriptSummary } from "./types";

type SortKey = "recent" | "duration" | "title" | "channel";

const SORT_LABELS: Record<SortKey, string> = {
  recent: "recently added",
  duration: "longest first",
  title: "title A-Z",
  channel: "channel A-Z",
};

export interface LibraryProps {
  onAdd: () => void;
  onMenuToggle?: () => void;
  refreshKey?: number;
}

function totalHours(items: TranscriptSummary[]): string {
  const total = items.reduce((acc, t) => acc + (t.duration_sec || 0), 0);
  const h = total / 3600;
  if (h >= 10) return `${Math.round(h)}h`;
  return `${h.toFixed(1)}h`;
}

export default function Library({ onAdd, onMenuToggle, refreshKey }: LibraryProps) {
  const [items, setItems] = useState<TranscriptSummary[]>([]);
  const [filter, setFilter] = useState<"all" | "diarized">("all");
  const [ingests, setIngests] = useState<IngestState[]>([]);
  const [query, setQuery] = useState("");
  const [hits, setHits] = useState<SearchHit[]>([]);
  const [searching, setSearching] = useState(false);
  const [searchErr, setSearchErr] = useState<string | null>(null);
  const [searchTruncated, setSearchTruncated] = useState(false);
  const [searchElapsedMs, setSearchElapsedMs] = useState<number | null>(null);
  const [sortKey, setSortKey] = useState<SortKey>("recent");
  const [sortOpen, setSortOpen] = useState(false);
  const searchGenRef = useRef(0);

  // Project list comes from the global context — when it gets renamed
  // anywhere in the app, the chip strip updates automatically.
  const { projects: ctxProjects, projectsById } = useProjects();
  const projects = ctxProjects ?? [];

  // Project filter is URL-driven so deep links from Project pages preselect it.
  // Empty / missing => "all projects".
  const [searchParams, setSearchParams] = useSearchParams();
  const selectedProjectId = searchParams.get("project") || null;
  const setSelectedProject = useCallback(
    (pid: string | null) => {
      const next = new URLSearchParams(searchParams);
      if (pid) next.set("project", pid);
      else next.delete("project");
      setSearchParams(next, { replace: true });
    },
    [searchParams, setSearchParams],
  );

  useEffect(() => {
    listTranscripts().then(setItems).catch(console.error);
  }, [refreshKey]);

  // If a previously-selected project disappears (deleted in another tab),
  // silently fall back to "all".
  useEffect(() => {
    if (selectedProjectId && ctxProjects && !projectsById.has(selectedProjectId)) {
      setSelectedProject(null);
    }
  }, [selectedProjectId, ctxProjects, projectsById, setSelectedProject]);

  // Poll active ingests so we can show a "currently ingesting" strip.
  useEffect(() => {
    let cancelled = false;
    const tick = async () => {
      try {
        const data = await listIngests();
        if (cancelled) return;
        setIngests(data);
        // When an ingest transitions to done, bump the transcripts list too
        // so the new row appears without a manual refresh.
        if (data.some((i) => i.done)) {
          listTranscripts().then((v) => { if (!cancelled) setItems(v); }).catch(() => {});
        }
      } catch { /* swallow */ }
    };
    tick();
    const id = window.setInterval(tick, 2500);
    return () => { cancelled = true; clearInterval(id); };
  }, []);

  const projectScoped = useMemo(() => {
    if (!selectedProjectId) return items;
    return items.filter((v) => (v.project_ids ?? []).includes(selectedProjectId));
  }, [items, selectedProjectId]);

  const visible = useMemo(() => {
    let out = projectScoped;
    if (filter === "diarized") out = out.filter((v) => v.diarized);
    const sorted = [...out];
    switch (sortKey) {
      case "duration":
        sorted.sort((a, b) => (b.duration_sec ?? 0) - (a.duration_sec ?? 0));
        break;
      case "title":
        sorted.sort((a, b) => a.title.localeCompare(b.title));
        break;
      case "channel":
        sorted.sort((a, b) => (a.channel ?? "").localeCompare(b.channel ?? ""));
        break;
      case "recent":
      default:
        // Server already returns videos in created_at DESC order; keep that.
        break;
    }
    return sorted;
  }, [projectScoped, filter, sortKey]);

  const segCount = items.reduce((acc, v) => acc + (v.segment_count || 0), 0);

  const handleArchive = useCallback(async (id: string) => {
    try {
      await archiveVideo(id);
      setItems((prev) => prev.filter((v) => v.id !== id));
    } catch (e) {
      alert(`Archive failed: ${e}`);
    }
  }, []);

  // Debounced search across all transcripts.
  useEffect(() => {
    const trimmed = query.trim();
    if (trimmed.length < 2) {
      setHits([]);
      setSearchErr(null);
      setSearching(false);
      setSearchTruncated(false);
      setSearchElapsedMs(null);
      return;
    }
    const gen = ++searchGenRef.current;
    setSearching(true);
    setSearchErr(null);
    const t0 = performance.now();
    const timer = window.setTimeout(async () => {
      try {
        const resp = await searchTranscripts(trimmed);
        if (searchGenRef.current !== gen) return; // stale
        setHits(resp.results);
        setSearchTruncated(resp.truncated);
        setSearchElapsedMs(Math.round(performance.now() - t0));
      } catch (e) {
        if (searchGenRef.current !== gen) return;
        setSearchErr(String(e));
      } finally {
        if (searchGenRef.current === gen) setSearching(false);
      }
    }, 220);
    return () => clearTimeout(timer);
  }, [query]);

  const inSearchMode = query.trim().length >= 2;

  return (
    <div className="main">
      <TopBar
        title="library"
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
          <button className="btn btn-accent" onClick={onAdd}>
            <PlusIcon /> add video
          </button>
        }
      />
      <div className="library">
        {ingests.length > 0 && (
          <IngestStrip ingests={ingests} />
        )}
        <div className="library-hero">
          <div>
            <h2>
              your library<em>.</em>
            </h2>
            <div className="sub">
              {items.length} videos transcribed. {totalHours(items)} of material.{" "}
              {segCount.toLocaleString()} segments. paste a youtube url and it&rsquo;ll
              be ready to chat with in a few minutes &mdash; everything runs locally
              on your gpu.
            </div>
          </div>
          <div className="library-stats">
            <div className="stat">
              <div className="n">{items.length}</div>
              <div className="l">videos</div>
            </div>
            <div className="stat">
              <div className="n">{totalHours(items)}</div>
              <div className="l">material</div>
            </div>
            <div className="stat">
              <div className="n">{items.filter((v) => v.diarized).length}</div>
              <div className="l">diarized</div>
            </div>
            <div className="stat">
              <div className="n">{segCount.toLocaleString()}</div>
              <div className="l">segments</div>
            </div>
          </div>
        </div>

        <div className="library-search">
          <SearchBar value={query} onChange={setQuery} busy={searching} />
        </div>

        {inSearchMode ? (
          <SearchResults
            query={query.trim()}
            hits={hits}
            loading={searching}
            error={searchErr}
            truncated={searchTruncated}
            elapsedMs={searchElapsedMs}
          />
        ) : (
          <>

        <div className="filters">
          <span className="mono-lbl">filter</span>
          <span
            className={`chip ${filter === "all" ? "active" : ""}`}
            onClick={() => setFilter("all")}
          >
            all {projectScoped.length}
          </span>
          <span
            className={`chip ${filter === "diarized" ? "active" : ""}`}
            onClick={() => setFilter("diarized")}
          >
            <SpeakerGlyph /> multi-speaker {projectScoped.filter((v) => v.diarized).length}
          </span>

          <span className="sort-control">
            <button
              type="button"
              className="sort-button"
              onClick={() => setSortOpen((v) => !v)}
              title="Change sort"
            >
              sort: {SORT_LABELS[sortKey]} v
            </button>
            {sortOpen && (
              <div className="sort-menu" role="menu">
                {(Object.keys(SORT_LABELS) as SortKey[]).map((k) => (
                  <button
                    key={k}
                    type="button"
                    className={`sort-menu-item ${k === sortKey ? "active" : ""}`}
                    onClick={() => { setSortKey(k); setSortOpen(false); }}
                    role="menuitem"
                  >
                    {SORT_LABELS[k]}
                  </button>
                ))}
              </div>
            )}
          </span>
        </div>

        {projects.length > 0 && (
          <div className="filters">
            <span className="mono-lbl">project</span>
            <span
              className={`chip ${selectedProjectId === null ? "active" : ""}`}
              onClick={() => setSelectedProject(null)}
            >
              all {items.length}
            </span>
            {projects.map((p) => {
              const count = items.filter((v) => (v.project_ids ?? []).includes(p.id)).length;
              return (
                <span
                  key={p.id}
                  className={`chip ${selectedProjectId === p.id ? "active" : ""}`}
                  onClick={() => setSelectedProject(p.id)}
                  title={p.description || p.name}
                >
                  {p.name} {count}
                </span>
              );
            })}
          </div>
        )}

        <div className="row-head">
          <div></div>
          <div>title</div>
          <div>speakers</div>
          <div>status</div>
          <div>activity</div>
          <div></div>
        </div>

        {visible.length === 0 ? (
          <div className="library-empty">
            {selectedProjectId
              ? "no videos match this project filter yet."
              : <>no videos yet. click <span className="accent">add video</span> to get started.</>}
          </div>
        ) : (
          visible.map((v) => (
            <VideoRow
              key={v.id}
              v={v}
              onArchiveToggle={handleArchive}
              projectsById={projectsById}
            />
          ))
        )}
          </>
        )}
      </div>
    </div>
  );
}

function PlusIcon() {
  return (
    <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round">
      <path d="M12 5v14M5 12h14" />
    </svg>
  );
}
function SpeakerGlyph() {
  return (
    <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M12 2a3 3 0 0 0-3 3v6a3 3 0 1 0 6 0V5a3 3 0 0 0-3-3z" />
      <path d="M19 10v1a7 7 0 0 1-14 0v-1" />
    </svg>
  );
}
function HamburgerIcon() {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round">
      <path d="M3 6h18M3 12h18M3 18h18" />
    </svg>
  );
}

