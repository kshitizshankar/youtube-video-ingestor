import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { getTranscript, openTranscribeStream } from "./api";
import ResizeHandle from "./components/ResizeHandle";
import TerminalPanel from "./components/Terminal";
import TopBar from "./components/TopBar";
import TranscriptPane from "./components/TranscriptPane";
import VideoPlayer, { type VideoPlayerHandle } from "./components/VideoPlayer";
import type { Segment } from "./types";

const VIDEO_HEIGHT_KEY = "vvi.videoHeightPx";
const MIN_VIDEO_PX = 180;
const MIN_TAIL_PX = 260; // meta + terminal reserved below


export interface DetailProps {
  /**
   * If set, the matching pending stream (an ingest started from outside)
   * should be resumed/started against this video. Otherwise we just load the
   * existing transcript JSON.
   */
  pendingIngestUrl?: string | null;
  /** Cleared by parent once consumed. */
  onPendingIngestConsumed?: () => void;
  /** Called when the ingest completes — parent uses to bump library refreshKey. */
  onIngestDone?: () => void;
}

function fmtDuration(sec: number | null): string | null {
  if (!sec) return null;
  const h = Math.floor(sec / 3600);
  const m = Math.floor((sec % 3600) / 60);
  if (h) return `${h} hr ${m} min`;
  return `${m} min`;
}

export default function Detail({
  pendingIngestUrl, onPendingIngestConsumed, onIngestDone,
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

  const playerRef = useRef<VideoPlayerHandle>(null);
  const [currentTime, setCurrentTime] = useState(0);
  const esRef = useRef<EventSource | null>(null);

  // Resizable video / terminal split (persisted)
  const leftColRef = useRef<HTMLDivElement>(null);
  const [videoHeight, setVideoHeight] = useState<number>(() => {
    const saved = localStorage.getItem(VIDEO_HEIGHT_KEY);
    const n = saved ? parseInt(saved, 10) : NaN;
    return Number.isFinite(n) && n > 0 ? n : 440;
  });
  useEffect(() => {
    localStorage.setItem(VIDEO_HEIGHT_KEY, String(videoHeight));
  }, [videoHeight]);

  const onResizeDelta = useCallback((deltaPx: number) => {
    setVideoHeight((h) => {
      const colH = leftColRef.current?.clientHeight ?? window.innerHeight - 56;
      const max = Math.max(MIN_VIDEO_PX + 1, colH - MIN_TAIL_PX);
      return Math.max(MIN_VIDEO_PX, Math.min(max, h + deltaPx));
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

    getTranscript(videoId)
      .then((t) => {
        if (cancelled) return;
        setTitle(t.title || videoId);
        setLanguage(t.language);
        setDuration(t.duration_sec);
        setSegments(t.segments);
        setDiarized(t.diarized);
        setStatus(`${t.segments.length} segments`);
      })
      .catch(() => {
        if (cancelled) return;
        // No transcript yet — likely an in-progress ingest.
        setTitle(videoId);
        setStatus("Not transcribed yet");
      });
    return () => { cancelled = true; };
  }, [videoId]);

  // Keep latest prop callbacks + pending URL in refs so we can trigger the
  // stream on `videoId` change only. If we used pendingIngestUrl as a dep,
  // the parent clearing it (via onPendingIngestConsumed) would immediately
  // fire this effect's cleanup and close the just-opened EventSource.
  const pendingUrlRef = useRef(pendingIngestUrl);
  pendingUrlRef.current = pendingIngestUrl;
  const consumedFnRef = useRef(onPendingIngestConsumed);
  consumedFnRef.current = onPendingIngestConsumed;
  const doneFnRef = useRef(onIngestDone);
  doneFnRef.current = onIngestDone;
  const startedForIdRef = useRef<string | null>(null);

  // If we arrived here from a fresh ingest, start the SSE stream — once.
  useEffect(() => {
    if (!videoId) return;
    const url = pendingUrlRef.current;
    if (!url) return;
    if (startedForIdRef.current === videoId) return; // already started
    startedForIdRef.current = videoId;
    consumedFnRef.current?.(); // clear parent state; doesn't re-trigger (no dep)

    esRef.current?.close();
    setBusy(true);
    setSegments([]);
    setStatus("Starting");
    const nowMs = Date.now();
    setStartedAt(nowMs);
    setLastEventAt(nowMs);

    const es = openTranscribeStream(url, {});
    esRef.current = es;
    const markEvent = () => setLastEventAt(Date.now());

    es.addEventListener("phase", (ev) => {
      const d = JSON.parse((ev as MessageEvent).data);
      setStatus(d.message ?? d.phase);
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
    es.addEventListener("done", (ev) => {
      const d = JSON.parse((ev as MessageEvent).data);
      setStatus(`Done · ${d.elapsed_sec.toFixed(1)}s · ${d.realtime_factor.toFixed(1)}× realtime`);
      setBusy(false);
      es.close();
      if (esRef.current === es) esRef.current = null;
      doneFnRef.current?.();
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

    return () => {
      // Only close on actual videoId change / unmount.
      es.close();
      if (esRef.current === es) esRef.current = null;
    };
  }, [videoId]);

  const handleSeek = useCallback((s: number) => {
    playerRef.current?.seekTo(s);
  }, []);

  if (!videoId) {
    navigate("/");
    return null;
  }

  const dur = fmtDuration(duration);

  return (
    <div className="main">
      <TopBar
        crumbs={[
          { label: "Library", to: "/" },
          title,
        ]}
        actions={
          <>
            <Link to="/" className="btn btn-ghost">
              ← Library
            </Link>
          </>
        }
      />
      <div className="detail-body">
        <div className="detail-left" ref={leftColRef}>
          <div className="video-wrap" style={{ height: `${videoHeight}px` }}>
            <VideoPlayer
              videoId={videoId}
              onTimeUpdate={setCurrentTime}
              ref={playerRef}
            />
          </div>
          <ResizeHandle onDelta={onResizeDelta} />
          <div className="video-meta">
            <h2>{title}</h2>
            <div className="vm-sub">
              <span>{videoId}</span>
              {dur && (<><span>·</span><span>{dur}</span></>)}
              {language && (<><span>·</span><span style={{ textTransform: "uppercase", fontFamily: "var(--font-mono)", fontSize: 11 }}>{language}</span></>)}
              {segments.length > 0 && (<><span>·</span><span>{segments.length} segments</span></>)}
              {diarized && (<><span>·</span><span style={{ color: "var(--accent)" }}>Diarized</span></>)}
              {busy && <span className="live">{status || "Live"}</span>}
              {!busy && status && <span style={{ color: "var(--ink-3)" }}>{status}</span>}
            </div>
          </div>
          <TerminalPanel videoId={videoId} />
        </div>
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
        />
      </div>
    </div>
  );
}
