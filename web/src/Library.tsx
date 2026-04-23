import { useCallback, useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import {
  archiveVideo,
  type IngestState,
  listIngests,
  listTranscripts,
  type SearchHit,
  searchTranscripts,
} from "./api";
import SearchBar from "./components/SearchBar";
import SearchResults from "./components/SearchResults";
import TopBar from "./components/TopBar";
import VideoRow from "./components/VideoRow";
import type { TranscriptSummary } from "./types";

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
  const searchGenRef = useRef(0);

  useEffect(() => {
    listTranscripts().then(setItems).catch(console.error);
  }, [refreshKey]);

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

  const visible = filter === "diarized" ? items.filter((v) => v.diarized) : items;
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
        title="Library"
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
          <>
            <button className="btn hide-on-narrow">
              <FilterIcon /> Filter
            </button>
            <button className="btn btn-primary" onClick={onAdd}>
              <PlusIcon /> Add
            </button>
          </>
        }
      />
      <div className="library">
        {ingests.length > 0 && (
          <IngestStrip ingests={ingests} />
        )}
        <div className="library-hero">
          <div>
            <h2>
              Your library<em>.</em>
            </h2>
            <div className="sub">
              {items.length} videos transcribed · {totalHours(items)} of material ·{" "}
              {segCount.toLocaleString()} segments. Paste a YouTube URL and it'll be ready
              to chat with in a few minutes — everything runs locally on your GPU.
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
          <span style={{ fontFamily: "var(--font-mono)", fontSize: 10.5, textTransform: "uppercase", letterSpacing: 0.6, color: "var(--ink-3)" }}>
            Filter
          </span>
          <span
            className={`chip ${filter === "all" ? "active" : ""}`}
            onClick={() => setFilter("all")}
          >
            All {items.length}
          </span>
          <span
            className={`chip ${filter === "diarized" ? "active" : ""}`}
            onClick={() => setFilter("diarized")}
          >
            <SpeakerGlyph /> Multi-speaker {items.filter((v) => v.diarized).length}
          </span>
          <span className="sort">Sort: Recently added ↓</span>
        </div>

        <div className="row-head">
          <div></div>
          <div>Title</div>
          <div>Speakers</div>
          <div>Status</div>
          <div>Activity</div>
          <div></div>
        </div>

        {visible.length === 0 ? (
          <div className="library-empty">
            No videos yet. Click <span className="accent">Add video</span> to get started.
          </div>
        ) : (
          visible.map((v) => (
            <VideoRow key={v.id} v={v} onArchiveToggle={handleArchive} />
          ))
        )}
          </>
        )}
      </div>
    </div>
  );
}

function FilterIcon() {
  return (
    <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <path d="M3 6h18M7 12h10M10 18h4" />
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

// -----------------------------------------------------------------
// IngestStrip — compact live progress for all in-flight ingests
// -----------------------------------------------------------------

function IngestStrip({ ingests }: { ingests: IngestState[] }) {
  const [now, setNow] = useState(() => Date.now() / 1000);
  useEffect(() => {
    const id = window.setInterval(() => setNow(Date.now() / 1000), 1000);
    return () => clearInterval(id);
  }, []);
  const sorted = [...ingests].sort((a, b) => Number(a.done) - Number(b.done) || b.started_at - a.started_at);
  return (
    <section className="ingest-strip">
      <header>
        <span className="label">In progress</span>
        <span className="count">
          {ingests.filter((i) => !i.done).length} active · {ingests.filter((i) => i.done).length} just finished
        </span>
      </header>
      <div className="ingest-cards">
        {sorted.map((ing) => (
          <IngestCard key={ing.id} ing={ing} now={now} />
        ))}
      </div>
    </section>
  );
}

function IngestCard({ ing, now }: { ing: IngestState; now: number }) {
  const thumb = `https://img.youtube.com/vi/${ing.id}/hqdefault.jpg`;
  const elapsedSec = Math.max(0, now - ing.started_at);
  const staleSec = Math.max(0, now - ing.last_event_at);
  const pct = ing.duration_sec && ing.duration_sec > 0
    ? Math.min(100, (ing.last_segment_end / ing.duration_sec) * 100)
    : 0;
  const stalled = !ing.done && staleSec > 15;
  const cls = ["job-card"];
  if (ing.done) cls.push(ing.error ? "is-error" : "is-done");
  else if (stalled) cls.push("is-stalled");
  else cls.push("is-busy");

  const phaseLabel = ing.error
    ? `Error: ${ing.error}`
    : ing.done
      ? "Done"
      : ing.phase || "working";

  return (
    <Link to={`/v/${ing.id}`} className={cls.join(" ")}>
      <div className="ic-thumb">
        <img src={thumb} alt="" loading="lazy" />
      </div>
      <div className="ic-body">
        <div className="ic-title">{ing.title || ing.id}</div>
        <div className="ic-meta">
          <span className="ic-phase">{phaseLabel}</span>
          <span className="ic-sep">·</span>
          <span>{fmtMinSec(elapsedSec)} elapsed</span>
          {ing.segments > 0 && (<><span className="ic-sep">·</span><span>{ing.segments} seg</span></>)}
          {ing.duration_sec ? (<><span className="ic-sep">·</span><span>{pct.toFixed(0)}%</span></>) : null}
          {stalled && <span className="ic-stall">stalled {Math.floor(staleSec)}s</span>}
        </div>
        <div className="ic-bar"><div className="ic-fill" style={{ width: `${pct}%` }} /></div>
      </div>
    </Link>
  );
}

function fmtMinSec(sec: number): string {
  const s = Math.floor(sec);
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  const rem = s % 60;
  return `${m}m ${String(rem).padStart(2, "0")}s`;
}
