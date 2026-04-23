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
from . import projects as projects_mod
from .analyze import analyze_video, stream_analyze_video
from .db import open_connection, run_migrations
from .layout import migrate_flat_outputs, video_dir
from .migrate_data import migrate_data
from .pty_handler import handle_pty_session
from .transcriber import TranscribeRequest, stream_transcription


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
except Exception as e:
    log.warning("DB bootstrap failed: %s", e)

app = FastAPI(title="youtube-video-ingestor", version="0.3.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)


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
    return {"ok": True, "archived": True}


@app.post("/api/transcripts/{video_id}/restore", dependencies=[Depends(auth.require_http)])
def api_restore_video(video_id: str):
    if not transcripts.set_archived(OUTPUT_DIR, video_id, False):
        raise HTTPException(status_code=404, detail="not found")
    return {"ok": True, "archived": False}


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
    # Drop the old record so the new run starts clean.
    state.drop(video_id)
    req = TranscribeRequest(url=url)
    hf_token = os.environ.get("HUGGINGFACE_TOKEN")
    gen = stream_transcription(req, OUTPUT_DIR, hf_token=hf_token)
    asyncio.create_task(_consume_silently(gen))
    return {"started": True, "video_id": video_id, "url": url}


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

    # Scan each video folder, skip archived.
    for sub in sorted(
        [p for p in OUTPUT_DIR.iterdir() if p.is_dir()],
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    ):
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


@app.post("/api/transcripts/{video_id}/analyze", dependencies=[Depends(auth.require_http)])
async def api_trigger_analyze(video_id: str):
    """Regenerate (or generate for the first time) analysis.json for an
    existing transcript. Runs `claude -p` in a worker thread — can take
    minutes for a long video. Use `/analyze/stream` for live progress."""
    import anyio
    path = await anyio.to_thread.run_sync(analyze_video, OUTPUT_DIR, video_id)
    if path is None:
        raise HTTPException(status_code=500, detail="analysis failed or claude unavailable")
    return {"ok": True, "path": path.name}


@app.get("/api/transcripts/{video_id}/analyze/stream", dependencies=[Depends(auth.require_http)])
async def api_stream_analyze(video_id: str):
    """Stream Claude's live progress (tool calls, current step) as SSE events
    while analysis runs. Use this instead of POST when the UI wants to show
    what Claude is doing in real time.

    Event types emitted: `progress`, `done`, `error`."""
    import asyncio
    import json as _json

    async def event_gen():
        loop = asyncio.get_running_loop()
        q: asyncio.Queue = asyncio.Queue()
        SENTINEL = object()

        def worker() -> None:
            try:
                for evt in stream_analyze_video(OUTPUT_DIR, video_id):
                    loop.call_soon_threadsafe(q.put_nowait, {
                        "event": evt.get("type") or "progress",
                        "data": _json.dumps(evt, ensure_ascii=False),
                    })
            except Exception as e:
                loop.call_soon_threadsafe(q.put_nowait, {
                    "event": "error",
                    "data": _json.dumps({"message": f"{type(e).__name__}: {e}"}),
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
# API: SSE transcription stream
# ---------------------------------------------------------------------------


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
):
    req = TranscribeRequest(
        url=url, model=model, compute_type=compute_type, device=device,
        language=language, beam_size=beam_size,
        batched=batched, batch_size=batch_size,
        diarize=diarize, min_speakers=min_speakers, max_speakers=max_speakers,
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
