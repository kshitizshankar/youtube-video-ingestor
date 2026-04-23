import { Link } from "react-router-dom";
import type { Project } from "../types";

export default function ProjectCard({ p }: { p: Project }) {
  const hours =
    p.total_seconds >= 3600
      ? `${(p.total_seconds / 3600).toFixed(1)}h`
      : `${Math.round(p.total_seconds / 60)}m`;
  return (
    <Link to={`/p/${p.id}`} className="project-card">
      <div className="project-card-name">{p.name}</div>
      {p.description && <div className="project-card-desc">{p.description}</div>}
      <div className="project-card-meta">
        <span>{p.video_count} videos</span>
        <span>·</span>
        <span>{hours}</span>
      </div>
    </Link>
  );
}
