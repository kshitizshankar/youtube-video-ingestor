import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type FormEvent,
} from "react";
import { extractVideoId } from "../api";
import { bulkIngest, previewPlaylist } from "../projects";
import type {
  BulkIngestResponse,
  PlaylistEntry,
  PlaylistPreview,
} from "../types";

export interface AddVideosModalProps {
  open: boolean;
  projectId: string;
  onClose: () => void;
  /** Called after a successful bulkIngest. Parent is responsible for
   *  refreshing the project detail and surfacing a toast. */
  onSuccess: (resp: BulkIngestResponse) => void;
}

type TabId = "playlist" | "urls";

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
    [tab, preview, selectedIds, parsedUrls, projectId, onSuccess, submitting],
  );

  if (!open) return null;

  const selectedCount =
    tab === "playlist" ? selectedIds.size : parsedUrls.length;

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
            Import from a YouTube playlist or paste individual URLs. Everything
            transcribes locally on your GPU.
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
// Helpers
// -------------------------------------------------------------------

function fmtDuration(sec: number): string {
  if (!isFinite(sec) || sec <= 0) return "—";
  const s = Math.floor(sec);
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const r = s % 60;
  if (h > 0) return `${h}:${String(m).padStart(2, "0")}:${String(r).padStart(2, "0")}`;
  return `${m}:${String(r).padStart(2, "0")}`;
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
