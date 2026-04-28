import { Link } from "react-router-dom";
import { formatCount, formatDuration, formatYtDate } from "../format";
import type { Project, TranscriptSummary } from "../types";

export interface VideoRowProps {
  v: TranscriptSummary;
  highlight?: boolean;
  /** When provided, renders an Archive (or Restore) icon button at the right. */
  onArchiveToggle?: (id: string) => void;
  archiveLabel?: string;
  /** Optional extra right-side action (e.g. Delete on Archive page). */
  onDelete?: (id: string) => void;
  /** Look-up of project_id → Project for rendering badges. Pass an empty
   *  map (the default) to suppress badges. */
  projectsById?: Map<string, Project>;
}

export default function VideoRow({
  v, highlight, onArchiveToggle, archiveLabel = "Archive", onDelete, projectsById,
}: VideoRowProps) {
  function handleArchive(e: React.MouseEvent) {
    e.preventDefault(); e.stopPropagation();
    onArchiveToggle?.(v.id);
  }
  function handleDelete(e: React.MouseEvent) {
    e.preventDefault(); e.stopPropagation();
    onDelete?.(v.id);
  }
  const uploadDate = formatYtDate(v.upload_date);
  const views = formatCount(v.view_count);
  const likes = formatCount(v.like_count);
  const sp = v.speaker_count ?? 0;
  // Source-aware thumbnail: podcasts persist `image_url`; YouTube derives
  // from the video id. Anything else (e.g. a podcast row missing image_url)
  // renders a gradient placeholder rather than a broken <img>.
  const isPodcast = v.source === "podcast";
  const thumb = v.image_url
    || (!isPodcast ? `https://img.youtube.com/vi/${v.id}/hqdefault.jpg` : null);
  const sourceLabel = isPodcast ? "Podcast" : "YouTube";
  return (
    <Link to={`/v/${v.id}`} className={`row ${highlight ? "highlight" : ""}`}>
      <div className="thumb">
        {thumb ? (
          <img src={thumb} alt="" loading="lazy" />
        ) : (
          <div className="thumb-fallback" aria-hidden="true" />
        )}
        <span className="dur">{formatDuration(v.duration_sec) ?? "—"}</span>
      </div>
      <div className="title-cell">
        <div className="title">{v.title}</div>
        <div className="meta">
          {v.channel ? (
            <>
              {isPodcast ? <PodcastGlyph /> : <YTGlyph />}
              <span className="meta-channel">{v.channel}</span>
            </>
          ) : (
            <>
              {isPodcast ? <PodcastGlyph /> : <YTGlyph />}
              <span>{sourceLabel}</span>
            </>
          )}
          {uploadDate && (<><span className="meta-sep">·</span><span>{uploadDate}</span></>)}
          {!isPodcast && views && (<><span className="meta-sep">·</span><span>{views} views</span></>)}
          {!isPodcast && likes && (<><span className="meta-sep">·</span><span>{likes} likes</span></>)}
          {v.language && (
            <>
              <span className="meta-sep">·</span>
              <span className="meta-lang">{v.language.toUpperCase()}</span>
            </>
          )}
        </div>
        {(v.project_ids?.length ?? 0) > 0 && projectsById && (
          <div className="row-projects">
            {v.project_ids!.map((pid) => {
              const p = projectsById.get(pid);
              if (!p) return null;
              return (
                <Link
                  key={pid}
                  to={`/p/${pid}`}
                  className="project-pill"
                  title={p.name}
                  onClick={(e) => e.stopPropagation()}
                >
                  {p.name}
                </Link>
              );
            })}
          </div>
        )}
      </div>
      <div className="speakers-cell">
        {sp >= 2 ? (
          <>
            <span className="mini-avatar" style={{ background: "var(--speaker-1)" }}>S1</span>
            <span className="mini-avatar" style={{ background: "var(--speaker-2)" }}>S2</span>
            {sp > 2 && (
              <span className="mini-avatar" style={{ background: "var(--speaker-3)" }}>
                {sp > 9 ? "9+" : `+${sp - 2}`}
              </span>
            )}
            <span className="extra">{sp}</span>
          </>
        ) : sp === 1 ? (
          <>
            <span className="mini-avatar" style={{ background: "var(--speaker-1)" }}>S1</span>
            <span className="extra">1</span>
          </>
        ) : (
          <span className="extra">—</span>
        )}
      </div>
      <div className="status-cell">
        <span className="status-dot" />
        transcribed
      </div>
      <div className="status-cell" style={{ fontFamily: "var(--ok-font-mono)" }}>
        {v.segment_count} seg
      </div>
      <div className="row-actions">
        {onArchiveToggle && (
          <button
            type="button"
            className="row-action"
            onClick={handleArchive}
            title={archiveLabel}
            aria-label={archiveLabel}
          >
            {archiveLabel === "Restore" ? <RestoreIcon /> : <ArchiveIcon />}
          </button>
        )}
        {onDelete && (
          <button
            type="button"
            className="row-action row-action-danger"
            onClick={handleDelete}
            title="Delete forever"
            aria-label="Delete forever"
          >
            <TrashIcon />
          </button>
        )}
        {!onArchiveToggle && !onDelete && <DotsIcon />}
      </div>
    </Link>
  );
}

function YTGlyph() {
  return (
    <svg width="13" height="13" viewBox="0 0 24 24" fill="#ff0033">
      <path d="M23.498 6.186a3.016 3.016 0 0 0-2.122-2.136C19.505 3.545 12 3.545 12 3.545s-7.505 0-9.377.505A3.017 3.017 0 0 0 .502 6.186C0 8.07 0 12 0 12s0 3.93.502 5.814a3.016 3.016 0 0 0 2.122 2.136c1.871.505 9.376.505 9.376.505s7.505 0 9.377-.505a3.015 3.015 0 0 0 2.122-2.136C24 15.93 24 12 24 12s0-3.93-.502-5.814zM9.545 15.568V8.432L15.818 12l-6.273 3.568z" />
    </svg>
  );
}
function PodcastGlyph() {
  // Microphone — denotes a podcast row. Sized + stroked to match the YT
  // glyph's optical weight in the .meta line.
  return (
    <svg
      className="podcast-glyph"
      width="13"
      height="13"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <rect x="9" y="3" width="6" height="11" rx="3" />
      <path d="M5 11a7 7 0 0 0 14 0" />
      <path d="M12 18v3" />
      <path d="M9 21h6" />
    </svg>
  );
}
function DotsIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
      <circle cx="5" cy="12" r="0.5" /><circle cx="12" cy="12" r="0.5" /><circle cx="19" cy="12" r="0.5" />
    </svg>
  );
}
function ArchiveIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <rect x="3" y="4" width="18" height="5" rx="1" />
      <path d="M5 9v10a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1V9M10 13h4" />
    </svg>
  );
}
function RestoreIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <path d="M3 7V3h4M3 3l7 7M21 17v4h-4M21 21l-7-7" />
      <path d="M3 13a9 9 0 0 0 17 4M21 11a9 9 0 0 0-17-4" />
    </svg>
  );
}
function TrashIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <path d="M3 6h18M8 6V4a1 1 0 0 1 1-1h6a1 1 0 0 1 1 1v2M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6M10 11v6M14 11v6" />
    </svg>
  );
}
