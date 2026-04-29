import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import {
  archiveVideo,
  type IngestState,
  listIngests,
  listTranscripts,
  refreshMetadata,
  type SearchHit,
  searchTranscripts,
} from "./api";
import { useProjects } from "./ProjectsContext";
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

/** A row's title is an opaque ID when:
 *  - it equals the row id
 *  - it's empty
 *  - it's a bare alphanumeric/underscore/dash slug with no whitespace,
 *    long enough that it can't reasonably be a real human title.
 *  Used to surface "needs metadata refresh" rows. */
function isOpaqueId(title: string | null | undefined, id: string): boolean {
  if (!title) return true;
  if (title === id) return true;
  if (title.trim().length === 0) return true;
  if (/^[a-zA-Z0-9_-]{10,}$/.test(title) && !/\s/.test(title)) return true;
  return false;
}

function tokenize(s: string): string {
  return s.toLowerCase().normalize("NFKD").replace(/\s+/g, " ").trim();
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
  const [projectMenuOpen, setProjectMenuOpen] = useState(false);
  const [fixBusy, setFixBusy] = useState(false);
  const [fixReport, setFixReport] = useState<string | null>(null);
  const searchGenRef = useRef(0);

  const { projects: ctxProjects, projectsById } = useProjects();
  const projects = ctxProjects ?? [];

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

  useEffect(() => {
    if (selectedProjectId && ctxProjects && !projectsById.has(selectedProjectId)) {
      setSelectedProject(null);
    }
  }, [selectedProjectId, ctxProjects, projectsById, setSelectedProject]);

  // Poll active ingests so the failed-jobs banner stays live without
  // requiring the user to navigate away. The IngestStrip moved to /queue;
  // here we only track counts for the at-a-glance status pill.
  useEffect(() => {
    let cancelled = false;
    const tick = async () => {
      try {
        const data = await listIngests();
        if (cancelled) return;
        setIngests(data);
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
        if (searchGenRef.current !== gen) return;
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

  // Title + channel matches against the local items list. These are computed
  // synchronously on every keystroke so the user sees something the moment
  // they type, even before the server-side transcript search returns.
  const titleMatches = useMemo<TranscriptSummary[]>(() => {
    if (!inSearchMode) return [];
    const q = tokenize(query);
    return items.filter((v) => {
      const hay = [v.title, v.channel ?? ""]
        .map(tokenize)
        .join(" ");
      return hay.includes(q);
    });
  }, [items, query, inSearchMode]);

  // Rows whose title looks like an ID — server-side metadata fetch failed
  // or never ran. The user can bulk-refresh them via the banner.
  const brokenRows = useMemo(
    () => items.filter((v) => isOpaqueId(v.title, v.id)),
    [items],
  );

  const handleFixMetadata = useCallback(async () => {
    if (brokenRows.length === 0 || fixBusy) return;
    setFixBusy(true);
    setFixReport(null);
    const CONCURRENCY = 3;
    let next = 0;
    let ok = 0;
    const errors: string[] = [];
    async function worker() {
      while (true) {
        const idx = next++;
        if (idx >= brokenRows.length) return;
        const r = brokenRows[idx];
        try {
          await refreshMetadata(r.id);
          ok++;
        } catch (e) {
          errors.push(`${r.id}: ${e}`);
        }
      }
    }
    await Promise.all(Array.from({ length: CONCURRENCY }, worker));
    setFixBusy(false);
    setFixReport(
      errors.length === 0
        ? `Fixed ${ok}.`
        : `Fixed ${ok}, failed ${errors.length}. (${errors.slice(0, 2).join("; ")}${errors.length > 2 ? "..." : ""})`,
    );
    // Reload the library list so refreshed titles appear.
    listTranscripts().then(setItems).catch(() => {});
  }, [brokenRows, fixBusy]);

  // Counts for the status banner at the top of the page.
  const activeJobCount = ingests.filter((i) => !i.done).length;
  const failedJobCount = ingests.filter(
    (i) => i.done && i.error && !i.cancel_requested,
  ).length;

  const selectedProject = selectedProjectId
    ? projectsById.get(selectedProjectId) ?? null
    : null;

  // Close the project dropdown on outside click. Cheap document-level
  // listener; only attached while the menu is open.
  useEffect(() => {
    if (!projectMenuOpen) return;
    const onDown = (e: MouseEvent) => {
      const target = e.target as HTMLElement | null;
      if (target?.closest(".filter-pill-menu")) return;
      if (target?.closest(".filter-pill-trigger")) return;
      setProjectMenuOpen(false);
    };
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
  }, [projectMenuOpen]);

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
        {(activeJobCount > 0 || failedJobCount > 0) && (
          <Link
            to="/queue"
            className={`status-banner ${failedJobCount > 0 ? "has-failures" : ""}`}
          >
            <span className="status-banner-dot" aria-hidden />
            <span className="status-banner-text">
              {failedJobCount > 0 && (
                <strong>
                  {failedJobCount} ingest{failedJobCount === 1 ? "" : "s"} failed
                </strong>
              )}
              {failedJobCount > 0 && activeJobCount > 0 && (
                <span className="status-banner-sep"> · </span>
              )}
              {activeJobCount > 0 && (
                <span>
                  {activeJobCount} in progress
                </span>
              )}
            </span>
            <span className="status-banner-cta">
              review queue <ArrowIcon />
            </span>
          </Link>
        )}

        {brokenRows.length > 0 && (
          <div className="broken-banner" role="status">
            <span className="broken-banner-dot" aria-hidden />
            <span className="broken-banner-text">
              <strong>
                {brokenRows.length} video{brokenRows.length === 1 ? "" : "s"}
              </strong>{" "}
              {brokenRows.length === 1 ? "has" : "have"} an ID instead of a title
              {fixReport && (
                <span className="broken-banner-report"> · {fixReport}</span>
              )}
            </span>
            <button
              type="button"
              className="btn btn-primary"
              onClick={handleFixMetadata}
              disabled={fixBusy}
              title="Re-run yt-dlp metadata extraction for each affected video"
            >
              {fixBusy ? "fixing..." : "fix metadata"}
            </button>
          </div>
        )}

        <div className="library-hero library-hero-tight">
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
          <SearchBar
            value={query}
            onChange={setQuery}
            busy={searching}
            placeholder="search titles, channels, transcripts…"
          />
        </div>

        {inSearchMode ? (
          <>
            {titleMatches.length > 0 && (
              <section className="title-matches">
                <header className="title-matches-head">
                  <span className="title-matches-label">title matches</span>
                  <span className="title-matches-count">
                    {titleMatches.length} {titleMatches.length === 1 ? "video" : "videos"}
                  </span>
                </header>
                <div className="row-head row-head-compact">
                  <div></div>
                  <div>title</div>
                  <div>speakers</div>
                  <div>status</div>
                  <div>activity</div>
                  <div></div>
                </div>
                {titleMatches.map((v) => (
                  <VideoRow
                    key={v.id}
                    v={v}
                    onArchiveToggle={handleArchive}
                    projectsById={projectsById}
                  />
                ))}
              </section>
            )}
            <SearchResults
              query={query.trim()}
              hits={hits}
              loading={searching}
              error={searchErr}
              truncated={searchTruncated}
              elapsedMs={searchElapsedMs}
            />
          </>
        ) : (
          <>
            <div className="filters filters-single">
              <div className="filters-left">
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

                {projects.length > 0 && (
                  <div className="filter-pill-wrap">
                    <button
                      type="button"
                      className={`filter-pill-trigger ${selectedProject ? "is-active" : ""}`}
                      onClick={() => setProjectMenuOpen((v) => !v)}
                      title="Filter by project"
                    >
                      <FolderGlyph />
                      <span>
                        {selectedProject ? selectedProject.name : "all projects"}
                      </span>
                      <span className="filter-pill-count">
                        {selectedProject
                          ? items.filter((v) => (v.project_ids ?? []).includes(selectedProject.id)).length
                          : items.length}
                      </span>
                      <ChevronIcon />
                    </button>
                    {projectMenuOpen && (
                      <div className="filter-pill-menu" role="menu">
                        <button
                          type="button"
                          className={`filter-pill-item ${!selectedProject ? "active" : ""}`}
                          onClick={() => {
                            setSelectedProject(null);
                            setProjectMenuOpen(false);
                          }}
                          role="menuitem"
                        >
                          <span>all projects</span>
                          <span className="filter-pill-item-count">{items.length}</span>
                        </button>
                        {projects.map((p) => {
                          const count = items.filter((v) => (v.project_ids ?? []).includes(p.id)).length;
                          return (
                            <button
                              key={p.id}
                              type="button"
                              className={`filter-pill-item ${selectedProjectId === p.id ? "active" : ""}`}
                              onClick={() => {
                                setSelectedProject(p.id);
                                setProjectMenuOpen(false);
                              }}
                              role="menuitem"
                              title={p.description || p.name}
                            >
                              <span>{p.name}</span>
                              <span className="filter-pill-item-count">{count}</span>
                            </button>
                          );
                        })}
                      </div>
                    )}
                  </div>
                )}
              </div>

              <span className="sort-control">
                <button
                  type="button"
                  className="sort-button"
                  onClick={() => setSortOpen((v) => !v)}
                  title="Change sort"
                >
                  sort: {SORT_LABELS[sortKey]} <ChevronIcon />
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
function FolderGlyph() {
  return (
    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z" />
    </svg>
  );
}
function ChevronIcon() {
  return (
    <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round">
      <path d="M6 9l6 6 6-6" />
    </svg>
  );
}
function ArrowIcon() {
  return (
    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M5 12h14M13 6l6 6-6 6" />
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
