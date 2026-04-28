import {
  forwardRef,
  useEffect,
  useImperativeHandle,
  useRef,
} from "react";
import type { MediaPlayerHandle } from "./VideoPlayer";

/** Same minimal shape as VideoPlayer's handle so Detail.tsx can hold a single
 *  ref against either player. */
export type AudioPlayerHandle = MediaPlayerHandle;

export interface AudioPlayerProps {
  /** Video id — accepted for parity with VideoPlayer, currently unused
   *  internally because the parent already resolved it into `audioUrl`. */
  videoId: string;
  /** Pre-built authenticated URL pointing at /api/transcripts/{id}/audio.
   *  Browsers can't attach custom headers to <audio src=...>, so the auth
   *  token must already be encoded into the query string (see api.audioUrl). */
  audioUrl: string;
  /** Cover art for the episode / show. When null, a tasteful gradient
   *  placeholder fills the cover slot instead. */
  imageUrl: string | null;
  /** Mirrors VideoPlayer — fires periodically with the playhead position so
   *  the transcript pane can highlight the current segment. */
  onTimeUpdate?: (seconds: number) => void;
}

const AudioPlayer = forwardRef<AudioPlayerHandle, AudioPlayerProps>(
  function AudioPlayer({ audioUrl, imageUrl, onTimeUpdate }, ref) {
    const audioRef = useRef<HTMLAudioElement | null>(null);
    const onTimeRef = useRef(onTimeUpdate);
    onTimeRef.current = onTimeUpdate;

    useImperativeHandle(
      ref,
      () => ({
        seekTo(s) {
          const a = audioRef.current;
          if (!a) return;
          // Match VideoPlayer's clamp-to-zero behaviour. We intentionally do
          // NOT auto-play here — the user clicked a transcript timestamp,
          // they didn't ask for playback to start.
          try {
            a.currentTime = Math.max(0, s);
          } catch {
            /* element may not have metadata yet — ignored */
          }
        },
      }),
      [],
    );

    // Wire the native timeupdate event to the parent's onTimeUpdate. Using
    // an effect (rather than the JSX prop) lets us read the latest callback
    // out of the ref without remounting the audio element on every render.
    useEffect(() => {
      const a = audioRef.current;
      if (!a) return;
      const handle = () => {
        const t = a.currentTime;
        if (typeof t === "number" && !Number.isNaN(t)) {
          onTimeRef.current?.(t);
        }
      };
      a.addEventListener("timeupdate", handle);
      return () => a.removeEventListener("timeupdate", handle);
    }, [audioUrl]);

    return (
      <div className="audio-player">
        {imageUrl ? (
          <img
            className="audio-cover"
            src={imageUrl}
            alt=""
            loading="lazy"
            draggable={false}
          />
        ) : (
          <div className="audio-cover audio-cover-fallback" aria-hidden="true" />
        )}
        <div className="audio-controls">
          <audio
            ref={audioRef}
            src={audioUrl}
            controls
            preload="metadata"
            style={{ width: "100%" }}
          />
        </div>
      </div>
    );
  },
);

export default AudioPlayer;
