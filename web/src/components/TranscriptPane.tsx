import { useEffect, useMemo, useRef } from "react";
import type { Segment } from "../types";
import IngestStatus from "./IngestStatus";
import TranscriptSegment from "./TranscriptSegment";

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
}

const TONES = ["s1", "s2", "s3", "s4"] as const;

function speakerInitials(name: string): string {
  // SPEAKER_00 → S0, SPEAKER_01 → S1, otherwise first two letters.
  const m = name.match(/SPEAKER_(\d+)/i);
  if (m) return `S${parseInt(m[1], 10)}`;
  return name.slice(0, 2).toUpperCase();
}

function speakerDisplay(name: string): string {
  const m = name.match(/SPEAKER_(\d+)/i);
  if (m) return `Speaker ${parseInt(m[1], 10) + 1}`;
  return name;
}

function findActiveIndex(segments: Segment[], t: number): number {
  let lo = 0, hi = segments.length - 1, ans = -1;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    if (segments[mid].start <= t) {
      ans = mid;
      lo = mid + 1;
    } else {
      hi = mid - 1;
    }
  }
  return ans;
}

export default function TranscriptPane({
  segments, currentTime, followLive, onSeek,
  busy = false, phase = "", startedAt = null, lastEventAt = null, duration = null,
}: TranscriptPaneProps) {
  const lastSegEnd = segments.length > 0 ? segments[segments.length - 1].end : 0;
  // Map every distinct speaker to a tone class, in encounter order.
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
    if (followLive && bodyRef.current) {
      bodyRef.current.scrollTop = bodyRef.current.scrollHeight;
    }
  }, [segments.length, followLive]);

  useEffect(() => {
    if (followLive) return;
    if (activeIdx < 0) return;
    activeRef.current?.scrollIntoView({ block: "center", behavior: "smooth" });
  }, [activeIdx, followLive]);

  return (
    <section className="transcript-pane">
      <div className="transcript-top">
        <div className="transcript-tabs">
          <button className="tr-tab active">Transcript</button>
          <button className="tr-tab" disabled style={{ opacity: 0.3 }}>Summary</button>
          <button className="tr-tab" disabled style={{ opacity: 0.3 }}>Highlights</button>
          <button className="tr-tab" disabled style={{ opacity: 0.3 }}>Chapters</button>
        </div>
        <div className="tr-search">
          <SearchIcon />
          <input placeholder="Search in transcript…" />
          <span className="kbd">Ctrl F</span>
        </div>
        <IngestStatus
          busy={busy}
          phase={phase}
          startedAt={startedAt}
          lastEventAt={lastEventAt}
          segmentCount={segments.length}
          lastSegmentEnd={lastSegEnd}
          duration={duration}
        />
      </div>
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
                speakerLabel={seg.speaker ? speakerDisplay(seg.speaker) : "—"}
                initials={seg.speaker ? speakerInitials(seg.speaker) : "·"}
                current={isActive}
                onSeek={onSeek}
              />
            </div>
          );
        })}
      </div>
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
