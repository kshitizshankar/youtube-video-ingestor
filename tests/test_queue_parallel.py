"""Parallel-queue tests: verify the split download / transcribe executor
design behaves as advertised:

- Downloads run in parallel (up to MAX_CONCURRENT_DOWNLOADS, default 4).
- Transcribes serialize on the GPU (up to MAX_CONCURRENT_TRANSCRIPTIONS,
  default 1).
- Phase transitions hit: queued → downloading → awaiting_gpu →
  transcribing → done.
- Cancel requested between stages prevents the transcribe stage from
  running at all.
"""
from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor
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


def test_downloads_run_in_parallel(tmp_path: Path):
    """4 downloads mocked to each take 200ms should finish within ~500ms
    with a 4-way pool. Previously serial: would take ~800ms."""
    done_all = threading.Event()
    n_done = [0]
    lock = threading.Lock()

    def slow_download(url, out_dir):
        time.sleep(0.2)
        vid = url.split("/")[-1][:11]
        folder = out_dir / vid
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "audio.mp3").write_bytes(b"fake")
        return folder / "audio.mp3", {"id": vid, "title": f"V{vid}", "duration": 10}

    def fake_transcribe_task(video_id, *args, **kwargs):
        state_mod.update(video_id, phase="done")
        state_mod.finish(video_id)
        with lock:
            n_done[0] += 1
            if n_done[0] == 4:
                done_all.set()

    with patch.object(queue_mod, "download_audio", slow_download), \
         patch.object(queue_mod, "_transcribe_task", fake_transcribe_task):
        t0 = time.time()
        for i in range(4):
            vid = f"aaaa{i}{i}{i}{i}{i}{i}{i}"
            req = TranscribeRequest(url=f"https://youtu.be/{vid}", device="cpu")
            state_mod.begin(vid, url=req.url)
            state_mod.update(vid, phase="queued")
            queue_mod.enqueue_ingest(req, tmp_path, None)
        assert done_all.wait(3.0), "pool didn't drain 4 downloads"
        elapsed = time.time() - t0
    # Parallel 4-way should be well under 0.7s; serial would be ~0.8s.
    assert elapsed < 0.7, f"downloads serialized ({elapsed:.2f}s)"


def test_transcribe_serialized_on_single_gpu(tmp_path: Path):
    """With MAX_CONCURRENT_TRANSCRIPTIONS=1, two transcribes must not overlap."""
    overlap = [False]
    running = [0]
    lock = threading.Lock()

    def fake_run_plain(req, audio_path, info, push):
        with lock:
            running[0] += 1
            if running[0] > 1:
                overlap[0] = True
        time.sleep(0.1)
        with lock:
            running[0] -= 1
        return {
            "segments": [], "language": "en", "language_probability": 0.9,
            "elapsed_sec": 0.1, "diarized": False,
        }

    # Swap in a fresh 1-wide executor for this test so we don't care about
    # the module default (which is 1, but let's be explicit).
    old_exec = queue_mod._transcribe_executor
    queue_mod._transcribe_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="test-gpu")

    done_all = threading.Event()
    finished_count = [0]
    finish_lock = threading.Lock()

    def slow_dl(url, out_dir):
        vid = url.split("/")[-1][:11]
        folder = out_dir / vid
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "audio.mp3").write_bytes(b"fake")
        return folder / "audio.mp3", {"id": vid, "title": "T", "duration": 1}

    try:
        original_finish = state_mod.finish

        def finish_hook(vid, error=None):
            original_finish(vid, error=error)
            with finish_lock:
                finished_count[0] += 1
                if finished_count[0] == 2:
                    done_all.set()

        with patch.object(queue_mod, "download_audio", slow_dl), \
             patch.object(queue_mod, "_run_plain", fake_run_plain), \
             patch.object(queue_mod, "_run_post_diarize", lambda *a, **kw: False), \
             patch.object(queue_mod, "write_outputs", lambda *a, **kw: {}), \
             patch.object(queue_mod, "persist_video_to_db", lambda *a, **kw: None), \
             patch.object(state_mod, "finish", finish_hook):
            for i in range(2):
                vid = f"bbbb{i}{i}{i}{i}{i}{i}{i}"
                req = TranscribeRequest(url=f"https://youtu.be/{vid}", device="cpu")
                state_mod.begin(vid, url=req.url)
                state_mod.update(vid, phase="queued")
                queue_mod.enqueue_ingest(req, tmp_path, None)
            assert done_all.wait(5.0), "both transcribes didn't finish"
    finally:
        queue_mod._transcribe_executor.shutdown(wait=True)
        queue_mod._transcribe_executor = old_exec

    assert overlap[0] is False, "transcribes overlapped despite cap=1"


def test_state_progression(tmp_path: Path):
    """Phase should progress queued → downloading → awaiting_gpu → transcribing → done."""
    transcribing_seen = [False]
    done_seen = [False]

    def slow_dl(url, out_dir):
        vid = url.split("/")[-1][:11]
        folder = out_dir / vid
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "audio.mp3").write_bytes(b"fake")
        return folder / "audio.mp3", {"id": vid, "title": "T", "duration": 1}

    def fake_run_plain(req, audio_path, info, push):
        vid = info["id"]
        rec = state_mod.get(vid)
        if rec is not None and rec.phase == "transcribing":
            transcribing_seen[0] = True
        return {
            "segments": [], "language": "en", "language_probability": 0.9,
            "elapsed_sec": 0.01, "diarized": False,
        }

    done_all = threading.Event()
    original_finish = state_mod.finish

    def observing_finish(vid, error=None):
        rec = state_mod.get(vid)
        if rec is not None and rec.phase == "done":
            done_seen[0] = True
        original_finish(vid, error=error)
        done_all.set()

    with patch.object(queue_mod, "download_audio", slow_dl), \
         patch.object(queue_mod, "_run_plain", fake_run_plain), \
         patch.object(queue_mod, "_run_post_diarize", lambda *a, **kw: False), \
         patch.object(queue_mod, "write_outputs", lambda *a, **kw: {}), \
         patch.object(queue_mod, "persist_video_to_db", lambda *a, **kw: None), \
         patch.object(state_mod, "finish", observing_finish):
        vid = "cccc1111ccc"
        req = TranscribeRequest(url=f"https://youtu.be/{vid}", device="cpu")
        state_mod.begin(vid, url=req.url)
        state_mod.update(vid, phase="queued")
        queue_mod.enqueue_ingest(req, tmp_path, None)
        assert done_all.wait(3.0), "job didn't complete"

    assert transcribing_seen[0], "phase=transcribing never observed inside _run_plain"
    assert done_seen[0], "phase=done never observed at finish time"


def test_cancel_between_stages(tmp_path: Path):
    """Setting cancel_requested during download should stop the transcribe."""
    transcribe_ran = [False]

    def slow_dl(url, out_dir):
        vid = url.split("/")[-1][:11]
        state_mod.request_cancel(vid)  # cancel between stages
        folder = out_dir / vid
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "audio.mp3").write_bytes(b"fake")
        return folder / "audio.mp3", {"id": vid, "title": "T", "duration": 1}

    def would_run_plain(*args, **kwargs):
        transcribe_ran[0] = True
        return {
            "segments": [], "language": "en", "language_probability": 0.9,
            "elapsed_sec": 0.01, "diarized": False,
        }

    done_all = threading.Event()
    original_finish = state_mod.finish

    def observing_finish(vid, error=None):
        original_finish(vid, error=error)
        done_all.set()

    with patch.object(queue_mod, "download_audio", slow_dl), \
         patch.object(queue_mod, "_run_plain", would_run_plain), \
         patch.object(queue_mod, "_run_post_diarize", lambda *a, **kw: False), \
         patch.object(queue_mod, "write_outputs", lambda *a, **kw: {}), \
         patch.object(queue_mod, "persist_video_to_db", lambda *a, **kw: None), \
         patch.object(state_mod, "finish", observing_finish):
        vid = "dddd2222ddd"
        req = TranscribeRequest(url=f"https://youtu.be/{vid}", device="cpu")
        state_mod.begin(vid, url=req.url)
        state_mod.update(vid, phase="queued")
        queue_mod.enqueue_ingest(req, tmp_path, None)
        assert done_all.wait(3.0), "cancelled job didn't emit finish()"

    assert transcribe_ran[0] is False, "transcribe stage ran despite cancel"
