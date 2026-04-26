import { useEffect, useMemo, useState, type FormEvent } from "react";
import { listAiProviders } from "../api";
import type { AiProvider } from "../types";

export interface AnalyzeTriggerModalProps {
  open: boolean;
  onClose: () => void;
  /** Fires when the user hits "Run analysis". Modal closes immediately after.
   *  `skipDialog` reflects the "Don't show this again" checkbox state — the
   *  caller is responsible for persisting the chosen defaults. */
  onSubmit: (
    provider: string,
    model: string | undefined,
    skipDialog: boolean,
  ) => void | Promise<void>;
  /** Optional preselection (e.g. from a previous run). */
  initialProvider?: string;
  initialModel?: string;
  /** Preselected state for the "Don't show this again" checkbox. */
  initialSkipDialog?: boolean;
}

/** Modal that lets the user pick a provider + model before kicking off an
 *  analysis. Loads `/api/ai/providers` on open and surfaces unavailable
 *  providers with their reason (disabled with tooltip). */
export default function AnalyzeTriggerModal({
  open,
  onClose,
  onSubmit,
  initialProvider,
  initialModel,
  initialSkipDialog,
}: AnalyzeTriggerModalProps) {
  const [providers, setProviders] = useState<AiProvider[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [provider, setProvider] = useState<string>(initialProvider ?? "claude_cli");
  const [model, setModel] = useState<string>(""); // "" = default
  const [submitting, setSubmitting] = useState(false);
  const [skipDialog, setSkipDialog] = useState<boolean>(Boolean(initialSkipDialog));

  // Reset + fetch on every open.
  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    setProviders(null);
    setLoadError(null);
    setSubmitting(false);
    setSkipDialog(Boolean(initialSkipDialog));
    listAiProviders()
      .then((list) => {
        if (cancelled) return;
        setProviders(list);
        // Pick initialProvider if still available, else first available, else first.
        const preferred = list.find(
          (p) => p.name === (initialProvider ?? "claude_cli") && p.available,
        );
        const firstAvail = list.find((p) => p.available);
        const picked = preferred ?? firstAvail ?? list[0];
        if (picked) {
          setProvider(picked.name);
          // Preload initialModel only if this provider actually has it.
          if (initialModel && picked.models.includes(initialModel)) {
            setModel(initialModel);
          } else {
            setModel("");
          }
        }
      })
      .catch((e) => {
        if (!cancelled) setLoadError(String(e));
      });
    return () => {
      cancelled = true;
    };
  }, [open, initialProvider, initialModel, initialSkipDialog]);

  // Esc closes.
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  const selected = useMemo<AiProvider | null>(
    () => providers?.find((p) => p.name === provider) ?? null,
    [providers, provider],
  );

  const anyAvailable = useMemo(
    () => (providers ?? []).some((p) => p.available),
    [providers],
  );

  if (!open) return null;

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (!selected || !selected.available) return;
    setSubmitting(true);
    try {
      await onSubmit(
        selected.name,
        model.trim() === "" ? undefined : model,
        skipDialog,
      );
      onClose();
    } catch (err) {
      // Surface error inline — caller typically toasts, but we also want
      // the modal to not eat a failure silently.
      setLoadError(String(err));
      setSubmitting(false);
    }
  }

  return (
    <div className="modal-backdrop analyze-trigger-backdrop" onClick={onClose}>
      <div
        className="modal analyze-trigger-modal"
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-labelledby="analyze-trigger-title"
      >
        <div className="atm-header">
          <div className="atm-eyebrow">
            <span className="atm-eyebrow-dot" />
            AI analysis
          </div>
          <h2 id="analyze-trigger-title">Understand this video</h2>
          <p className="atm-sub">
            Pick a provider and model. Summary, highlights, and chapters
            stream in live as the model reads the transcript.
          </p>
        </div>

        {providers === null && !loadError && (
          <div className="atm-loading" role="status">
            <span className="atm-spinner" aria-hidden />
            <span>Checking providers…</span>
          </div>
        )}

        {loadError && (
          <div className="form-error" role="alert">
            {loadError}
          </div>
        )}

        {providers !== null && !anyAvailable && (
          <div className="atm-none-available" role="alert">
            <strong>No AI provider is configured.</strong>
            <span>
              Install <code>claude</code> (Claude Code CLI) or run Ollama
              locally, then reload this page.
            </span>
          </div>
        )}

        {providers !== null && anyAvailable && (
          <form onSubmit={submit} className="atm-form">
            <fieldset className="atm-field">
              <legend>Provider</legend>
              <div className="atm-provider-grid" role="radiogroup">
                {providers.map((p) => {
                  const isActive = p.name === provider;
                  const disabled = !p.available;
                  return (
                    <label
                      key={p.name}
                      className={`atm-provider-card ${isActive ? "is-active" : ""} ${disabled ? "is-disabled" : ""}`}
                      title={disabled && p.reason ? p.reason : undefined}
                    >
                      <input
                        type="radio"
                        name="provider"
                        value={p.name}
                        checked={isActive}
                        disabled={disabled}
                        onChange={() => {
                          setProvider(p.name);
                          setModel("");
                        }}
                      />
                      <span className="atm-provider-head">
                        <span
                          className={`atm-provider-dot ${p.available ? "ok" : "warn"}`}
                          aria-hidden
                        />
                        <span className="atm-provider-name">{p.display_name}</span>
                      </span>
                      <span className="atm-provider-meta">
                        {p.available
                          ? p.models.length === 0
                            ? "default model"
                            : `${p.models.length} model${p.models.length === 1 ? "" : "s"}`
                          : (p.reason ?? "unavailable")}
                      </span>
                    </label>
                  );
                })}
              </div>
            </fieldset>

            <label className="atm-select-label">
              Model
              <span className="hint">
                {selected?.models.length
                  ? "— override the provider default"
                  : "— provider picks its own"}
              </span>
              <select
                value={model}
                onChange={(e) => setModel(e.target.value)}
                disabled={!selected?.available || (selected?.models.length ?? 0) === 0}
                className="atm-select"
              >
                <option value="">Default</option>
                {selected?.models.map((m) => (
                  <option key={m} value={m}>
                    {m}
                  </option>
                ))}
              </select>
            </label>

            <div
              style={{
                display: "flex",
                flexDirection: "column",
                gap: 4,
                marginTop: 4,
              }}
            >
              <label
                style={{
                  display: "inline-flex",
                  alignItems: "center",
                  gap: 8,
                  cursor: "pointer",
                  fontSize: 13,
                  color: "var(--ink-2)",
                }}
              >
                <input
                  type="checkbox"
                  checked={skipDialog}
                  onChange={(e) => setSkipDialog(e.target.checked)}
                  disabled={submitting}
                  style={{ margin: 0 }}
                />
                <span>Don&rsquo;t show this again</span>
              </label>
              <span
                style={{
                  fontSize: 11.5,
                  color: "var(--ink-3)",
                  marginLeft: 24,
                }}
              >
                You can change this anytime in Settings.
              </span>
            </div>

            <div className="modal-actions atm-actions">
              <button type="button" onClick={onClose} disabled={submitting}>
                Cancel
              </button>
              <button
                type="submit"
                disabled={submitting || !selected?.available}
              >
                {submitting ? "Starting…" : "Run analysis"}
              </button>
            </div>
          </form>
        )}
      </div>
    </div>
  );
}
