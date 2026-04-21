import { useEffect, useState } from "react";
import { listTranscripts } from "./api";
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

  useEffect(() => {
    listTranscripts().then(setItems).catch(console.error);
  }, [refreshKey]);

  const visible = filter === "diarized" ? items.filter((v) => v.diarized) : items;
  const segCount = items.reduce((acc, v) => acc + (v.segment_count || 0), 0);

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
          visible.map((v) => <VideoRow key={v.id} v={v} />)
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
