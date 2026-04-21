import type { ReactNode } from "react";

export interface TopBarProps {
  crumbs?: (string | { label: string; to?: string })[];
  title?: string;
  leading?: ReactNode;
  actions?: ReactNode;
}

export default function TopBar({ crumbs, title, leading, actions }: TopBarProps) {
  return (
    <header className="topbar">
      {leading}
      {crumbs ? (
        <div className="topbar-crumbs">
          {crumbs.map((c, i) => {
            const label = typeof c === "string" ? c : c.label;
            const isLast = i === crumbs.length - 1;
            return (
              <span key={i} style={{ display: "inline-flex", alignItems: "center", minWidth: 0 }}>
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
