"""After ingest, the videos table has a row with the right metadata.
Mirrors the Slice-0 mocking harness but asserts DB state."""
from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import patch

import pytest

import server.transcriber as tr
from server import state as state_mod
from server.db import open_connection, run_migrations


@pytest.fixture(autouse=True)
def _clean_state():
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


def test_ingest_inserts_videos_row(tmp_path: Path) -> None:
    out_dir = tmp_path
    video_id = "abcXYZ11111"
    folder = out_dir / video_id
    folder.mkdir()
    (folder / "audio.mp3").write_bytes(b"fake")
    (folder / "transcript.json").write_text("{}", encoding="utf-8")
    (folder / "transcript.txt").write_text("", encoding="utf-8")
    (folder / "transcript.srt").write_text("", encoding="utf-8")
    (folder / "CLAUDE.md").write_text("", encoding="utf-8")

    conn = open_connection(out_dir / "app.db")
    run_migrations(conn)
    conn.close()

    fake_info = {
        "id": video_id, "title": "Hello", "duration": 60,
        "channel": "Ch", "description": "d",
    }
    fake_result = {
        "segments": [{"id": 1, "start": 0, "end": 1, "text": "hi"}],
        "language": "en", "language_probability": 0.9,
        "elapsed_sec": 5.0, "diarized": False,
    }
    fake_files = {k: folder / f for k, f in [
        ("txt", "transcript.txt"),
        ("srt", "transcript.srt"),
        ("json", "transcript.json"),
        ("claude_md", "CLAUDE.md"),
    ]}

    with (
        patch.object(tr, "download_audio", return_value=(folder / "audio.mp3", fake_info)),
        patch.object(tr, "_run_plain", return_value=fake_result),
        patch.object(tr, "_run_post_diarize", return_value=False),
        patch.object(tr, "write_outputs", return_value=fake_files),
    ):
        req = tr.TranscribeRequest(url=f"https://youtu.be/{video_id}", device="cpu")
        events = asyncio.run(_drain(tr.stream_transcription(req, out_dir, hf_token=None)))

    assert any(e["event"] == "done" for e in events)
    conn = open_connection(out_dir / "app.db")
    try:
        row = conn.execute(
            "SELECT id, title, segment_count, language FROM videos WHERE id=?",
            (video_id,),
        ).fetchone()
        assert row is not None
        assert row["title"] == "Hello"
        assert row["segment_count"] == 1
        assert row["language"] == "en"
    finally:
        conn.close()
