import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import {
  archiveVideo,
  cancelIngest,
  discardOrphanFolder,
  getAnalysis,
  getMeta,
  getTranscript,
  getVideoFolderStatus,
  type IngestState,
  listIngests,
  openTranscribeStream,
  retryIngest,
  type VideoFolderStatus,
} from "./api";
import { loadAnalysisDefaults, saveAnalysisDefaults } from "./analysisDefaults";
import { formatCount, formatSpeakers, formatYtDate } from "./format";
import AnalysisProgress from "./components/AnalysisProgress";
import AnalyzeTriggerModal from "./components/AnalyzeTriggerModal";
import ResizeHandle from "./components/ResizeHandle";
import TopBar from "./components/TopBar";
import TranscriptPane from "./components/TranscriptPane";
import VideoDetailsPanel from "./components/VideoDetailsPanel";
import VideoPlayer, { type VideoPlayerHandle } from "./components/VideoPlayer";
import type { Analysis, Segment, VideoMeta } from "./types";

const TRANSCRIPT_W_KEY = "vvi.transcriptWidthPx";
const MIN_TRANSCRIPT_W = 320;
const MIN_LEFT_W = 360;


export interface DetailProps {
  /**
   * If set, the matching pending stream (an ingest started from outside)
   * should be resumed/started against this video. Otherwise we just load the
   * existing transcript JSON.
   */
  pendingIngestUrl?: string | null;
  /** Options chosen in the Ingest modal (model, diarize, batched). */
  pendingIngestOpts?: { diarize: boolean; model: string; batched: boolean } | null;
  /** Cleared by parent once consumed. */
  onPendingIngestConsumed?: () => void;
  /** Called when the ingest completes — parent uses to bump library refreshKey. */
  onIngestDone?: () => void;
  /** Open the mobile drawer. Only used on narrow viewports. */
  onMenuToggle?: () => void;
}

function fmtDuration(sec: number | null): string | null {
  if (!sec) return null;
  const h = Math.floor(sec / 3600);
  const m = Math.floor((sec % 3600) / 60);
  if (h) return `${h} hr ${m} min`;
  return `${m} min`;
}

function HamburgerIcon() {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round">
      <path d="M3 6h18M3 12h18M3 18h18" />
    </svg>
  );
}

export default function Detail({
  pendingIngestUrl, pendingIngestOpts, onPendingIngestConsumed, onIngestDone, onMenuToggle,
}: DetailProps) {
  const { videoId } = useParams<{ videoId: string }>();
  const navigate = useNavigate();

  const [title, setTitle] = useState<string>("Loading…");
  const [language, setLanguage] = useState<string | null>(null);
  const [duration, setDuration] = useState<number | null>(null);
  const [segments, setSegments] = useState<Segment[]>([]);
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState<string>("");
  const [diarized, setDiarized] = useState(false);
  const [startedAt, setStartedAt] = useState<number | null>(null);
  const [lastEventAt, setLastEventAt] = useState<number | null>(null);
  const [transcript, setTranscript] = useState<import("./types").Transcript | null>(null);
  const [meta, setMeta] = useState<VideoMeta | null>(null);
  const [analysis, setAnalysis] = useState<Analysis | null>(null);
  const [analysisLoading, setAnalysisLoading] = useState(false);
  const [analysisError, setAnalysisError] = useState<string | null>(null);
  // Slice 3 — provider/model picker modal + live progress surface.
  const [triggerOpen, setTriggerOpen] = useState(false);
  const [activeRun, setActiveRun] = useState<{
    /** Monotonically increments each time we kick off a new run — used as
     *  the React `key` for AnalysisProgress so re-runs fully reset its
     *  internal SSE subscription state. */
    runId: number;
    provider: string;
    model?: string;
  } | null>(null);
  // Remember the last chosen provider/model so the modal preselects it on
  // the next open — small quality-of-life win for repeat runs. Seeded from
  // localStorage so Settings-saved defaults flow through even before the
  // user has run an analysis in this session.
  const [lastChosen, setLastChosen] = useState<{
    provider: string;
    model?: string;
    skipDialog: boolean;
  }>(() => {
    const d = loadAnalysisDefaults();
    if (d) {
      return {
        provider: d.provider,
        model: d.model ?? undefined,
        skipDialog: d.skip_dialog,
      };
    }
    return { provider: "claude_cli", skipDialog: false };
  });

  const playerRef = useRef<VideoPlayerHandle>(null);
  const [currentTime, setCurrentTime] = useState(0);
  const esRef = useRef<EventSource | null>(null);

  // Server-side ingest record for this video, populated by polling
  // /api/ingests. Lets us show live state for jobs started in another tab
  // or recovered after a page refresh, and drives the Retry / Cancel buttons.
  const [ingestRecord, setIngestRecord] = useState<IngestState | null>(null);
  const [retryPending, setRetryPending] = useState(false);
  const [cancelPending, setCancelPending] = useState(false);
  // Detects the "started ingest but never finished" orphan state — folder
  // exists on disk, no transcript inside, no active /api/ingests record.
  // Surfaces a Retry / Discard banner so the user isn't stuck on a page
  // saying "Waiting for transcript" forever.
  const [orphan, setOrphan] = useState<VideoFolderStatus | null>(null);
  const [discardPending, setDiscardPending] = useState(false);

  // Resizable transcript / video split (persisted). Only used on desktop —
  // on mobile the detail body stacks vertically and the handle is hidden.
  const bodyRef = useRef<HTMLDivElement>(null);
  const [transcriptWidth, setTranscriptWidth] = useState<number>(() => {
    const saved = localStorage.getItem(TRANSCRIPT_W_KEY);
    const n = saved ? parseInt(saved, 10) : NaN;
    return Number.isFinite(n) && n > 0 ? n : 440;
  });
  useEffect(() => {
    localStorage.setItem(TRANSCRIPT_W_KEY, String(transcriptWidth));
  }, [transcriptWidth]);
  const onTranscriptResize = useCallback((deltaPx: number) => {
    setTranscriptWidth((w) => {
      const bodyW = bodyRef.current?.clientWidth ?? window.innerWidth;
      const max = Math.max(MIN_TRANSCRIPT_W, bodyW - MIN_LEFT_W);
      // Dragging right → transcript shrinks; dragging left → transcript grows.
      return Math.max(MIN_TRANSCRIPT_W, Math.min(max, w - deltaPx));
    });
  }, []);

  // Load existing transcript (if any) when videoId changes
  useEffect(() => {
    if (!videoId) return;
    let cancelled = false;
    setSegments([]);
    setLanguage(null);
    setDuration(null);
    setStatus("");
    setBusy(false);
    setTitle("Loading…");
    setDiarized(false);
    setAnalysis(null);
    setAnalysisError(null);

    getTranscript(videoId)
      .then((t) => {
        if (cancelled) return;
        setTranscript(t);
        setTitle(t.title || videoId);
        setLanguage(t.language);
        setDuration(t.duration_sec);
        setSegments(t.segments);
        setDiarized(t.diarized);
        setStatus(`${t.segments.length} segments`);
      })
      .catch(() => {
        if (cancelled) return;
        setTranscript(null);
        setTitle(videoId);
        setStatus("Not transcribed yet");
      });

    setAnalysisLoading(true);
    getAnalysis(videoId)
      .then((a) => { if (!cancelled) setAnalysis(a); })
      .catch(() => { /* swallow — endpoint can 404 */ })
      .finally(() => { if (!cancelled) setAnalysisLoading(false); });

    setMeta(null);
    getMeta(videoId)
      .then((m) => { if (!cancelled) setMeta(m); })
      .catch(() => { /* 404 before first transcript write */ });

    setOrphan(null);
    return () => { cancelled = true; };
  }, [videoId]);

  // Orphan detection: when no transcript loaded AND no active ingest record,
  // peek at the folder on disk. If a folder exists but has no transcript,
  // we're looking at a crashed-mid-ingest state and the user gets a recovery
  // banner. Re-runs whenever those signals change.
  useEffect(() => {
    if (!videoId) return;
    if (transcript) { setOrphan(null); return; }
    if (ingestRecord && !ingestRecord.done) { setOrphan(null); return; }
    if (pendingIngestUrl) { setOrphan(null); return; }
    let cancelled = false;
    getVideoFolderStatus(videoId)
      .then((s) => { if (!cancelled) setOrphan(s.exists && !s.has_transcript ? s : null); })
      .catch(() => { if (!cancelled) setOrphan(null); });
    return () => { cancelled = true; };
  }, [videoId, transcript, ingestRecord, pendingIngestUrl]);

  // Mounts AnalysisProgress, whose SSE subscription is the SINGLE entry
  // point that starts the backend worker. Bumping runId remounts it cleanly
  // for re-runs. There is intentionally no separate POST here: a previous
  // version posted /analyze AND opened the SSE, which spawned two parallel
  // analyses per click.
  const fireAnalysis = useCallback(
    (provider: string, model: string | undefined) => {
      if (!videoId) return;
      setAnalysisError(null);
      setActiveRun((prev) => ({
        runId: (prev?.runId ?? 0) + 1,
        provider,
        model,
      }));
    },
    [videoId],
  );

  // Slice 3 — the entry point for "the user clicked Analyze". If they have
  // previously checked "Don't show this again", skip straight to firing the
  // saved provider/model. Otherwise open the picker modal as before.
  const handleOpenAnalyzeModal = useCallback(() => {
    setAnalysisError(null);
    const d = loadAnalysisDefaults();
    if (d && d.skip_dialog && d.provider) {
      fireAnalysis(d.provider, d.model ?? undefined);
      return;
    }
    setTriggerOpen(true);
  }, [fireAnalysis]);

  const handleAnalyzeSubmit = useCallback(
    async (provider: string, model: string | undefined, skipDialog: boolean) => {
      if (!videoId) return;
      // Persist the chosen defaults regardless of the toggle — this keeps
      // preselection working on the next open and lets Settings reflect the
      // most recent choice. The toggle itself controls whether we show the
      // modal next time.
      saveAnalysisDefaults({
        provider,
        model: model ?? null,
        skip_dialog: skipDialog,
      });
      setLastChosen({ provider, model, skipDialog });
      fireAnalysis(provider, model);
    },
    [videoId, fireAnalysis],
  );

  const handleAnalysisDone = useCallback(
    async (result: Analysis, _meta: { durationMs: number; usage: import("./types").AnalysisUsage }) => {
      if (!videoId) return;
      // Trust the streamed `result`, but also refetch the persisted analysis
      // so `_meta` (generated_at / num_turns / etc.) is populated.
      setAnalysis(result);
      try {
        const a = await getAnalysis(videoId);
        if (a) setAnalysis(a);
      } catch {
        /* swallow — the streamed result is already on screen */
      }
      // Keep the "done" one-liner visible for ~4s before tearing down, so
      // users see a confirmation of cost/tokens before it vanishes.
      window.setTimeout(() => setActiveRun(null), 4000);
    },
    [videoId],
  );

  const handleAnalysisError = useCallback((msg: string) => {
    setAnalysisError(msg);
    // Leave the error state mounted so the "Try again" retry button is
    // reachable. activeRun clears when the user hits retry.
  }, []);

  const handleAnalysisRetry = useCallback(() => {
    setActiveRun(null);
    setAnalysisError(null);
    setTriggerOpen(true);
  }, []);

  // Poll /api/ingests so we can surface server-side job state for this video
  // even when no SSE is attached (fresh tab, page refresh, job started in
  // another window). Stops once we have a transcript AND the server has no
  // active record for this id — any subsequent ingest re-arms via
  // pendingIngestUrl, which has its own SSE.
  const transcriptRef = useRef(transcript);
  transcriptRef.current = transcript;
  useEffect(() => {
    if (!videoId) return;
    let cancelled = false;
    let intervalId: number | null = null;
    const stop = () => {
      if (intervalId !== null) { clearInterval(intervalId); intervalId = null; }
    };

    const tick = async () => {
      try {
        const all = await listIngests();
        if (cancelled) return;
        const mine = all.find((i) => i.id === videoId) ?? null;
        setIngestRecord(mine);

        if (!mine) {
          // No active record. If we already have a transcript, we're in the
          // stable terminal state — no reason to keep polling.
          if (transcriptRef.current) stop();
          return;
        }

        // If no local SSE is attached, mirror the server record into the
        // display state so the user sees live phase + elapsed regardless.
        if (!esRef.current) {
          if (!mine.done) setBusy(true);
          setStatus(mine.phase || "working");
          setStartedAt(mine.started_at ? mine.started_at * 1000 : null);
          setLastEventAt(mine.last_event_at ? mine.last_event_at * 1000 : null);
          if (mine.title) setTitle(mine.title);
          if (mine.duration_sec) setDuration(mine.duration_sec);
          if (mine.done) {
            setBusy(false);
            // Pull the freshly-written transcript if the job just finished cleanly.
            if (!mine.error) {
              getTranscript(videoId).then((t) => {
                if (cancelled) return;
                setTranscript(t); setTitle(t.title || videoId);
                setLanguage(t.language); setDuration(t.duration_sec);
                setSegments(t.segments); setDiarized(t.diarized);
                setStatus(`${t.segments.length} segments`);
              }).catch(() => {});
              getAnalysis(videoId).then((a) => { if (!cancelled) setAnalysis(a); }).catch(() => {});
            }
          }
        }
      } catch { /* swallow — next tick will retry */ }
    };
    tick();
    intervalId = window.setInterval(tick, 2500);
    return () => { cancelled = true; stop(); };
  }, [videoId]);

  const handleRetryIngest = useCallback(async () => {
    if (!videoId) return;
    setRetryPending(true);
    try {
      // Crashed-ingest case: registry was wiped, so ingestRecord?.url is
      // null. The canonical YouTube URL is derivable from the videoId — let
      // the server use that instead of erroring.
      const fallbackUrl = `https://www.youtube.com/watch?v=${videoId}`;
      await retryIngest(videoId, ingestRecord?.url ?? fallbackUrl);
      setOrphan(null);
      setBusy(true);
      setStatus("Retry queued");
      setStartedAt(Date.now());
    } catch (e) {
      alert(`Retry failed: ${e}`);
    } finally {
      setRetryPending(false);
    }
  }, [videoId, ingestRecord?.url]);

  const handleDiscardOrphan = useCallback(async () => {
    if (!videoId) return;
    setDiscardPending(true);
    try {
      await discardOrphanFolder(videoId);
      setOrphan(null);
      navigate("/");
    } catch (e) {
      alert(`Discard failed: ${e}`);
    } finally {
      setDiscardPending(false);
    }
  }, [videoId, navigate]);

  const handleCancelIngest = useCallback(async () => {
    if (!videoId) return;
    setCancelPending(true);
    try {
      await cancelIngest(videoId);
      setStatus("Cancelling… (takes effect at next phase boundary)");
    } catch (e) {
      alert(`Cancel failed: ${e}`);
    } finally {
      setCancelPending(false);
    }
  }, [videoId]);

  // Keep latest callbacks in refs so the SSE-start effect can read them
  // without taking them as deps.
  const pendingOptsRef = useRef(pendingIngestOpts);
  pendingOptsRef.current = pendingIngestOpts;
  const consumedFnRef = useRef(onPendingIngestConsumed);
  consumedFnRef.current = onPendingIngestConsumed;
  const doneFnRef = useRef(onIngestDone);
  doneFnRef.current = onIngestDone;

  // Close the SSE on videoId change / unmount. Split out from the start
  // effect so we can safely re-fire the start effect (on a fresh
  // pendingIngestUrl) without the cleanup closing the just-opened stream.
  useEffect(() => {
    return () => {
      esRef.current?.close();
      esRef.current = null;
    };
  }, [videoId]);

  // Start a fresh SSE stream whenever pendingIngestUrl arrives (including
  // re-ingest of the same videoId). Keyed on both so null→value transitions
  // from the parent always fire this. The start logic itself calls
  // consumedFnRef to null out pendingIngestUrl, which re-fires this effect
  // with url=null — the early-return guards handle that.
  useEffect(() => {
    if (!videoId) return;
    const url = pendingIngestUrl;
    if (!url) return;
    consumedFnRef.current?.(); // clears parent state; our next fire returns early

    esRef.current?.close();
    setBusy(true);
    setSegments([]);
    setStatus("Starting");
    const nowMs = Date.now();
    setStartedAt(nowMs);
    setLastEventAt(nowMs);

    const opts = pendingOptsRef.current ?? { diarize: false, model: "distil-large-v3", batched: true };
    const es = openTranscribeStream(url, opts);
    esRef.current = es;
    const markEvent = () => setLastEventAt(Date.now());

    es.addEventListener("phase", (ev) => {
      const d = JSON.parse((ev as MessageEvent).data);
      const msg = d.message ?? d.phase;
      setStatus(msg);
      markEvent();
    });
    es.addEventListener("downloaded", (ev) => {
      const d = JSON.parse((ev as MessageEvent).data);
      setTitle(d.title || videoId);
      setDuration(d.duration_sec);
      markEvent();
    });
    es.addEventListener("language", (ev) => {
      const d = JSON.parse((ev as MessageEvent).data);
      setLanguage(d.language);
      markEvent();
    });
    es.addEventListener("segment", (ev) => {
      const seg = JSON.parse((ev as MessageEvent).data) as Segment;
      setSegments((prev) => [...prev, seg]);
      markEvent();
    });
    // Silent keep-alive event emitted every ~5s from the worker thread so
    // our stall detector doesn't fire during long synchronous whisperx blocks.
    es.addEventListener("heartbeat", () => {
      markEvent();
    });
    es.addEventListener("done", (ev) => {
      const d = JSON.parse((ev as MessageEvent).data);
      setStatus(`Done · ${d.elapsed_sec.toFixed(1)}s · ${d.realtime_factor.toFixed(1)}× realtime`);
      setBusy(false);
      es.close();
      if (esRef.current === es) esRef.current = null;
      doneFnRef.current?.();
      // Transcript now carries timing meta; re-fetch it. Analysis is user-
      // triggered now (see handleRegenerateAnalysis) — no auto-fetch here.
      if (videoId) {
        getTranscript(videoId).then(setTranscript).catch(() => {});
      }
    });
    es.addEventListener("error", (ev) => {
      const me = ev as MessageEvent;
      let msg = "stream error";
      if (me.data) {
        try { msg = JSON.parse(me.data).message ?? msg; } catch { /* ignore */ }
      }
      setStatus(`Error: ${msg}`);
      setBusy(false);
      es.close();
      if (esRef.current === es) esRef.current = null;
    });

    // No cleanup return: we intentionally do NOT close on re-fire (e.g. when
    // the parent nulls pendingIngestUrl). Cleanup on videoId change happens
    // in the separate effect above.
  }, [videoId, pendingIngestUrl]);

  const handleSeek = useCallback((s: number) => {
    playerRef.current?.seekTo(s);
  }, []);

  const handleArchive = useCallback(async () => {
    if (!videoId) return;
    try {
      await archiveVideo(videoId);
      navigate("/");
    } catch (e) {
      alert(`Archive failed: ${e}`);
    }
  }, [videoId, navigate]);

  if (!videoId) {
    navigate("/");
    return null;
  }

  const dur = fmtDuration(duration);

  return (
    <div className="main">
      <TopBar
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
        crumbs={[
          { label: "Library", to: "/" },
          title,
        ]}
        actions={
          <>
            {ingestRecord && !ingestRecord.done && (
              <button
                type="button"
                className="btn btn-ghost"
                onClick={handleCancelIngest}
                disabled={cancelPending || ingestRecord.cancel_requested}
                title="Abort at next phase boundary"
              >
                {ingestRecord.cancel_requested ? "Cancelling…" : "Cancel"}
              </button>
            )}
            {ingestRecord && ingestRecord.done && ingestRecord.error && (
              <button
                type="button"
                className="btn btn-primary"
                onClick={handleRetryIngest}
                disabled={retryPending}
                title={`Re-run pipeline. Last error: ${ingestRecord.error}`}
              >
                {retryPending ? "Starting…" : "Retry"}
              </button>
            )}
            <Link to="/" className="btn btn-ghost hide-on-narrow">
              ← Library
            </Link>
          </>
        }
      />
      {ingestRecord?.error && (
        <div
          role="alert"
          style={{
            padding: "8px 14px",
            borderBottom: "1px solid var(--border)",
            background: "color-mix(in srgb, var(--danger, #ff4d6d) 10%, transparent)",
            color: "var(--danger, #ff4d6d)",
            fontSize: 12.5,
            fontFamily: "var(--font-mono)",
          }}
        >
          Ingest error: {ingestRecord.error}
          {ingestRecord.url && (
            <>
              {"  "}·{"  "}
              <span style={{ color: "var(--ink-3)" }}>
                source: {ingestRecord.url}
              </span>
            </>
          )}
        </div>
      )}
      {orphan && (
        <div role="alert" className="orphan-banner">
          <div className="orphan-banner-body">
            <span className="orphan-banner-icon" aria-hidden>!</span>
            <div>
              <div className="orphan-banner-title">Previous ingest didn't finish</div>
              <div className="orphan-banner-detail">
                A folder for this video exists but it's empty (no transcript was written).
                Most likely the server was restarted mid-ingest. The original job's record is gone.
                {orphan.files.length > 0 && (
                  <>
                    {" "}Partial files: <span className="orphan-files">{orphan.files.join(", ")}</span>.
                  </>
                )}
              </div>
            </div>
          </div>
          <div className="orphan-banner-actions">
            <button
              type="button"
              className="btn btn-primary"
              onClick={handleRetryIngest}
              disabled={retryPending}
            >
              {retryPending ? "Starting…" : "Retry ingest"}
            </button>
            <button
              type="button"
              className="btn btn-ghost"
              onClick={handleDiscardOrphan}
              disabled={discardPending}
              title="Delete the empty folder and go back"
            >
              {discardPending ? "Discarding…" : "Discard"}
            </button>
          </div>
        </div>
      )}
      <div
        className="detail-body"
        ref={bodyRef}
        style={{ gridTemplateColumns: `1fr 6px ${transcriptWidth}px` }}
      >
        <div className="detail-left">
          <div className="video-wrap">
            <VideoPlayer
              videoId={videoId}
              onTimeUpdate={setCurrentTime}
              ref={playerRef}
            />
          </div>
          <div className="video-meta">
            <h2>{title}</h2>
            <div className="vm-sub">
              {transcript?.channel && (
                <>
                  {transcript?.channel_url ? (
                    <a
                      className="vm-channel"
                      href={transcript.channel_url}
                      target="_blank"
                      rel="noopener noreferrer"
                    >{transcript.channel}</a>
                  ) : (
                    <span className="vm-channel">{transcript.channel}</span>
                  )}
                  <span className="vm-sep">·</span>
                </>
              )}
              {transcript?.upload_date && (() => {
                const d = formatYtDate(transcript.upload_date);
                return d ? (<><span>{d}</span><span className="vm-sep">·</span></>) : null;
              })()}
              {transcript?.view_count != null && (
                <><span>{formatCount(transcript.view_count)} views</span><span className="vm-sep">·</span></>
              )}
              {transcript?.like_count != null && (
                <><span>{formatCount(transcript.like_count)} likes</span><span className="vm-sep">·</span></>
              )}
              {dur && (<><span>{dur}</span><span className="vm-sep">·</span></>)}
              {language && (
                <><span className="vm-lang">{language.toUpperCase()}</span><span className="vm-sep">·</span></>
              )}
              {segments.length > 0 && (
                <><span>{segments.length.toLocaleString()} segments</span></>
              )}
              {(transcript?.speaker_count && transcript.speaker_count > 0) ? (
                <><span className="vm-sep">·</span><span>{formatSpeakers(transcript.speaker_count, diarized)}</span></>
              ) : null}
              {busy && <><span className="vm-sep">·</span><span className="live">{status || "Live"}</span></>}
            </div>
          </div>
          <VideoDetailsPanel
            videoId={videoId}
            videoUrl={transcript?.url ?? `https://www.youtube.com/watch?v=${videoId}`}
            shareUrl={`${window.location.origin}/v/${videoId}`}
            diarized={diarized}
            segments={segments}
            meta={meta}
            onMetaChange={setMeta}
            onArchive={transcript ? handleArchive : undefined}
            onMetadataRefreshed={() => {
              if (videoId) getTranscript(videoId).then(setTranscript).catch(() => {});
            }}
          />
        </div>
        <ResizeHandle orientation="vertical" onDelta={onTranscriptResize} />
        <TranscriptPane
          segments={segments}
          currentTime={currentTime}
          followLive={busy}
          onSeek={handleSeek}
          busy={busy}
          phase={status}
          startedAt={startedAt}
          lastEventAt={lastEventAt}
          duration={duration}
          analysis={analysis}
          analysisLoading={analysisLoading}
          analysisError={analysisError}
          onRegenerateAnalysis={handleOpenAnalyzeModal}
          regenerating={activeRun !== null}
          analysisProgressNode={
            activeRun ? (
              <AnalysisProgress
                key={activeRun.runId}
                videoId={videoId}
                provider={activeRun.provider}
                model={activeRun.model}
                onDone={handleAnalysisDone}
                onError={handleAnalysisError}
                onRetry={handleAnalysisRetry}
              />
            ) : null
          }
          transcript={transcript}
          speakerNames={meta?.speaker_names ?? {}}
        />
      </div>
      <AnalyzeTriggerModal
        open={triggerOpen}
        onClose={() => setTriggerOpen(false)}
        onSubmit={handleAnalyzeSubmit}
        initialProvider={lastChosen.provider}
        initialModel={lastChosen.model}
        initialSkipDialog={lastChosen.skipDialog}
      />
    </div>
  );
}
