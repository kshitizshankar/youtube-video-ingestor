import { useCallback, useEffect, useState } from "react";
import {
  BrowserRouter,
  Navigate,
  Route,
  Routes,
  useNavigate,
} from "react-router-dom";
import { extractVideoId, listTranscripts } from "./api";
import IngestModal from "./components/IngestModal";
import LoginGate from "./components/LoginGate";
import Sidebar from "./components/Sidebar";
import Detail from "./Detail";
import Library from "./Library";
import type { TranscriptSummary } from "./types";

/** Holds shell state (sidebar count, ingest modal, pending ingest URL). */
function Shell() {
  const [items, setItems] = useState<TranscriptSummary[]>([]);
  const [refreshKey, setRefreshKey] = useState(0);
  const [ingestOpen, setIngestOpen] = useState(false);
  // When user submits an ingest, we navigate to /v/<id> AND set this so the
  // Detail screen knows to start the stream against this URL.
  const [pendingIngestUrl, setPendingIngestUrl] = useState<string | null>(null);

  const navigate = useNavigate();

  useEffect(() => {
    listTranscripts().then(setItems).catch(console.error);
  }, [refreshKey]);

  // Global keyboard shortcuts
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "n") {
        e.preventDefault();
        setIngestOpen(true);
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const handleIngest = useCallback(
    (url: string) => {
      const id = extractVideoId(url);
      setIngestOpen(false);
      if (id) {
        setPendingIngestUrl(url);
        navigate(`/v/${id}`);
      } else {
        // Couldn't parse the URL client-side — the SSE stream will tell us
        // the id from yt-dlp. For now, just reject.
        alert("Could not parse a YouTube video ID from that URL.");
      }
    },
    [navigate],
  );

  const refresh = useCallback(() => setRefreshKey((k) => k + 1), []);

  return (
    <div className="app-shell">
      <Sidebar
        libraryCount={items.length}
        onNewIngest={() => setIngestOpen(true)}
      />
      <Routes>
        <Route path="/" element={<Library onAdd={() => setIngestOpen(true)} refreshKey={refreshKey} />} />
        <Route
          path="/v/:videoId"
          element={
            <Detail
              pendingIngestUrl={pendingIngestUrl}
              onPendingIngestConsumed={() => setPendingIngestUrl(null)}
              onIngestDone={refresh}
            />
          }
        />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>

      <IngestModal
        open={ingestOpen}
        onClose={() => setIngestOpen(false)}
        onSubmit={handleIngest}
      />
    </div>
  );
}

export default function App() {
  return (
    <LoginGate>
      <BrowserRouter>
        <Shell />
      </BrowserRouter>
    </LoginGate>
  );
}
