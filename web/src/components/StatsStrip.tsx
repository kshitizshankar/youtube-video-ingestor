import type { Stats } from "../types";

function fmtHours(sec: number): string {
  const h = sec / 3600;
  if (h >= 10) return `${Math.round(h)}h`;
  if (h >= 1) return `${h.toFixed(1)}h`;
  const m = Math.round(sec / 60);
  return `${m}m`;
}

function fmtBytes(n: number): string {
  if (n <= 0) return "0";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let i = 0;
  let v = n;
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024;
    i += 1;
  }
  return `${v.toFixed(v < 10 ? 1 : 0)}${units[i]}`;
}

export default function StatsStrip({ stats }: { stats: Stats }) {
  return (
    <div className="stats-strip">
      <div className="stat">
        <span className="stat-num">{stats.video_count}</span>
        <span className="stat-lbl">videos</span>
      </div>
      <div className="stat">
        <span className="stat-num">{fmtHours(stats.total_seconds)}</span>
        <span className="stat-lbl">content</span>
      </div>
      <div className="stat">
        <span className="stat-num">{stats.project_count}</span>
        <span className="stat-lbl">projects</span>
      </div>
      <div className="stat">
        <span className="stat-num">{fmtBytes(stats.storage_bytes)}</span>
        <span className="stat-lbl">storage</span>
      </div>
    </div>
  );
}
