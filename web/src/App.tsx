import { useCallback, useEffect, useState } from "react";
import {
  BrowserRouter,
  Navigate,
  Route,
  Routes,
  useLocation,
  useNavigate,
} from "react-router-dom";
import * as Tooltip from "@radix-ui/react-tooltip";
import { extractVideoId, listTranscripts } from "./api";
import IngestModal from "./components/IngestModal";
import LoginGate from "./components/LoginGate";
import Sidebar from "./components/Sidebar";
import Archive from "./Archive";
import Detail from "./Detail";
import Library from "./Library";
import type { TranscriptSummary } from "./types";

/** Holds shell state (sidebar count, ingest modal, pending ingest URL). */
function Shell() {
  const [items, setItems] = useState<TranscriptSummary[]>([]);
  const [refreshKey, setRefreshKey] = useState(0);
  const [ingestOpen, setIngestOpen] = useState(false);
  const [drawerOpen, setDrawerOpen] = useState(false);
  // When user submits an ingest, we navigate to /v/<id> AND set this so the
  // Detail screen knows to start the stream against this URL.
  const [pendingIngestUrl, setPendingIngestUrl] = useState<string | null>(null);

  const navigate = useNavigate();
  const location = useLocation();

  // Close the mobile drawer whenever we navigate (e.g. tapping a library row).
  useEffect(() => { setDrawerOpen(false); }, [location.pathname]);

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

  const [pendingOpts, setPendingOpts] = useState<{ diarize: boolean; model: string; batched: boolean } | null>(null);

  const handleIngest = useCallback(
    (url: string, opts: { diarize: boolean; model: string; batched: boolean }) => {
      const id = extractVideoId(url);
      setIngestOpen(false);
      if (id) {
        setPendingIngestUrl(url);
        setPendingOpts(opts);
        navigate(`/v/${id}`);
      } else {
        alert("Could not parse a YouTube video ID from that URL.");
      }
    },
    [navigate],
  );

  const refresh = useCallback(() => setRefreshKey((k) => k + 1), []);

  const toggleDrawer = useCallback(() => setDrawerOpen((v) => !v), []);

  return (
    <div className={`app-shell${drawerOpen ? " drawer-open" : ""}`}>
      <Sidebar
        libraryCount={items.length}
        onNewIngest={() => setIngestOpen(true)}
      />
      <button
        type="button"
        className="drawer-backdrop"
        aria-label="Close menu"
        onClick={() => setDrawerOpen(false)}
      />
      <Routes>
        <Route
          path="/"
          element={
            <Library
              onAdd={() => setIngestOpen(true)}
              onMenuToggle={toggleDrawer}
              refreshKey={refreshKey}
            />
          }
        />
        <Route
          path="/v/:videoId"
          element={
            <Detail
              pendingIngestUrl={pendingIngestUrl}
              pendingIngestOpts={pendingOpts}
              onPendingIngestConsumed={() => { setPendingIngestUrl(null); setPendingOpts(null); }}
              onIngestDone={refresh}
              onMenuToggle={toggleDrawer}
            />
          }
        />
        <Route path="/archive" element={<Archive onMenuToggle={toggleDrawer} />} />
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
      <Tooltip.Provider delayDuration={180} skipDelayDuration={80}>
        <BrowserRouter>
          <Shell />
        </BrowserRouter>
      </Tooltip.Provider>
    </LoginGate>
  );
}
