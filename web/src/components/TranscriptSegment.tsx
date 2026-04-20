import type { Segment } from "../types";

export interface TranscriptSegmentProps {
  seg: Segment;
  toneClass: string;       // s1 / s2 / s3 / s4
  speakerLabel: string;    // displayable speaker name
  initials: string;        // 2-char initials shown in avatar
  current: boolean;
  onSeek: (s: number) => void;
}

function fmtTs(sec: number): string {
  const h = Math.floor(sec / 3600);
  const m = Math.floor((sec % 3600) / 60);
  const s = Math.floor(sec % 60);
  return h
    ? `${h}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`
    : `${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
}

export default function TranscriptSegment({
  seg, toneClass, speakerLabel, initials, current, onSeek,
}: TranscriptSegmentProps) {
  return (
    <div className={`tr-segment ${current ? "current" : ""}`}>
      <div className={`tr-avatar ${toneClass}`}>
        {initials}
        {current && (
          <>
            <span className="wave" /><span className="wave2" />
          </>
        )}
      </div>
      <div>
        <div className="tr-head">
          <span className={`tr-speaker ${toneClass}`}>{speakerLabel}</span>
          <button className="tr-time" onClick={() => onSeek(seg.start)}>
            {fmtTs(seg.start)}
          </button>
        </div>
        <div className="tr-text" onClick={() => onSeek(seg.start)} role="button">
          {seg.text}
        </div>
      </div>
    </div>
  );
}
