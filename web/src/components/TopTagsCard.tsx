import type { TagStat } from "../types";

export interface TopTagsCardProps {
  tags: TagStat[];
}

/** Map a tag's count into one of four weight buckets, relative to the
 *  highest count in the set. Drives font-size + color intensity in CSS. */
function weightFor(count: number, max: number): number {
  if (max <= 1) return 2;
  const ratio = count / max;
  if (ratio > 0.75) return 4;
  if (ratio > 0.5) return 3;
  if (ratio > 0.25) return 2;
  return 1;
}

export default function TopTagsCard({ tags }: TopTagsCardProps) {
  if (tags.length === 0) return null;
  const max = Math.max(...tags.map((t) => t.count), 1);
  return (
    <section className="analytics-card" aria-labelledby="ana-tags">
      <div className="ana-head">
        <span className="ana-label" id="ana-tags">Top tags</span>
        <span className="ana-hint">across your library</span>
      </div>
      <ul className="ana-tag-cloud">
        {tags.map((t) => (
          <li key={t.tag} className={`ana-tag w-${weightFor(t.count, max)}`} title={`${t.count} videos`}>
            <span className="ana-tag-name">{t.tag}</span>
            <span className="ana-tag-count">{t.count}</span>
          </li>
        ))}
      </ul>
    </section>
  );
}
