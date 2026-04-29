import { useCallback, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import {
  cancelIngest,
  dropIngest,
  type IngestState,
  retryIngest,
} from "./api";
import IngestCard from "./components/IngestCard";
import TopBar from "./components/TopBar";
import { useActiveIngests } from "./useActiveIngests";

export interface QueueProps {
  onMenuToggle?: () => void;
  onAdd: () => void;
  onIngestDone?: () => void;
}

type Bucket = {
  label: string;
  kind: "active" | "failed" | "cancelled" | "done";
  ingests: IngestState[];
};

function bucketize(ings: IngestState[]): Bucket[] {
  const active: IngestState[] = [];
  const failed: IngestState[] = [];
  const cancelled: IngestState[] = [];
  const done: IngestState[] = [];
  for (const i of ings) {
    if (!i.done) active.push(i);
    else if (i.cancel_requested) cancelled.push(i);
    else if (i.error) failed.push(i);
    else done.push(i);
  }
  active.sort((a, b) => b.started_at - a.started_at);
  failed.sort((a, b) => b.last_event_at - a.last_event_at);
  cancelled.sort((a, b) => b.last_event_at - a.last_event_at);
  done.sort((a, b) => b.last_event_at - a.last_event_at);
  const all: Bucket[] = [
    { label: "active", kind: "active", ingests: active },
    { label: "failed", kind: "failed", ingests: failed },
    { label: "cancelled", kind: "cancelled", ingests: cancelled },
    { label: "recently finished", kind: "done", ingests: done },
  ];
  return all.filter((b) => b.ingests.length > 0);
}

export default function Queue({ onMenuToggle, onAdd, onIngestDone }: QueueProps) {
  const ingests = useActiveIngests();
  const [pendingIds, setPendingIds] = useState<Set<string>>(new Set());

  const buckets = useMemo(() => bucketize(ingests), [ingests]);
  const failedCount = buckets.find((b) => b.kind === "failed")?.ingests.length ?? 0;
  const activeCount = buckets.find((b) => b.kind === "active")?.ingests.length ?? 0;

  const markPending = useCallback((id: string, isPending: boolean) => {
    setPendingIds((prev) => {
      const next = new Set(prev);
      if (isPending) next.add(id);
      else next.delete(id);
      return next;
    });
  }, []);

  const handleRetry = useCallback(
    async (id: string, url: string | null) => {
      markPending(id, true);
      try {
        const fallback = url ?? `https://www.youtube.com/watch?v=${id}`;
        await retryIngest(id, fallback);
      } catch (e) {
        alert(`Retry failed for ${id}: ${e}`);
      } finally {
        markPending(id, false);
      }
    },
    [markPending],
  );

  const handleCancel = useCallback(
    async (id: string) => {
      markPending(id, true);
      try {
        await cancelIngest(id);
      } catch (e) {
        alert(`Cancel failed for ${id}: ${e}`);
      } finally {
        markPending(id, false);
      }
    },
    [markPending],
  );

  const handleDrop = useCallback(
    async (id: string) => {
      markPending(id, true);
      try {
        await dropIngest(id);
        onIngestDone?.();
      } catch (e) {
        alert(`Dismiss failed for ${id}: ${e}`);
      } finally {
        markPending(id, false);
      }
    },
    [markPending, onIngestDone],
  );

  const handleRetryAllFailed = useCallback(async () => {
    const failed = buckets.find((b) => b.kind === "failed")?.ingests ?? [];
    if (failed.length === 0) return;
    if (!window.confirm(`Retry all ${failed.length} failed ingest${failed.length === 1 ? "" : "s"}?`)) {
      return;
    }
    // Mark all pending up-front so the buttons disable in one render.
    setPendingIds((prev) => {
      const next = new Set(prev);
      for (const i of failed) next.add(i.id);
      return next;
    });
    // Fan out retries with a small concurrency cap so the server isn't
    // hammered. Errors on individual retries surface as alerts; the loop
    // still continues. This is a personal-tool pattern — for hundreds of
    // failures we'd swap to a server-side bulk endpoint.
    const CONCURRENCY = 3;
    let next = 0;
    const errors: string[] = [];
    async function worker() {
      while (true) {
        const idx = next++;
        if (idx >= failed.length) return;
        const job = failed[idx];
        const fallback = job.url ?? `https://www.youtube.com/watch?v=${job.id}`;
        try {
          await retryIngest(job.id, fallback);
        } catch (e) {
          errors.push(`${job.id}: ${e}`);
        } finally {
          markPending(job.id, false);
        }
      }
    }
    await Promise.all(Array.from({ length: CONCURRENCY }, worker));
    if (errors.length > 0) {
      alert(`${errors.length} retry call(s) failed:\n${errors.slice(0, 5).join("\n")}${errors.length > 5 ? "\n..." : ""}`);
    }
  }, [buckets, markPending]);

  return (
    <div className="main queue-page">
      <TopBar
        crumbs={[{ label: "library", to: "/library" }, "queue"]}
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
        actions={
          <button className="btn btn-accent" onClick={onAdd}>
            <PlusIcon /> add video
          </button>
        }
      />
      <div className="queue-body">
        <header className="queue-hero">
          <div>
            <span className="queue-kicker">ingest queue</span>
            <h1>
              {activeCount > 0 && failedCount > 0
                ? `${activeCount} running, ${failedCount} failed.`
                : activeCount > 0
                  ? `${activeCount} running.`
                  : failedCount > 0
                    ? `${failedCount} failed.`
                    : "everything's caught up."}
            </h1>
            <p className="queue-sub">
              all ingestion jobs the server knows about. failed jobs stay here
              until you retry them or dismiss them.
            </p>
          </div>
          {failedCount > 0 && (
            <div className="queue-bulk">
              <button
                type="button"
                className="btn btn-primary"
                onClick={handleRetryAllFailed}
                disabled={failedCount === 0}
              >
                retry all {failedCount} failed
              </button>
            </div>
          )}
        </header>

        {buckets.length === 0 ? (
          <div className="queue-empty">
            no ingest jobs to show.{" "}
            <Link to="/" className="accent">back to dashboard</Link>.
          </div>
        ) : (
          buckets.map((b) => (
            <QueueSection
              key={b.kind}
              bucket={b}
              pendingIds={pendingIds}
              onRetry={handleRetry}
              onCancel={handleCancel}
              onDrop={handleDrop}
            />
          ))
        )}
      </div>
    </div>
  );
}

function QueueSection({
  bucket,
  pendingIds,
  onRetry,
  onCancel,
  onDrop,
}: {
  bucket: Bucket;
  pendingIds: Set<string>;
  onRetry: (id: string, url: string | null) => void;
  onCancel: (id: string) => void;
  onDrop: (id: string) => void;
}) {
  return (
    <section className={`queue-section queue-section-${bucket.kind}`}>
      <header className="queue-section-head">
        <span className="queue-section-label">{bucket.label}</span>
        <span className="queue-section-count">
          {bucket.ingests.length} {bucket.ingests.length === 1 ? "job" : "jobs"}
        </span>
      </header>
      <div className="queue-rows">
        {bucket.ingests.map((ing) => {
          const pending = pendingIds.has(ing.id);
          return (
            <div key={ing.id} className="queue-row">
              <IngestCard ing={ing} />
              <div className="queue-row-actions">
                {bucket.kind === "active" && (
                  <button
                    type="button"
                    className="btn btn-ghost"
                    onClick={() => onCancel(ing.id)}
                    disabled={pending || ing.cancel_requested}
                  >
                    {ing.cancel_requested ? "cancelling..." : "cancel"}
                  </button>
                )}
                {(bucket.kind === "failed" || bucket.kind === "cancelled") && (
                  <>
                    <button
                      type="button"
                      className="btn btn-primary"
                      onClick={() => onRetry(ing.id, ing.url)}
                      disabled={pending}
                      title={ing.error ? `Last error: ${ing.error}` : undefined}
                    >
                      {pending ? "starting..." : "retry"}
                    </button>
                    <button
                      type="button"
                      className="btn btn-ghost"
                      onClick={() => onDrop(ing.id)}
                      disabled={pending}
                      title="Remove this entry from the queue"
                    >
                      dismiss
                    </button>
                  </>
                )}
                {bucket.kind === "done" && (
                  <button
                    type="button"
                    className="btn btn-ghost"
                    onClick={() => onDrop(ing.id)}
                    disabled={pending}
                  >
                    dismiss
                  </button>
                )}
              </div>
            </div>
          );
        })}
      </div>
    </section>
  );
}

function HamburgerIcon() {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round">
      <path d="M3 6h18M3 12h18M3 18h18" />
    </svg>
  );
}
function PlusIcon() {
  return (
    <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round">
      <path d="M12 5v14M5 12h14" />
    </svg>
  );
}
