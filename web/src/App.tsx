import { useCallback, useEffect, useRef, useState } from "react";
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
import Dashboard from "./Dashboard";
import Detail from "./Detail";
import Library from "./Library";
import Project from "./Project";
import { ProjectsProvider } from "./ProjectsContext";
import Settings from "./Settings";
import { useActiveIngests } from "./useActiveIngests";
import type { TranscriptSummary } from "./types";

const BASE_TITLE = "vidan.";

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

  // Live count of in-flight ingests, polled at the top level so it's
  // visible everywhere: sidebar badge, browser tab title, and (via
  // refreshKey when an ingest finishes) the library/dashboard counts.
  const ingests = useActiveIngests();
  const activeIngestCount = ingests.filter((i) => !i.done).length;

  // Tab title reflects active jobs so a backgrounded tab still tells the
  // user something is running. Reset to the base title when nothing is
  // active so the user isn't left with a stale "(N)" prefix.
  useEffect(() => {
    document.title = activeIngestCount > 0
      ? `(${activeIngestCount}) ${BASE_TITLE}`
      : BASE_TITLE;
    return () => { document.title = BASE_TITLE; };
  }, [activeIngestCount]);

  // When an ingest transitions to done, bump refreshKey so consumers that
  // key on it (Library, Sidebar count) refetch their lists.
  const prevDoneCount = useRef(0);
  useEffect(() => {
    const doneCount = ingests.filter((i) => i.done).length;
    if (doneCount > prevDoneCount.current) refresh();
    prevDoneCount.current = doneCount;
  }, [ingests, refresh]);

  return (
    <div className={`app-shell${drawerOpen ? " drawer-open" : ""}`}>
      <Sidebar
        libraryCount={items.length}
        activeIngestCount={activeIngestCount}
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
            <Dashboard
              onMenuToggle={toggleDrawer}
              onAdd={() => setIngestOpen(true)}
            />
          }
        />
        <Route
          path="/library"
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
        <Route
          path="/p/:projectId"
          element={<Project onMenuToggle={toggleDrawer} />}
        />
        <Route path="/archive" element={<Archive onMenuToggle={toggleDrawer} />} />
        <Route path="/settings" element={<Settings onMenuToggle={toggleDrawer} />} />
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
          <ProjectsProvider>
            <Shell />
          </ProjectsProvider>
        </BrowserRouter>
      </Tooltip.Provider>
    </LoginGate>
  );
}
