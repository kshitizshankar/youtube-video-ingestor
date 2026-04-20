import type { ReactNode } from "react";

export interface TopBarProps {
  crumbs?: (string | { label: string; to?: string })[];
  title?: string;
  actions?: ReactNode;
}

export default function TopBar({ crumbs, title, actions }: TopBarProps) {
  return (
    <header className="topbar">
      {crumbs ? (
        <div style={{ display: "flex", alignItems: "center", gap: 4 }}>
          {crumbs.map((c, i) => {
            const label = typeof c === "string" ? c : c.label;
            const isLast = i === crumbs.length - 1;
            return (
              <span key={i} style={{ display: "inline-flex", alignItems: "center" }}>
                <span className={`crumb${isLast ? " current" : ""}`}>{label}</span>
                {!isLast && <span className="crumb-sep">›</span>}
              </span>
            );
          })}
        </div>
      ) : (
        <h1>{title}</h1>
      )}
      <div className="spacer" />
      {actions}
    </header>
  );
}
