import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type FormEvent,
} from "react";
import { extractVideoId } from "../api";
import { bulkIngest, previewPlaylist, previewPodcast } from "../projects";
import type {
  BulkIngestResponse,
  PlaylistEntry,
  PlaylistPreview,
  PodcastEpisode,
  PodcastPreview,
  PodcastSource,
} from "../types";

export interface AddVideosModalProps {
  open: boolean;
  projectId: string;
  onClose: () => void;
  /** Called after a successful bulkIngest. Parent is responsible for
   *  refreshing the project detail and surfacing a toast. */
  onSuccess: (resp: BulkIngestResponse) => void;
}

type TabId = "playlist" | "podcast" | "urls";

export default function AddVideosModal({
  open,
  projectId,
  onClose,
  onSuccess,
}: AddVideosModalProps) {
  const [tab, setTab] = useState<TabId>("playlist");

  // Playlist tab state
  const [playlistUrl, setPlaylistUrl] = useState("");
  const [preview, setPreview] = useState<PlaylistPreview | null>(null);
  const [previewing, setPreviewing] = useState(false);
  const [previewError, setPreviewError] = useState<string | null>(null);
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const [failuresOpen, setFailuresOpen] = useState(false);

  // Podcast tab state
  const [podcastUrl, setPodcastUrl] = useState("");
  const [podcastPreview, setPodcastPreview] = useState<PodcastPreview | null>(
    null,
  );
  const [podcastPreviewing, setPodcastPreviewing] = useState(false);
  const [podcastPreviewError, setPodcastPreviewError] = useState<string | null>(
    null,
  );
  const [podcastSelectedGuids, setPodcastSelectedGuids] = useState<Set<string>>(
    new Set(),
  );

  // URLs tab state
  const [urlsText, setUrlsText] = useState("");

  // Shared submit state
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);

  const firstFieldRef = useRef<HTMLInputElement | HTMLTextAreaElement | null>(null);

  // Reset everything on open.
  useEffect(() => {
    if (!open) return;
    setTab("playlist");
    setPlaylistUrl("");
    setPreview(null);
    setPreviewing(false);
    setPreviewError(null);
    setSelectedIds(new Set());
    setFailuresOpen(false);
    setPodcastUrl("");
    setPodcastPreview(null);
    setPodcastPreviewing(false);
    setPodcastPreviewError(null);
    setPodcastSelectedGuids(new Set());
    setUrlsText("");
    setSubmitError(null);
    setSubmitting(false);
    requestAnimationFrame(() => firstFieldRef.current?.focus());
  }, [open]);

  // Escape closes the modal unless we're mid-submit.
  useEffect(() => {
    if (!open) return;
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape" && !submitting) onClose();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose, submitting]);

  // Parse the URLs textarea lazily — accept newline- AND comma-separated.
  const parsedUrls = useMemo(() => {
    return urlsText
      .split(/[\n,]+/)
      .map((s) => s.trim())
      .filter((s) => s.length > 0)
      .filter((s) => extractVideoId(s) !== null);
  }, [urlsText]);

  const totalUrlLines = useMemo(() => {
    return urlsText
      .split(/[\n,]+/)
      .map((s) => s.trim())
      .filter((s) => s.length > 0).length;
  }, [urlsText]);

  const handlePreview = useCallback(async () => {
    const trimmed = playlistUrl.trim();
    if (!trimmed) return;
    setPreviewing(true);
    setPreviewError(null);
    setPreview(null);
    try {
      const p = await previewPlaylist(trimmed);
      setPreview(p);
      // Default: every entry selected.
      setSelectedIds(new Set(p.entries.map((e) => e.id)));
    } catch (e) {
      setPreviewError(e instanceof Error ? e.message : String(e));
    } finally {
      setPreviewing(false);
    }
  }, [playlistUrl]);

  const toggleEntry = useCallback((id: string) => {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }, []);

  const selectAll = useCallback(() => {
    if (!preview) return;
    setSelectedIds(new Set(preview.entries.map((e) => e.id)));
  }, [preview]);

  const selectNone = useCallback(() => setSelectedIds(new Set()), []);

  const allSelected =
    preview !== null &&
    preview.entries.length > 0 &&
    selectedIds.size === preview.entries.length;

  // ----- Podcast handlers ----------------------------------------

  const handlePodcastPreview = useCallback(async () => {
    const trimmed = podcastUrl.trim();
    if (!trimmed) return;
    setPodcastPreviewing(true);
    setPodcastPreviewError(null);
    setPodcastPreview(null);
    try {
      const p = await previewPodcast(trimmed);
      setPodcastPreview(p);
      // Default: every episode selected (single-episode previews still get
      // their lone guid pre-checked so the submit button is immediately live).
      setPodcastSelectedGuids(new Set(p.episodes.map((ep) => ep.guid)));
    } catch (e) {
      setPodcastPreviewError(e instanceof Error ? e.message : String(e));
    } finally {
      setPodcastPreviewing(false);
    }
  }, [podcastUrl]);

  const togglePodcastEpisode = useCallback((guid: string) => {
    setPodcastSelectedGuids((prev) => {
      const next = new Set(prev);
      if (next.has(guid)) next.delete(guid);
      else next.add(guid);
      return next;
    });
  }, []);

  const selectAllPodcast = useCallback(() => {
    if (!podcastPreview) return;
    setPodcastSelectedGuids(
      new Set(podcastPreview.episodes.map((ep) => ep.guid)),
    );
  }, [podcastPreview]);

  const selectNonePodcast = useCallback(
    () => setPodcastSelectedGuids(new Set()),
    [],
  );

  const selectLatestPodcast = useCallback(
    (n: number) => {
      if (!podcastPreview) return;
      // Episodes arrive newest-first per the server contract; take the head.
      setPodcastSelectedGuids(
        new Set(podcastPreview.episodes.slice(0, n).map((ep) => ep.guid)),
      );
    },
    [podcastPreview],
  );

  const allPodcastSelected =
    podcastPreview !== null &&
    podcastPreview.episodes.length > 0 &&
    podcastSelectedGuids.size === podcastPreview.episodes.length;

  const handleSubmit = useCallback(
    async (e?: FormEvent) => {
      e?.preventDefault();
      if (submitting) return;
      setSubmitError(null);

      let urls: string[] = [];
      if (tab === "playlist") {
        if (!preview) return;
        urls = preview.entries
          .filter((en) => selectedIds.has(en.id))
          .map((en) => en.url);
      } else if (tab === "podcast") {
        if (!podcastPreview) return;
        urls = podcastPreview.episodes
          .filter((ep) => podcastSelectedGuids.has(ep.guid))
          .map((ep) => ep.mp3_url);
      } else {
        urls = parsedUrls;
      }
      if (urls.length === 0) return;

      setSubmitting(true);
      try {
        const resp = await bulkIngest({ project_id: projectId, urls });
        onSuccess(resp);
      } catch (err) {
        setSubmitError(err instanceof Error ? err.message : String(err));
      } finally {
        setSubmitting(false);
      }
    },
    [
      tab,
      preview,
      selectedIds,
      podcastPreview,
      podcastSelectedGuids,
      parsedUrls,
      projectId,
      onSuccess,
      submitting,
    ],
  );

  if (!open) return null;

  const selectedCount =
    tab === "playlist"
      ? selectedIds.size
      : tab === "podcast"
        ? podcastSelectedGuids.size
        : parsedUrls.length;

  return (
    <div
      className="modal-backdrop add-videos-backdrop"
      onClick={() => !submitting && onClose()}
    >
      <div
        className="modal add-videos-modal"
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-modal="true"
        aria-label="Add videos to project"
      >
        <div className="add-videos-header">
          <h2>Add videos</h2>
          <p className="add-videos-sub">
            Import from a YouTube playlist, a podcast feed, or paste individual
            URLs. Everything transcribes locally on your GPU.
          </p>
        </div>

        <div className="modal-tabs" role="tablist">
          <button
            type="button"
            role="tab"
            aria-selected={tab === "playlist"}
            className={`modal-tab ${tab === "playlist" ? "is-active" : ""}`}
            onClick={() => setTab("playlist")}
          >
            Playlist URL
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={tab === "podcast"}
            className={`modal-tab ${tab === "podcast" ? "is-active" : ""}`}
            onClick={() => setTab("podcast")}
          >
            Podcast
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={tab === "urls"}
            className={`modal-tab ${tab === "urls" ? "is-active" : ""}`}
            onClick={() => setTab("urls")}
          >
            Video URLs
          </button>
        </div>

        <form onSubmit={handleSubmit} className="add-videos-form">
          {tab === "playlist" ? (
            <PlaylistTab
              firstFieldRef={firstFieldRef as React.RefObject<HTMLInputElement>}
              url={playlistUrl}
              setUrl={setPlaylistUrl}
              preview={preview}
              previewing={previewing}
              previewError={previewError}
              onPreview={handlePreview}
              selectedIds={selectedIds}
              onToggle={toggleEntry}
              allSelected={allSelected}
              onSelectAll={selectAll}
              onSelectNone={selectNone}
              failuresOpen={failuresOpen}
              setFailuresOpen={setFailuresOpen}
            />
          ) : tab === "podcast" ? (
            <PodcastTab
              firstFieldRef={firstFieldRef as React.RefObject<HTMLInputElement>}
              url={podcastUrl}
              setUrl={setPodcastUrl}
              preview={podcastPreview}
              previewing={podcastPreviewing}
              previewError={podcastPreviewError}
              onPreview={handlePodcastPreview}
              selectedGuids={podcastSelectedGuids}
              onToggle={togglePodcastEpisode}
              allSelected={allPodcastSelected}
              onSelectAll={selectAllPodcast}
              onSelectNone={selectNonePodcast}
              onSelectLatest={selectLatestPodcast}
            />
          ) : (
            <UrlsTab
              firstFieldRef={
                firstFieldRef as React.RefObject<HTMLTextAreaElement>
              }
              value={urlsText}
              onChange={setUrlsText}
              parsedCount={parsedUrls.length}
              totalLines={totalUrlLines}
            />
          )}

          {submitError && <div className="form-error">{submitError}</div>}

          <div className="modal-actions add-videos-actions">
            <button type="button" onClick={onClose} disabled={submitting}>
              Cancel
            </button>
            <button
              type="submit"
              disabled={submitting || selectedCount === 0}
            >
              {submitting
                ? "Importing…"
                : selectedCount > 0
                  ? `Import ${selectedCount}`
                  : "Import"}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}

// -------------------------------------------------------------------
// Playlist tab
// -------------------------------------------------------------------

interface PlaylistTabProps {
  firstFieldRef: React.RefObject<HTMLInputElement>;
  url: string;
  setUrl: (v: string) => void;
  preview: PlaylistPreview | null;
  previewing: boolean;
  previewError: string | null;
  onPreview: () => void;
  selectedIds: Set<string>;
  onToggle: (id: string) => void;
  allSelected: boolean;
  onSelectAll: () => void;
  onSelectNone: () => void;
  failuresOpen: boolean;
  setFailuresOpen: (v: boolean) => void;
}

function PlaylistTab({
  firstFieldRef,
  url,
  setUrl,
  preview,
  previewing,
  previewError,
  onPreview,
  selectedIds,
  onToggle,
  allSelected,
  onSelectAll,
  onSelectNone,
  failuresOpen,
  setFailuresOpen,
}: PlaylistTabProps) {
  return (
    <div className="tab-panel" role="tabpanel">
      <div className="playlist-input-row">
        <input
          ref={firstFieldRef}
          className="playlist-input"
          placeholder="https://youtube.com/playlist?list=…"
          value={url}
          onChange={(e) => setUrl(e.target.value)}
          spellCheck={false}
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              e.preventDefault();
              if (url.trim() && !previewing) onPreview();
            }
          }}
        />
        <button
          type="button"
          className="btn-preview"
          onClick={onPreview}
          disabled={!url.trim() || previewing}
        >
          {previewing ? "Loading…" : "Preview"}
        </button>
      </div>

      {previewError && <div className="form-error">{previewError}</div>}

      {preview && (
        <>
          <div className="playlist-summary">
            <div className="playlist-title">
              {preview.title || "Untitled playlist"}
            </div>
            <div className="playlist-meta">
              {preview.uploader ? <span>{preview.uploader}</span> : null}
              {preview.uploader ? <span className="sep">·</span> : null}
              <span>
                {preview.entry_count} video
                {preview.entry_count === 1 ? "" : "s"}
              </span>
              {preview.failures.length > 0 && (
                <>
                  <span className="sep">·</span>
                  <span className="muted">
                    {preview.failures.length} unavailable
                  </span>
                </>
              )}
            </div>
          </div>

          {preview.entries.length > 0 && (
            <>
              <div className="playlist-toolbar">
                <span className="selected-count">
                  {selectedIds.size} of {preview.entries.length} selected
                </span>
                <div className="playlist-toolbar-actions">
                  <button
                    type="button"
                    className="link-btn"
                    onClick={allSelected ? onSelectNone : onSelectAll}
                  >
                    {allSelected ? "Select none" : "Select all"}
                  </button>
                </div>
              </div>

              <div className="playlist-preview">
                {preview.entries.map((entry) => (
                  <PreviewRow
                    key={entry.id}
                    entry={entry}
                    checked={selectedIds.has(entry.id)}
                    onToggle={() => onToggle(entry.id)}
                  />
                ))}
              </div>
            </>
          )}

          {preview.failures.length > 0 && (
            <div className="preview-failures">
              <button
                type="button"
                className="failures-toggle"
                onClick={() => setFailuresOpen(!failuresOpen)}
                aria-expanded={failuresOpen}
              >
                <Caret open={failuresOpen} />
                {preview.failures.length} unavailable
              </button>
              {failuresOpen && (
                <ul className="failures-list">
                  {preview.failures.map((f, i) => (
                    <li key={`${f.id ?? "unknown"}-${i}`}>
                      <code>{f.id ?? "unknown"}</code>
                      <span className="reason">{f.reason}</span>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          )}
        </>
      )}

      {!preview && !previewing && !previewError && (
        <div className="tab-hint">
          Paste a YouTube playlist URL, then pick which videos to import.
        </div>
      )}
    </div>
  );
}

function PreviewRow({
  entry,
  checked,
  onToggle,
}: {
  entry: PlaylistEntry;
  checked: boolean;
  onToggle: () => void;
}) {
  const thumb =
    entry.thumbnail_url || `https://img.youtube.com/vi/${entry.id}/mqdefault.jpg`;
  return (
    <label
      className={`preview-row ${checked ? "is-selected" : ""}`}
      htmlFor={`pv-${entry.id}`}
    >
      <input
        id={`pv-${entry.id}`}
        type="checkbox"
        checked={checked}
        onChange={onToggle}
      />
      <div className="preview-thumb">
        <img src={thumb} alt="" loading="lazy" />
      </div>
      <div className="preview-body">
        <div className="preview-title">{entry.title || entry.id}</div>
        <div className="preview-meta">
          {entry.duration_sec != null ? fmtDuration(entry.duration_sec) : "—"}
          <span className="sep">·</span>
          <code>{entry.id}</code>
        </div>
      </div>
    </label>
  );
}

// -------------------------------------------------------------------
// URLs tab
// -------------------------------------------------------------------

interface UrlsTabProps {
  firstFieldRef: React.RefObject<HTMLTextAreaElement>;
  value: string;
  onChange: (v: string) => void;
  parsedCount: number;
  totalLines: number;
}

function UrlsTab({
  firstFieldRef,
  value,
  onChange,
  parsedCount,
  totalLines,
}: UrlsTabProps) {
  const hasContent = totalLines > 0;
  const countKind = parsedCount === 0 && hasContent ? "warn" : "ok";
  return (
    <div className="tab-panel" role="tabpanel">
      <label className="urls-label">
        Paste one URL per line — or comma-separated. Both YouTube URLs and bare
        11-character video IDs work.
      </label>
      <textarea
        ref={firstFieldRef}
        className="urls-textarea"
        rows={8}
        placeholder={"https://youtube.com/watch?v=…\nhttps://youtu.be/…\nor_a_bare_id"}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        spellCheck={false}
      />
      <div className={`parse-count parse-${countKind}`}>
        {hasContent ? (
          parsedCount === 0 ? (
            "No valid YouTube URLs found."
          ) : parsedCount === totalLines ? (
            <>Parsed {parsedCount} valid URL{parsedCount === 1 ? "" : "s"}.</>
          ) : (
            <>
              Parsed {parsedCount} of {totalLines} entr
              {totalLines === 1 ? "y" : "ies"} — the rest don't look like
              YouTube URLs.
            </>
          )
        ) : (
          <span className="muted">Waiting for input…</span>
        )}
      </div>
    </div>
  );
}

// -------------------------------------------------------------------
// Podcast tab
// -------------------------------------------------------------------

interface PodcastTabProps {
  firstFieldRef: React.RefObject<HTMLInputElement>;
  url: string;
  setUrl: (v: string) => void;
  preview: PodcastPreview | null;
  previewing: boolean;
  previewError: string | null;
  onPreview: () => void;
  selectedGuids: Set<string>;
  onToggle: (guid: string) => void;
  allSelected: boolean;
  onSelectAll: () => void;
  onSelectNone: () => void;
  onSelectLatest: (n: number) => void;
}

function PodcastTab({
  firstFieldRef,
  url,
  setUrl,
  preview,
  previewing,
  previewError,
  onPreview,
  selectedGuids,
  onToggle,
  allSelected,
  onSelectAll,
  onSelectNone,
  onSelectLatest,
}: PodcastTabProps) {
  const isSingle =
    preview !== null &&
    (preview.source === "spotify_episode" || preview.source === "direct_audio");
  return (
    <div className="tab-panel" role="tabpanel">
      <div className="playlist-input-row">
        <input
          ref={firstFieldRef}
          className="playlist-input"
          placeholder="https://open.spotify.com/show/... or RSS URL or .mp3 URL"
          value={url}
          onChange={(e) => setUrl(e.target.value)}
          spellCheck={false}
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              e.preventDefault();
              if (url.trim() && !previewing) onPreview();
            }
          }}
        />
        <button
          type="button"
          className="btn-preview"
          onClick={onPreview}
          disabled={!url.trim() || previewing}
        >
          {previewing ? "Loading..." : "Preview"}
        </button>
      </div>

      {previewError && <div className="form-error">{previewError}</div>}

      {previewing && !preview && (
        <div className="tab-hint">Resolving feed...</div>
      )}

      {preview && (
        <>
          <div className="playlist-summary">
            <div className="playlist-title">
              {preview.title || "Untitled podcast"}
            </div>
            <div className="playlist-meta">
              {preview.publisher ? <span>{preview.publisher}</span> : null}
              {preview.publisher ? <span className="sep">.</span> : null}
              <span>
                {preview.episodes.length} episode
                {preview.episodes.length === 1 ? "" : "s"}
              </span>
              <span className="sep">.</span>
              <span className="source-chip">
                {sourceChipLabel(preview.source)}
              </span>
            </div>
          </div>

          {preview.episodes.length > 0 ? (
            <>
              {!isSingle && (
                <div className="playlist-toolbar">
                  <span className="selected-count">
                    {selectedGuids.size} of {preview.episodes.length} selected
                  </span>
                  <div className="playlist-toolbar-actions">
                    <button
                      type="button"
                      className="link-btn"
                      onClick={() => onSelectLatest(5)}
                      disabled={preview.episodes.length === 0}
                    >
                      Latest 5
                    </button>
                    <button
                      type="button"
                      className="link-btn"
                      onClick={() => onSelectLatest(10)}
                      disabled={preview.episodes.length === 0}
                    >
                      Latest 10
                    </button>
                    <button
                      type="button"
                      className="link-btn"
                      onClick={allSelected ? onSelectNone : onSelectAll}
                    >
                      {allSelected ? "Select none" : "Select all"}
                    </button>
                  </div>
                </div>
              )}

              <div className="playlist-preview">
                {preview.episodes.map((ep) => (
                  <PodcastPreviewRow
                    key={ep.guid}
                    episode={ep}
                    showImage={preview.image_url}
                    checked={selectedGuids.has(ep.guid)}
                    onToggle={() => onToggle(ep.guid)}
                  />
                ))}
              </div>
            </>
          ) : (
            <div className="tab-hint">
              The feed didn't return any episodes. Try a different URL or check
              the publisher's RSS feed directly.
            </div>
          )}
        </>
      )}

      {!preview && !previewing && !previewError && (
        <div className="tab-hint">
          Paste a Spotify show or episode URL, an RSS feed URL, or a direct
          .mp3 URL. We'll resolve it to a list of episodes.
        </div>
      )}
    </div>
  );
}

function PodcastPreviewRow({
  episode,
  showImage,
  checked,
  onToggle,
}: {
  episode: PodcastEpisode;
  showImage: string | null;
  checked: boolean;
  onToggle: () => void;
}) {
  const thumb = episode.image_url || showImage || null;
  const safeId = `pc-${episode.guid.replace(/[^a-zA-Z0-9_-]/g, "_")}`;
  return (
    <label
      className={`preview-row ${checked ? "is-selected" : ""}`}
      htmlFor={safeId}
    >
      <input
        id={safeId}
        type="checkbox"
        checked={checked}
        onChange={onToggle}
      />
      <div className="preview-thumb is-podcast">
        {thumb ? (
          <img src={thumb} alt="" loading="lazy" />
        ) : (
          <div className="preview-thumb-fallback" aria-hidden="true" />
        )}
      </div>
      <div className="preview-body">
        <div className="preview-title">{episode.title || "Untitled episode"}</div>
        <div className="preview-meta">
          <span>
            {episode.duration_sec != null
              ? fmtDuration(episode.duration_sec)
              : "--"}
          </span>
          <span className="sep">.</span>
          <span>{fmtPubDate(episode.pub_date)}</span>
        </div>
      </div>
    </label>
  );
}

// -------------------------------------------------------------------
// Helpers
// -------------------------------------------------------------------

function fmtDuration(sec: number): string {
  if (!isFinite(sec) || sec <= 0) return "--";
  const s = Math.floor(sec);
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const r = s % 60;
  if (h > 0) return `${h}:${String(m).padStart(2, "0")}:${String(r).padStart(2, "0")}`;
  return `${m}:${String(r).padStart(2, "0")}`;
}

const MONTHS_SHORT = [
  "Jan",
  "Feb",
  "Mar",
  "Apr",
  "May",
  "Jun",
  "Jul",
  "Aug",
  "Sep",
  "Oct",
  "Nov",
  "Dec",
];

/** Format an ISO-8601 publish date as "DD MMM YYYY". Returns "--" when the
 *  input is missing or unparseable so meta lines never render NaN. */
function fmtPubDate(iso: string | null): string {
  if (!iso) return "--";
  const d = new Date(iso);
  if (isNaN(d.getTime())) return "--";
  const day = String(d.getUTCDate()).padStart(2, "0");
  const month = MONTHS_SHORT[d.getUTCMonth()];
  const year = d.getUTCFullYear();
  return `${day} ${month} ${year}`;
}

function sourceChipLabel(source: PodcastSource): string {
  switch (source) {
    case "spotify_show":
      return "spotify show";
    case "spotify_episode":
      return "spotify episode";
    case "rss":
      return "rss feed";
    case "direct_audio":
      return "direct audio";
    default:
      return source;
  }
}

function Caret({ open }: { open: boolean }) {
  return (
    <svg
      width="10"
      height="10"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2.2"
      strokeLinecap="round"
      strokeLinejoin="round"
      style={{
        transform: open ? "rotate(90deg)" : "rotate(0deg)",
        transition: "transform 160ms ease",
      }}
    >
      <path d="M9 6l6 6-6 6" />
    </svg>
  );
}
