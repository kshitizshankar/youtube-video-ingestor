/**
 * Single source of truth for project data across the app.
 *
 * Why this exists: each consumer (Sidebar, Library, Project page) used to
 * own its own `useState<Project[]>` + its own `listProjects()` call. When
 * the user renamed a project on the Project page, the Sidebar's stale
 * cached copy kept showing the old name until a hard reload. This provider
 * owns the list once; every mutation funnels through it and triggers a
 * single re-fetch that all consumers re-render against.
 *
 * The provider does NOT cache the per-project ProjectDetail (videos array,
 * etc.) — that stays a per-page concern via getProject(). It only owns the
 * list shape consumed by sidebar entries, library filter chips, and badge
 * lookups on VideoRow.
 */
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import {
  addVideosToProject,
  createProject,
  deleteProject,
  listProjects,
  removeVideoFromProject,
  updateProject,
} from "./projects";
import { moveVideo as apiMoveVideo, type MoveVideoResult } from "./api";
import type { Project, ProjectDetail } from "./types";

interface ProjectsContextValue {
  /** Sorted projects list (most recent activity first). `null` while the
   *  initial fetch is in flight; an empty array means "loaded, none exist". */
  projects: Project[] | null;
  /** O(1) lookup, kept in sync with `projects`. */
  projectsById: Map<string, Project>;
  /** Last error from a refresh attempt; null when healthy. */
  error: string | null;
  /** Force a re-fetch. Mutations call this internally; expose it for
   *  external triggers (e.g. polling that detects a server-side change). */
  refresh: () => Promise<void>;

  // ---- Mutations ----
  // Each one hits the API, then triggers a refresh of the cached list so
  // every subscriber re-renders with fresh data. Mutations return whatever
  // the server returned so callers don't have to refetch manually.
  create: (name: string, description?: string) => Promise<Project>;
  rename: (id: string, name: string) => Promise<ProjectDetail>;
  updateDescription: (id: string, description: string | null) => Promise<ProjectDetail>;
  remove: (id: string) => Promise<void>;
  addVideos: (id: string, videoIds: string[]) => Promise<{ added: number }>;
  removeVideo: (projectId: string, videoId: string) => Promise<void>;
  /** Reassign a single video into `projectId`. Triggers the server-side
   *  move protocol (atomic rename when same drive; copy/verify/delete
   *  otherwise). Refreshes the project list because membership counts
   *  on both projects change. */
  moveVideoToProject: (videoId: string, projectId: string) => Promise<MoveVideoResult>;
}

const ProjectsContext = createContext<ProjectsContextValue | null>(null);

function sortByLastActivity(list: Project[]): Project[] {
  return [...list].sort((a, b) => {
    const ta = a.last_activity ? Date.parse(a.last_activity) : 0;
    const tb = b.last_activity ? Date.parse(b.last_activity) : 0;
    return tb - ta;
  });
}

export function ProjectsProvider({ children }: { children: ReactNode }) {
  const [projects, setProjects] = useState<Project[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      const list = await listProjects();
      setProjects(sortByLastActivity(list));
      setError(null);
    } catch (e) {
      setError(String(e));
    }
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const projectsById = useMemo(() => {
    const m = new Map<string, Project>();
    for (const p of projects ?? []) m.set(p.id, p);
    return m;
  }, [projects]);

  const create = useCallback(
    async (name: string, description?: string) => {
      const p = await createProject(name, description);
      refresh();
      return p;
    },
    [refresh],
  );

  const rename = useCallback(
    async (id: string, name: string) => {
      const detail = await updateProject(id, { name });
      refresh();
      return detail;
    },
    [refresh],
  );

  const updateDescription = useCallback(
    async (id: string, description: string | null) => {
      const detail = await updateProject(id, { description });
      refresh();
      return detail;
    },
    [refresh],
  );

  const remove = useCallback(
    async (id: string) => {
      await deleteProject(id);
      refresh();
    },
    [refresh],
  );

  const addVideos = useCallback(
    async (id: string, videoIds: string[]) => {
      const r = await addVideosToProject(id, videoIds);
      refresh();
      return r;
    },
    [refresh],
  );

  const removeVideo = useCallback(
    async (projectId: string, videoId: string) => {
      await removeVideoFromProject(projectId, videoId);
      refresh();
    },
    [refresh],
  );

  const moveVideoToProject = useCallback(
    async (videoId: string, projectId: string) => {
      const r = await apiMoveVideo(videoId, projectId);
      refresh();
      return r;
    },
    [refresh],
  );

  const value: ProjectsContextValue = {
    projects,
    projectsById,
    error,
    refresh,
    create,
    rename,
    updateDescription,
    remove,
    addVideos,
    removeVideo,
    moveVideoToProject,
  };

  return <ProjectsContext.Provider value={value}>{children}</ProjectsContext.Provider>;
}

export function useProjects(): ProjectsContextValue {
  const ctx = useContext(ProjectsContext);
  if (!ctx) {
    throw new Error("useProjects must be called inside <ProjectsProvider>");
  }
  return ctx;
}
