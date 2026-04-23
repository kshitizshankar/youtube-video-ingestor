import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import {
  archiveVideo,
  cancelIngest,
  getAnalysis,
  getMeta,
  getTranscript,
  type IngestState,
  listIngests,
  openAnalysisStream,
  openTranscribeStream,
  retryIngest,
} from "./api";
import { formatCount, formatSpeakers, formatYtDate } from "./format";
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
  const [regenerating, setRegenerating] = useState(false);
  const [analysisBusy, setAnalysisBusy] = useState(false);
  const [analysisStartedAt, setAnalysisStartedAt] = useState<number | null>(null);
  const [analysisPhase, setAnalysisPhase] = useState<string>("");
  const [analysisTokensIn, setAnalysisTokensIn] = useState<number>(0);
  const [analysisTokensOut, setAnalysisTokensOut] = useState<number>(0);
  const [analysisCostUsd, setAnalysisCostUsd] = useState<number>(0);

  const playerRef = useRef<VideoPlayerHandle>(null);
  const [currentTime, setCurrentTime] = useState(0);
  const esRef = useRef<EventSource | null>(null);

  // Server-side ingest record for this video, populated by polling
  // /api/ingests. Lets us show live state for jobs started in another tab
  // or recovered after a page refresh, and drives the Retry / Cancel buttons.
  const [ingestRecord, setIngestRecord] = useState<IngestState | null>(null);
  const [retryPending, setRetryPending] = useState(false);
  const [cancelPending, setCancelPending] = useState(false);

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

    return () => { cancelled = true; };
  }, [videoId]);

  const handleRegenerateAnalysis = useCallback(() => {
    if (!videoId) return;
    setRegenerating(true);
    setAnalysisBusy(true);
    setAnalysisStartedAt(Date.now());
    setAnalysisPhase("Launching Claude");
    setAnalysisTokensIn(0);
    setAnalysisTokensOut(0);
    setAnalysisCostUsd(0);
    setAnalysisError(null);

    const es = openAnalysisStream(videoId);
    let finished = false;
    const finish = () => {
      if (finished) return;
      finished = true;
      setRegenerating(false);
      setAnalysisBusy(false);
      setAnalysisStartedAt(null);
      setAnalysisPhase("");
      es.close();
    };

    const applyUsage = (d: Record<string, unknown>) => {
      if (typeof d.tokens_in === "number") setAnalysisTokensIn(d.tokens_in);
      if (typeof d.tokens_out === "number") setAnalysisTokensOut(d.tokens_out);
      if (typeof d.cost_usd === "number") setAnalysisCostUsd(d.cost_usd);
    };

    es.addEventListener("progress", (ev) => {
      try {
        const d = JSON.parse((ev as MessageEvent).data);
        if (d.message) setAnalysisPhase(d.message);
        applyUsage(d);
      } catch { /* ignore */ }
    });
    es.addEventListener("done", async (ev) => {
      try {
        const d = JSON.parse((ev as MessageEvent).data);
        applyUsage(d);
      } catch { /* ignore */ }
      try {
        const a = await getAnalysis(videoId);
        setAnalysis(a);
      } catch (e) {
        setAnalysisError(String(e));
      } finally {
        finish();
      }
    });
    es.addEventListener("error", (ev) => {
      const me = ev as MessageEvent;
      let msg = "analysis failed";
      if (me.data) {
        try { msg = JSON.parse(me.data).message ?? msg; } catch { /* ignore */ }
      }
      setAnalysisError(msg);
      finish();
    });
  }, [videoId]);

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
      await retryIngest(videoId, ingestRecord?.url ?? undefined);
      // Poll will pick up the new record on the next tick.
      setBusy(true);
      setStatus("Retry queued");
      setStartedAt(Date.now());
    } catch (e) {
      alert(`Retry failed: ${e}`);
    } finally {
      setRetryPending(false);
    }
  }, [videoId, ingestRecord?.url]);

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
          onRegenerateAnalysis={handleRegenerateAnalysis}
          regenerating={regenerating}
          analysisBusy={analysisBusy}
          analysisStartedAt={analysisStartedAt}
          analysisPhase={analysisPhase}
          analysisTokensIn={analysisTokensIn}
          analysisTokensOut={analysisTokensOut}
          analysisCostUsd={analysisCostUsd}
          transcript={transcript}
          speakerNames={meta?.speaker_names ?? {}}
        />
      </div>
    </div>
  );
}
