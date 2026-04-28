import { useEffect, useState } from "react";
import type { IngestState } from "../api";
import IngestCard from "./IngestCard";

export interface IngestStripProps {
  ingests: IngestState[];
  /** When false, render an empty hairline strip even with no active jobs.
   *  Default true: render nothing when there's nothing to show. */
  hideWhenEmpty?: boolean;
}

/**
 * Compact strip of in-flight + just-finished ingest jobs. Pure presentation:
 * the parent owns the data and handles polling. Sorts active-first, then
 * by start time descending. Ticks a wall-clock at 1Hz so each card's
 * "elapsed" / "stalled NN s" labels stay live without per-card timers.
 */
export default function IngestStrip({ ingests, hideWhenEmpty = true }: IngestStripProps) {
  const [now, setNow] = useState(() => Date.now() / 1000);
  useEffect(() => {
    const id = window.setInterval(() => setNow(Date.now() / 1000), 1000);
    return () => clearInterval(id);
  }, []);

  if (hideWhenEmpty && ingests.length === 0) return null;

  const sorted = [...ingests].sort(
    (a, b) => Number(a.done) - Number(b.done) || b.started_at - a.started_at,
  );
  const activeCount = ingests.filter((i) => !i.done).length;
  const doneCount = ingests.filter((i) => i.done).length;

  return (
    <section className="ingest-strip">
      <header>
        <span className="label">in progress</span>
        <span className="count">
          {activeCount} active &middot; {doneCount} just finished
        </span>
      </header>
      <div className="ingest-cards">
        {sorted.map((ing) => (
          <IngestCard key={ing.id} ing={ing} now={now} />
        ))}
      </div>
    </section>
  );
}
