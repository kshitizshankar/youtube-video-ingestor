from __future__ import annotations

import threading
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from server import queue as queue_mod
from server import state as state_mod
from server.transcriber import TranscribeRequest


@pytest.fixture(autouse=True)
def _clean_state():
    state_mod._INGESTS.clear()
    old = state_mod._PERSIST_PATH
    state_mod._PERSIST_PATH = None
    yield
    state_mod._INGESTS.clear()
    state_mod._PERSIST_PATH = old


def test_enqueue_returns_immediately(tmp_path: Path):
    blocked = threading.Event()
    released = threading.Event()

    async def fake_stream(req, out_dir, hf_token=None):
        blocked.set()
        released.wait(timeout=5.0)
        if False:  # pragma: no cover
            yield {}  # make it an async generator
        return

    with patch.object(queue_mod, "stream_transcription", fake_stream):
        t0 = time.time()
        queue_mod.enqueue_ingest(
            TranscribeRequest(url="https://youtu.be/aaaa1111aaa", device="cpu"),
            tmp_path, None,
        )
        elapsed = time.time() - t0
        # enqueue should return in well under 1s regardless of worker state.
        assert elapsed < 1.0, f"enqueue blocked for {elapsed:.2f}s"
        # Make sure the worker actually started before we release the patch.
        assert blocked.wait(timeout=5.0)
        released.set()


def test_mark_queued_orphans(tmp_path: Path):
    # Seed a queued record as if from a previous run.
    state_mod.begin("ghostvid999", url="https://x")
    state_mod.update("ghostvid999", phase="queued")
    fixed = queue_mod.mark_queued_orphans()
    assert fixed == 1
    rec = state_mod.get("ghostvid999")
    assert rec.done
    assert "orphan" in (rec.error or "").lower()
