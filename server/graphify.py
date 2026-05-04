"""Per-project knowledge-graph builds via the `/graphify` Claude skill.

Runs `claude --print "/graphify <project-folder> [flags]"` as a
subprocess, streams its JSON event log, translates the events into
the same phase/usage/done envelope the analysis pipeline uses, and
on success stamps `projects.graph_*` columns from the resulting
`graphify-out/graph.json`.

Three knobs:
  * mode='update'  -> /graphify <path> --update         (incremental)
  * mode='rebuild' -> /graphify <path>                  (full re-extract)
  * mode='deep'    -> /graphify <path> --mode deep      (richer edges)

Outputs land at `<project>/graphify-out/`. graphify itself owns that
folder; we don't touch it directly.
"""
from __future__ import annotations

import json
import logging
import os
import queue
import shutil
import signal
import subprocess
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from threading import Event
from typing import Any, Iterator

import psutil

from .layout import project_dir


log = logging.getLogger(__name__)


GRAPH_OUT_SUBDIR = "graphify-out"
# Sentinel that closes a subscriber's queue. Exposed at module scope so
# different subscribers can share the same value without identity drift.
_END_OF_STREAM = object()
# Recent events kept per session so a refreshed UI gets context, not a
# blank progress feed. Sized to comfortably cover a typical stage list
# (~10-30 events for a small corpus, 50-100 for deep mode).
_RING_BUFFER_SIZE = 200


class GraphifyError(Exception):
    pass


class GraphifyAlreadyRunning(GraphifyError):
    """Raised when a build is requested for a project that already has
    one in flight AND the caller asked us to refuse (legacy behavior).
    The current `stream_graph_build` code path attaches to the running
    session instead of raising, so the only callers that still see this
    are the explicit pre-checks via `is_build_active()`."""


class BuildSession:
    """One per in-flight build. Owns a ring buffer of events emitted so
    far + a fan-out list of subscriber queues. Refreshed UIs subscribe
    again, get a replay of the buffer, then continue receiving live
    events. The build's worker thread is the sole producer; route
    handlers / SSE generators are consumers."""

    def __init__(self, project_id: str, mode: str) -> None:
        self.project_id = project_id
        self.mode = mode
        self.cancel = Event()
        self.started_at = time.monotonic()
        self._lock = threading.Lock()
        self._history: list[dict] = []
        self._subscribers: list["queue.Queue[Any]"] = []
        self._done = False

    def emit(self, evt: dict) -> None:
        """Append to the ring buffer + push to every live subscriber.
        Called from the build worker thread."""
        with self._lock:
            self._history.append(evt)
            if len(self._history) > _RING_BUFFER_SIZE:
                self._history = self._history[-_RING_BUFFER_SIZE:]
            subs = list(self._subscribers)
        for q in subs:
            try:
                q.put_nowait(evt)
            except Exception:
                pass

    def subscribe(self) -> "queue.Queue[Any]":
        """Return a queue pre-loaded with the replay buffer. Live events
        will be pushed as the worker emits them. If the build already
        finished, the queue has its sentinel queued and the iterator
        will exit immediately after replay."""
        q: "queue.Queue[Any]" = queue.Queue()
        with self._lock:
            for evt in self._history:
                q.put_nowait(evt)
            if self._done:
                q.put_nowait(_END_OF_STREAM)
            else:
                self._subscribers.append(q)
        return q

    def finish(self) -> None:
        """Mark the build as terminal and signal every live subscriber
        to close out cleanly. Called from the worker's finally clause."""
        with self._lock:
            self._done = True
            subs = list(self._subscribers)
            self._subscribers.clear()
        for q in subs:
            try:
                q.put_nowait(_END_OF_STREAM)
            except Exception:
                pass

    def is_done(self) -> bool:
        with self._lock:
            return self._done


# Process-lifetime registry: at most one BuildSession per project at a
# time. A finished session is dropped once the worker exits so the next
# build creates a fresh one.
_active_builds: dict[str, BuildSession] = {}
_active_builds_lock = threading.Lock()


def is_build_active(project_id: str) -> bool:
    with _active_builds_lock:
        sess = _active_builds.get(project_id)
        return sess is not None and not sess.is_done()


# ---------------------------------------------------------------------------
# Subprocess tree-kill (cancel) -- mirrors claude_cli + codex_cli
# ---------------------------------------------------------------------------


def _kill_tree(proc: subprocess.Popen, grace_sec: float = 2.0) -> None:
    try:
        parent = psutil.Process(proc.pid)
    except psutil.NoSuchProcess:
        return
    try:
        children = parent.children(recursive=True)
    except psutil.NoSuchProcess:
        children = []
    if os.name == "nt":
        try:
            proc.send_signal(signal.CTRL_BREAK_EVENT)
        except (OSError, ValueError):
            pass
    else:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        except OSError:
            pass
    try:
        proc.wait(timeout=grace_sec)
    except subprocess.TimeoutExpired:
        pass
    for c in children + [parent]:
        try:
            if c.is_running():
                c.kill()
        except psutil.NoSuchProcess:
            pass


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _shorten(s: str, n: int = 80) -> str:
    s = " ".join(s.split())
    return s if len(s) <= n else s[: n - 1] + "..."


# ---------------------------------------------------------------------------
# Graph build status helpers
# ---------------------------------------------------------------------------


def _set_graph_state(
    out_dir: Path, project_id: str, *, state: str,
    built_at: str | None = None,
    node_count: int | None = None,
    edge_count: int | None = None,
    last_error: str | None = None,
    reset_events: bool = False,
) -> None:
    from .db import open_connection
    conn = open_connection(out_dir / "app.db")
    try:
        sets = ["graph_state=?"]
        params: list[Any] = [state]
        if built_at is not None:
            sets.append("graph_built_at=?")
            params.append(built_at)
        if node_count is not None:
            sets.append("graph_node_count=?")
            params.append(node_count)
        if edge_count is not None:
            sets.append("graph_edge_count=?")
            params.append(edge_count)
        if last_error is not None:
            sets.append("graph_last_error=?")
            params.append(last_error)
        if reset_events:
            sets.append("events_since_build=0")
        sets.append("updated_at=?")
        params.append(_iso_now())
        params.append(project_id)
        conn.execute(
            f"UPDATE projects SET {', '.join(sets)} WHERE id=?", params,
        )
    finally:
        conn.close()


def get_graph_status(out_dir: Path, project_id: str) -> dict | None:
    from .db import open_connection
    conn = open_connection(out_dir / "app.db")
    try:
        row = conn.execute(
            "SELECT graph_state, graph_built_at, graph_node_count, "
            "       graph_edge_count, graph_last_error, events_since_build "
            "FROM projects WHERE id=?",
            (project_id,),
        ).fetchone()
        if row is None:
            return None
        out_folder = project_dir(out_dir, project_id) / GRAPH_OUT_SUBDIR
        # graphify writes either `index.html` or `graph.html` depending
        # on its version. The route handler serves whichever is present
        # at `/graph/file/`, so the UI just needs to know "is there
        # something to show."
        has_viewer = (
            (out_folder / "index.html").is_file()
            or (out_folder / "graph.html").is_file()
        )
        return {
            "state": row["graph_state"],
            "built_at": row["graph_built_at"],
            "node_count": row["graph_node_count"],
            "edge_count": row["graph_edge_count"],
            "last_error": row["graph_last_error"],
            "events_since_build": row["events_since_build"] or 0,
            # `has_index_html` kept for backwards compat; semantics now
            # broaden to "there's a viewer HTML to open."
            "has_index_html": has_viewer,
            "has_graph_json": (out_folder / "graph.json").is_file(),
        }
    finally:
        conn.close()


def bump_events_since_build(out_dir: Path, project_id: str | None) -> None:
    """Increment the per-project staleness counter. Called from ingest /
    move / analysis / archive event paths so the project page can show
    'N events since last graph build'. Silent no-op when project_id is
    None (defensive)."""
    if not project_id:
        return
    from .db import open_connection
    conn = open_connection(out_dir / "app.db")
    try:
        conn.execute(
            "UPDATE projects SET events_since_build = events_since_build + 1, "
            "updated_at=? WHERE id=?",
            (_iso_now(), project_id),
        )
    except Exception:
        log.debug("bump_events_since_build failed for %s", project_id, exc_info=True)
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# graph.json -> node/edge counts
# ---------------------------------------------------------------------------


def _count_nodes_edges(graph_json_path: Path) -> tuple[int | None, int | None]:
    if not graph_json_path.is_file():
        return None, None
    try:
        data = json.loads(graph_json_path.read_text(encoding="utf-8"))
    except Exception:
        return None, None
    nodes = data.get("nodes") or []
    edges = data.get("edges") or []
    return (len(nodes) if isinstance(nodes, list) else None,
            len(edges) if isinstance(edges, list) else None)


# ---------------------------------------------------------------------------
# Streaming runner
# ---------------------------------------------------------------------------


def stream_graph_build(
    out_dir: Path,
    project_id: str,
    *,
    mode: str = "update",
    cancel_event: Event | None = None,
) -> Iterator[dict]:
    """Subscribe to the (possibly already-running) graph build for
    `project_id` and yield events. If a build is in flight, attach to
    its session: the subscriber gets the ring-buffer replay (so a
    refreshed UI doesn't see a blank progress feed), then continues
    receiving live events. If no build is in flight, kick a fresh one
    and attach.

    `mode` is 'update' (default, incremental), 'rebuild' (full), or
    'deep' (full + --mode deep). Modes only matter on a fresh kick --
    when attaching to a running build, the in-flight mode wins.

    `cancel_event`: deprecated; the session owns its own cancel signal.
    Kept in the signature for backwards compat with old callers."""
    folder = project_dir(out_dir, project_id)
    if not folder.is_dir():
        yield {"type": "error", "error_message": f"project folder missing: {folder}"}
        return

    # Attach-or-kick. Using a single critical section so two near-
    # simultaneous subscribers (e.g. EventSource reconnect within a few
    # ms) don't both create a session.
    is_fresh: bool
    with _active_builds_lock:
        existing = _active_builds.get(project_id)
        if existing is not None and not existing.is_done():
            session = existing
            is_fresh = False
        else:
            cli = shutil.which("claude")
            if not cli:
                yield {"type": "error", "error_message": "`claude` CLI not found on PATH"}
                return
            session = BuildSession(project_id, mode)
            _active_builds[project_id] = session
            is_fresh = True

    sub = session.subscribe()
    if not is_fresh:
        # Joining a running build: just stream what's in the buffer +
        # what comes next. Don't start a worker, don't touch the DB.
        while True:
            evt = sub.get()
            if evt is _END_OF_STREAM:
                return
            yield evt
        return

    # Fresh build: launch a worker thread that drives the subprocess
    # and emits events into the session. The current generator (this
    # function call) just drains its own subscriber queue.
    threading.Thread(
        target=_run_build_into_session,
        args=(out_dir, session, folder),
        daemon=True,
        name=f"graphify-{project_id}",
    ).start()

    while True:
        evt = sub.get()
        if evt is _END_OF_STREAM:
            return
        yield evt


def _run_build_into_session(
    out_dir: Path,
    session: BuildSession,
    folder: Path,
) -> None:
    """Worker entry point. Runs the actual claude subprocess, parses
    its stream-json, and emits normalised events into the session.
    Always finishes the session and releases the active-builds slot,
    even on unexpected exceptions."""
    project_id = session.project_id
    mode = session.mode
    cli = shutil.which("claude")  # already verified at session creation
    cancel_event = session.cancel

    # Build the prompt. graphify's slash command handler reads the path
    # as the first positional after the command, so we pass it inline.
    if mode == "deep":
        flags = " --mode deep"
    elif mode == "rebuild":
        # Full re-extract: no flag, graphify defaults to a clean run.
        flags = ""
    else:  # update (default)
        flags = " --update"
    # graphify is built around an existing path; quote it so paths with
    # spaces (which our output dir has -- "Structured Experiments") survive
    # the slash-command parser intact.
    prompt = f'/graphify "{folder}"{flags}'

    # Mark the project as building before we kick the subprocess so the
    # UI immediately reflects the state.
    _set_graph_state(out_dir, project_id, state="building", last_error=None)
    session.emit({"type": "stage", "stage": "Launching graphify..."})

    cmd = [
        cli, "--print",
        "--output-format=stream-json",
        "--verbose",
        # Auto-accept tool runs so graphify's pipeline (pip install,
        # python invocations, file writes inside the project folder) can
        # proceed without an interactive prompt. The runner is a trusted
        # local subprocess on a folder the server already owns.
        "--dangerously-skip-permissions",
        prompt,
    ]

    creationflags = 0
    preexec_fn = None
    if os.name == "nt":
        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        preexec_fn = os.setsid  # type: ignore[assignment]

    start = time.monotonic()
    proc: subprocess.Popen | None = None
    try:
        try:
            proc = subprocess.Popen(
                cmd,
                cwd=str(folder),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                creationflags=creationflags,
                preexec_fn=preexec_fn,
            )
        except OSError as e:
            msg = f"failed to launch claude: {e}"
            _set_graph_state(out_dir, project_id, state="error", last_error=msg)
            session.emit({"type": "error", "error_message": msg})
            return

        usage = {"tokens_in": 0, "tokens_out": 0, "cached_tokens": 0, "cost_usd": 0.0}
        last_text = ""
        err_msg: str | None = None
        # Claude emits a `system/init` event for the orchestrator AND
        # every Task-dispatched subagent. Only emit the friendly label
        # once per build; subsequent inits drop silently and are covered
        # by the Task tool_use path with their real subagent label.
        saw_init = False

        assert proc.stdout is not None
        for line in iter(proc.stdout.readline, ""):
            if cancel_event.is_set():
                _kill_tree(proc)
                err_msg = "cancelled"
                break
            line = line.strip()
            if not line or not line.startswith("{"):
                continue
            try:
                evt = json.loads(line)
            except json.JSONDecodeError:
                continue
            etype = evt.get("type")
            if etype == "system":
                subtype = evt.get("subtype")
                if subtype == "init" and not saw_init:
                    saw_init = True
                    session.emit({"type": "stage", "stage": "Claude session started"})
            elif etype == "assistant":
                msg = evt.get("message") or {}
                for block in msg.get("content") or []:
                    btype = block.get("type")
                    if btype == "text":
                        text = (block.get("text") or "").strip()
                        if text:
                            last_text = text
                            session.emit({"type": "stage", "stage": _shorten(text, 100)})
                    elif btype == "tool_use":
                        name = block.get("name") or ""
                        inp = block.get("input") or {}
                        label = _tool_use_label(name, inp)
                        if label:
                            session.emit({"type": "stage", "stage": label})
                u = msg.get("usage") or {}
                if u:
                    usage["tokens_in"] = int(u.get("input_tokens") or 0) + usage["tokens_in"]
                    usage["tokens_out"] = int(u.get("output_tokens") or 0) + usage["tokens_out"]
                    cache_read = int(u.get("cache_read_input_tokens") or 0)
                    cache_creation = int(u.get("cache_creation_input_tokens") or 0)
                    usage["cached_tokens"] += cache_read + cache_creation
                    session.emit({
                        "type": "usage",
                        "tokens_in": usage["tokens_in"],
                        "tokens_out": usage["tokens_out"],
                        "cached_tokens": usage["cached_tokens"],
                        "cost_usd": usage["cost_usd"],
                    })
            elif etype == "result":
                cost = evt.get("total_cost_usd")
                if cost is not None:
                    usage["cost_usd"] = float(cost)
                if evt.get("is_error"):
                    err_msg = evt.get("result") or "claude reported an error"
            elif etype == "error":
                err_msg = evt.get("message") or "claude error"
        stderr = ""
        if proc.stderr:
            try:
                stderr = proc.stderr.read() or ""
            except Exception:
                pass
        try:
            proc.wait(timeout=2.0)
        except subprocess.TimeoutExpired:
            _kill_tree(proc, grace_sec=1.0)

        duration_ms = int((time.monotonic() - start) * 1000)
        graph_json = folder / GRAPH_OUT_SUBDIR / "graph.json"
        node_count, edge_count = _count_nodes_edges(graph_json)

        if err_msg or proc.returncode not in (0, None):
            failure = err_msg or f"claude exited with rc={proc.returncode}; {_shorten(stderr, 200)}"
            _set_graph_state(out_dir, project_id, state="error", last_error=failure)
            session.emit({"type": "error", "error_message": failure})
            return

        if not graph_json.is_file():
            failure = (
                "graphify finished but produced no graph.json -- check "
                f"`{folder / GRAPH_OUT_SUBDIR}` for partial output"
            )
            _set_graph_state(out_dir, project_id, state="error", last_error=failure)
            session.emit({"type": "error", "error_message": failure})
            return

        _set_graph_state(
            out_dir, project_id,
            state="ready",
            built_at=_iso_now(),
            node_count=node_count,
            edge_count=edge_count,
            last_error=None,
            reset_events=True,
        )
        session.emit({
            "type": "done",
            "duration_ms": duration_ms,
            "tokens_in": usage["tokens_in"],
            "tokens_out": usage["tokens_out"],
            "cached_tokens": usage["cached_tokens"],
            "cost_usd": usage["cost_usd"],
            "node_count": node_count,
            "edge_count": edge_count,
            "graph_path": str(graph_json),
            "last_text": _shorten(last_text, 400),
        })
    except Exception as e:
        # Worker thread crashed -- surface it as a build failure rather
        # than letting the session hang silently with no terminal event.
        log.exception("graphify worker crashed for %s", project_id)
        _set_graph_state(
            out_dir, project_id, state="error",
            last_error=f"worker crashed: {type(e).__name__}: {e}",
        )
        session.emit({
            "type": "error",
            "error_message": f"worker crashed: {type(e).__name__}: {e}",
        })
    finally:
        if proc is not None:
            try:
                proc.wait(timeout=1.0)
            except subprocess.TimeoutExpired:
                _kill_tree(proc, grace_sec=1.0)
        # Close out subscribers + remove the session from the registry.
        # The session lingers briefly in the active dict if a subscriber
        # is still draining; finish() flushes the END_OF_STREAM sentinel
        # so they exit cleanly.
        session.finish()
        with _active_builds_lock:
            current = _active_builds.get(project_id)
            if current is session:
                _active_builds.pop(project_id, None)


def _tool_use_label(name: str, inp: dict) -> str:
    """Translate a tool_use into a short user-visible stage label.
    Mirrors claude_cli._tool_use_label so the UI feels consistent."""
    if name == "Read":
        fp = inp.get("file_path") or ""
        return f"Reading {Path(fp).name}" if fp else "Reading file"
    if name == "Bash":
        cmd = inp.get("command") or ""
        return f"Running: {_shorten(cmd, 60)}" if cmd else "Running command"
    if name == "Grep":
        return f"Searching: {_shorten(inp.get('pattern') or '', 40)}"
    if name == "Glob":
        return f"Finding: {_shorten(inp.get('pattern') or '', 40)}"
    if name == "Write":
        fp = inp.get("file_path") or ""
        return f"Writing {Path(fp).name}" if fp else "Writing file"
    if name == "Task":
        desc = inp.get("description") or inp.get("subagent_type") or ""
        return f"Subagent: {_shorten(desc, 50)}" if desc else "Dispatching subagent"
    if name == "TodoWrite":
        return "Updating task list"
    if name:
        return f"Using {name}"
    return ""
