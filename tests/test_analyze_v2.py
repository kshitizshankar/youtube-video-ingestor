"""Provider-dispatch tests for stream_analyze_video. Uses an in-memory
fake provider so we never touch a real CLI."""
from __future__ import annotations

import json
from pathlib import Path
from threading import Event
from typing import Iterator

import pytest

from server.ai.events import AnalysisEvent
from server.db import open_connection, run_migrations


class FakeProvider:
    name = "fake"
    display_name = "Fake"

    def available(self):
        return (True, None)

    def list_models(self):
        return ["fake-1"]

    def stream_analyze(
        self, *, video_folder, prompt, model, cancel_event
    ) -> Iterator[AnalysisEvent]:
        yield {"type": "stage", "stage": "Starting"}
        yield {
            "type": "usage",
            "tokens_in": 100,
            "tokens_out": 20,
            "cost_usd": 0.001,
        }
        yield {
            "type": "done",
            "result": {
                "summary": "ok",
                "takeaways": ["a", "b"],
                "chapters": [{"start": 0, "title": "Intro"}],
                "highlights": [],
            },
            "tokens_in": 100,
            "tokens_out": 20,
            "cost_usd": 0.001,
            "duration_ms": 1234,
        }


def _seed_video(tmp_path: Path, video_id: str) -> None:
    conn = open_connection(tmp_path / "app.db")
    try:
        run_migrations(conn)
        conn.execute(
            "INSERT INTO videos(id, url, created_at, updated_at) "
            "VALUES(?,?,?,?)",
            (video_id, "https://x", "2026-01-01", "2026-01-01"),
        )
    finally:
        conn.close()
    folder = tmp_path / video_id
    folder.mkdir(exist_ok=True)
    (folder / "transcript.json").write_text(
        json.dumps({"id": video_id, "segments": []})
    )


@pytest.fixture
def register_fake():
    """Register + clean up an arbitrary provider in the global registry."""
    import server.ai as ai_mod
    added: list[str] = []

    def _add(name: str, provider) -> None:
        ai_mod._REGISTRY[name] = provider
        added.append(name)

    yield _add

    for name in added:
        ai_mod._REGISTRY.pop(name, None)


def test_fake_provider_writes_analyses_row_and_file(tmp_path: Path, register_fake):
    video_id = "aaaa1111aaa"
    _seed_video(tmp_path, video_id)
    register_fake("fake", FakeProvider())

    from server.analyze import stream_analyze_video
    events = list(
        stream_analyze_video(tmp_path, video_id, provider_name="fake")
    )

    types = [e.get("type") for e in events]
    assert "done" in types
    done = [e for e in events if e["type"] == "done"][0]
    assert done["result"]["summary"] == "ok"
    assert done.get("analysis_id") is not None

    conn = open_connection(tmp_path / "app.db")
    try:
        row = conn.execute(
            "SELECT status, provider, file_path, cost_usd, tokens_in, tokens_out "
            "FROM analyses WHERE video_id=?",
            (video_id,),
        ).fetchone()
        assert row is not None
        assert row["status"] == "done"
        assert row["provider"] == "fake"
        assert row["tokens_in"] == 100
        assert row["tokens_out"] == 20
        assert row["file_path"]
        p = tmp_path / video_id / row["file_path"]
        assert p.exists()
        data = json.loads(p.read_text(encoding="utf-8"))
        assert data["summary"] == "ok"
        # Metadata stamp present.
        assert data["_meta"]["provider"] == "fake"
    finally:
        conn.close()


def test_unknown_provider_yields_error(tmp_path: Path):
    video_id = "bbbb2222bbb"
    _seed_video(tmp_path, video_id)

    from server.analyze import stream_analyze_video
    events = list(
        stream_analyze_video(
            tmp_path, video_id, provider_name="no-such-provider"
        )
    )
    assert len(events) == 1
    assert events[0]["type"] == "error"
    assert "unknown provider" in events[0]["error_message"]


def test_error_event_marks_row_as_error(tmp_path: Path, register_fake):
    class ErrProvider:
        name = "err"
        display_name = "Err"
        def available(self): return (True, None)
        def list_models(self): return []
        def stream_analyze(self, *, video_folder, prompt, model, cancel_event):
            yield {"type": "stage", "stage": "Starting"}
            yield {"type": "error", "error_message": "boom"}

    video_id = "cccc3333ccc"
    _seed_video(tmp_path, video_id)
    register_fake("err", ErrProvider())

    from server.analyze import stream_analyze_video
    events = list(
        stream_analyze_video(tmp_path, video_id, provider_name="err")
    )
    assert any(e.get("type") == "error" for e in events)

    conn = open_connection(tmp_path / "app.db")
    try:
        row = conn.execute(
            "SELECT status, error FROM analyses WHERE video_id=?",
            (video_id,),
        ).fetchone()
        assert row["status"] == "error"
        assert row["error"] == "boom"
    finally:
        conn.close()


def test_cancel_event_terminates_provider(tmp_path: Path, register_fake):
    """Cancel signal halts the stream mid-flight and writes `cancelled`."""
    import threading
    import time

    class HangingProvider:
        name = "hang"
        display_name = "Hang"
        def available(self): return (True, None)
        def list_models(self): return []
        def stream_analyze(self, *, video_folder, prompt, model, cancel_event):
            yield {"type": "stage", "stage": "Starting"}
            for _ in range(500):
                if cancel_event.wait(0.02):
                    return
            yield {"type": "done", "result": {"summary": "would-not-happen"}}

    video_id = "aaaa1111aaa"
    _seed_video(tmp_path, video_id)
    register_fake("hang", HangingProvider())

    import server.analyze as an
    events: list[dict] = []
    done_evt = threading.Event()

    def worker():
        try:
            for evt in an.stream_analyze_video(
                tmp_path, video_id, provider_name="hang"
            ):
                events.append(evt)
        finally:
            done_evt.set()

    t = threading.Thread(target=worker, daemon=True)
    t.start()

    # Wait for the row to show up in DB.
    aid = None
    deadline = time.time() + 3.0
    while time.time() < deadline:
        conn = open_connection(tmp_path / "app.db")
        try:
            row = conn.execute(
                "SELECT id FROM analyses WHERE video_id=? AND status='running'",
                (video_id,),
            ).fetchone()
        finally:
            conn.close()
        if row is not None:
            aid = row["id"]
            break
        time.sleep(0.05)

    assert aid is not None, "analysis row never transitioned to running"
    assert an.cancel_analysis(aid), "cancel_analysis returned False"
    assert done_evt.wait(3.0), "worker did not terminate after cancel"

    conn = open_connection(tmp_path / "app.db")
    try:
        row = conn.execute(
            "SELECT status, error FROM analyses WHERE id=?", (aid,)
        ).fetchone()
        assert row["status"] == "cancelled"
        assert row["error"] == "cancelled by user"
    finally:
        conn.close()


def test_cancel_missing_id_returns_false():
    from server.analyze import cancel_analysis
    assert cancel_analysis(999999) is False
