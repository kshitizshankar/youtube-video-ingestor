import { useEffect, useState } from "react";
import { type IngestState, listIngests } from "./api";

/**
 * Poll /api/ingests on a fixed cadence and surface the result. Each call
 * to the hook spawns its own poller — that's intentional. Callers get
 * fresh data without coordinating, and the cost (one cheap GET every
 * 2.5s when the tab is visible) is trivial against a localhost API.
 *
 * Pauses while the tab is hidden so backgrounded tabs don't make
 * pointless requests; resumes immediately on focus.
 */
const POLL_MS = 2500;

export function useActiveIngests(): IngestState[] {
  const [ingests, setIngests] = useState<IngestState[]>([]);

  useEffect(() => {
    let cancelled = false;
    let timer: number | null = null;

    const tick = async () => {
      if (document.visibilityState !== "visible") return;
      try {
        const data = await listIngests();
        if (!cancelled) setIngests(data);
      } catch {
        /* swallow — next tick retries */
      }
    };

    const start = () => {
      if (timer != null) return;
      timer = window.setInterval(tick, POLL_MS);
    };
    const stop = () => {
      if (timer != null) {
        clearInterval(timer);
        timer = null;
      }
    };
    const onVis = () => {
      if (document.visibilityState === "visible") {
        tick();
        start();
      } else {
        stop();
      }
    };

    tick();
    start();
    document.addEventListener("visibilitychange", onVis);
    return () => {
      cancelled = true;
      stop();
      document.removeEventListener("visibilitychange", onVis);
    };
  }, []);

  return ingests;
}
