"""FastAPI app: serves the React SPA + the API surface (transcripts, SSE
transcribe, WebSocket PTY scoped per-video)."""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from typing import Any

from fastapi import Body, Depends, FastAPI, HTTPException, Query, Response, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sse_starlette.sse import EventSourceResponse

from . import auth, meta as meta_mod, state, transcripts
from . import marks as marks_mod
from . import playlist as playlist_mod
from . import podcast as podcast_mod
from . import projects as projects_mod
from . import queue as queue_mod
from . import sources as sources_mod
from .analyze import analyze_video, stream_analyze_video
from .db import open_connection, run_migrations
from .layout import find_audio_file, migrate_flat_outputs, video_dir
from .migrate_data import migrate_data
from .pty_handler import handle_pty_session
from .transcriber import (
    TranscribeRequest,
    _safe_video_id,
    extract_video_id,
    stream_transcription,
)


PROJECT_ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = Path(os.environ.get("OUTPUT_DIR") or (PROJECT_ROOT / "output"))
WEB_DIST = PROJECT_ROOT / "web" / "dist"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("server")

# One-time idempotent migration of any legacy flat-file outputs.
_counts = migrate_flat_outputs(OUTPUT_DIR)
if _counts["migrated"]:
    log.info("Migrated %s flat-file transcripts to per-video folders", _counts["migrated"])

# Load + persist the job registry. On startup, any non-done entries from
# the previous run are marked as orphaned (their worker threads are gone).
state.configure_persistence(OUTPUT_DIR / "_ingests.json")

# Open DB, run schema migrations, and do the one-shot data migration if
# the marker hasn't been set. Idempotent — safe on every startup.
try:
    _conn = open_connection(OUTPUT_DIR / "app.db")
    run_migrations(_conn)
    migrate_data(_conn, OUTPUT_DIR)
    _conn.close()
    queue_mod.mark_queued_orphans()
    # Move-protocol crash recovery: walk the move_log for any moves that
    # didn't finish before the previous shutdown and resolve them
    # before serving requests. Idempotent; clean tree -> no-op.
    from .moves import recover_in_flight_moves
    _recovery = recover_in_flight_moves(OUTPUT_DIR)
    if any(_recovery.values()):
        log.info("move recovery: %s", _recovery)
except Exception as e:
    log.warning("DB bootstrap failed: %s", e)

app = FastAPI(title="youtube-video-ingestor", version="0.3.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# Check-screen marks router — annotation layer sitting alongside transcripts.
# Bound to OUTPUT_DIR at construction time so it shares the same app.db.
app.include_router(marks_mod.build_router(OUTPUT_DIR))


@app.on_event("shutdown")
def _on_shutdown():
    queue_mod.on_shutdown()


# ---------------------------------------------------------------------------
# API: transcripts
# ---------------------------------------------------------------------------


# Auth probe endpoints. /info reveals whether auth is active (unauthenticated);
# /check validates the caller's current token.
@app.get("/api/auth/info")
def api_auth_info():
    return {"enabled": auth.is_enabled()}


@app.get("/api/auth/check", dependencies=[Depends(auth.require_http)])
def api_auth_check():
    return {"ok": True}


@app.get("/api/transcripts", dependencies=[Depends(auth.require_http)])
def api_list_transcripts():
    return transcripts.list_transcripts(OUTPUT_DIR)


@app.get("/api/transcripts/{video_id}", dependencies=[Depends(auth.require_http)])
def api_get_transcript(video_id: str):
    data = transcripts.read_transcript(OUTPUT_DIR, video_id)
    if data is None:
        raise HTTPException(status_code=404, detail=f"No transcript for {video_id}")
    return data


@app.get("/api/archive", dependencies=[Depends(auth.require_http)])
def api_list_archive():
    return transcripts.list_archived(OUTPUT_DIR)


@app.post("/api/transcripts/{video_id}/refresh-metadata", dependencies=[Depends(auth.require_http)])
def api_refresh_metadata(video_id: str):
    """Re-fetch YouTube metadata (channel, views, likes, description, upload
    date) via yt-dlp without re-downloading audio, and merge into the existing
    transcript.json. Transcript segments are preserved."""
    import json as _json
    from yt_dlp import YoutubeDL
    from .transcriber import _extract_video_metadata, _ytdlp_cookie_opts

    data = transcripts.read_transcript(OUTPUT_DIR, video_id)
    if data is None:
        raise HTTPException(status_code=404, detail="not found")
    url = data.get("url") or f"https://www.youtube.com/watch?v={video_id}"

    opts: dict = {"quiet": True, "no_warnings": True, "skip_download": True}
    opts.update(_ytdlp_cookie_opts())
    try:
        with YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"yt-dlp failed: {type(e).__name__}: {e}")

    if not isinstance(info, dict):
        raise HTTPException(status_code=502, detail="yt-dlp returned no info")

    data.update(_extract_video_metadata(info))
    # Keep duration + title fresh too in case YouTube edited them.
    if info.get("duration") is not None:
        data["duration_sec"] = info.get("duration")
    if info.get("title"):
        data["title"] = info["title"]

    p = video_dir(OUTPUT_DIR, video_id) / "transcript.json"
    p.write_text(_json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    # Keep the DB row in sync so dashboard / library reflect the refreshed metadata.
    try:
        _c = open_connection(OUTPUT_DIR / "app.db")
        try:
            _c.execute(
                "UPDATE videos SET title=?, channel=?, channel_id=?, channel_url=?, "
                "channel_follower_count=?, upload_date=?, view_count=?, like_count=?, "
                "comment_count=?, description=?, updated_at=datetime('now') "
                "WHERE id=?",
                (
                    data.get("title"),
                    data.get("channel"),
                    data.get("channel_id"),
                    data.get("channel_url"),
                    data.get("channel_follower_count"),
                    data.get("upload_date"),
                    data.get("view_count"),
                    data.get("like_count"),
                    data.get("comment_count"),
                    data.get("description"),
                    video_id,
                ),
            )
        finally:
            _c.close()
    except Exception as e:
        log.warning("refresh-metadata DB update failed for %s: %s", video_id, e)
    return {"ok": True}


@app.post("/api/transcripts/{video_id}/archive", dependencies=[Depends(auth.require_http)])
def api_archive_video(video_id: str):
    if not transcripts.set_archived(OUTPUT_DIR, video_id, True):
        raise HTTPException(status_code=404, detail="not found")
    _bump_owning_project(video_id)
    return {"ok": True, "archived": True}


@app.post("/api/transcripts/{video_id}/restore", dependencies=[Depends(auth.require_http)])
def api_restore_video(video_id: str):
    if not transcripts.set_archived(OUTPUT_DIR, video_id, False):
        raise HTTPException(status_code=404, detail="not found")
    _bump_owning_project(video_id)
    return {"ok": True, "archived": False}


def _bump_owning_project(video_id: str) -> None:
    """Look up the video's project and bump its events_since_build
    counter. Used by archive/restore -- ingest and move bump from their
    own code paths."""
    try:
        _c = open_connection(OUTPUT_DIR / "app.db")
        try:
            row = _c.execute(
                "SELECT project_id FROM videos WHERE id=?", (video_id,),
            ).fetchone()
        finally:
            _c.close()
        if row and row["project_id"]:
            from .graphify import bump_events_since_build
            bump_events_since_build(OUTPUT_DIR, row["project_id"])
    except Exception:
        log.debug("_bump_owning_project failed for %s", video_id, exc_info=True)


@app.post("/api/transcripts/{video_id}/move", dependencies=[Depends(auth.require_http)])
def api_move_video(video_id: str, body: dict = Body(...)):
    """Move a video into a different project. Body: {"project_id": "<id>"}.
    Returns the from/to summary on success. Refuses (409) if a move is
    already in progress, an ingest is active, or the destination is
    already populated. Returns 400 for unknown project, 404 for unknown
    video."""
    target = (body or {}).get("project_id")
    if not isinstance(target, str) or not target:
        raise HTTPException(status_code=400, detail="project_id is required")
    from .moves import move_video, MoveError
    try:
        return move_video(OUTPUT_DIR, video_id, target)
    except MoveError as e:
        msg = str(e)
        if "unknown video" in msg:
            raise HTTPException(status_code=404, detail=msg)
        if "unknown project" in msg:
            raise HTTPException(status_code=400, detail=msg)
        # already-in-progress, ingest-active, dst-exists, already-in -- all conflicts.
        raise HTTPException(status_code=409, detail=msg)


@app.delete("/api/transcripts/{video_id}", dependencies=[Depends(auth.require_http)])
def api_delete_video(video_id: str):
    """Permanently delete the per-video folder. Refuses unless the video
    has been soft-archived first."""
    data = transcripts.read_transcript(OUTPUT_DIR, video_id)
    if data is None:
        raise HTTPException(status_code=404, detail="not found")
    if not data.get("archived"):
        raise HTTPException(
            status_code=400,
            detail="video must be archived before permanent deletion",
        )
    if not transcripts.delete_video(OUTPUT_DIR, video_id):
        raise HTTPException(status_code=500, detail="delete failed")
    return {"ok": True, "deleted": True}


@app.get("/api/ingests", dependencies=[Depends(auth.require_http)])
def api_list_ingests():
    """Global view of what's transcribing right now. Polled by the Library
    so you can see progress even if you started the ingest in another tab."""
    return state.list_active()


async def _consume_silently(gen) -> None:
    """Drain an SSE generator to nothing — used when we kick a retry without
    a browser SSE consumer. The worker thread writes progress to state;
    we just need the generator iterated so its internal queue doesn't block."""
    try:
        async for _ in gen:
            pass
    except Exception:  # pragma: no cover — never let this take down the server
        log.exception("background ingest drain died")


@app.post("/api/ingests/{video_id}/retry", dependencies=[Depends(auth.require_http)])
async def api_retry_ingest(video_id: str, body: dict[str, Any] | None = Body(default=None)):
    """Re-run the full pipeline for a failed / stalled / orphaned ingest.
    Looks up the original URL from the registry; caller may override via
    {"url": "..."} in the JSON body. Fire-and-forget — returns 202."""
    record = state.get(video_id)
    provided_url = (body or {}).get("url") if isinstance(body, dict) else None
    url = provided_url or (record.url if record else None)
    if not url:
        raise HTTPException(
            status_code=400,
            detail="no url on record and none provided — include {\"url\": \"...\"} in the body",
        )
    # Rehydrate podcast metadata so persist_video_to_db lands on the right
    # branch (source='podcast', rich title/host/show fields) instead of
    # the yt-dlp-generic-extractor fallback that would clobber title to a
    # CDN slug and source to 'youtube'. Sources, in priority order:
    #   1. The original metadata stashed on the registry by the
    #      bulk-podcast endpoint. Survives within a server lifetime.
    #   2. Reconstructed from transcript.json on disk + the videos row,
    #      for restart-orphaned podcast jobs whose registry entry got
    #      reloaded with metadata=None (e.g. from a pre-fix server).
    #   3. None — pure YouTube path; behavior unchanged.
    metadata: dict | None = None
    if record is not None and record.metadata:
        metadata = dict(record.metadata)
    else:
        metadata = _reconstruct_podcast_metadata(video_id)

    # Drop the old record so the new run starts clean.
    state.drop(video_id)
    req = TranscribeRequest(url=url, metadata=metadata)
    hf_token = os.environ.get("HUGGINGFACE_TOKEN")
    gen = stream_transcription(req, OUTPUT_DIR, hf_token=hf_token)
    asyncio.create_task(_consume_silently(gen))
    return {"started": True, "video_id": video_id, "url": url}


def _reconstruct_podcast_metadata(video_id: str) -> dict | None:
    """Best-effort fallback: rebuild a podcast metadata dict from disk +
    DB for a video whose in-memory registry entry has been wiped. Returns
    None when this isn't a podcast (no special metadata is needed for
    YouTube retries — yt-dlp's extractor handles those correctly)."""
    import json as _json
    tj = video_dir(OUTPUT_DIR, video_id) / "transcript.json"
    disk: dict = {}
    if tj.exists():
        try:
            disk = _json.loads(tj.read_text(encoding="utf-8"))
        except Exception:
            disk = {}
    src = (disk.get("source") or "").lower()
    if src != "podcast":
        # Also peek at the DB row in case transcript.json hasn't been
        # written yet (early-failure retry).
        try:
            conn = open_connection(OUTPUT_DIR / "app.db")
            try:
                row = conn.execute(
                    "SELECT source, title, channel, show_name, show_url, "
                    "image_url, duration_sec, description "
                    "FROM videos WHERE id=?",
                    (video_id,),
                ).fetchone()
            finally:
                conn.close()
        except Exception:
            row = None
        if row is None or (row["source"] or "").lower() != "podcast":
            return None
        return {
            "source": "podcast",
            "title": row["title"],
            "host": row["channel"],
            "show_name": row["show_name"],
            "show_url": row["show_url"],
            "image_url": row["image_url"],
            "pub_date": None,
            "duration_sec": row["duration_sec"],
            "description": row["description"],
        }
    return {
        "source": "podcast",
        "title": disk.get("title"),
        "host": disk.get("channel"),
        "show_name": disk.get("show_name"),
        "show_url": disk.get("show_url"),
        "image_url": disk.get("image_url"),
        "pub_date": disk.get("upload_date"),
        "duration_sec": disk.get("duration_sec"),
        "description": disk.get("description"),
    }


@app.delete("/api/ingests/{video_id}", dependencies=[Depends(auth.require_http)])
def api_drop_ingest(video_id: str):
    """Force-clear a zombie registry entry. Doesn't kill an actual worker
    thread — if one is genuinely still running, the next state.update() will
    resurrect the entry."""
    removed = state.drop(video_id)
    if not removed:
        raise HTTPException(status_code=404, detail="no ingest record")
    return {"dropped": True, "video_id": video_id}


@app.post("/api/ingests/{video_id}/cancel", dependencies=[Depends(auth.require_http)])
def api_cancel_ingest(video_id: str):
    """Signal the worker thread to abort at its next phase boundary. Python
    threads can't be force-killed, so effective latency = time until next
    phase transition (typically ≤ current phase duration: download ~seconds,
    transcribe ~minutes, diarize ~minutes). Returns 404 if no active job."""
    ok = state.request_cancel(video_id)
    if not ok:
        raise HTTPException(status_code=404, detail="no active ingest for this id")
    return {"cancel_requested": True, "video_id": video_id}


@app.get("/api/transcripts/{video_id}/folder", dependencies=[Depends(auth.require_http)])
def api_video_folder_status(video_id: str):
    """What's actually on disk for this video. Lets the Detail page detect
    the "started ingest but never finished" case (folder exists, empty or
    missing transcript.json, and no active /api/ingests record either) so
    it can surface a Retry / Discard affordance instead of just sitting
    on 'Waiting for transcript' forever."""
    folder = video_dir(OUTPUT_DIR, video_id)
    if not folder.exists():
        return {
            "exists": False,
            "is_empty": False,
            "has_transcript": False,
            "has_audio": False,
            "files": [],
        }
    try:
        names = [p.name for p in folder.iterdir()]
    except OSError:
        names = []
    return {
        "exists": True,
        "is_empty": len(names) == 0,
        "has_transcript": "transcript.json" in names,
        "has_audio": any(n.startswith("audio.") for n in names),
        "files": names,
    }


@app.delete("/api/transcripts/{video_id}/folder", dependencies=[Depends(auth.require_http)])
def api_discard_orphan_folder(video_id: str):
    """Remove the per-video folder when it's an orphan from a crashed
    ingest. Refuses if a transcript exists or an ingest is in flight, so
    a stray DELETE can't trash real data."""
    if transcripts.read_transcript(OUTPUT_DIR, video_id) is not None:
        raise HTTPException(status_code=409, detail="folder has a real transcript; archive then delete instead")
    rec = state.get(video_id)
    if rec is not None and not rec.done:
        raise HTTPException(status_code=409, detail="ingest is currently running")

    folder = video_dir(OUTPUT_DIR, video_id)
    if not folder.exists():
        return {"removed": False, "reason": "folder does not exist"}
    import shutil
    try:
        shutil.rmtree(folder)
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"could not remove folder: {e}")
    # Best-effort: clear any stale registry entry too.
    state.drop(video_id)
    return {"removed": True}


@app.get("/api/transcripts/{video_id}/meta", dependencies=[Depends(auth.require_http)])
def api_get_meta(video_id: str):
    # Existence check: only return meta if the video has a transcript.
    if transcripts.read_transcript(OUTPUT_DIR, video_id) is None:
        raise HTTPException(status_code=404, detail="not found")
    return meta_mod.read_meta(OUTPUT_DIR, video_id)


@app.patch("/api/transcripts/{video_id}/meta", dependencies=[Depends(auth.require_http)])
async def api_patch_meta(video_id: str, body: dict):
    if transcripts.read_transcript(OUTPUT_DIR, video_id) is None:
        raise HTTPException(status_code=404, detail="not found")
    return meta_mod.write_meta(OUTPUT_DIR, video_id, body or {})


@app.get("/api/transcripts/{video_id}/analysis", dependencies=[Depends(auth.require_http)])
def api_get_analysis(video_id: str):
    import json
    p = video_dir(OUTPUT_DIR, video_id) / "analysis.json"
    if not p.exists():
        raise HTTPException(status_code=404, detail="no analysis yet")
    return json.loads(p.read_text(encoding="utf-8"))


@app.get("/api/search", dependencies=[Depends(auth.require_http)])
def api_search(q: str = Query("", min_length=0), limit: int = Query(60, ge=1, le=200)):
    """Simple substring search across all active transcripts' segments.

    Returns up to `limit` hits with:
      video_id, title, start (sec), end (sec), speaker (or null), text.
    Archived transcripts are excluded. Case-insensitive.
    """
    import json
    needle = (q or "").strip().lower()
    if len(needle) < 2:
        return {"query": q, "results": [], "truncated": False}

    hits: list[dict] = []
    truncated = False
    if not OUTPUT_DIR.exists():
        return {"query": q, "results": [], "truncated": False}

    # Walk each video folder under output/projects/<project>/<video>/,
    # skip archived. iter_video_dirs handles the post-migration layout;
    # any flat output/<id>/ leftovers from a partial / unmigrated install
    # are intentionally NOT scanned -- they're orphans.
    from .layout import iter_video_dirs
    folders = sorted(
        iter_video_dirs(OUTPUT_DIR),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    for sub in folders:
        jp = sub / "transcript.json"
        if not jp.exists():
            continue
        try:
            data = json.loads(jp.read_text(encoding="utf-8"))
        except Exception:
            continue
        if data.get("archived"):
            continue
        title = data.get("title") or sub.name
        vid = data.get("id") or sub.name
        for seg in data.get("segments", []) or []:
            text = seg.get("text") or ""
            if needle in text.lower():
                hits.append({
                    "video_id": vid,
                    "title": title,
                    "start": float(seg.get("start", 0) or 0),
                    "end": float(seg.get("end", 0) or 0),
                    "speaker": seg.get("speaker"),
                    "text": text,
                })
                if len(hits) >= limit:
                    truncated = True
                    break
        if truncated:
            break
    return {"query": q, "results": hits, "truncated": truncated}


# NOTE: there is intentionally no POST /analyze kickoff endpoint. The SSE
# stream below is the single entry point for analysis: subscribing to it
# starts the worker. A separate POST kickoff used to live here, but the
# frontend always opened the SSE *and* hit the POST, which spawned two
# parallel runs (two `analyses` rows, two provider invocations, double
# tokens). One door = one analysis.


@app.get("/api/transcripts/{video_id}/analyze/stream", dependencies=[Depends(auth.require_http)])
async def api_stream_analyze(
    video_id: str,
    provider: str = "claude_cli",
    model: str | None = None,
):
    """Stream live analysis progress as SSE. Event types: `stage`, `usage`,
    `progress`, `done`, `error`. Query params: `provider`, `model`."""
    import asyncio
    import json as _json

    import server.ai as ai_registry

    p = ai_registry.get_provider(provider)
    if p is None:
        raise HTTPException(status_code=400, detail=f"unknown provider: {provider}")
    ok, reason = p.available()
    if not ok:
        raise HTTPException(
            status_code=400, detail=f"provider unavailable: {reason}"
        )

    async def event_gen():
        loop = asyncio.get_running_loop()
        q: asyncio.Queue = asyncio.Queue()
        SENTINEL = object()

        def worker() -> None:
            try:
                for evt in stream_analyze_video(
                    OUTPUT_DIR, video_id,
                    provider_name=provider, model=model,
                ):
                    loop.call_soon_threadsafe(q.put_nowait, {
                        "event": evt.get("type") or "progress",
                        "data": _json.dumps(evt, ensure_ascii=False),
                    })
            except Exception as e:
                loop.call_soon_threadsafe(q.put_nowait, {
                    "event": "error",
                    "data": _json.dumps({"error_message": f"{type(e).__name__}: {e}"}),
                })
            finally:
                loop.call_soon_threadsafe(q.put_nowait, SENTINEL)

        asyncio.create_task(asyncio.to_thread(worker))
        while True:
            item = await q.get()
            if item is SENTINEL:
                return
            yield item

    return EventSourceResponse(event_gen())


@app.get("/api/ai/providers", dependencies=[Depends(auth.require_http)])
def api_list_providers():
    """List all analysis providers with availability + models."""
    import server.ai as ai_registry
    return ai_registry.list_providers()


@app.post("/api/analyses/{analysis_id}/cancel", dependencies=[Depends(auth.require_http)])
def api_cancel_analysis(analysis_id: int):
    """Signal a running analysis to cancel. 404 if no in-flight run with that id."""
    from .analyze import cancel_analysis
    if not cancel_analysis(analysis_id):
        raise HTTPException(status_code=404, detail="no running analysis with that id")
    return {"cancel_requested": True, "analysis_id": analysis_id}


# ---------------------------------------------------------------------------
# Projects
# ---------------------------------------------------------------------------


@app.get("/api/projects", dependencies=[Depends(auth.require_http)])
def api_list_projects():
    return projects_mod.list_projects(OUTPUT_DIR)


@app.post("/api/projects", dependencies=[Depends(auth.require_http)])
def api_create_project(body: dict = Body(...), response: Response = None):
    name = (body.get("name") or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="name required")
    proj = projects_mod.create_project(
        OUTPUT_DIR, name=name, description=body.get("description"),
    )
    if response is not None:
        response.status_code = 201
    return proj


@app.get("/api/projects/{project_id}", dependencies=[Depends(auth.require_http)])
def api_get_project(project_id: str):
    data = projects_mod.get_project(OUTPUT_DIR, project_id)
    if data is None:
        raise HTTPException(status_code=404, detail="not found")
    return data


@app.patch("/api/projects/{project_id}", dependencies=[Depends(auth.require_http)])
def api_update_project(project_id: str, body: dict = Body(...)):
    ok = projects_mod.update_project(OUTPUT_DIR, project_id, body or {})
    if not ok:
        raise HTTPException(status_code=404, detail="not found")
    return projects_mod.get_project(OUTPUT_DIR, project_id)


@app.delete("/api/projects/{project_id}", dependencies=[Depends(auth.require_http)])
def api_delete_project(project_id: str):
    if not projects_mod.delete_project(OUTPUT_DIR, project_id):
        raise HTTPException(status_code=404, detail="not found")
    return {"ok": True}


@app.post("/api/projects/{project_id}/videos", dependencies=[Depends(auth.require_http)])
def api_add_project_videos(project_id: str, body: dict = Body(...)):
    ids = body.get("video_ids") or []
    if not isinstance(ids, list):
        raise HTTPException(status_code=400, detail="video_ids must be a list")
    added = projects_mod.add_videos(OUTPUT_DIR, project_id, ids)
    if added == -1:
        raise HTTPException(status_code=404, detail="project not found")
    return {"added": added}


@app.delete(
    "/api/projects/{project_id}/videos/{video_id}",
    dependencies=[Depends(auth.require_http)],
)
def api_remove_project_video(project_id: str, video_id: str):
    if not projects_mod.remove_video(OUTPUT_DIR, project_id, video_id):
        raise HTTPException(status_code=404, detail="membership not found")
    return {"ok": True}


# ---------------------------------------------------------------------------
# API: per-project knowledge graph (graphify integration)
# ---------------------------------------------------------------------------


@app.get("/api/projects/{project_id}/graph/status", dependencies=[Depends(auth.require_http)])
def api_graph_status(project_id: str):
    from .graphify import get_graph_status
    s = get_graph_status(OUTPUT_DIR, project_id)
    if s is None:
        raise HTTPException(status_code=404, detail="project not found")
    return s


@app.get("/api/projects/{project_id}/graph/build", dependencies=[Depends(auth.require_http)])
async def api_graph_build(
    project_id: str,
    mode: str = "update",
):
    """Kick a graphify run for the project. Query: ?mode=update|rebuild|deep.
    Returns SSE stream of phase / usage / done / error events. Uses GET
    so the browser's EventSource can subscribe directly. Concurrent
    builds for the same project are refused with 409; the per-project
    guard inside `stream_graph_build` ensures an EventSource auto-
    reconnect doesn't spawn a second claude subprocess."""
    import asyncio
    import json as _json
    from .graphify import (
        GraphifyAlreadyRunning,
        is_build_active,
        stream_graph_build,
    )

    mode = (mode or "update").lower()
    if mode not in ("update", "rebuild", "deep"):
        raise HTTPException(status_code=400, detail=f"unknown mode: {mode}")

    # Reject if the project doesn't exist (avoids spawning claude only
    # to fail at the folder check).
    proj = projects_mod.get_project(OUTPUT_DIR, project_id)
    if proj is None:
        raise HTTPException(status_code=404, detail="project not found")

    # Pre-check the concurrent-build guard so the route returns a clean
    # 409 (which the browser EventSource cannot auto-retry on) instead
    # of streaming an error event from inside the SSE response. The
    # generator below also re-checks atomically; this is just for a
    # cleaner UX on the rejection path.
    if is_build_active(project_id):
        raise HTTPException(
            status_code=409,
            detail=f"a graph build is already running for '{project_id}'",
        )

    async def event_gen():
        loop = asyncio.get_running_loop()
        q: asyncio.Queue = asyncio.Queue()
        SENTINEL = object()

        def worker() -> None:
            try:
                for evt in stream_graph_build(OUTPUT_DIR, project_id, mode=mode):
                    loop.call_soon_threadsafe(q.put_nowait, evt)
            except GraphifyAlreadyRunning as e:
                # Lost the race against another concurrent request.
                # Surface as an error event so the EventSource closes
                # without retrying.
                loop.call_soon_threadsafe(q.put_nowait, {
                    "type": "error", "error_message": str(e),
                })
            finally:
                loop.call_soon_threadsafe(q.put_nowait, SENTINEL)

        import threading
        threading.Thread(target=worker, daemon=True).start()
        while True:
            evt = await q.get()
            if evt is SENTINEL:
                break
            yield {
                "event": evt.get("type", "event"),
                "data": _json.dumps(evt, ensure_ascii=False),
            }

    return EventSourceResponse(event_gen())


@app.get(
    "/api/projects/{project_id}/graph/file/{path:path}",
    dependencies=[Depends(auth.require_http)],
)
def api_graph_static(project_id: str, path: str):
    """Static-serve files from `output/projects/<id>/graphify-out/`.
    The leading `file/` segment in the route disambiguates from
    `/build` and `/status`. The path can be empty, in which case
    `index.html` is returned."""
    from fastapi.responses import FileResponse
    from .layout import project_dir
    from .graphify import GRAPH_OUT_SUBDIR
    folder = (project_dir(OUTPUT_DIR, project_id) / GRAPH_OUT_SUBDIR).resolve()
    if not folder.is_dir():
        raise HTTPException(status_code=404, detail="graph not built yet")
    target = (folder / (path or "index.html")).resolve()
    # Path traversal guard: target MUST live under folder.
    try:
        target.relative_to(folder)
    except ValueError:
        raise HTTPException(status_code=403, detail="forbidden")
    if not target.is_file():
        raise HTTPException(status_code=404, detail="file not found")
    return FileResponse(target)


# ---------------------------------------------------------------------------
# Dashboard rollups
# ---------------------------------------------------------------------------


@app.get("/api/stats", dependencies=[Depends(auth.require_http)])
def api_stats():
    conn = open_connection(OUTPUT_DIR / "app.db")
    try:
        run_migrations(conn)
        video_count = conn.execute(
            "SELECT COUNT(*) FROM videos WHERE archived = 0"
        ).fetchone()[0]
        totals = conn.execute(
            "SELECT COALESCE(SUM(duration_sec),0), COALESCE(SUM(storage_bytes),0) "
            "FROM videos WHERE archived = 0"
        ).fetchone()
        project_count = conn.execute("SELECT COUNT(*) FROM projects").fetchone()[0]
        latest = [
            {
                "id": r["id"],
                "title": r["title"],
                "duration_sec": r["duration_sec"],
                "channel": r["channel"],
                "created_at": r["created_at"],
            }
            for r in conn.execute(
                "SELECT id, title, duration_sec, channel, created_at "
                "FROM videos WHERE archived = 0 ORDER BY created_at DESC LIMIT 8"
            )
        ]

        # ----- Analytics rollups for the Dashboard ---------------------
        by_channel = [
            {
                "channel": r["channel"],
                "count": int(r["count"]),
                "total_seconds": float(r["total_seconds"] or 0),
            }
            for r in conn.execute(
                "SELECT channel, COUNT(*) AS count, "
                "COALESCE(SUM(duration_sec), 0) AS total_seconds "
                "FROM videos WHERE archived = 0 AND channel IS NOT NULL "
                "GROUP BY channel ORDER BY count DESC, total_seconds DESC LIMIT 6"
            )
        ]

        by_language = [
            {"language": r["language"], "count": int(r["count"])}
            for r in conn.execute(
                "SELECT language, COUNT(*) AS count "
                "FROM videos WHERE archived = 0 AND language IS NOT NULL "
                "GROUP BY language ORDER BY count DESC"
            )
        ]

        top_tags = [
            {"tag": r["tag"], "count": int(r["count"])}
            for r in conn.execute(
                "SELECT vt.tag AS tag, COUNT(*) AS count "
                "FROM video_tags vt JOIN videos v ON v.id = vt.video_id "
                "WHERE v.archived = 0 "
                "GROUP BY vt.tag ORDER BY count DESC LIMIT 10"
            )
        ]

        longest_videos = [
            {
                "id": r["id"],
                "title": r["title"],
                "duration_sec": float(r["duration_sec"] or 0),
                "channel": r["channel"],
            }
            for r in conn.execute(
                "SELECT id, title, duration_sec, channel "
                "FROM videos WHERE archived = 0 AND duration_sec IS NOT NULL "
                "ORDER BY duration_sec DESC LIMIT 5"
            )
        ]

        recently_analyzed = [
            {
                "id": r["id"],
                "title": r["title"],
                "finished_at": r["finished_at"],
                "provider": r["provider"],
            }
            for r in conn.execute(
                "SELECT v.id AS id, v.title AS title, "
                "a.finished_at AS finished_at, a.provider AS provider "
                "FROM analyses a JOIN videos v ON v.id = a.video_id "
                "WHERE a.status = 'done' AND v.archived = 0 "
                "ORDER BY a.finished_at DESC LIMIT 5"
            )
        ]

        return {
            "video_count": int(video_count),
            "project_count": int(project_count),
            "total_seconds": float(totals[0] or 0),
            "storage_bytes": int(totals[1] or 0),
            "latest_videos": latest,
            "by_channel": by_channel,
            "by_language": by_language,
            "top_tags": top_tags,
            "longest_videos": longest_videos,
            "recently_analyzed": recently_analyzed,
        }
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Playlist preview
# ---------------------------------------------------------------------------


@app.post("/api/playlist/preview", dependencies=[Depends(auth.require_http)])
def api_playlist_preview(body: dict = Body(...)):
    url = (body.get("url") or "").strip()
    if not url:
        raise HTTPException(status_code=400, detail="url required")
    try:
        preview = playlist_mod.preview_playlist(url)
    except playlist_mod.PlaylistError as e:
        raise HTTPException(status_code=400, detail=str(e))
    # Manually unpack dataclasses (FastAPI handles the outer dict).
    return {
        "playlist_id": preview.playlist_id,
        "title": preview.title,
        "uploader": preview.uploader,
        "entry_count": preview.entry_count,
        "entries": [
            {
                "id": e.id, "title": e.title,
                "duration_sec": e.duration_sec,
                "thumbnail_url": e.thumbnail_url,
                "url": e.url,
            } for e in preview.entries
        ],
        "failures": [
            {"id": f.id, "reason": f.reason} for f in preview.failures
        ],
    }


# ---------------------------------------------------------------------------
# Podcast preview
# ---------------------------------------------------------------------------


@app.post("/api/podcast/preview", dependencies=[Depends(auth.require_http)])
def api_podcast_preview(body: dict = Body(...)):
    url = (body.get("url") or "").strip()
    if not url:
        raise HTTPException(status_code=400, detail="url required")
    try:
        preview = podcast_mod.preview_podcast(url)
    except podcast_mod.PodcastError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception:
        # Don't leak hostnames, ports, or filesystem paths from URLError /
        # FileNotFoundError / etc. into the HTTP body.
        log.exception("podcast preview failed for %s", url)
        raise HTTPException(status_code=502, detail="upstream fetch failed")
    return {
        "source": preview.source,
        "rss_url": preview.rss_url,
        "title": preview.title,
        "publisher": preview.publisher,
        "description": preview.description,
        "image_url": preview.image_url,
        "episodes": [
            {
                "guid": ep.guid,
                "title": ep.title,
                "description": ep.description,
                "pub_date": ep.pub_date,
                "duration_sec": ep.duration_sec,
                "mp3_url": ep.mp3_url,
                "image_url": ep.image_url,
            } for ep in preview.episodes
        ],
    }


# ---------------------------------------------------------------------------
# Bulk ingest (POST /api/ingests)
# ---------------------------------------------------------------------------


@app.post("/api/ingests", dependencies=[Depends(auth.require_http)])
def api_bulk_ingest(body: dict = Body(...)):
    project_id = body.get("project_id")
    urls = body.get("urls") or []
    playlist_url = body.get("playlist_url")
    options = body.get("options") or {}
    force = bool(body.get("force"))

    # Resolve the working set of URLs.
    expanded_urls: list[str] = []
    if playlist_url:
        try:
            preview = playlist_mod.preview_playlist(playlist_url)
        except playlist_mod.PlaylistError as e:
            raise HTTPException(status_code=400, detail=str(e))
        expanded_urls.extend(e.url for e in preview.entries)
    if urls:
        if not isinstance(urls, list):
            raise HTTPException(status_code=400, detail="urls must be a list")
        expanded_urls.extend(str(u) for u in urls)

    if not expanded_urls:
        raise HTTPException(
            status_code=400,
            detail="need at least one of playlist_url or urls",
        )

    # Extract canonical ids up front for dedup checks.
    conn = open_connection(OUTPUT_DIR / "app.db")
    try:
        kicked: list[str] = []
        skipped: list[dict] = []
        hf_token = os.environ.get("HUGGINGFACE_TOKEN")
        for url in expanded_urls:
            # Fast path for plain YouTube video URLs: no provider resolve
            # needed -- yt-dlp will fetch metadata at download time. This
            # also keeps `_safe_video_id`-style dedup against the videos
            # row cheap (one SELECT, no network round-trip).
            yt_id = extract_video_id(url)
            if yt_id is not None:
                _enqueue_one(
                    conn, kicked, skipped, url=url, vid=yt_id,
                    metadata=None, options=options, force=force,
                    project_id=project_id, hf_token=hf_token,
                )
                continue

            # Non-YouTube URL: dispatch to the right Provider, resolve, and
            # enqueue every Source it returns. A Spotify show URL pasted
            # into /api/ingests therefore expands into N episodes.
            try:
                provider = sources_mod.dispatch(url)
            except sources_mod.ProviderError as e:
                skipped.append({"video_id": url, "reason": f"no_provider: {e}"})
                continue
            try:
                source_list = provider.resolve(url)
            except sources_mod.ProviderError as e:
                skipped.append({"video_id": url, "reason": f"resolve_failed: {e}"})
                continue
            except Exception:
                log.exception("provider.resolve failed for %s", url)
                skipped.append({"video_id": url, "reason": "resolve_failed"})
                continue
            for src in source_list.sources:
                _enqueue_one(
                    conn, kicked, skipped,
                    url=src.url, vid=src.safe_id,
                    metadata=src.to_metadata(),
                    options=options, force=force,
                    project_id=project_id, hf_token=hf_token,
                    source_obj=src,
                )
        return {"job_ids": kicked, "skipped": skipped, "project_id": project_id}
    finally:
        conn.close()


def _enqueue_one(
    conn,
    kicked: list[str],
    skipped: list[dict],
    *,
    url: str,
    vid: str | None,
    metadata: dict | None,
    options: dict,
    force: bool,
    project_id: str | None,
    hf_token: str | None,
    source_obj: "sources_mod.Source | None" = None,
) -> None:
    """Common enqueue path used by /api/ingests (regardless of provider).
    `vid` is the canonical id for dedup -- YouTube id for YouTube URLs,
    `aud-<sha1[:12]>` for everything else. When None, we skip dedup and
    let the worker resolve the id at download time."""
    if vid is not None:
        existing = conn.execute(
            "SELECT archived FROM videos WHERE id=?", (vid,),
        ).fetchone()
        if existing is not None and not force:
            if existing["archived"]:
                skipped.append({"video_id": vid, "reason": "archived"})
                return
            # Already-transcribed: add to project if given.
            if project_id:
                projects_mod.add_videos(
                    OUTPUT_DIR, project_id, [vid], conn=conn,
                )
            skipped.append({"video_id": vid, "reason": "already_transcribed"})
            return
        if existing is not None and force:
            conn.execute("DELETE FROM videos WHERE id=?", (vid,))

    req_opts: dict[str, Any] = {
        "url": url,
        "diarize": bool(options.get("diarize", False)),
        "batched": bool(options.get("batched", True)),
    }
    if options.get("model"):
        req_opts["model"] = str(options["model"])
    if metadata is not None:
        req_opts["metadata"] = metadata
    req = TranscribeRequest(**req_opts)

    track_id = vid or f"pending-{url[-11:]}"
    state.begin(track_id, url=url)
    pre_seed: dict[str, Any] = {"phase": "queued"}
    if metadata:
        if metadata.get("title"):
            pre_seed["title"] = metadata["title"]
        if metadata.get("duration_sec") is not None:
            pre_seed["duration_sec"] = metadata["duration_sec"]
    state.update(track_id, **pre_seed)
    queue_mod.enqueue_ingest(req, OUTPUT_DIR, hf_token, project_id=project_id)
    kicked.append(vid or url)


# ---------------------------------------------------------------------------
# Podcast ingest (POST /api/ingests/podcast)
# ---------------------------------------------------------------------------


@app.post("/api/ingests/podcast", dependencies=[Depends(auth.require_http)])
def api_podcast_ingest(body: dict = Body(...)):
    """Schedule one-or-more podcast episodes for ingestion. The frontend
    sends the resolved show + episode metadata from /api/podcast/preview
    so the rich data (title, host, image, pub_date, duration) is preserved
    end-to-end, instead of getting overwritten by yt-dlp's generic
    extractor (which sees the audio CDN URL and pulls a slug out of it).

    Body shape:
        {
          "project_id": "...",            # optional
          "show":   { title, publisher, image_url, rss_url },
          "episodes": [
            { title, description, pub_date, duration_sec, mp3_url,
              image_url? },
            ...
          ]
        }

    Returns the same envelope as POST /api/ingests:
        { "job_ids": [...], "skipped": [...], "project_id": ... }
    """
    project_id = body.get("project_id")
    show = body.get("show") or {}
    episodes = body.get("episodes")

    if not isinstance(episodes, list) or not episodes:
        raise HTTPException(
            status_code=400, detail="episodes must be a non-empty list",
        )
    if not isinstance(show, dict):
        raise HTTPException(status_code=400, detail="show must be an object")

    # Validate every mp3_url through the same SSRF gate the preview path
    # uses so a malicious frontend can't smuggle file:// or an internal IP.
    bad = 0
    cleaned: list[dict] = []
    for ep in episodes:
        if not isinstance(ep, dict):
            raise HTTPException(
                status_code=400, detail="each episode must be an object",
            )
        mp3 = (ep.get("mp3_url") or "").strip()
        if not mp3:
            raise HTTPException(
                status_code=400, detail="episode missing mp3_url",
            )
        try:
            podcast_mod._validate_url(mp3)
        except podcast_mod.PodcastError:
            bad += 1
            continue
        cleaned.append(ep)
    if bad:
        raise HTTPException(
            status_code=400,
            detail=f"{bad} episode URL(s) failed SSRF validation",
        )

    show_title = (show.get("title") or "").strip() or None
    show_publisher = (show.get("publisher") or "").strip() or None
    show_image = (show.get("image_url") or "").strip() or None
    show_rss = (show.get("rss_url") or "").strip() or None

    conn = open_connection(OUTPUT_DIR / "app.db")
    try:
        kicked: list[str] = []
        skipped: list[dict] = []
        hf_token = os.environ.get("HUGGINGFACE_TOKEN")

        for ep in cleaned:
            mp3_url = ep["mp3_url"].strip()
            safe_id = _safe_video_id(mp3_url)

            # Dedup vs already-ingested rows. Same semantics as
            # api_bulk_ingest: archived rows skip, transcribed rows skip
            # (and join the project if one was passed), force flag isn't
            # supported on this endpoint yet.
            existing = conn.execute(
                "SELECT archived FROM videos WHERE id=?", (safe_id,),
            ).fetchone()
            if existing is not None:
                if existing["archived"]:
                    skipped.append({"video_id": safe_id, "reason": "archived"})
                    continue
                if project_id:
                    projects_mod.add_videos(
                        OUTPUT_DIR, project_id, [safe_id], conn=conn,
                    )
                skipped.append({"video_id": safe_id, "reason": "already_transcribed"})
                continue

            ep_title = (ep.get("title") or "").strip() or "(untitled)"
            ep_desc = ep.get("description") or None
            ep_pub = ep.get("pub_date") or None
            ep_duration = ep.get("duration_sec")
            ep_image = (ep.get("image_url") or "").strip() or None

            metadata = {
                "source": "podcast",
                "title": ep_title,
                "host": show_publisher,
                "show_name": show_title,
                "show_url": show_rss,
                "image_url": ep_image or show_image,
                "pub_date": ep_pub,
                "duration_sec": ep_duration,
                "description": ep_desc,
            }

            # Pre-seed the registry so the active-ingest strip shows the
            # proper title + duration the moment this returns. Without
            # this, the strip would briefly show the raw URL until the
            # download stage finishes.
            #
            # Also stash the resolved podcast metadata on the registry
            # entry so /api/ingests/{id}/retry can rehydrate it. Without
            # that, retries would build a bare TranscribeRequest(url=...)
            # and persist_video_to_db would fall back to yt-dlp's generic
            # extractor — which produces the cryptic CDN-slug title and
            # source='youtube' bug we burned a session on.
            state.begin(safe_id, url=mp3_url, metadata=metadata)
            state.update(
                safe_id,
                phase="queued",
                title=ep_title,
                duration_sec=ep_duration,
            )

            req = TranscribeRequest(url=mp3_url, metadata=metadata)
            queue_mod.enqueue_ingest(req, OUTPUT_DIR, hf_token, project_id=project_id)
            kicked.append(safe_id)

        return {"job_ids": kicked, "skipped": skipped, "project_id": project_id}
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Audio file (GET /api/transcripts/{video_id}/audio)
# ---------------------------------------------------------------------------


# Map common audio extensions to media types so the browser's <audio>
# element can pick the right decoder. FastAPI's FileResponse handles
# Range requests natively, which is what an HTML5 audio element uses to
# seek through long podcast episodes without re-downloading from byte 0.
_AUDIO_MIME = {
    ".mp3":  "audio/mpeg",
    ".m4a":  "audio/mp4",
    ".aac":  "audio/aac",
    ".wav":  "audio/wav",
    ".ogg":  "audio/ogg",
    ".opus": "audio/ogg",
    ".webm": "audio/webm",
}


@app.get(
    "/api/transcripts/{video_id}/audio",
    dependencies=[Depends(auth.require_http)],
)
def api_get_audio(video_id: str):
    """Stream the per-video audio file. Used by the Detail page audio
    player for podcast episodes, and as a fallback for YouTube ingests
    when the user wants to scrub the local copy. FileResponse handles
    Range requests natively (HTTP 206 partial content)."""
    p = find_audio_file(OUTPUT_DIR, video_id)
    if p is None:
        raise HTTPException(status_code=404, detail="no audio for this video")
    media_type = _AUDIO_MIME.get(p.suffix.lower(), "application/octet-stream")
    return FileResponse(str(p), media_type=media_type, filename=p.name)


# ---------------------------------------------------------------------------
# Per-job SSE (supersedes URL-driven GET /api/transcribe for new callers)
# ---------------------------------------------------------------------------


@app.get(
    "/api/ingests/{video_id}/stream",
    dependencies=[Depends(auth.require_http)],
)
async def api_ingest_stream(video_id: str):
    """SSE stream of events for an in-flight ingest. Reads state.py records
    and emits diff events as the worker updates them. Useful when the tab
    that started the ingest died and the user reopened the detail page.
    """
    import json as _json

    async def gen():
        last_phase = None
        last_segments = -1
        while True:
            rec = state.get(video_id)
            if rec is None:
                yield {"event": "error", "data": _json.dumps({"message": "no record"})}
                return
            if rec.phase != last_phase:
                yield {"event": "phase", "data": _json.dumps({
                    "phase": rec.phase, "message": rec.phase,
                })}
                last_phase = rec.phase
            if rec.segments != last_segments:
                last_segments = rec.segments
                yield {"event": "heartbeat", "data": _json.dumps({
                    "segments": rec.segments, "last_end": rec.last_segment_end,
                })}
            if rec.done:
                if rec.error:
                    yield {"event": "error", "data": _json.dumps({"message": rec.error})}
                else:
                    yield {"event": "done", "data": _json.dumps({
                        "id": rec.id,
                        "elapsed_sec": (rec.last_event_at - rec.started_at),
                        "realtime_factor": 0,
                        "duration_sec": rec.duration_sec or 0,
                        "files": {},
                    })}
                return
            await asyncio.sleep(1.0)

    return EventSourceResponse(gen())


# ---------------------------------------------------------------------------
# API: SSE transcription stream
# ---------------------------------------------------------------------------


# DEPRECATED: POST /api/ingests + GET /api/ingests/{id}/stream supersedes this for new callers
@app.get("/api/transcribe", dependencies=[Depends(auth.require_http)])
async def api_transcribe(
    url: str = Query(...),
    model: str = "distil-large-v3",
    diarize: bool = False,
    language: str | None = None,
    device: str = "cuda",
    compute_type: str = "int8_float16",
    batched: bool = True,
    batch_size: int = 16,
    beam_size: int = 5,
    min_speakers: int | None = None,
    max_speakers: int | None = None,
    project_id: str | None = None,
):
    """Single-video transcription stream. `project_id` controls which
    project folder the video lands in; defaults to Inbox when omitted."""
    target_project = project_id or "inbox"
    req = TranscribeRequest(
        url=url, model=model, compute_type=compute_type, device=device,
        language=language, beam_size=beam_size,
        batched=batched, batch_size=batch_size,
        diarize=diarize, min_speakers=min_speakers, max_speakers=max_speakers,
        project_id=target_project,
    )
    hf_token = os.environ.get("HUGGINGFACE_TOKEN")
    return EventSourceResponse(
        stream_transcription(req, OUTPUT_DIR, hf_token=hf_token)
    )


# ---------------------------------------------------------------------------
# API: PTY (claude session scoped to a video's folder)
# ---------------------------------------------------------------------------


@app.websocket("/api/pty")
async def ws_pty(ws: WebSocket, video_id: str | None = Query(None)):
    if not auth.check_ws(ws):
        await ws.close(code=1008)  # policy violation
        return
    if video_id:
        cwd = video_dir(OUTPUT_DIR, video_id)
        if not cwd.exists():
            await ws.accept()
            await ws.send_json({"type": "error", "message": f"No folder for {video_id}"})
            await ws.close()
            return
    else:
        cwd = OUTPUT_DIR
        cwd.mkdir(parents=True, exist_ok=True)
    await handle_pty_session(ws, cwd=cwd)


# ---------------------------------------------------------------------------
# Static SPA — mounted last so /api/* routes still match.
# ---------------------------------------------------------------------------


if WEB_DIST.exists():
    app.mount("/assets", StaticFiles(directory=str(WEB_DIST / "assets")), name="assets")

    @app.get("/")
    def spa_index():
        return FileResponse(str(WEB_DIST / "index.html"))

    @app.get("/{full_path:path}")
    def spa_fallback(full_path: str):
        if full_path.startswith("api/") or full_path.startswith("assets/"):
            raise HTTPException(status_code=404)
        return FileResponse(str(WEB_DIST / "index.html"))
else:
    @app.get("/")
    def no_spa_yet():
        return {
            "ok": True,
            "message": (
                "API is up. For dev: `cd web && npm run dev` and open "
                "http://localhost:5173. For prod: `cd web && npm run build`."
            ),
        }
