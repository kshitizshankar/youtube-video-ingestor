/**
 * Persistence for the user's preferred analysis defaults (provider, model,
 * skip-dialog toggle). Stored in localStorage under a namespaced key.
 *
 * All three helpers are guarded with try/catch so SSR environments, privacy
 * modes, and exhausted quotas degrade gracefully (return null / no-op).
 *
 * On load we validate the shape: anything malformed is rejected and returns
 * null so callers fall back to the "first-analysis-ever" flow.
 */

export interface AnalysisDefaults {
  provider: string;
  /** null = "use whatever the provider picks as its default model". */
  model: string | null;
  skip_dialog: boolean;
}

const KEY = "vidan.analysisDefaults";

function isValid(v: unknown): v is AnalysisDefaults {
  if (v === null || typeof v !== "object") return false;
  const o = v as Record<string, unknown>;
  if (typeof o.provider !== "string" || o.provider.length === 0) return false;
  if (o.model !== null && typeof o.model !== "string") return false;
  if (typeof o.skip_dialog !== "boolean") return false;
  return true;
}

export function loadAnalysisDefaults(): AnalysisDefaults | null {
  try {
    if (typeof localStorage === "undefined") return null;
    const raw = localStorage.getItem(KEY);
    if (!raw) return null;
    const parsed: unknown = JSON.parse(raw);
    if (!isValid(parsed)) return null;
    return {
      provider: parsed.provider,
      model: parsed.model,
      skip_dialog: parsed.skip_dialog,
    };
  } catch {
    return null;
  }
}

export function saveAnalysisDefaults(d: AnalysisDefaults): void {
  try {
    if (typeof localStorage === "undefined") return;
    localStorage.setItem(KEY, JSON.stringify(d));
  } catch {
    /* swallow: storage disabled / quota exceeded — not fatal. */
  }
}

export function clearAnalysisDefaults(): void {
  try {
    if (typeof localStorage === "undefined") return;
    localStorage.removeItem(KEY);
  } catch {
    /* swallow */
  }
}
