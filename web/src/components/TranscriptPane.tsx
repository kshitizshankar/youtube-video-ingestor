import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { createMark, deleteMark, listMarks, updateMark } from "../api";
import type { Analysis, CheckScreenMark, Segment, Transcript } from "../types";
import ChaptersView from "./ChaptersView";
import CostsView from "./CostsView";
import HighlightsView from "./HighlightsView";
import IngestStatus from "./IngestStatus";
import SummaryView from "./SummaryView";
import TranscriptSegment from "./TranscriptSegment";

export type Tab = "transcript" | "summary" | "highlights" | "chapters" | "costs";

export interface TranscriptPaneProps {
  segments: Segment[];
  currentTime: number;
  followLive: boolean;
  onSeek: (s: number) => void;
  // Live ingest feedback
  busy?: boolean;
  phase?: string;
  startedAt?: number | null;
  lastEventAt?: number | null;
  duration?: number | null;
  // Analysis (summary / highlights / chapters)
  analysis: Analysis | null;
  analysisLoading: boolean;
  analysisError?: string | null;
  onRegenerateAnalysis?: () => void;
  regenerating?: boolean;
  /** Full transcript object used by the AI Costs observability tab. */
  transcript?: Transcript | null;
  /** When non-null, the live AnalysisProgress surface from Detail. Rendered
   *  exactly once — its own SSE subscription is the single thing that kicks
   *  off the backend analysis worker. */
  analysisProgressNode?: ReactNode;
  /** Map of SPEAKER_XX → user-chosen name, applied everywhere speakers show. */
  speakerNames?: Record<string, string>;
}

const TONES = ["s1", "s2", "s3", "s4"] as const;

function speakerInitials(name: string, override?: string): string {
  const src = override && override.trim() ? override.trim() : name;
  // Use first letter(s) of each word up to 2 chars.
  const m = src.match(/\b\w/g);
  if (m && m.length > 0) return m.slice(0, 2).join("").toUpperCase();
  const s = name.match(/SPEAKER_(\d+)/i);
  if (s) return `S${parseInt(s[1], 10)}`;
  return name.slice(0, 2).toUpperCase();
}

function speakerDisplay(name: string, override?: string): string {
  if (override && override.trim()) return override.trim();
  const m = name.match(/SPEAKER_(\d+)/i);
  if (m) return `Speaker ${parseInt(m[1], 10) + 1}`;
  return name;
}

function findActiveIndex(segments: Segment[], t: number): number {
  let lo = 0, hi = segments.length - 1, ans = -1;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    if (segments[mid].start <= t) { ans = mid; lo = mid + 1; }
    else { hi = mid - 1; }
  }
  return ans;
}

/** Given a segment, find the marks it owns. A mark "belongs to" a segment if
 *  its segment_id matches; otherwise by t_sec falling inside [start, end).
 *  We precompute a per-segment-id map and a sorted list so lookups are O(log n). */
function groupMarksBySegment(
  segments: Segment[],
  marks: CheckScreenMark[],
): Map<number, CheckScreenMark[]> {
  const byId = new Map<number, CheckScreenMark[]>();
  const leftover: CheckScreenMark[] = [];
  const idSet = new Set(segments.map((s) => s.id));
  for (const m of marks) {
    if (m.segment_id != null && idSet.has(m.segment_id)) {
      const arr = byId.get(m.segment_id) ?? [];
      arr.push(m);
      byId.set(m.segment_id, arr);
    } else {
      leftover.push(m);
    }
  }
  // For any mark whose segment_id was null or stale, fall back to
  // timestamp-range matching. Small N; linear scan per segment is fine.
  for (const m of leftover) {
    const seg = segments.find(
      (s) => m.t_sec >= s.start && m.t_sec < s.end,
    );
    if (!seg) continue;
    const arr = byId.get(seg.id) ?? [];
    arr.push(m);
    byId.set(seg.id, arr);
  }
  return byId;
}

export default function TranscriptPane(props: TranscriptPaneProps) {
  const {
    segments, currentTime, followLive, onSeek,
    busy = false, phase = "", startedAt = null, lastEventAt = null, duration = null,
    analysis, analysisLoading, analysisError, onRegenerateAnalysis, regenerating,
    analysisProgressNode,
    transcript = null,
    speakerNames = {},
  } = props;

  const videoId = transcript?.id ?? null;
  const [tab, setTab] = useState<Tab>("transcript");
  const [marks, setMarks] = useState<CheckScreenMark[]>([]);
  const [onlyMarked, setOnlyMarked] = useState(false);
  const [searchQuery, setSearchQuery] = useState("");
  const lastSegEnd = segments.length > 0 ? segments[segments.length - 1].end : 0;

  // Fetch marks whenever the video id changes. Resets local state so stale
  // marks from the previous video don't bleed into the next render.
  useEffect(() => {
    if (!videoId) {
      setMarks([]);
      return;
    }
    let cancelled = false;
    listMarks(videoId, { activeOnly: true })
      .then((ms) => {
        if (!cancelled) setMarks(ms);
      })
      .catch(() => {
        if (!cancelled) setMarks([]);
      });
    return () => { cancelled = true; };
  }, [videoId]);

  // Refetch when a fresh analysis completes — it may have just written new
  // codex_suggested marks. `regenerating` is truthy during the run; when it
  // flips back to false we're clear to pull.
  const prevRegen = useRef(regenerating);
  useEffect(() => {
    if (!videoId) return;
    if (prevRegen.current && !regenerating) {
      listMarks(videoId, { activeOnly: true })
        .then(setMarks)
        .catch(() => { /* keep stale state */ });
    }
    prevRegen.current = regenerating;
  }, [regenerating, videoId]);

  const marksBySegment = useMemo(
    () => groupMarksBySegment(segments, marks),
    [segments, marks],
  );

  const markedSegmentCount = marksBySegment.size;

  const speakerTone = useMemo(() => {
    const m = new Map<string, string>();
    let i = 0;
    for (const s of segments) {
      const key = s.speaker ?? "_";
      if (!m.has(key)) {
        m.set(key, TONES[i % TONES.length]);
        i++;
      }
    }
    return m;
  }, [segments]);

  const activeIdx = useMemo(
    () => findActiveIndex(segments, currentTime),
    [segments, currentTime],
  );

  const bodyRef = useRef<HTMLDivElement>(null);
  const activeRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (followLive && bodyRef.current && tab === "transcript") {
      bodyRef.current.scrollTop = bodyRef.current.scrollHeight;
    }
  }, [segments.length, followLive, tab]);

  useEffect(() => {
    if (tab !== "transcript" || followLive) return;
    if (activeIdx < 0) return;
    activeRef.current?.scrollIntoView({ block: "center", behavior: "smooth" });
  }, [activeIdx, followLive, tab]);

  // ---- mark mutation handlers (optimistic, rollback on error) ----

  const handleToggleMark = useCallback(
    (seg: Segment) => {
      if (!videoId) return;
      const existing = marksBySegment.get(seg.id) ?? [];
      // Prefer removing the user's own mark; leave codex suggestions alone
      // (they have their own accept/dismiss affordance).
      const userOwned = existing.find(
        (m) => m.kind === "user_marked" || m.kind === "user_confirmed",
      );
      if (userOwned) {
        const snapshot = marks;
        setMarks((cur) => cur.filter((m) => m.id !== userOwned.id));
        deleteMark(videoId, userOwned.id).catch(() => setMarks(snapshot));
        return;
      }
      // No user mark yet — create one. Optimistically add a placeholder row
      // with a negative id so we can remove-by-id on failure; swap with the
      // real row when the POST resolves.
      const tempId = -Date.now();
      const now = new Date().toISOString();
      const placeholder: CheckScreenMark = {
        id: tempId,
        video_id: videoId,
        t_sec: seg.start,
        segment_id: seg.id,
        kind: "user_marked",
        trigger_text: null,
        signal: null,
        what_i_expect_to_see: null,
        priority: null,
        note: null,
        analysis_id: null,
        created_at: now,
        updated_at: now,
      };
      setMarks((cur) => [...cur, placeholder]);
      createMark(videoId, { t_sec: seg.start, segment_id: seg.id })
        .then((real) => {
          setMarks((cur) => cur.map((m) => (m.id === tempId ? real : m)));
        })
        .catch(() => {
          setMarks((cur) => cur.filter((m) => m.id !== tempId));
        });
    },
    [videoId, marksBySegment, marks],
  );

  const handleAcceptMark = useCallback(
    (mark: CheckScreenMark) => {
      if (!videoId) return;
      const snapshot = marks;
      setMarks((cur) =>
        cur.map((m) => (m.id === mark.id ? { ...m, kind: "user_confirmed" } : m)),
      );
      updateMark(videoId, mark.id, { kind: "user_confirmed" }).catch(() =>
        setMarks(snapshot),
      );
    },
    [videoId, marks],
  );

  const handleDismissMark = useCallback(
    (mark: CheckScreenMark) => {
      if (!videoId) return;
      const snapshot = marks;
      // Dismiss = filter out locally (activeOnly loads already skip dismissed).
      setMarks((cur) => cur.filter((m) => m.id !== mark.id));
      updateMark(videoId, mark.id, { kind: "dismissed" }).catch(() =>
        setMarks(snapshot),
      );
    },
    [videoId, marks],
  );

  const visibleSegments = useMemo(() => {
    const needle = searchQuery.trim().toLowerCase();
    let out = segments;
    if (onlyMarked) out = out.filter((s) => marksBySegment.has(s.id));
    if (needle.length >= 2) out = out.filter((s) => s.text.toLowerCase().includes(needle));
    return out;
  }, [segments, onlyMarked, marksBySegment, searchQuery]);

  const TabButton = ({ id, label, count }: { id: Tab; label: string; count?: number }) => (
    <button
      className={`tr-tab ${tab === id ? "active" : ""}`}
      onClick={() => setTab(id)}
    >
      {label}
      {typeof count === "number" && count > 0 && (
        <span style={{ fontFamily: "var(--font-mono)", fontSize: 10, marginLeft: 4 }}>{count}</span>
      )}
    </button>
  );

  return (
    <section className="transcript-pane">
      <div className="transcript-top">
        <div className="transcript-tabs">
          <TabButton id="transcript" label="Transcript" />
          <TabButton id="summary" label="Summary" />
          <TabButton id="highlights" label="Highlights" count={analysis?.highlights.length} />
          <TabButton id="chapters" label="Chapters" count={analysis?.chapters.length} />
          <TabButton id="costs" label="AI Costs" />
        </div>
        {tab === "transcript" && (
          <div className="tr-search">
            <SearchIcon />
            <input
              placeholder="Search in transcript…"
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
            />
            {searchQuery && (
              <button
                type="button"
                className="tr-search-clear"
                onClick={() => setSearchQuery("")}
                aria-label="Clear search"
              >
                ×
              </button>
            )}
          </div>
        )}
        {tab === "transcript" && videoId && markedSegmentCount > 0 && (
          <button
            type="button"
            className={`tr-marked-filter ${onlyMarked ? "active" : ""}`}
            onClick={() => setOnlyMarked((v) => !v)}
            title={onlyMarked ? "Show all segments" : "Show only marked segments"}
          >
            <span aria-hidden>{"\u{1F441}"}</span>{" "}
            Show only marked ({markedSegmentCount})
          </button>
        )}
        <IngestStatus
          busy={busy}
          phase={phase}
          startedAt={startedAt}
          lastEventAt={lastEventAt}
          segmentCount={segments.length}
          lastSegmentEnd={lastSegEnd}
          duration={duration}
        />
        {analysisProgressNode}
      </div>

      {tab === "transcript" && (
        <div className="transcript-body" ref={bodyRef}>
          {segments.length === 0 && (
            <div className="tr-loading">
              <span className="dot" /> Waiting for transcript…
            </div>
          )}
          {visibleSegments.length === 0 && segments.length > 0 && (
            <div className="tr-loading">
              <span className="dot" />{" "}
              {searchQuery.trim().length >= 2
                ? `No segments matching "${searchQuery.trim()}".`
                : onlyMarked
                  ? "No marked segments yet."
                  : "No segments to show."}
            </div>
          )}
          {visibleSegments.map((seg) => {
            const i = segments.indexOf(seg);
            const speaker = seg.speaker ?? "_";
            const tone = speakerTone.get(speaker) ?? "s1";
            const isActive = i === activeIdx;
            const segMarks = marksBySegment.get(seg.id) ?? [];
            return (
              <div key={seg.id} ref={isActive ? activeRef : undefined}>
                <TranscriptSegment
                  seg={seg}
                  toneClass={tone}
                  speakerLabel={seg.speaker ? speakerDisplay(seg.speaker, speakerNames[seg.speaker]) : "—"}
                  initials={seg.speaker ? speakerInitials(seg.speaker, speakerNames[seg.speaker]) : "·"}
                  current={isActive}
                  onSeek={onSeek}
                  marks={segMarks}
                  onToggleMark={videoId ? handleToggleMark : undefined}
                  onAcceptMark={videoId ? handleAcceptMark : undefined}
                  onDismissMark={videoId ? handleDismissMark : undefined}
                />
              </div>
            );
          })}
        </div>
      )}

      {tab === "summary" && (
        <div className="transcript-body">
          <SummaryView
            analysis={analysis}
            loading={analysisLoading}
            error={analysisError}
            onRegenerate={onRegenerateAnalysis}
            regenerating={regenerating}
          />
        </div>
      )}

      {tab === "highlights" && (
        <div className="transcript-body">
          <HighlightsView
            analysis={analysis}
            loading={analysisLoading}
            onSeek={onSeek}
            speakerNames={speakerNames}
          />
        </div>
      )}

      {tab === "chapters" && (
        <div className="transcript-body">
          <ChaptersView
            analysis={analysis}
            loading={analysisLoading}
            currentTime={currentTime}
            onSeek={onSeek}
          />
        </div>
      )}

      {tab === "costs" && (
        <div className="transcript-body">
          <CostsView
            transcript={transcript}
            analysis={analysis}
            onRegenerate={onRegenerateAnalysis}
            regenerating={regenerating}
          />
        </div>
      )}
    </section>
  );
}

function SearchIcon() {
  return (
    <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <circle cx="11" cy="11" r="7" /><path d="m21 21-4.3-4.3" />
    </svg>
  );
}
