"""FastAPI app: serves the React SPA + the API surface (transcripts, SSE
transcribe, WebSocket PTY scoped per-video)."""

from __future__ import annotations

import logging
import os
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Query, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sse_starlette.sse import EventSourceResponse

from . import auth, transcripts
from .analyze import analyze_video
from .layout import migrate_flat_outputs, video_dir
from .pty_handler import handle_pty_session
from .transcriber import TranscribeRequest, stream_transcription


PROJECT_ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = PROJECT_ROOT / "output"
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


@app.get("/api/transcripts/{video_id}/analysis", dependencies=[Depends(auth.require_http)])
def api_get_analysis(video_id: str):
    import json
    p = video_dir(OUTPUT_DIR, video_id) / "analysis.json"
    if not p.exists():
        raise HTTPException(status_code=404, detail="no analysis yet")
    return json.loads(p.read_text(encoding="utf-8"))


@app.post("/api/transcripts/{video_id}/analyze", dependencies=[Depends(auth.require_http)])
async def api_trigger_analyze(video_id: str):
    """Regenerate (or generate for the first time) analysis.json for an
    existing transcript. Runs `claude -p` in a worker thread — can take
    minutes for a long video."""
    import anyio
    path = await anyio.to_thread.run_sync(analyze_video, OUTPUT_DIR, video_id)
    if path is None:
        raise HTTPException(status_code=500, detail="analysis failed or claude unavailable")
    return {"ok": True, "path": path.name}


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
