import { authFetch } from "./auth";
import type { Project, ProjectDetail, Stats } from "./types";

export async function listProjects(): Promise<Project[]> {
  const r = await authFetch("/api/projects");
  if (!r.ok) throw new Error(`listProjects: ${r.status}`);
  return r.json();
}

export async function createProject(name: string, description?: string): Promise<Project> {
  const r = await authFetch("/api/projects", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name, description: description ?? null }),
  });
  if (!r.ok) throw new Error(`createProject: ${r.status}`);
  return r.json();
}

export async function getProject(id: string): Promise<ProjectDetail> {
  const r = await authFetch(`/api/projects/${id}`);
  if (!r.ok) throw new Error(`getProject: ${r.status}`);
  return r.json();
}

export async function updateProject(
  id: string,
  patch: Partial<{ name: string; description: string | null }>,
): Promise<ProjectDetail> {
  const r = await authFetch(`/api/projects/${id}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(patch),
  });
  if (!r.ok) throw new Error(`updateProject: ${r.status}`);
  return r.json();
}

export async function deleteProject(id: string): Promise<void> {
  const r = await authFetch(`/api/projects/${id}`, { method: "DELETE" });
  if (!r.ok) throw new Error(`deleteProject: ${r.status}`);
}

export async function addVideosToProject(
  id: string,
  videoIds: string[],
): Promise<{ added: number }> {
  const r = await authFetch(`/api/projects/${id}/videos`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ video_ids: videoIds }),
  });
  if (!r.ok) throw new Error(`addVideosToProject: ${r.status}`);
  return r.json();
}

export async function removeVideoFromProject(
  projectId: string,
  videoId: string,
): Promise<void> {
  const r = await authFetch(`/api/projects/${projectId}/videos/${videoId}`, {
    method: "DELETE",
  });
  if (!r.ok) throw new Error(`removeVideoFromProject: ${r.status}`);
}

export async function getStats(): Promise<Stats> {
  const r = await authFetch("/api/stats");
  if (!r.ok) throw new Error(`getStats: ${r.status}`);
  return r.json();
}
