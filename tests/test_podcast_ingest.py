"""Tests for the podcast ingest path:
  POST /api/ingests/podcast              -- new endpoint
  GET  /api/transcripts/{id}/audio       -- new audio streamer
  persist_video_to_db with metadata      -- direct unit test
"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path: Path, monkeypatch) -> TestClient:
    monkeypatch.setenv("OUTPUT_DIR", str(tmp_path))
    monkeypatch.setenv("AUTH_DISABLED", "1")
    import importlib
    import server.main as main_mod
    importlib.reload(main_mod)
    return TestClient(main_mod.app)


@pytest.fixture(autouse=True)
def _clean_state():
    """Tests share the global state.py registry; flush it between tests so
    safe_ids set in one test don't bleed into the next."""
    from server import state as state_mod
    state_mod._INGESTS.clear()
    old = state_mod._PERSIST_PATH
    state_mod._PERSIST_PATH = None
    yield
    state_mod._INGESTS.clear()
    state_mod._PERSIST_PATH = old


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _payload(*, episodes: list[dict] | None = None, project_id: str | None = None) -> dict:
    """Two real-looking Buzzsprout-style episode URLs (public DNS, no SSRF
    rejects) so the endpoint's _validate_url gate passes."""
    if episodes is None:
        episodes = [
            {
                "title": "How Lendi Shipped",
                "description": "An interview about engineering velocity.",
                "pub_date": "2026-01-28T07:00:00+11:00",
                "duration_sec": 1742,
                "mp3_url": "https://www.buzzsprout.com/2406640/episodes/ep-001.mp3",
                "image_url": None,
            },
            {
                "title": "Why Founders Pivot",
                "description": "Pivoting late vs. early.",
                "pub_date": "2026-02-04T07:00:00+11:00",
                "duration_sec": 2003,
                "mp3_url": "https://www.buzzsprout.com/2406640/episodes/ep-002.mp3",
                "image_url": "https://image-cdn.example.com/ep-002.jpg",
            },
        ]
    body = {
        "show": {
            "title": "The Moonshot Podcast",
            "publisher": "Tatjana Pandurevic",
            "image_url": "https://image-cdn.example.com/show-cover.jpg",
            "rss_url": "https://rss.buzzsprout.com/2406640.rss",
        },
        "episodes": episodes,
    }
    if project_id is not None:
        body["project_id"] = project_id
    return body


# ---------------------------------------------------------------------------
# /api/ingests/podcast
# ---------------------------------------------------------------------------


def test_podcast_endpoint_dispatches_episodes_with_metadata(
    client, tmp_path, monkeypatch,
):
    """Submitting a 2-episode payload should return aud-* job ids, pre-seed
    the state registry with title/duration, and call enqueue_ingest with a
    TranscribeRequest carrying the metadata dict."""
    captured: list[dict] = []

    def fake_enqueue(req, out_dir, hf_token=None, project_id=None):
        captured.append({
            "url": req.url,
            "metadata": req.metadata,
            "project_id": project_id,
        })

    import server.main as main_mod
    monkeypatch.setattr(main_mod.queue_mod, "enqueue_ingest", fake_enqueue)

    r = client.post("/api/ingests/podcast", json=_payload())
    assert r.status_code == 200, r.text
    body = r.json()

    # job_ids are aud-<sha1[:12]>; envelope shape mirrors api_bulk_ingest.
    assert len(body["job_ids"]) == 2
    for job_id in body["job_ids"]:
        assert job_id.startswith("aud-")
        assert len(job_id) == len("aud-") + 12
    assert body["skipped"] == []
    assert body["project_id"] is None

    # State registry pre-seeded with podcast title + duration so the active-
    # ingest strip shows real info before download starts.
    from server import state as state_mod
    rec = state_mod.get(body["job_ids"][0])
    assert rec is not None
    assert rec.title == "How Lendi Shipped"
    assert rec.duration_sec == 1742
    assert rec.phase == "queued"

    # enqueue_ingest got the full metadata dict on each call.
    assert len(captured) == 2
    md0 = captured[0]["metadata"]
    assert md0["source"] == "podcast"
    assert md0["title"] == "How Lendi Shipped"
    assert md0["host"] == "Tatjana Pandurevic"
    assert md0["show_name"] == "The Moonshot Podcast"
    assert md0["show_url"] == "https://rss.buzzsprout.com/2406640.rss"
    # Episode without its own image_url falls back to the show's cover.
    assert md0["image_url"] == "https://image-cdn.example.com/show-cover.jpg"
    assert md0["pub_date"] == "2026-01-28T07:00:00+11:00"
    assert md0["duration_sec"] == 1742

    # Episode with explicit image_url wins over the show-level one.
    md1 = captured[1]["metadata"]
    assert md1["image_url"] == "https://image-cdn.example.com/ep-002.jpg"


def test_podcast_endpoint_dedup(client, tmp_path, monkeypatch):
    """Pre-insert a videos row whose id matches one of the safe_ids; that
    episode lands in `skipped`, the other still gets enqueued."""
    captured: list[str] = []

    def fake_enqueue(req, out_dir, hf_token=None, project_id=None):
        captured.append(req.url)

    import server.main as main_mod
    monkeypatch.setattr(main_mod.queue_mod, "enqueue_ingest", fake_enqueue)

    # Compute the safe id of episode 0 the same way the endpoint does.
    from server.transcriber import _safe_video_id
    body = _payload()
    dup_id = _safe_video_id(body["episodes"][0]["mp3_url"])

    from server.db import open_connection, run_migrations
    c = open_connection(tmp_path / "app.db")
    run_migrations(c)
    c.execute(
        "INSERT INTO videos(id, url, title, source, created_at, updated_at) "
        "VALUES(?, ?, ?, ?, ?, ?)",
        (dup_id, body["episodes"][0]["mp3_url"], "old", "podcast",
         "2026-01-01", "2026-01-01"),
    )
    c.close()

    r = client.post("/api/ingests/podcast", json=body)
    assert r.status_code == 200
    out = r.json()
    assert len(out["job_ids"]) == 1
    assert out["skipped"] == [
        {"video_id": dup_id, "reason": "already_transcribed"},
    ]
    # Only the second episode got enqueued.
    assert captured == [body["episodes"][1]["mp3_url"]]


def test_podcast_endpoint_creates_project_membership(
    client, tmp_path, monkeypatch,
):
    """When a project_id is supplied, it threads through to enqueue_ingest
    on every kicked episode."""
    seen_project_ids: list[str | None] = []

    def fake_enqueue(req, out_dir, hf_token=None, project_id=None):
        seen_project_ids.append(project_id)

    import server.main as main_mod
    monkeypatch.setattr(main_mod.queue_mod, "enqueue_ingest", fake_enqueue)

    # Project must exist for the dedup-path's `add_videos` call to be a
    # no-op rather than crash; here all episodes are fresh, so project
    # existence isn't strictly required, but it's the realistic case.
    from server.db import open_connection, run_migrations
    c = open_connection(tmp_path / "app.db")
    run_migrations(c)
    c.execute(
        "INSERT INTO projects(id, name, created_at, updated_at) "
        "VALUES(?, ?, ?, ?)",
        ("the-moonshot-podcast", "P", "2026-01-01", "2026-01-01"),
    )
    c.close()

    r = client.post(
        "/api/ingests/podcast",
        json=_payload(project_id="the-moonshot-podcast"),
    )
    assert r.status_code == 200
    assert r.json()["project_id"] == "the-moonshot-podcast"
    assert seen_project_ids == ["the-moonshot-podcast", "the-moonshot-podcast"]


def test_podcast_endpoint_validates_payload(client):
    # Missing episodes entirely.
    r = client.post("/api/ingests/podcast", json={"show": {"title": "x"}})
    assert r.status_code == 400

    # Empty episodes list.
    r = client.post(
        "/api/ingests/podcast",
        json={"show": {"title": "x"}, "episodes": []},
    )
    assert r.status_code == 400

    # Episode missing mp3_url.
    r = client.post(
        "/api/ingests/podcast",
        json={
            "show": {"title": "x"},
            "episodes": [{"title": "ep", "duration_sec": 1}],
        },
    )
    assert r.status_code == 400

    # Episode whose mp3_url is loopback / private fails the SSRF gate.
    r = client.post(
        "/api/ingests/podcast",
        json={
            "show": {"title": "x"},
            "episodes": [{
                "title": "ep",
                "mp3_url": "http://127.0.0.1/internal.mp3",
            }],
        },
    )
    assert r.status_code == 400


# ---------------------------------------------------------------------------
# /api/transcripts/{video_id}/audio
# ---------------------------------------------------------------------------


def test_audio_endpoint_serves_file(client, tmp_path):
    """Drop a fake audio.mp3 into output/<id>/, GET the endpoint, assert
    200 with audio/mpeg + Content-Length, and that Range requests return
    206 with the right partial body."""
    video_id = "aud-test12345"
    folder = tmp_path / video_id
    folder.mkdir(parents=True)
    payload = b"\x00\x01\x02ABCDE" * 100  # 700 bytes of stand-in audio
    (folder / "audio.mp3").write_bytes(payload)

    r = client.get(f"/api/transcripts/{video_id}/audio")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("audio/mpeg")
    assert int(r.headers.get("content-length", "0")) == len(payload)
    assert r.content == payload

    # Range request: bytes 0-99 should yield 206 + 100 bytes of body.
    r2 = client.get(
        f"/api/transcripts/{video_id}/audio",
        headers={"Range": "bytes=0-99"},
    )
    assert r2.status_code == 206
    assert r2.content == payload[:100]
    assert "bytes 0-99" in r2.headers.get("content-range", "")


def test_audio_endpoint_404_when_missing(client, tmp_path):
    r = client.get("/api/transcripts/aud-nonexistent/audio")
    assert r.status_code == 404
    assert r.json() == {"detail": "no audio for this video"}


def test_audio_endpoint_finds_non_mp3_extension(client, tmp_path):
    """The audio post-processor sometimes leaves audio.m4a (or similar)
    when ffmpeg reuses the source codec. The finder must pick it up."""
    video_id = "aud-m4a999"
    folder = tmp_path / video_id
    folder.mkdir(parents=True)
    (folder / "audio.m4a").write_bytes(b"FAKE_M4A")

    r = client.get(f"/api/transcripts/{video_id}/audio")
    assert r.status_code == 200
    assert r.headers["content-type"] == "audio/mp4"
    assert r.content == b"FAKE_M4A"


# ---------------------------------------------------------------------------
# persist_video_to_db with metadata
# ---------------------------------------------------------------------------


def test_persist_with_metadata(tmp_path):
    """Drive persist_video_to_db directly with a podcast-style req.metadata
    and assert source='podcast', show_name set, etc."""
    from server.db import open_connection, run_migrations
    from server.transcriber import TranscribeRequest, persist_video_to_db

    out_dir = tmp_path
    c = open_connection(out_dir / "app.db")
    run_migrations(c)
    c.close()

    video_id = "aud-podtest9"
    (out_dir / video_id).mkdir(parents=True)
    (out_dir / video_id / "audio.mp3").write_bytes(b"x" * 1024)

    info = {
        "id": video_id,
        # The CDN slug yt-dlp would otherwise stamp on the row -- we want
        # to assert this gets DROPPED in favor of the metadata title.
        "title": "ep-001",
        "duration": 30.0,
        "channel": "buzzsprout-user-junk",
        "description": "generic-extractor garbage",
    }
    result = {
        "segments": [{"id": 1, "start": 0, "end": 1, "text": "hi"}],
        "language": "en",
        "language_probability": 0.95,
        "elapsed_sec": 4.0,
        "diarized": False,
    }
    metadata = {
        "source": "podcast",
        "title": "How Lendi Shipped",
        "host": "Tatjana Pandurevic",
        "show_name": "The Moonshot Podcast",
        "show_url": "https://rss.buzzsprout.com/2406640.rss",
        "image_url": "https://image-cdn.example.com/ep-001.jpg",
        "pub_date": "2026-01-28T07:00:00+11:00",
        "duration_sec": 1742,
        "description": "An interview about engineering velocity.",
    }
    req = TranscribeRequest(
        url="https://www.buzzsprout.com/2406640/episodes/ep-001.mp3",
        device="cpu",
        metadata=metadata,
    )
    persist_video_to_db(out_dir, video_id, info, result, req)

    c = open_connection(out_dir / "app.db")
    try:
        row = c.execute(
            "SELECT title, channel, channel_url, source, image_url, "
            "       show_name, show_url, upload_date, duration_sec, "
            "       description "
            "FROM videos WHERE id=?",
            (video_id,),
        ).fetchone()
    finally:
        c.close()

    assert row is not None
    assert row["title"] == "How Lendi Shipped"
    assert row["channel"] == "Tatjana Pandurevic"
    # channel_url falls through info dict -- not overridden because the
    # podcast endpoint never sets a channel_url. Fine to be None or
    # whatever yt-dlp put there.
    assert row["source"] == "podcast"
    assert row["image_url"] == "https://image-cdn.example.com/ep-001.jpg"
    assert row["show_name"] == "The Moonshot Podcast"
    assert row["show_url"] == "https://rss.buzzsprout.com/2406640.rss"
    # ISO -> YYYYMMDD; 2026-01-28 in +11:00 is 2026-01-27 UTC.
    assert row["upload_date"] == "20260127"
    # Duration came from metadata, not info.
    assert row["duration_sec"] == 1742
    # Description came from metadata, not info.
    assert row["description"] == "An interview about engineering velocity."


def test_persist_youtube_path_unchanged(tmp_path):
    """When metadata is None (YouTube path), source defaults to 'youtube'
    and the new podcast columns stay NULL."""
    from server.db import open_connection, run_migrations
    from server.transcriber import TranscribeRequest, persist_video_to_db

    out_dir = tmp_path
    c = open_connection(out_dir / "app.db")
    run_migrations(c)
    c.close()

    video_id = "yyttvideo123"
    (out_dir / video_id).mkdir(parents=True)
    (out_dir / video_id / "audio.mp3").write_bytes(b"x")

    info = {
        "id": video_id, "title": "Real YT Title", "duration": 60,
        "channel": "RealChannel", "channel_url": "https://www.youtube.com/@x",
        "upload_date": "20260101", "description": "yt desc",
    }
    result = {"segments": [], "language": "en", "language_probability": 1.0,
              "elapsed_sec": 1.0, "diarized": False}
    req = TranscribeRequest(url=f"https://youtu.be/{video_id}", device="cpu")
    persist_video_to_db(out_dir, video_id, info, result, req)

    c = open_connection(out_dir / "app.db")
    try:
        row = c.execute(
            "SELECT title, channel, source, image_url, show_name, show_url "
            "FROM videos WHERE id=?",
            (video_id,),
        ).fetchone()
    finally:
        c.close()
    assert row is not None
    assert row["title"] == "Real YT Title"
    assert row["channel"] == "RealChannel"
    assert row["source"] == "youtube"
    assert row["image_url"] is None
    assert row["show_name"] is None
    assert row["show_url"] is None
