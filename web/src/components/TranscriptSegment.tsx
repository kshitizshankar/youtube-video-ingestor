import type { CheckScreenMark, Segment } from "../types";

export interface TranscriptSegmentProps {
  seg: Segment;
  toneClass: string;       // s1 / s2 / s3 / s4
  speakerLabel: string;    // displayable speaker name
  initials: string;        // 2-char initials shown in avatar
  current: boolean;
  onSeek: (s: number) => void;
  /** All marks belonging to this segment. Drives both the indicator state
   *  and the accept/dismiss affordances. Optional — omitting it preserves
   *  the legacy unmarked rendering. */
  marks?: CheckScreenMark[];
  /** Called when the user clicks the eye button. If the segment has at
   *  least one active user-owned mark we toggle it off; otherwise a new
   *  mark is created. Optional — hides the button when not provided. */
  onToggleMark?: (seg: Segment) => void;
  /** Accept a codex-suggested mark — upgrades kind → user_confirmed. */
  onAcceptMark?: (mark: CheckScreenMark) => void;
  /** Dismiss a codex-suggested mark — upgrades kind → dismissed. */
  onDismissMark?: (mark: CheckScreenMark) => void;
}

function fmtTs(sec: number): string {
  const h = Math.floor(sec / 3600);
  const m = Math.floor((sec % 3600) / 60);
  const s = Math.floor(sec % 60);
  return h
    ? `${h}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`
    : `${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
}

/** Is this mark currently "active" i.e. visible in the UI as a mark.
 *  dismissed suggestions are tombstones — they exist but don't render. */
function isActive(m: CheckScreenMark): boolean {
  return m.kind !== "dismissed";
}

export default function TranscriptSegment({
  seg, toneClass, speakerLabel, initials, current, onSeek,
  marks = [], onToggleMark, onAcceptMark, onDismissMark,
}: TranscriptSegmentProps) {
  const activeMarks = marks.filter(isActive);
  // An unconfirmed codex suggestion is the one state that gets the
  // accept/dismiss affordance. Everything else uses the plain "marked" treatment.
  const codexSuggestion = activeMarks.find((m) => m.kind === "codex_suggested");
  const hasMark = activeMarks.length > 0;
  const hasUserMark = activeMarks.some(
    (m) => m.kind === "user_marked" || m.kind === "user_confirmed",
  );

  const classes = [
    "tr-segment",
    current ? "current" : "",
    hasMark ? "has-mark" : "",
    codexSuggestion && !hasUserMark ? "mark-codex-suggested" : "",
  ]
    .filter(Boolean)
    .join(" ");

  return (
    <div className={classes}>
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
          {onToggleMark && (
            <button
              type="button"
              className={`tr-mark-btn ${hasUserMark ? "is-marked" : ""} ${codexSuggestion && !hasUserMark ? "is-suggested" : ""}`}
              title={
                hasUserMark
                  ? "Remove check-screen mark"
                  : codexSuggestion
                    ? "AI-suggested: review below"
                    : "Mark as check screen here"
              }
              aria-pressed={hasMark}
              onClick={(e) => {
                e.stopPropagation();
                onToggleMark(seg);
              }}
            >
              <span aria-hidden>{"\u{1F441}"}</span>
            </button>
          )}
        </div>
        <div className="tr-text" onClick={() => onSeek(seg.start)} role="button">
          {seg.text}
        </div>
        {codexSuggestion && !hasUserMark && (
          <div className="tr-mark-suggested">
            <div className="tr-mark-meta">
              {codexSuggestion.priority && (
                <span className={`tr-mark-priority p-${codexSuggestion.priority}`}>
                  {codexSuggestion.priority}
                </span>
              )}
              {codexSuggestion.signal && (
                <span className="tr-mark-signal">{codexSuggestion.signal}</span>
              )}
            </div>
            {codexSuggestion.what_i_expect_to_see && (
              <div className="tr-mark-expect">
                {codexSuggestion.what_i_expect_to_see}
              </div>
            )}
            <div className="tr-mark-actions">
              {onAcceptMark && (
                <button
                  type="button"
                  className="tr-mark-accept"
                  onClick={(e) => {
                    e.stopPropagation();
                    onAcceptMark(codexSuggestion);
                  }}
                >
                  Accept
                </button>
              )}
              {onDismissMark && (
                <button
                  type="button"
                  className="tr-mark-dismiss"
                  onClick={(e) => {
                    e.stopPropagation();
                    onDismissMark(codexSuggestion);
                  }}
                >
                  Dismiss
                </button>
              )}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
