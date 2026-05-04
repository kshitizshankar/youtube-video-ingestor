import { useCallback, useEffect, useRef, useState } from "react";
import {
  getProjectGraphStatus,
  openGraphBuildStream,
  projectGraphIndexUrl,
  type ProjectGraphStatus,
} from "../api";


export interface ProjectGraphSectionProps {
  projectId: string;
  /** Bumped by the parent when the project's video list changes so we
   *  can refresh the events_since_build counter without polling. */
  refreshKey?: number;
}

interface BuildStage {
  message: string;
  receivedAt: number;
}

export default function ProjectGraphSection({
  projectId, refreshKey,
}: ProjectGraphSectionProps) {
  const [status, setStatus] = useState<ProjectGraphStatus | null>(null);
  const [building, setBuilding] = useState(false);
  const [stages, setStages] = useState<BuildStage[]>([]);
  const [usage, setUsage] = useState<{ tokens_in: number; tokens_out: number; cost_usd: number } | null>(null);
  const [doneSummary, setDoneSummary] = useState<{ duration_ms: number; node_count: number | null; edge_count: number | null } | null>(null);
  const [errMsg, setErrMsg] = useState<string | null>(null);
  const esRef = useRef<EventSource | null>(null);

  const refresh = useCallback(async () => {
    try {
      const s = await getProjectGraphStatus(projectId);
      setStatus(s);
    } catch (e) {
      // Project disappeared mid-poll -- swallow; the parent will
      // navigate away on its own.
    }
  }, [projectId]);

  useEffect(() => {
    refresh();
  }, [refresh, refreshKey]);

  // Re-poll status while a build is in flight so the UI catches state
  // changes (server flips to 'building' immediately on subscription).
  useEffect(() => {
    if (!building) return;
    const id = window.setInterval(refresh, 4000);
    return () => clearInterval(id);
  }, [building, refresh]);

  const startBuild = useCallback((mode: "update" | "rebuild" | "deep") => {
    if (esRef.current) {
      esRef.current.close();
      esRef.current = null;
    }
    setStages([]);
    setUsage(null);
    setDoneSummary(null);
    setErrMsg(null);
    setBuilding(true);

    const es = openGraphBuildStream(projectId, mode);
    esRef.current = es;

    const onAny = (ev: MessageEvent, kind: string) => {
      let data: any = null;
      try { data = JSON.parse(ev.data); } catch { return; }
      if (kind === "stage" && typeof data?.stage === "string") {
        setStages((prev) => [
          ...prev.slice(-9),
          { message: data.stage, receivedAt: Date.now() },
        ]);
      } else if (kind === "usage") {
        setUsage({
          tokens_in: data?.tokens_in ?? 0,
          tokens_out: data?.tokens_out ?? 0,
          cost_usd: data?.cost_usd ?? 0,
        });
      } else if (kind === "done") {
        setDoneSummary({
          duration_ms: data?.duration_ms ?? 0,
          node_count: data?.node_count ?? null,
          edge_count: data?.edge_count ?? null,
        });
        setBuilding(false);
        es.close();
        esRef.current = null;
        refresh();
      } else if (kind === "error") {
        setErrMsg(data?.error_message || "graphify failed");
        setBuilding(false);
        es.close();
        esRef.current = null;
        refresh();
      }
    };
    es.addEventListener("stage", (ev) => onAny(ev as MessageEvent, "stage"));
    es.addEventListener("usage", (ev) => onAny(ev as MessageEvent, "usage"));
    es.addEventListener("done", (ev) => onAny(ev as MessageEvent, "done"));
    es.addEventListener("error", (ev) => {
      const me = ev as MessageEvent;
      if (me.data) onAny(me, "error");
      else {
        setErrMsg("connection lost");
        setBuilding(false);
        es.close();
        esRef.current = null;
        refresh();
      }
    });
  }, [projectId, refresh]);

  useEffect(() => {
    return () => {
      esRef.current?.close();
      esRef.current = null;
    };
  }, []);

  if (!status) {
    return (
      <section className="project-graph">
        <header className="project-graph-head">
          <span className="project-graph-kicker">knowledge graph</span>
        </header>
        <div className="project-graph-body">loading...</div>
      </section>
    );
  }

  const hasGraph = status.state === "ready" || status.has_index_html;

  return (
    <section className="project-graph">
      <header className="project-graph-head">
        <span className="project-graph-kicker">knowledge graph</span>
        <span className={`project-graph-state state-${status.state}`}>
          {status.state === "never" && "not built yet"}
          {status.state === "building" && "building..."}
          {status.state === "ready" && (
            <>
              ready
              {status.events_since_build > 0 && (
                <em>
                  {" · "}{status.events_since_build} event{status.events_since_build === 1 ? "" : "s"} since last build
                </em>
              )}
            </>
          )}
          {status.state === "error" && "errored"}
        </span>
      </header>

      <div className="project-graph-body">
        {status.state === "ready" && (
          <div className="project-graph-stats">
            {status.node_count != null && (
              <span><strong>{status.node_count}</strong> nodes</span>
            )}
            {status.edge_count != null && (
              <span><strong>{status.edge_count}</strong> edges</span>
            )}
            {status.built_at && (
              <span className="project-graph-built">
                built {new Date(status.built_at).toLocaleString()}
              </span>
            )}
          </div>
        )}

        {status.state === "error" && status.last_error && (
          <div className="project-graph-error" role="alert">
            {status.last_error}
          </div>
        )}

        {(building || stages.length > 0 || doneSummary || errMsg) && (
          <div className="project-graph-progress">
            <div className="project-graph-progress-head">
              <span className="project-graph-progress-label">
                {building ? "running graphify" : doneSummary ? "build complete" : "build failed"}
              </span>
              {usage && (
                <span className="project-graph-progress-usage">
                  in {usage.tokens_in.toLocaleString()} · out {usage.tokens_out.toLocaleString()}
                  {usage.cost_usd > 0 && ` · $${usage.cost_usd.toFixed(4)}`}
                </span>
              )}
            </div>
            {stages.length > 0 && (
              <ul className="project-graph-stages">
                {stages.map((s, i) => (
                  <li key={i}>{s.message}</li>
                ))}
              </ul>
            )}
            {doneSummary && (
              <div className="project-graph-done">
                {doneSummary.node_count ?? "?"} nodes · {doneSummary.edge_count ?? "?"} edges · {(doneSummary.duration_ms / 1000).toFixed(1)}s
              </div>
            )}
            {errMsg && <div className="project-graph-error" role="alert">{errMsg}</div>}
          </div>
        )}

        <div className="project-graph-actions">
          {status.state === "never" && (
            <button
              type="button"
              className="btn btn-accent"
              onClick={() => startBuild("rebuild")}
              disabled={building}
              title="Run graphify on this project's videos for the first time"
            >
              build graph
            </button>
          )}
          {(status.state === "ready" || status.state === "error") && (
            <>
              <button
                type="button"
                className="btn btn-accent"
                onClick={() => startBuild("update")}
                disabled={building}
                title="Incremental: only re-extract new / changed files"
              >
                update graph
              </button>
              <button
                type="button"
                className="btn btn-ghost"
                onClick={() => {
                  if (window.confirm(
                    "Rebuild from scratch? This re-runs every semantic-extraction subagent and costs more tokens.",
                  )) startBuild("rebuild");
                }}
                disabled={building}
                title="Full re-extract; costs more tokens"
              >
                rebuild from scratch
              </button>
            </>
          )}
          {hasGraph && (
            <a
              className="btn btn-ghost"
              href={projectGraphIndexUrl(projectId)}
              target="_blank"
              rel="noopener noreferrer"
              title="Open the interactive graph in a new tab"
            >
              open graph
            </a>
          )}
        </div>
      </div>
    </section>
  );
}
