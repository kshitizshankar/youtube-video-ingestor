import { useCallback, useEffect, useRef, useState, type KeyboardEvent } from "react";
import { patchMeta, refreshMetadata } from "../api";
import { useProjects } from "../ProjectsContext";
import type { Segment, VideoMeta } from "../types";

export interface VideoDetailsPanelProps {
  videoId: string;
  /** Discriminator that drives the action row's label/icon — "YouTube URL"
   *  for YouTube videos, "Show URL" for podcasts. Defaulting to "youtube"
   *  preserves legacy behavior for rows without a source field. */
  source?: "youtube" | "podcast";
  /** The canonical human-facing URL for this video. For YouTube this is the
   *  watch URL; for podcasts it's the show page (Spotify / RSS), never the
   *  raw mp3 CDN. May be null for podcasts that didn't carry a show URL. */
  videoUrl: string | null;
  shareUrl: string;          // the Vidan /v/<id> URL
  diarized: boolean;
  segments: Segment[];
  meta: VideoMeta | null;
  onMetaChange: (m: VideoMeta) => void;
  onArchive?: () => void;
  /** Fired when yt-dlp metadata is re-fetched — parent should refetch transcript. */
  onMetadataRefreshed?: () => void;
  /** Current owning project id (folder-per-project). When set, the action
   *  row renders a "Move to project" picker. */
  currentProjectId?: string | null;
  /** Fired after a successful move so the parent can refetch transcript /
   *  refresh state. */
  onMoved?: () => void;
}

export default function VideoDetailsPanel({
  videoId, source = "youtube", videoUrl, shareUrl, diarized, segments,
  meta, onMetaChange, onArchive, onMetadataRefreshed,
  currentProjectId, onMoved,
}: VideoDetailsPanelProps) {
  const detected = useDetectedSpeakers(segments);
  const speakerCount = detected.length;
  return (
    <aside className="details-panel">
      <ActionRow
        videoId={videoId}
        source={source}
        videoUrl={videoUrl}
        shareUrl={shareUrl}
        onArchive={onArchive}
        onMetadataRefreshed={onMetadataRefreshed}
        currentProjectId={currentProjectId ?? null}
        onMoved={onMoved}
      />
      <DetectionRow
        speakerCount={speakerCount}
        diarized={diarized}
      />
      <TagsEditor videoId={videoId} meta={meta} onMetaChange={onMetaChange} />
      {speakerCount > 1 && (
        <SpeakersEditor
          videoId={videoId}
          segments={segments}
          meta={meta}
          onMetaChange={onMetaChange}
        />
      )}
      <NotesEditor videoId={videoId} meta={meta} onMetaChange={onMetaChange} />
    </aside>
  );
}

/* -----------------------------------------------------------------
   Detection row: "N speaker detected" badge
   ----------------------------------------------------------------- */

function DetectionRow({
  speakerCount, diarized,
}: { speakerCount: number; diarized: boolean }) {
  // Diarization hasn't run yet (no speakers in segments, and the transcript
  // JSON says not diarized) — show nothing, it'll populate when ingest completes.
  if (!diarized && speakerCount === 0) return null;

  return (
    <section className="dp-section dp-detection">
      <div className="dp-detection-row">
        <SpeakersGlyph />
        {speakerCount === 0 ? (
          <span className="dp-detection-text">
            <strong>No speakers detected</strong>
            <span className="dp-detection-sub"> · pyannote couldn't identify distinct voices</span>
          </span>
        ) : speakerCount === 1 ? (
          <span className="dp-detection-text">
            <strong>1 speaker</strong>
            <span className="dp-detection-sub"> · single voice throughout</span>
          </span>
        ) : (
          <span className="dp-detection-text">
            <strong>{speakerCount} speakers</strong>
            <span className="dp-detection-sub"> · rename below to replace SPEAKER_0N labels</span>
          </span>
        )}
      </div>
    </section>
  );
}

function SpeakersGlyph() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <circle cx="9" cy="8" r="3.2" />
      <path d="M3 20a6 6 0 0 1 12 0" />
      <path d="M16 11a3 3 0 1 0 0-6" />
      <path d="M18 20a5 5 0 0 0-3-4.6" />
    </svg>
  );
}

/* -----------------------------------------------------------------
   Action row: copy link, open on YouTube, archive
   ----------------------------------------------------------------- */

function ActionRow({
  videoId, source, videoUrl, shareUrl, onArchive, onMetadataRefreshed,
  currentProjectId, onMoved,
}: {
  videoId: string;
  source: "youtube" | "podcast";
  videoUrl: string | null;
  shareUrl: string;
  onArchive?: () => void;
  onMetadataRefreshed?: () => void;
  currentProjectId: string | null;
  onMoved?: () => void;
}) {
  const [copied, setCopied] = useState<"" | "yt" | "share">("");
  const [refreshing, setRefreshing] = useState(false);
  const [moveOpen, setMoveOpen] = useState(false);
  const [moving, setMoving] = useState(false);
  const { projects, projectsById, moveVideoToProject } = useProjects();
  const currentProject = currentProjectId ? projectsById.get(currentProjectId) : null;
  const movePickerRef = useRef<HTMLDivElement>(null);

  // Outside click closes the move-picker dropdown.
  useEffect(() => {
    if (!moveOpen) return;
    const onDown = (e: MouseEvent) => {
      const t = e.target as HTMLElement | null;
      if (movePickerRef.current && t && !movePickerRef.current.contains(t)) {
        setMoveOpen(false);
      }
    };
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
  }, [moveOpen]);

  const handleMove = useCallback(async (target: string) => {
    if (!target || target === currentProjectId) {
      setMoveOpen(false);
      return;
    }
    setMoving(true);
    setMoveOpen(false);
    try {
      await moveVideoToProject(videoId, target);
      onMoved?.();
    } catch (e) {
      alert(`Move failed: ${e}`);
    } finally {
      setMoving(false);
    }
  }, [videoId, currentProjectId, moveVideoToProject, onMoved]);

  // Source-aware labels/icons for the second copy/open buttons. The first
  // button is always "Copy link" (the share URL); the second is the
  // canonical source URL — different per provider.
  const isPodcast = source === "podcast";
  const sourceCopyLabel = isPodcast ? "Show URL" : "YouTube URL";
  const sourceCopyTitle = isPodcast
    ? "Copy the original show URL"
    : "Copy the original YouTube URL";
  const sourceOpenLabel = isPodcast ? "Open show" : "Open";
  const sourceOpenTitle = isPodcast ? "Open the show page" : "Open on YouTube";
  const SourceIcon = isPodcast ? PodcastGlyph : YTIcon;

  const copy = useCallback(async (kind: "yt" | "share", text: string) => {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(kind);
      window.setTimeout(() => setCopied((prev) => (prev === kind ? "" : prev)), 1600);
    } catch {
      // Fallback: create a temp textarea (best-effort)
      const t = document.createElement("textarea");
      t.value = text;
      document.body.appendChild(t);
      t.select();
      try { document.execCommand("copy"); setCopied(kind); } catch { /* give up */ }
      document.body.removeChild(t);
    }
  }, []);

  return (
    <section className="dp-section dp-actions">
      <h5>Actions</h5>
      <div className="dp-action-row">
        <button
          type="button"
          className="dp-action"
          onClick={() => copy("share", shareUrl)}
          title="Copy the Vidan URL for this video"
        >
          {copied === "share" ? <CheckIcon /> : <LinkIcon />}
          <span>{copied === "share" ? "Copied!" : "Copy link"}</span>
        </button>
        {videoUrl && (
          <button
            type="button"
            className="dp-action"
            onClick={() => copy("yt", videoUrl)}
            title={sourceCopyTitle}
          >
            {copied === "yt" ? <CheckIcon /> : <SourceIcon />}
            <span>{copied === "yt" ? "Copied!" : sourceCopyLabel}</span>
          </button>
        )}
        {videoUrl && (
          <a
            className="dp-action"
            href={videoUrl}
            target="_blank"
            rel="noopener noreferrer"
            title={sourceOpenTitle}
          >
            <ExternalIcon /> {sourceOpenLabel}
          </a>
        )}
        <button
          type="button"
          className="dp-action dp-action-muted"
          onClick={async () => {
            setRefreshing(true);
            try {
              await refreshMetadata(videoId);
              onMetadataRefreshed?.();
            } catch (e) {
              alert(`Refresh failed: ${e}`);
            } finally {
              setRefreshing(false);
            }
          }}
          disabled={refreshing}
          title="Re-fetch channel, views, likes, and description from YouTube"
        >
          <RefreshIcon /> {refreshing ? "Refreshing…" : "Refresh info"}
        </button>
        {currentProjectId && (
          <div className="dp-move-wrap" ref={movePickerRef}>
            <button
              type="button"
              className="dp-action dp-action-muted"
              onClick={() => setMoveOpen((v) => !v)}
              disabled={moving}
              title={
                currentProject
                  ? `Currently in: ${currentProject.name}`
                  : "Move to a different project"
              }
            >
              <MoveIcon />{" "}
              {moving
                ? "Moving…"
                : currentProject
                  ? `Project: ${currentProject.name}`
                  : "Move to project"}
            </button>
            {moveOpen && (
              <div className="dp-move-menu" role="menu">
                {(projects ?? [])
                  .filter((p) => p.id !== currentProjectId)
                  .map((p) => (
                    <button
                      key={p.id}
                      type="button"
                      className="dp-move-item"
                      onClick={() => handleMove(p.id)}
                      role="menuitem"
                    >
                      <span>{p.name}</span>
                      {p.system_kind === "inbox" && (
                        <span className="dp-move-tag">system</span>
                      )}
                    </button>
                  ))}
                {(projects ?? []).filter((p) => p.id !== currentProjectId).length === 0 && (
                  <div className="dp-move-empty">No other projects yet.</div>
                )}
              </div>
            )}
          </div>
        )}
        {onArchive && (
          <button
            type="button"
            className="dp-action dp-action-muted"
            onClick={onArchive}
            title="Archive (soft delete)"
          >
            <ArchiveIcon /> Archive
          </button>
        )}
      </div>
    </section>
  );
}

/* -----------------------------------------------------------------
   Tags editor — pill list + inline input
   ----------------------------------------------------------------- */

function TagsEditor({
  videoId, meta, onMetaChange,
}: { videoId: string; meta: VideoMeta | null; onMetaChange: (m: VideoMeta) => void }) {
  const tags = meta?.tags ?? [];
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);

  const commit = useCallback(async (next: string[]) => {
    setBusy(true);
    try {
      const m = await patchMeta(videoId, { tags: next });
      onMetaChange(m);
    } catch (e) {
      console.error(e);
    } finally {
      setBusy(false);
    }
  }, [videoId, onMetaChange]);

  const addTag = useCallback((raw: string) => {
    const t = raw.trim().replace(/^#+/, "").trim();
    if (!t) return;
    if (tags.some((x) => x.toLowerCase() === t.toLowerCase())) {
      setDraft("");
      return;
    }
    commit([...tags, t]);
    setDraft("");
  }, [tags, commit]);

  const removeTag = useCallback((t: string) => {
    commit(tags.filter((x) => x !== t));
  }, [tags, commit]);

  const onKey = useCallback((e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "Enter" || e.key === ",") {
      e.preventDefault();
      addTag(draft);
    } else if (e.key === "Backspace" && draft === "" && tags.length) {
      removeTag(tags[tags.length - 1]);
    }
  }, [draft, tags, addTag, removeTag]);

  return (
    <section className="dp-section">
      <h5>Tags {busy && <span className="dp-spinner" aria-hidden>…</span>}</h5>
      <div className="dp-tags">
        {tags.map((t) => (
          <span key={t} className="dp-tag">
            {t}
            <button
              type="button"
              className="dp-tag-x"
              onClick={() => removeTag(t)}
              title="Remove tag"
              aria-label={`Remove tag ${t}`}
            >×</button>
          </span>
        ))}
        <input
          type="text"
          className="dp-tag-input"
          placeholder={tags.length ? "add tag…" : "add tags (press Enter)"}
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={onKey}
          onBlur={() => draft && addTag(draft)}
        />
      </div>
    </section>
  );
}

/* -----------------------------------------------------------------
   Speaker renamer
   ----------------------------------------------------------------- */

function SpeakersEditor({
  videoId, segments, meta, onMetaChange,
}: {
  videoId: string; segments: Segment[];
  meta: VideoMeta | null;
  onMetaChange: (m: VideoMeta) => void;
}) {
  const detected = useDetectedSpeakers(segments);
  const names = meta?.speaker_names ?? {};
  const [drafts, setDrafts] = useState<Record<string, string>>({});

  // Keep drafts in sync when meta arrives from server
  useEffect(() => { setDrafts({}); }, [videoId]);

  const save = useCallback(async (key: string, value: string) => {
    const next = { ...names };
    const trimmed = value.trim();
    if (trimmed) next[key] = trimmed;
    else delete next[key];
    try {
      const m = await patchMeta(videoId, { speaker_names: next });
      onMetaChange(m);
    } catch (e) {
      console.error(e);
    }
  }, [videoId, names, onMetaChange]);

  if (detected.length === 0) return null;

  return (
    <section className="dp-section">
      <h5>Speakers</h5>
      <p className="dp-hint">
        Give each diarized speaker a real name — it'll replace SPEAKER_0N
        labels everywhere.
      </p>
      <ul className="dp-speakers">
        {detected.map((s, i) => {
          const tone = speakerTone(s, i);
          const current = drafts[s] ?? names[s] ?? "";
          return (
            <li key={s}>
              <span className={`dp-dot ${tone}`} />
              <span className="dp-speaker-key">{friendlyKey(s)}</span>
              <input
                type="text"
                className="dp-speaker-input"
                placeholder="name…"
                value={current}
                onChange={(e) => setDrafts((d) => ({ ...d, [s]: e.target.value }))}
                onBlur={() => save(s, current)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") {
                    (e.target as HTMLInputElement).blur();
                  }
                }}
              />
            </li>
          );
        })}
      </ul>
    </section>
  );
}

function useDetectedSpeakers(segments: Segment[]): string[] {
  const seen: string[] = [];
  for (const s of segments) {
    if (s.speaker && !seen.includes(s.speaker)) seen.push(s.speaker);
  }
  return seen;
}

function friendlyKey(k: string): string {
  const m = k.match(/SPEAKER_(\d+)/i);
  return m ? `Speaker ${parseInt(m[1], 10) + 1}` : k;
}

function speakerTone(s: string, i: number): string {
  const tones = ["s1", "s2", "s3", "s4"];
  const m = s.match(/SPEAKER_(\d+)/i);
  const n = m ? parseInt(m[1], 10) : i;
  return tones[n % tones.length];
}

/* -----------------------------------------------------------------
   Notes (freeform)
   ----------------------------------------------------------------- */

function NotesEditor({
  videoId, meta, onMetaChange,
}: { videoId: string; meta: VideoMeta | null; onMetaChange: (m: VideoMeta) => void }) {
  const [draft, setDraft] = useState(meta?.notes ?? "");
  const lastSavedRef = useRef(meta?.notes ?? "");

  // Keep in sync if meta is swapped out (e.g. on video change)
  useEffect(() => {
    setDraft(meta?.notes ?? "");
    lastSavedRef.current = meta?.notes ?? "";
  }, [videoId, meta?.notes]);

  const save = useCallback(async () => {
    if (draft === lastSavedRef.current) return;
    try {
      const m = await patchMeta(videoId, { notes: draft });
      lastSavedRef.current = draft;
      onMetaChange(m);
    } catch (e) {
      console.error(e);
    }
  }, [videoId, draft, onMetaChange]);

  return (
    <section className="dp-section">
      <h5>Notes</h5>
      <textarea
        className="dp-notes"
        placeholder="Freeform notes about this video…"
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
        onBlur={save}
        rows={4}
      />
    </section>
  );
}

/* -----------------------------------------------------------------
   Icons
   ----------------------------------------------------------------- */

function LinkIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <path d="M10 13a5 5 0 0 0 7.07 0l3-3a5 5 0 0 0-7.07-7.07L11 5" />
      <path d="M14 11a5 5 0 0 0-7.07 0l-3 3a5 5 0 0 0 7.07 7.07L13 19" />
    </svg>
  );
}
function CheckIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M20 6L9 17l-5-5" />
    </svg>
  );
}
function ExternalIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6M15 3h6v6M10 14L21 3" />
    </svg>
  );
}
function YTIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="#ff0033">
      <path d="M23.498 6.186a3.016 3.016 0 0 0-2.122-2.136C19.505 3.545 12 3.545 12 3.545s-7.505 0-9.377.505A3.017 3.017 0 0 0 .502 6.186C0 8.07 0 12 0 12s0 3.93.502 5.814a3.016 3.016 0 0 0 2.122 2.136c1.871.505 9.376.505 9.376.505s7.505 0 9.377-.505a3.015 3.015 0 0 0 2.122-2.136C24 15.93 24 12 24 12s0-3.93-.502-5.814zM9.545 15.568V8.432L15.818 12l-6.273 3.568z"/>
    </svg>
  );
}
function PodcastGlyph() {
  return (
    <svg
      width="14"
      height="14"
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
function ArchiveIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <rect x="3" y="4" width="18" height="5" rx="1" />
      <path d="M5 9v10a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1V9M10 13h4" />
    </svg>
  );
}
function RefreshIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <path d="M23 4v6h-6M1 20v-6h6" />
      <path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15" />
    </svg>
  );
}
function MoveIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <path d="M5 9l-3 3 3 3" />
      <path d="M9 5l3-3 3 3" />
      <path d="M15 19l-3 3-3-3" />
      <path d="M19 9l3 3-3 3" />
      <path d="M2 12h20M12 2v20" />
    </svg>
  );
}
