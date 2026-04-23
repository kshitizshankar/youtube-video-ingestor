import type { ChannelStat } from "../types";

function fmtMinutes(sec: number): string {
  const m = Math.round(sec / 60);
  if (m < 60) return `${m}m`;
  const h = sec / 3600;
  return h >= 10 ? `${Math.round(h)}h` : `${h.toFixed(1)}h`;
}

export interface TopChannelsCardProps {
  channels: ChannelStat[];
}

export default function TopChannelsCard({ channels }: TopChannelsCardProps) {
  if (channels.length < 2) return null;
  const maxCount = Math.max(...channels.map((c) => c.count), 1);
  return (
    <section className="analytics-card" aria-labelledby="ana-channels">
      <div className="ana-head">
        <span className="ana-label" id="ana-channels">Top channels</span>
        <span className="ana-hint">by video count</span>
      </div>
      <ul className="ana-bars">
        {channels.map((c) => {
          const pct = Math.max(6, (c.count / maxCount) * 100);
          return (
            <li key={c.channel} className="ana-bar-row" title={`${c.count} videos · ${fmtMinutes(c.total_seconds)}`}>
              <span className="ana-bar-name">{c.channel}</span>
              <span className="ana-bar-track">
                <span className="ana-bar-fill" style={{ width: `${pct}%` }} />
              </span>
              <span className="ana-bar-count">{c.count}</span>
              <span className="ana-bar-dur">{fmtMinutes(c.total_seconds)}</span>
            </li>
          );
        })}
      </ul>
    </section>
  );
}
