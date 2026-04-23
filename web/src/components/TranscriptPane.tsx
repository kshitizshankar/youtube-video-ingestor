import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import type { Analysis, Segment, Transcript } from "../types";
import AnalysisStatus from "./AnalysisStatus";
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
  /** True when Claude is running (either auto post-ingest or manual regen). */
  analysisBusy?: boolean;
  analysisStartedAt?: number | null;
  analysisPhase?: string;
  analysisTokensIn?: number;
  analysisTokensOut?: number;
  analysisCostUsd?: number;
  /** Slice 3 — when set, this node replaces the legacy AnalysisStatus pill
   *  and also takes over the summary-tab body while no analysis is resolved.
   *  Typically `<AnalysisProgress />`. */
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

export default function TranscriptPane(props: TranscriptPaneProps) {
  const {
    segments, currentTime, followLive, onSeek,
    busy = false, phase = "", startedAt = null, lastEventAt = null, duration = null,
    analysis, analysisLoading, analysisError, onRegenerateAnalysis, regenerating,
    analysisBusy = false, analysisStartedAt = null, analysisPhase = "",
    analysisTokensIn = 0, analysisTokensOut = 0, analysisCostUsd = 0,
    analysisProgressNode,
    transcript = null,
    speakerNames = {},
  } = props;

  const [tab, setTab] = useState<Tab>("transcript");
  const lastSegEnd = segments.length > 0 ? segments[segments.length - 1].end : 0;

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
            <input placeholder="Search in transcript…" />
            <span className="kbd">Ctrl F</span>
          </div>
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
        {analysisProgressNode ?? (
          <AnalysisStatus
            running={analysisBusy}
            startedAt={analysisStartedAt}
            phase={analysisPhase}
            error={analysisError}
            tokensIn={analysisTokensIn}
            tokensOut={analysisTokensOut}
            costUsd={analysisCostUsd}
          />
        )}
      </div>

      {tab === "transcript" && (
        <div className="transcript-body" ref={bodyRef}>
          {segments.length === 0 && (
            <div className="tr-loading">
              <span className="dot" /> Waiting for transcript…
            </div>
          )}
          {segments.map((seg, i) => {
            const speaker = seg.speaker ?? "_";
            const tone = speakerTone.get(speaker) ?? "s1";
            const isActive = i === activeIdx;
            return (
              <div key={seg.id} ref={isActive ? activeRef : undefined}>
                <TranscriptSegment
                  seg={seg}
                  toneClass={tone}
                  speakerLabel={seg.speaker ? speakerDisplay(seg.speaker, speakerNames[seg.speaker]) : "—"}
                  initials={seg.speaker ? speakerInitials(seg.speaker, speakerNames[seg.speaker]) : "·"}
                  current={isActive}
                  onSeek={onSeek}
                />
              </div>
            );
          })}
        </div>
      )}

      {tab === "summary" && (
        <div className="transcript-body">
          {analysisProgressNode && !analysis ? (
            <div className="analysis-progress-wrap">{analysisProgressNode}</div>
          ) : (
            <SummaryView
              analysis={analysis}
              loading={analysisLoading}
              error={analysisError}
              onRegenerate={onRegenerateAnalysis}
              regenerating={regenerating}
            />
          )}
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
