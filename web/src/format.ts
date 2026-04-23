/** Shared formatters for view counts, YouTube dates, durations, etc. */

export function formatCount(n: number | null | undefined): string | null {
  if (n == null) return null;
  if (n < 1000) return String(n);
  if (n < 1_000_000) return `${(n / 1000).toFixed(n < 10_000 ? 1 : 0)}K`;
  if (n < 1_000_000_000) return `${(n / 1_000_000).toFixed(n < 10_000_000 ? 1 : 0)}M`;
  return `${(n / 1_000_000_000).toFixed(1)}B`;
}

/** "20260417" → "Apr 17, 2026". Handles missing/short strings. */
export function formatYtDate(yyyymmdd: string | null | undefined): string | null {
  if (!yyyymmdd || yyyymmdd.length !== 8) return null;
  const y = yyyymmdd.slice(0, 4);
  const m = yyyymmdd.slice(4, 6);
  const d = yyyymmdd.slice(6, 8);
  const date = new Date(`${y}-${m}-${d}T00:00:00Z`);
  if (Number.isNaN(date.getTime())) return null;
  return date.toLocaleDateString("en-US", {
    year: "numeric",
    month: "short",
    day: "numeric",
  });
}

export function formatDuration(sec: number | null | undefined): string | null {
  if (!sec) return null;
  const h = Math.floor(sec / 3600);
  const m = Math.floor((sec % 3600) / 60);
  const s = Math.round(sec % 60);
  return h
    ? `${h}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`
    : `${m}:${String(s).padStart(2, "0")}`;
}

export function formatSpeakers(n: number | null | undefined, diarized?: boolean): string | null {
  if (!diarized && !n) return null;
  if (!n || n <= 0) return null;
  return `${n} speaker${n === 1 ? "" : "s"}`;
}
