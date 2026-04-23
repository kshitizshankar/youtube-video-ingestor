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


def test_bulk_ingest_by_urls(client, monkeypatch, tmp_path):
    called_with = []

    def fake_enqueue(req, out_dir, hf_token=None):
        called_with.append(req.url)

    import server.queue
    monkeypatch.setattr(server.queue, "enqueue_ingest", fake_enqueue)
    r = client.post("/api/ingests", json={"urls": ["https://youtu.be/aaaa1111aaa"]})
    assert r.status_code == 200
    assert r.json()["job_ids"] == ["aaaa1111aaa"]
    assert r.json()["skipped"] == []
    assert called_with == ["https://youtu.be/aaaa1111aaa"]


def test_dedup_skips_already_transcribed(client, tmp_path):
    # Seed a video in DB.
    from server.db import open_connection, run_migrations
    c = open_connection(tmp_path / "app.db")
    run_migrations(c)
    c.execute(
        "INSERT INTO videos(id, url, title, created_at, updated_at) VALUES(?,?,?,?,?)",
        ("aaaa1111aaa", "https://x", "V", "2026-01-01", "2026-01-01"),
    )
    c.close()

    r = client.post("/api/ingests", json={"urls": ["https://youtu.be/aaaa1111aaa"]})
    assert r.status_code == 200
    d = r.json()
    assert d["job_ids"] == []
    assert d["skipped"] == [{"video_id": "aaaa1111aaa", "reason": "already_transcribed"}]


def test_dedup_adds_to_project(client, tmp_path):
    from server.db import open_connection, run_migrations
    c = open_connection(tmp_path / "app.db")
    run_migrations(c)
    c.execute(
        "INSERT INTO videos(id, url, title, created_at, updated_at) VALUES(?,?,?,?,?)",
        ("aaaa1111aaa", "https://x", "V", "2026-01-01", "2026-01-01"),
    )
    c.execute(
        "INSERT INTO projects(id, name, created_at, updated_at) VALUES(?,?,?,?)",
        ("p1", "P", "2026-01-01", "2026-01-01"),
    )
    c.close()

    client.post(
        "/api/ingests",
        json={"urls": ["https://youtu.be/aaaa1111aaa"], "project_id": "p1"},
    )

    r = client.get("/api/projects/p1")
    assert r.json()["project"]["video_count"] == 1


def test_archived_video_rejected(client, tmp_path):
    from server.db import open_connection, run_migrations
    c = open_connection(tmp_path / "app.db")
    run_migrations(c)
    c.execute(
        "INSERT INTO videos(id, url, title, archived, created_at, updated_at) "
        "VALUES(?,?,?,?,?,?)",
        ("aaaa1111aaa", "https://x", "V", 1, "2026-01-01", "2026-01-01"),
    )
    c.close()

    r = client.post("/api/ingests", json={"urls": ["https://youtu.be/aaaa1111aaa"]})
    assert r.status_code == 200
    assert r.json()["skipped"] == [{"video_id": "aaaa1111aaa", "reason": "archived"}]


def test_playlist_url_expanded(client, monkeypatch):
    called_with = []

    def fake_enqueue(req, out_dir, hf_token=None):
        called_with.append(req.url)

    import server.queue
    monkeypatch.setattr(server.queue, "enqueue_ingest", fake_enqueue)

    import server.playlist as pl

    class FakePreview:
        entries = [
            type("E", (), {"url": "https://youtu.be/aaaa1111aaa"})(),
            type("E", (), {"url": "https://youtu.be/bbbb2222bbb"})(),
        ]

    monkeypatch.setattr(pl, "preview_playlist", lambda url: FakePreview())

    r = client.post("/api/ingests", json={"playlist_url": "https://x/playlist"})
    assert r.status_code == 200
    assert len(r.json()["job_ids"]) == 2
    assert set(called_with) == {"https://youtu.be/aaaa1111aaa", "https://youtu.be/bbbb2222bbb"}
