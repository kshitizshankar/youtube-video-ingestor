import { useCallback, useEffect, useState } from "react";
import { deleteVideo, listArchive, restoreVideo } from "./api";
import ConfirmDialog from "./components/ConfirmDialog";
import TopBar from "./components/TopBar";
import VideoRow from "./components/VideoRow";
import type { TranscriptSummary } from "./types";

export interface ArchiveProps {
  onMenuToggle?: () => void;
}

export default function Archive({ onMenuToggle }: ArchiveProps) {
  const [items, setItems] = useState<TranscriptSummary[]>([]);
  const [busy, setBusy] = useState(false);
  const [pendingDelete, setPendingDelete] = useState<TranscriptSummary | null>(null);
  const [refreshKey, setRefreshKey] = useState(0);

  useEffect(() => {
    listArchive().then(setItems).catch(console.error);
  }, [refreshKey]);

  const refresh = useCallback(() => setRefreshKey((k) => k + 1), []);

  const handleRestore = useCallback(async (id: string) => {
    setBusy(true);
    try {
      await restoreVideo(id);
      refresh();
    } catch (e) {
      alert(`Restore failed: ${e}`);
    } finally {
      setBusy(false);
    }
  }, [refresh]);

  const handleDeleteClick = useCallback((id: string) => {
    const v = items.find((x) => x.id === id);
    if (v) setPendingDelete(v);
  }, [items]);

  const handleDeleteConfirm = useCallback(async () => {
    if (!pendingDelete) return;
    setBusy(true);
    try {
      await deleteVideo(pendingDelete.id);
      setPendingDelete(null);
      refresh();
    } catch (e) {
      alert(`Delete failed: ${e}`);
    } finally {
      setBusy(false);
    }
  }, [pendingDelete, refresh]);

  return (
    <div className="main">
      <TopBar
        title="archive"
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
      />
      <div className="library">
        <div className="library-hero">
          <div>
            <h2>
              archive<em>.</em>
            </h2>
            <div className="sub">
              soft-deleted videos live here. transcripts, audio, and analysis
              are still on disk &mdash; restore anytime, or delete forever when
              you&rsquo;re sure. deleting forever throws away the whisper +
              claude work for that video.
            </div>
          </div>
          <div className="library-stats">
            <div className="stat">
              <div className="n">{items.length}</div>
              <div className="l">archived</div>
            </div>
          </div>
        </div>

        <div className="row-head">
          <div></div>
          <div>title</div>
          <div>speakers</div>
          <div>status</div>
          <div>activity</div>
          <div></div>
        </div>

        {items.length === 0 ? (
          <div className="library-empty">
            nothing archived. videos you archive from the library will land here.
          </div>
        ) : (
          items.map((v) => (
            <VideoRow
              key={v.id}
              v={v}
              onArchiveToggle={handleRestore}
              archiveLabel="Restore"
              onDelete={handleDeleteClick}
            />
          ))
        )}
      </div>

      <ConfirmDialog
        open={!!pendingDelete}
        title="delete forever?"
        destructive
        busy={busy}
        confirmLabel="delete forever"
        body={
          pendingDelete ? (
            <>
              <p>
                <strong>{pendingDelete.title}</strong>
              </p>
              <p>
                this will permanently delete the transcript, audio, subtitles,
                and claude analysis for this video. to get them back you&rsquo;d
                have to re-ingest &mdash; running whisper and the claude
                analysis pipeline from scratch.
              </p>
              <p>continue?</p>
            </>
          ) : null
        }
        onConfirm={handleDeleteConfirm}
        onCancel={() => setPendingDelete(null)}
      />
    </div>
  );
}

function HamburgerIcon() {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round">
      <path d="M3 6h18M3 12h18M3 18h18" />
    </svg>
  );
}
