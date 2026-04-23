"""Regression test for Slice 0: the ingest pipeline must NOT auto-trigger
Claude analysis. Users click the "Understand with AI" button to analyze.

Belt-and-braces assertions:
- `server.analyze.stream_analyze_video` is never called during an ingest.
- The emitted `done` event carries no `analyzed` key.
- No `analysis.json` file appears after ingest.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import patch

import pytest

import server.transcriber as tr
from server import state as state_mod


@pytest.fixture(autouse=True)
def _clean_state():
    """Drop any lingering in-memory ingest state between tests."""
    state_mod._INGESTS.clear()
    old = state_mod._PERSIST_PATH
    state_mod._PERSIST_PATH = None
    yield
    state_mod._INGESTS.clear()
    state_mod._PERSIST_PATH = old


async def _drain(gen):
    out = []
    async for evt in gen:
        out.append(evt)
    return out


def test_ingest_does_not_trigger_analysis(tmp_path: Path) -> None:
    """Drive the full `stream_transcription` flow with heavy dependencies
    mocked; assert Claude analysis is never invoked."""
    video_id = "abcDEF12345"
    video_folder = tmp_path / video_id
    video_folder.mkdir()
    audio_path = video_folder / "audio.mp3"
    audio_path.write_bytes(b"not-real-mp3")

    fake_info = {
        "id": video_id,
        "title": "Test Video",
        "duration": 10,
        "description": None,
    }

    fake_transcript_result = {
        "segments": [],
        "language": "en",
        "language_probability": 0.95,
        "elapsed_sec": 1.0,
        "diarized": False,
    }

    fake_files = {
        "txt": video_folder / "transcript.txt",
        "srt": video_folder / "transcript.srt",
        "json": video_folder / "transcript.json",
        "claude_md": video_folder / "CLAUDE.md",
    }

    with (
        patch.object(tr, "download_audio", return_value=(audio_path, fake_info)),
        patch.object(tr, "_run_plain", return_value=fake_transcript_result),
        patch.object(tr, "_run_post_diarize", return_value=False),
        patch.object(tr, "write_outputs", return_value=fake_files),
        patch("server.analyze.stream_analyze_video") as mock_analyze,
    ):
        req = tr.TranscribeRequest(
            url=f"https://youtu.be/{video_id}",
            device="cpu",
            diarize=False,
        )
        events = asyncio.run(_drain(tr.stream_transcription(req, tmp_path, hf_token=None)))

    # Assertion 1: analysis was never even called.
    mock_analyze.assert_not_called()

    # Assertion 2: the pipeline completed (else we'd have caught a premature
    # error event, not a done).
    event_names = [e["event"] for e in events]
    assert "done" in event_names, f"pipeline did not complete: {event_names}"
    assert "error" not in event_names, f"pipeline emitted an error: {events}"

    # Assertion 3: the done payload has no `analyzed` key — we stripped it
    # along with the auto-trigger.
    done = next(e for e in events if e["event"] == "done")
    done_data = json.loads(done["data"])
    assert "analyzed" not in done_data, (
        "done payload should no longer carry the `analyzed` field"
    )

    # Assertion 4: no analysis.json appeared on disk.
    assert not (video_folder / "analysis.json").exists()


def test_analyze_module_still_usable_standalone(tmp_path: Path) -> None:
    """Belt check: the `stream_analyze_video` function still exists and is
    importable. Removing the auto-trigger must NOT remove the module itself
    — manual-trigger endpoints still rely on it."""
    from server.analyze import stream_analyze_video  # noqa: F401

    # Calling it on a folder with no transcript must yield an error event
    # (the current contract), not raise.
    video_id = "zzzZZZzz999"
    (tmp_path / video_id).mkdir()
    events = list(stream_analyze_video(tmp_path, video_id))
    assert any(e.get("type") == "error" for e in events), (
        "expected an error event when transcript.json is missing"
    )
