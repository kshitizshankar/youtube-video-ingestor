import { useEffect, useRef, useState, type FormEvent } from "react";

export interface IngestModalProps {
  open: boolean;
  onClose: () => void;
  onSubmit: (url: string, opts: { diarize: boolean; model: string }) => void;
}

export default function IngestModal({ open, onClose, onSubmit }: IngestModalProps) {
  const [url, setUrl] = useState("");
  const [diarize, setDiarize] = useState(false);
  const [model, setModel] = useState("distil-large-v3");
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (open) {
      setUrl("");
      // Defer focus until the modal is mounted
      requestAnimationFrame(() => inputRef.current?.focus());
    }
  }, [open]);

  // Close on Escape
  useEffect(() => {
    if (!open) return;
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") onClose();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  if (!open) return null;

  function handle(e: FormEvent) {
    e.preventDefault();
    const trimmed = url.trim();
    if (!trimmed) return;
    onSubmit(trimmed, { diarize, model });
  }

  return (
    <div className="ingest-modal" onClick={(e) => e.target === e.currentTarget && onClose()}>
      <form className="ingest-card" onSubmit={handle}>
        <header>
          <h3>Add a video.</h3>
          <div className="hint">
            Pasted URL → downloaded → transcribed → ready to chat. Everything runs locally.
          </div>
        </header>

        <div className="ingest-url">
          <YTIcon />
          <input
            ref={inputRef}
            placeholder="https://youtube.com/watch?v=…"
            value={url}
            onChange={(e) => setUrl(e.target.value)}
            spellCheck={false}
          />
        </div>

        <div className="ingest-opts">
          <button
            type="button"
            className={`opt ${model === "distil-large-v3" ? "selected" : ""}`}
            onClick={() => setModel("distil-large-v3")}
          >
            distil · v3 — fast
          </button>
          <button
            type="button"
            className={`opt ${model === "large-v3" ? "selected" : ""}`}
            onClick={() => setModel("large-v3")}
          >
            large · v3 — best
          </button>
          <button
            type="button"
            className={`opt ${diarize ? "selected" : ""}`}
            onClick={() => setDiarize(!diarize)}
          >
            <SpeakerIcon /> Identify speakers
          </button>
        </div>

        <div className="ingest-footer">
          <span className="est">Processing locally · stays on this machine</span>
          <div className="actions">
            <button type="button" className="btn btn-ghost" onClick={onClose}>
              Cancel
            </button>
            <button type="submit" className="btn btn-primary" disabled={!url.trim()}>
              Ingest
            </button>
          </div>
        </div>
      </form>
    </div>
  );
}

function YTIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="#ff0033">
      <path d="M23.498 6.186a3.016 3.016 0 0 0-2.122-2.136C19.505 3.545 12 3.545 12 3.545s-7.505 0-9.377.505A3.017 3.017 0 0 0 .502 6.186C0 8.07 0 12 0 12s0 3.93.502 5.814a3.016 3.016 0 0 0 2.122 2.136c1.871.505 9.376.505 9.376.505s7.505 0 9.377-.505a3.015 3.015 0 0 0 2.122-2.136C24 15.93 24 12 24 12s0-3.93-.502-5.814zM9.545 15.568V8.432L15.818 12l-6.273 3.568z" />
    </svg>
  );
}

function SpeakerIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <path d="M12 2a3 3 0 0 0-3 3v6a3 3 0 1 0 6 0V5a3 3 0 0 0-3-3z" />
      <path d="M19 10v1a7 7 0 0 1-14 0v-1M12 18v4M8 22h8" />
    </svg>
  );
}
