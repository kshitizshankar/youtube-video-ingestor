import { forwardRef, useEffect, useImperativeHandle, useRef, useState } from "react";
import { loadYouTubeAPI, type YTPlayerInstance } from "../youtubePlayer";

export interface VideoPlayerHandle {
  seekTo(seconds: number): void;
  play(): void;
  pause(): void;
}

export interface VideoPlayerProps {
  videoId: string;
  onTimeUpdate?: (seconds: number) => void;
}

const VideoPlayer = forwardRef<VideoPlayerHandle, VideoPlayerProps>(
  function VideoPlayer({ videoId, onTimeUpdate }, ref) {
    const containerRef = useRef<HTMLDivElement>(null);
    const playerRef = useRef<YTPlayerInstance | null>(null);
    const [ready, setReady] = useState(false);
    const onTimeRef = useRef(onTimeUpdate);
    onTimeRef.current = onTimeUpdate;

    useImperativeHandle(ref, () => ({
      seekTo(s) {
        playerRef.current?.seekTo(Math.max(0, s), true);
        playerRef.current?.playVideo();
      },
      play()  { playerRef.current?.playVideo(); },
      pause() { playerRef.current?.pauseVideo(); },
    }), []);

    useEffect(() => {
      let mounted = true;
      let pollId: number | undefined;

      loadYouTubeAPI().then((YT) => {
        if (!mounted || !containerRef.current) return;
        if (playerRef.current) {
          try { playerRef.current.destroy(); } catch { /* noop */ }
          playerRef.current = null;
        }
        const host = document.createElement("div");
        containerRef.current.innerHTML = "";
        containerRef.current.appendChild(host);
        playerRef.current = new YT.Player(host, {
          videoId,
          width: "100%",
          height: "100%",
          playerVars: { modestbranding: 1, rel: 0, iv_load_policy: 3, playsinline: 1 },
          events: {
            onReady: () => {
              setReady(true);
              pollId = window.setInterval(() => {
                if (playerRef.current) {
                  const t = playerRef.current.getCurrentTime();
                  if (typeof t === "number") onTimeRef.current?.(t);
                }
              }, 250);
            },
          },
        });
      });

      return () => {
        mounted = false;
        if (pollId !== undefined) clearInterval(pollId);
        if (playerRef.current) {
          try { playerRef.current.destroy(); } catch { /* noop */ }
          playerRef.current = null;
        }
      };
    }, [videoId]);

    // Parent wraps us in a `.video-wrap` (with height control / aspect), so
    // we render the YT host and loading indicator positioned inside that.
    return (
      <>
        <div ref={containerRef} style={{ position: "absolute", inset: 0 }} />
        {!ready && <div className="video-loading">Loading video…</div>}
      </>
    );
  }
);

export default VideoPlayer;
