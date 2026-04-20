/**
 * Minimal YouTube IFrame API loader.
 *
 * Lazy-loads the script on first call; subsequent callers get the cached YT
 * namespace. We declare the bare minimum types we use — no @types/youtube
 * dependency.
 */

interface YTPlayerOptions {
  videoId: string;
  height?: string | number;
  width?: string | number;
  playerVars?: Record<string, string | number>;
  events?: {
    onReady?: (e: { target: YTPlayerInstance }) => void;
    onStateChange?: (e: { data: number; target: YTPlayerInstance }) => void;
    onError?: (e: { data: number }) => void;
  };
}

export interface YTPlayerInstance {
  seekTo(seconds: number, allowSeekAhead: boolean): void;
  playVideo(): void;
  pauseVideo(): void;
  stopVideo(): void;
  getCurrentTime(): number;
  getDuration(): number;
  getPlayerState(): number;
  destroy(): void;
}

interface YTNamespace {
  Player: new (el: HTMLElement | string, opts: YTPlayerOptions) => YTPlayerInstance;
  PlayerState: {
    UNSTARTED: -1;
    ENDED: 0;
    PLAYING: 1;
    PAUSED: 2;
    BUFFERING: 3;
    CUED: 5;
  };
}

declare global {
  interface Window {
    YT?: YTNamespace;
    onYouTubeIframeAPIReady?: () => void;
  }
}

let pending: Promise<YTNamespace> | null = null;

export function loadYouTubeAPI(): Promise<YTNamespace> {
  if (window.YT && window.YT.Player) {
    return Promise.resolve(window.YT);
  }
  if (pending) return pending;

  pending = new Promise((resolve) => {
    const prior = window.onYouTubeIframeAPIReady;
    window.onYouTubeIframeAPIReady = () => {
      prior?.();
      resolve(window.YT!);
    };
    if (!document.querySelector('script[src*="youtube.com/iframe_api"]')) {
      const s = document.createElement("script");
      s.src = "https://www.youtube.com/iframe_api";
      s.async = true;
      document.head.appendChild(s);
    }
  });
  return pending;
}
