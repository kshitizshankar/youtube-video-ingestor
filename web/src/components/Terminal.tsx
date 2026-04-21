import { useEffect, useRef, useState } from "react";
import { Terminal as XTerm } from "@xterm/xterm";
import { FitAddon } from "@xterm/addon-fit";
import { WebLinksAddon } from "@xterm/addon-web-links";
import { WebglAddon } from "@xterm/addon-webgl";
import "@xterm/xterm/css/xterm.css";
import { ptyWebSocketUrl } from "../api";

export interface TerminalPanelProps {
  videoId?: string;
}

export default function TerminalPanel({ videoId }: TerminalPanelProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const [conn, setConn] = useState<"connecting" | "open" | "closed" | "error">("connecting");

  useEffect(() => {
    if (!containerRef.current) return;

    // Narrow viewport → smaller terminal font. xterm uses a canvas/WebGL
    // renderer, so CSS won't resize text — we set this at construction.
    const isMobile = window.matchMedia("(max-width: 640px)").matches;

    const term = new XTerm({
      cursorBlink: true,
      fontFamily: '"Geist Mono", "JetBrains Mono", "Cascadia Mono", Consolas, monospace',
      fontSize: isMobile ? 10.5 : 12.5,
      lineHeight: 1.32,
      theme: {
        background: "#0f0d0b",
        foreground: "#e8e3d9",
        cursor: "#e0a76b",
        cursorAccent: "#0f0d0b",
        selectionBackground: "rgba(224, 167, 107, 0.22)",
        black: "#2a2520",
        red: "#e07b5b",
        green: "#9bbf6b",
        yellow: "#e0a76b",
        blue: "#7e9cc0",
        magenta: "#b08bc4",
        cyan: "#7eaaaa",
        white: "#e8e3d9",
        brightBlack: "#6b6258",
        brightRed: "#ec9a7c",
        brightGreen: "#b8d68d",
        brightYellow: "#ecc28e",
        brightBlue: "#9eb8d4",
        brightMagenta: "#c7a8d6",
        brightCyan: "#9bc4c4",
        brightWhite: "#fff8e6",
      },
      allowProposedApi: true,
      scrollback: 8000,
    });

    const fit = new FitAddon();
    term.loadAddon(fit);
    term.loadAddon(new WebLinksAddon());
    term.open(containerRef.current);
    // WebGL rendering is flaky on some mobile browsers (Safari in particular
    // scales canvas oddly at high DPR). Skip on narrow viewports.
    if (!isMobile) {
      try {
        const webgl = new WebglAddon();
        webgl.onContextLoss(() => webgl.dispose());
        term.loadAddon(webgl);
      } catch {
        // canvas/dom fallback
      }
    }
    // Defer the initial fit until the browser has actually laid out the
    // container — otherwise we size the terminal to 0 cols × 0 rows and
    // claude boots into a pin-hole.
    requestAnimationFrame(() => {
      try { fit.fit(); } catch { /* ignore */ }
    });

    const ws = new WebSocket(ptyWebSocketUrl(videoId));
    let ro: ResizeObserver | null = null;
    let onWinResize: (() => void) | null = null;

    ws.onopen = () => {
      setConn("open");
      const sendResize = () => {
        try { fit.fit(); } catch { /* ignore */ }
        if (ws.readyState === WebSocket.OPEN) {
          ws.send(JSON.stringify({ type: "resize", rows: term.rows, cols: term.cols }));
        }
      };
      // wait a frame so fit.fit() sees the real layout before telling the PTY
      requestAnimationFrame(sendResize);
      term.onData((data) => {
        if (ws.readyState === WebSocket.OPEN) {
          ws.send(JSON.stringify({ type: "input", data }));
        }
      });
      term.onResize(({ rows, cols }) => {
        if (ws.readyState === WebSocket.OPEN) {
          ws.send(JSON.stringify({ type: "resize", rows, cols }));
        }
      });
      onWinResize = sendResize;
      window.addEventListener("resize", onWinResize);
      ro = new ResizeObserver(() => sendResize());
      if (containerRef.current) ro.observe(containerRef.current);
    };

    ws.onmessage = (ev) => {
      const text = ev.data as string;
      if (text.startsWith("{") && (text.includes('"type":"exit"') || text.includes('"type":"error"'))) {
        try {
          const msg = JSON.parse(text);
          if (msg.type === "exit") {
            term.writeln(`\r\n\x1b[33m[claude exited · code ${msg.code ?? "?"}]\x1b[0m`);
            return;
          }
          if (msg.type === "error") {
            term.writeln(`\r\n\x1b[31m[error: ${msg.message}]\x1b[0m`);
            return;
          }
        } catch { /* ignore */ }
      }
      term.write(text);
    };

    ws.onclose = () => {
      setConn("closed");
      term.writeln("\r\n\x1b[90m[connection closed]\x1b[0m");
    };
    ws.onerror = () => {
      setConn("error");
    };

    return () => {
      ro?.disconnect();
      if (onWinResize) window.removeEventListener("resize", onWinResize);
      ws.close();
      term.dispose();
    };
  }, [videoId]);

  const dotColor =
    conn === "open"       ? "oklch(72% 0.14 130)" :
    conn === "connecting" ? "oklch(72% 0.14 80)"  :
                            "oklch(65% 0.16 25)";
  const connLabel =
    conn === "open"       ? "connected" :
    conn === "connecting" ? "connecting…" :
    conn === "closed"     ? "closed" :
                            "error";

  return (
    <div className="terminal-area">
      <div className="terminal-tabs">
        <div className="tterm active">
          <span className="dot" style={{ background: dotColor }} />
          claude
          <span style={{ color: "#5a524a", marginLeft: 2 }}>· {connLabel}</span>
        </div>
        <div className="tterm path">
          {videoId
            ? `output\\${videoId} · claude --add-dir .`
            : "output · claude"}
        </div>
      </div>
      <div className="terminal" ref={containerRef} />
    </div>
  );
}
