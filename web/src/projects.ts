import { authFetch } from "./auth";
import type {
  BulkIngestRequest,
  BulkIngestResponse,
  PlaylistPreview,
  PodcastPreview,
  Project,
  ProjectDetail,
  Stats,
} from "./types";

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

// -------------------------------------------------------------------
// Slice 2 — Playlist preview + bulk ingest
// -------------------------------------------------------------------

/** Fetch a playlist manifest from the backend without starting any ingests. */
export async function previewPlaylist(url: string): Promise<PlaylistPreview> {
  const r = await authFetch("/api/playlist/preview", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ url }),
  });
  if (!r.ok) {
    const text = await r.text().catch(() => "");
    try {
      const j = JSON.parse(text);
      throw new Error(j.detail || `previewPlaylist: ${r.status}`);
    } catch {
      throw new Error(text || `previewPlaylist: ${r.status}`);
    }
  }
  return r.json();
}

/** Resolve a podcast input (Spotify show / episode, RSS feed, or direct MP3
 *  URL) into a uniform preview shape. The server does the messy detection +
 *  RSS parsing; we only render. */
export async function previewPodcast(url: string): Promise<PodcastPreview> {
  const r = await authFetch("/api/podcast/preview", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ url }),
  });
  if (!r.ok) {
    const text = await r.text().catch(() => "");
    try {
      const j = JSON.parse(text);
      throw new Error(j.detail || `previewPodcast: ${r.status}`);
    } catch {
      throw new Error(text || `previewPodcast: ${r.status}`);
    }
  }
  return r.json();
}

/** Kick off one or more ingest jobs. Skipped videos are returned so the
 *  caller can show a summary toast. */
export async function bulkIngest(body: BulkIngestRequest): Promise<BulkIngestResponse> {
  const r = await authFetch("/api/ingests", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!r.ok) {
    const text = await r.text().catch(() => "");
    throw new Error(text || `bulkIngest: ${r.status}`);
  }
  return r.json();
}
