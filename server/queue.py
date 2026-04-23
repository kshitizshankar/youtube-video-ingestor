"""Process ingests through a shared ThreadPoolExecutor so POST /api/ingests
returns immediately and the queue worker drives transcription serially up
to MAX_CONCURRENT_INGESTS. Progress visible via state.py + SSE.
"""
from __future__ import annotations

import asyncio
import logging
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import state
from .transcriber import TranscribeRequest, stream_transcription


log = logging.getLogger(__name__)

_MAX = int(os.environ.get("MAX_CONCURRENT_INGESTS") or "1")
_executor = ThreadPoolExecutor(max_workers=_MAX, thread_name_prefix="ingest")


async def _drain(gen) -> None:
    try:
        async for _ in gen:
            pass
    except Exception:
        log.exception("queued ingest drained with exception")


def enqueue_ingest(req: TranscribeRequest, out_dir: Path, hf_token: str | None) -> None:
    """Schedule a transcription job. Returns immediately. Progress visible
    via /api/ingests (DB rows not used yet — state.py remains JSON-backed).
    """
    def _runner() -> None:
        # Create a fresh event loop for this worker thread so we can drive
        # the async generator that stream_transcription returns.
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(
                _drain(stream_transcription(req, out_dir, hf_token=hf_token))
            )
        finally:
            loop.close()

    _executor.submit(_runner)


def mark_queued_orphans() -> int:
    """Called at startup. Any ingests state record that's still `phase=queued`
    from a previous server run belongs to a dead executor — mark them as
    orphaned so they stop showing as in-flight forever."""
    fixed = 0
    for rec in state.list_active():
        if rec.get("phase") == "queued" and not rec.get("done"):
            state.update(rec["id"], phase="orphaned (restart)")
            state.finish(rec["id"], error="orphaned (queued at restart)")
            fixed += 1
    return fixed


def on_shutdown() -> None:
    """Best-effort drain on server shutdown. ThreadPoolExecutor won't
    hard-kill in-flight transcription — that's the existing contract."""
    _executor.shutdown(wait=False, cancel_futures=True)
