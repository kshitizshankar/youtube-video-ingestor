import type { LanguageStat } from "../types";

export interface LanguageBreakdownCardProps {
  languages: LanguageStat[];
}

/** Language display names for common ISO codes; falls back to uppercase code. */
const LANG_NAMES: Record<string, string> = {
  en: "English",
  es: "Spanish",
  fr: "French",
  de: "German",
  it: "Italian",
  pt: "Portuguese",
  ja: "Japanese",
  zh: "Chinese",
  ko: "Korean",
  hi: "Hindi",
  ar: "Arabic",
  ru: "Russian",
  nl: "Dutch",
  tr: "Turkish",
  pl: "Polish",
  sv: "Swedish",
};

function labelFor(code: string): string {
  return LANG_NAMES[code.toLowerCase()] ?? code.toUpperCase();
}

export default function LanguageBreakdownCard({ languages }: LanguageBreakdownCardProps) {
  if (languages.length < 2) return null;
  const total = languages.reduce((acc, l) => acc + l.count, 0);
  if (total === 0) return null;

  return (
    <section className="analytics-card" aria-labelledby="ana-lang">
      <div className="ana-head">
        <span className="ana-label" id="ana-lang">Languages</span>
        <span className="ana-hint">{total} videos</span>
      </div>
      <div className="ana-stacked-bar" role="img" aria-label="Language distribution">
        {languages.map((l, i) => {
          const pct = (l.count / total) * 100;
          return (
            <span
              key={l.language}
              className={`ana-stacked-seg seg-${(i % 4) + 1}`}
              style={{ width: `${pct}%` }}
              title={`${labelFor(l.language)} — ${l.count} (${pct.toFixed(0)}%)`}
            />
          );
        })}
      </div>
      <ul className="ana-lang-legend">
        {languages.map((l, i) => {
          const pct = (l.count / total) * 100;
          return (
            <li key={l.language}>
              <span className={`ana-swatch seg-${(i % 4) + 1}`} />
              <span className="ana-lang-name">{labelFor(l.language)}</span>
              <span className="ana-lang-count">{l.count}</span>
              <span className="ana-lang-pct">{pct.toFixed(0)}%</span>
            </li>
          );
        })}
      </ul>
    </section>
  );
}
