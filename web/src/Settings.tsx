import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { listAiProviders } from "./api";
import {
  clearAnalysisDefaults,
  loadAnalysisDefaults,
  saveAnalysisDefaults,
  type AnalysisDefaults,
} from "./analysisDefaults";
import TopBar from "./components/TopBar";
import type { AiProvider } from "./types";

export interface SettingsProps {
  onMenuToggle?: () => void;
}

function HamburgerIcon() {
  return (
    <svg
      width="18"
      height="18"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      strokeLinecap="round"
    >
      <path d="M3 6h18M3 12h18M3 18h18" />
    </svg>
  );
}

/**
 * Settings page — currently surfaces only the "Analysis defaults" section,
 * which is the escape hatch for the "Don't show this again" toggle on the
 * AnalyzeTriggerModal. Additional settings groups can be added as siblings
 * below the analysis section.
 */
export default function Settings({ onMenuToggle }: SettingsProps) {
  const [providers, setProviders] = useState<AiProvider[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);

  const [provider, setProvider] = useState<string>("claude_cli");
  const [model, setModel] = useState<string>(""); // "" = provider default
  const [showPicker, setShowPicker] = useState<boolean>(true); // inverse of skip_dialog
  const [savedAt, setSavedAt] = useState<number | null>(null);
  const [hasStoredDefaults, setHasStoredDefaults] = useState<boolean>(false);

  // Load provider list + any existing defaults on mount.
  useEffect(() => {
    let cancelled = false;
    const stored = loadAnalysisDefaults();
    if (stored) {
      setHasStoredDefaults(true);
      setProvider(stored.provider);
      setModel(stored.model ?? "");
      setShowPicker(!stored.skip_dialog);
    }
    listAiProviders()
      .then((list) => {
        if (cancelled) return;
        setProviders(list);
        // If there's no stored provider, default to first available.
        if (!stored) {
          const firstAvail = list.find((p) => p.available);
          const picked = firstAvail ?? list[0];
          if (picked) setProvider(picked.name);
        } else {
          // Stored provider might no longer be in the list; fall back to
          // first available so the UI doesn't render a ghost selection.
          if (!list.some((p) => p.name === stored.provider)) {
            const firstAvail = list.find((p) => p.available);
            if (firstAvail) setProvider(firstAvail.name);
          }
        }
      })
      .catch((e) => {
        if (!cancelled) setLoadError(String(e));
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const selected = useMemo<AiProvider | null>(
    () => providers?.find((p) => p.name === provider) ?? null,
    [providers, provider],
  );

  // If the currently-selected model isn't offered by the newly-selected
  // provider, clear it so we fall back to the provider default.
  useEffect(() => {
    if (!selected) return;
    if (model && !selected.models.includes(model)) {
      setModel("");
    }
  }, [selected, model]);

  const anyAvailable = useMemo(
    () => (providers ?? []).some((p) => p.available),
    [providers],
  );

  const handleSave = () => {
    if (!selected || !selected.available) return;
    const d: AnalysisDefaults = {
      provider: selected.name,
      model: model.trim() === "" ? null : model,
      skip_dialog: !showPicker,
    };
    saveAnalysisDefaults(d);
    setHasStoredDefaults(true);
    setSavedAt(Date.now());
  };

  const handleReset = () => {
    clearAnalysisDefaults();
    setHasStoredDefaults(false);
    setShowPicker(true);
    setModel("");
    // Reset provider to first-available.
    const firstAvail = providers?.find((p) => p.available);
    if (firstAvail) setProvider(firstAvail.name);
    setSavedAt(Date.now());
  };

  return (
    <div className="main">
      <TopBar
        crumbs={[{ label: "library", to: "/" }, "settings"]}
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
        actions={
          <Link to="/" className="btn btn-ghost hide-on-narrow">
            &larr; library
          </Link>
        }
      />

      <div
        style={{
          flex: 1,
          overflowY: "auto",
          padding: "28px 32px 48px",
          maxWidth: 820,
          width: "100%",
          margin: "0 auto",
        }}
      >
        <h1 className="display-md" style={{ margin: "0 0 10px" }}>settings.</h1>
        <p style={{ color: "var(--ok-plum-60)", margin: "0 0 32px", fontSize: 14, lineHeight: 1.55 }}>
          preferences that stick across sessions. stored locally in your
          browser.
        </p>

        <section
          style={{
            border: "1px solid var(--line)",
            borderRadius: 12,
            padding: 22,
            background: "var(--bg-2)",
          }}
        >
          <header style={{ marginBottom: 22 }}>
            <h2
              className="display-sm"
              style={{ margin: "0 0 6px" }}
            >
              analysis defaults.
            </h2>
            <p style={{ margin: 0, color: "var(--ok-plum-60)", fontSize: 13, lineHeight: 1.55 }}>
              the provider and model used when you click analyze on a video.
            </p>
          </header>

          {providers === null && !loadError && (
            <div role="status" style={{ color: "var(--ink-3)", fontSize: 13 }}>
              Loading providers…
            </div>
          )}

          {loadError && (
            <div
              role="alert"
              style={{
                padding: "10px 12px",
                borderRadius: 8,
                background:
                  "color-mix(in srgb, var(--danger, #ff4d6d) 10%, transparent)",
                color: "var(--danger, #ff4d6d)",
                fontFamily: "var(--font-mono)",
                fontSize: 12.5,
                marginBottom: 14,
              }}
            >
              Failed to load providers: {loadError}
            </div>
          )}

          {providers !== null && !anyAvailable && (
            <div
              role="alert"
              style={{
                padding: "12px 14px",
                borderRadius: 8,
                border: "1px dashed var(--line-2)",
                color: "var(--ink-2)",
                fontSize: 13,
                marginBottom: 14,
              }}
            >
              <strong>No AI provider is configured.</strong>{" "}
              <span>
                Install the <code>claude</code> CLI or run Ollama locally, then
                reload.
              </span>
            </div>
          )}

          {providers !== null && anyAvailable && (
            <div style={{ display: "flex", flexDirection: "column", gap: 20 }}>
              {/* Provider radio cards — same shape as AnalyzeTriggerModal. */}
              <fieldset
                style={{
                  border: "none",
                  padding: 0,
                  margin: 0,
                  display: "flex",
                  flexDirection: "column",
                  gap: 8,
                }}
              >
                <legend
                  style={{
                    fontSize: 11,
                    textTransform: "uppercase",
                    letterSpacing: 0.6,
                    color: "var(--ink-3)",
                    fontFamily: "var(--font-mono)",
                    marginBottom: 4,
                    padding: 0,
                  }}
                >
                  Provider
                </legend>
                <div
                  role="radiogroup"
                  style={{
                    display: "grid",
                    gridTemplateColumns:
                      "repeat(auto-fit, minmax(220px, 1fr))",
                    gap: 10,
                  }}
                >
                  {providers.map((p) => {
                    const isActive = p.name === provider;
                    const disabled = !p.available;
                    return (
                      <label
                        key={p.name}
                        title={disabled && p.reason ? p.reason : undefined}
                        style={{
                          display: "flex",
                          flexDirection: "column",
                          gap: 4,
                          padding: "10px 12px",
                          borderRadius: 10,
                          border: `1px solid ${
                            isActive ? "var(--accent)" : "var(--line)"
                          }`,
                          background: isActive ? "var(--bg-3)" : "var(--bg)",
                          cursor: disabled ? "not-allowed" : "pointer",
                          opacity: disabled ? 0.55 : 1,
                        }}
                      >
                        <span
                          style={{
                            display: "inline-flex",
                            alignItems: "center",
                            gap: 8,
                          }}
                        >
                          <input
                            type="radio"
                            name="settings-provider"
                            value={p.name}
                            checked={isActive}
                            disabled={disabled}
                            onChange={() => {
                              setProvider(p.name);
                              setModel("");
                            }}
                            style={{ margin: 0 }}
                          />
                          <span
                            aria-hidden
                            style={{
                              width: 7,
                              height: 7,
                              borderRadius: "50%",
                              background: p.available
                                ? "var(--accent, #8b5cf6)"
                                : "var(--ink-3)",
                            }}
                          />
                          <span
                            style={{ fontWeight: 600, fontSize: 13.5 }}
                          >
                            {p.display_name}
                          </span>
                        </span>
                        <span
                          style={{
                            fontSize: 11.5,
                            color: "var(--ink-3)",
                            marginLeft: 22,
                          }}
                        >
                          {p.available
                            ? p.models.length === 0
                              ? "default model"
                              : `${p.models.length} model${
                                  p.models.length === 1 ? "" : "s"
                                }`
                            : p.reason ?? "unavailable"}
                        </span>
                      </label>
                    );
                  })}
                </div>
              </fieldset>

              {/* Model dropdown */}
              <label
                style={{
                  display: "flex",
                  flexDirection: "column",
                  gap: 6,
                }}
              >
                <span
                  style={{
                    fontSize: 11,
                    textTransform: "uppercase",
                    letterSpacing: 0.6,
                    color: "var(--ink-3)",
                    fontFamily: "var(--font-mono)",
                  }}
                >
                  Model{" "}
                  <span
                    style={{
                      textTransform: "none",
                      letterSpacing: 0,
                      fontFamily: "var(--font-sans)",
                      color: "var(--ink-3)",
                    }}
                  >
                    {selected?.models.length
                      ? "— override the provider default"
                      : "— provider picks its own"}
                  </span>
                </span>
                <select
                  value={model}
                  onChange={(e) => setModel(e.target.value)}
                  disabled={
                    !selected?.available ||
                    (selected?.models.length ?? 0) === 0
                  }
                  style={{
                    padding: "8px 10px",
                    borderRadius: 8,
                    border: "1px solid var(--line)",
                    background: "var(--bg)",
                    color: "var(--ink)",
                    fontSize: 13.5,
                  }}
                >
                  <option value="">Default</option>
                  {selected?.models.map((m) => (
                    <option key={m} value={m}>
                      {m}
                    </option>
                  ))}
                </select>
              </label>

              {/* Show-picker toggle (inverse of skip_dialog). */}
              <div
                style={{
                  display: "flex",
                  flexDirection: "column",
                  gap: 4,
                  paddingTop: 6,
                  borderTop: "1px solid var(--line)",
                }}
              >
                <label
                  style={{
                    display: "inline-flex",
                    alignItems: "center",
                    gap: 8,
                    cursor: "pointer",
                    fontSize: 14,
                  }}
                >
                  <input
                    type="checkbox"
                    checked={showPicker}
                    onChange={(e) => setShowPicker(e.target.checked)}
                    style={{ margin: 0 }}
                  />
                  <span>Show the picker before each analysis</span>
                </label>
                <span
                  style={{
                    fontSize: 12,
                    color: "var(--ink-3)",
                    marginLeft: 24,
                  }}
                >
                  When off, clicking Analyze fires immediately using the
                  provider and model above.
                </span>
              </div>

              {/* Actions */}
              <div
                style={{
                  display: "flex",
                  gap: 10,
                  alignItems: "center",
                  flexWrap: "wrap",
                  paddingTop: 6,
                }}
              >
                <button
                  type="button"
                  className="btn btn-primary"
                  onClick={handleSave}
                  disabled={!selected?.available}
                >
                  Save
                </button>
                <button
                  type="button"
                  className="btn btn-ghost"
                  onClick={handleReset}
                  disabled={!hasStoredDefaults}
                  title={
                    hasStoredDefaults
                      ? "Clear saved analysis defaults"
                      : "No defaults saved yet"
                  }
                >
                  Reset to no defaults
                </button>
                {savedAt !== null && (
                  <span
                    style={{
                      fontSize: 12.5,
                      color: "var(--ink-3)",
                      fontStyle: "italic",
                    }}
                  >
                    Saved. These apply to the next analysis you start.
                  </span>
                )}
              </div>
            </div>
          )}
        </section>
      </div>
    </div>
  );
}
