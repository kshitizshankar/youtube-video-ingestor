import { useEffect, useRef, useState, type FormEvent } from "react";
import { extractVideoId } from "../api";
import { useProjects } from "../ProjectsContext";
import Tip from "./Tip";

const PROJECT_PREF_KEY = "vvi.ingest.lastProject";

export interface IngestModalProps {
  open: boolean;
  onClose: () => void;
  onSubmit: (
    url: string,
    opts: { diarize: boolean; model: string; batched: boolean; projectId: string },
  ) => void;
}

export default function IngestModal({ open, onClose, onSubmit }: IngestModalProps) {
  const [url, setUrl] = useState("");
  const [model, setModel] = useState("distil-large-v3");
  const [live, setLive] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [projectId, setProjectId] = useState<string>(() => {
    try {
      return localStorage.getItem(PROJECT_PREF_KEY) || "inbox";
    } catch {
      return "inbox";
    }
  });
  const { projects } = useProjects();
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (open) {
      setUrl("");
      setLive(false);
      setErr(null);
      requestAnimationFrame(() => inputRef.current?.focus());
    }
  }, [open]);

  // Persist the last picked project so the next ingest opens in the
  // same place. Saves the click for the common "I'm dropping ten URLs
  // into one project" flow.
  useEffect(() => {
    try {
      localStorage.setItem(PROJECT_PREF_KEY, projectId);
    } catch {
      /* swallow -- private mode etc. */
    }
  }, [projectId]);

  // If the persisted project disappeared (deleted in another tab),
  // fall back to Inbox so the picker isn't pointing at a tombstone.
  useEffect(() => {
    if (!projects) return;
    if (!projects.some((p) => p.id === projectId)) {
      setProjectId("inbox");
    }
  }, [projects, projectId]);

  // Clear any stale error as soon as the user starts editing again.
  useEffect(() => { if (err) setErr(null); }, [url]); // eslint-disable-line react-hooks/exhaustive-deps

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
    // Validate up-front so we can show inline feedback instead of a
    // browser alert() fired from the parent after navigate().
    if (!extractVideoId(trimmed)) {
      setErr("Couldn't read a YouTube video ID from that. Paste a full URL (youtube.com/watch?v=…, youtu.be/…, /shorts/…) or just the 11-character ID.");
      return;
    }
    // Diarization now runs automatically as a post-step — no UI toggle.
    onSubmit(trimmed, { diarize: false, model, batched: !live, projectId });
  }

  return (
    <div className="ingest-modal" onClick={(e) => e.target === e.currentTarget && onClose()}>
      <form className="ingest-card" onSubmit={handle}>
        <header>
          <h3>add a video.</h3>
          <div className="hint">
            paste url &rarr; downloaded &rarr; transcribed &rarr; ready to chat.
            everything runs locally.
          </div>
        </header>

        <div className="ingest-url">
          <YTIcon />
          <input
            ref={inputRef}
            placeholder="https://youtube.com/watch?v=…  or  _qZvORxGqI0"
            value={url}
            onChange={(e) => setUrl(e.target.value)}
            spellCheck={false}
          />
        </div>
        {err && (
          <div
            className="ingest-note"
            role="alert"
            style={{ color: "var(--danger, #ff4d6d)", borderLeft: "2px solid var(--danger, #ff4d6d)", paddingLeft: 10 }}
          >
            {err}
          </div>
        )}

        <label className="ingest-project-row">
          <span className="ingest-project-label">project</span>
          <select
            className="ingest-project-select"
            value={projectId}
            onChange={(e) => setProjectId(e.target.value)}
          >
            {(projects ?? []).map((p) => (
              <option key={p.id} value={p.id}>
                {p.system_kind === "inbox" ? `${p.name} (default)` : p.name}
              </option>
            ))}
          </select>
        </label>

        <div className="ingest-opts">
          <div className="opt-group" role="group" aria-label="Model">
            <Tip content={
              <>
                <strong>Distilled Whisper · English-only.</strong>
                <br />
                ~30–50× realtime on your RTX 4060. Fast and nearly as accurate
                as large-v3 for English content.
              </>
            }>
              <button
                type="button"
                className={`opt ${model === "distil-large-v3" ? "selected" : ""}`}
                onClick={() => setModel("distil-large-v3")}
              >
                distil · v3 — fast
              </button>
            </Tip>
            <Tip content={
              <>
                <strong>OpenAI Whisper large-v3 · Multilingual.</strong>
                <br />
                Supports 100+ languages. Best accuracy for accents, proper
                nouns, technical terms. ~20× realtime on your RTX 4060.
              </>
            }>
              <button
                type="button"
                className={`opt ${model === "large-v3" ? "selected" : ""}`}
                onClick={() => setModel("large-v3")}
              >
                large · v3 — best
              </button>
            </Tip>
          </div>
          <div className="opt-group" role="group" aria-label="Stream mode">
            <Tip content={
              <>
                <strong>Live stream.</strong>
                <br />
                Sequential faster-whisper — segments drip in one at a time
                as audio is processed (~5× realtime). ~10× slower overall
                than batched mode, but visibly alive during the run.
              </>
            }>
              <button
                type="button"
                className={`opt ${live ? "selected" : ""}`}
                onClick={() => setLive(!live)}
              >
                <LiveIcon /> Live stream
              </button>
            </Tip>
          </div>
        </div>
        <div className="ingest-note">
          Speaker diarization runs automatically as a post-step — no toggle
          needed. Detected speakers surface on the video's details panel.
        </div>
        {live && (
          <div className="ingest-note">
            Live mode uses sequential whisper: segments drip in one at a time
            (~5× realtime, ~10× slower overall than batched). Diarization still
            runs at the end.
          </div>
        )}

        <div className="ingest-footer">
          <span className="est">processing locally &middot; stays on this machine</span>
          <div className="actions">
            <button type="button" className="btn btn-ghost" onClick={onClose}>
              cancel
            </button>
            <button type="submit" className="btn btn-accent" disabled={!url.trim()}>
              ingest
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

function LiveIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <circle cx="12" cy="12" r="3" />
      <path d="M5.64 5.64a9 9 0 0 0 0 12.72M18.36 5.64a9 9 0 0 1 0 12.72" />
    </svg>
  );
}
