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


def test_empty_stats(client) -> None:
    r = client.get("/api/stats")
    assert r.status_code == 200
    d = r.json()
    assert d == {
        "video_count": 0,
        "project_count": 0,
        "total_seconds": 0.0,
        "storage_bytes": 0,
        "latest_videos": [],
        "by_channel": [],
        "by_language": [],
        "top_tags": [],
        "longest_videos": [],
        "recently_analyzed": [],
    }


def test_populated_stats(client, tmp_path) -> None:
    from server.db import open_connection, run_migrations
    c = open_connection(tmp_path / "app.db")
    run_migrations(c)
    # Two live videos (one on channel "Ch-A" twice, one on "Ch-B"), plus an
    # archived video that must be excluded from every rollup.
    c.execute(
        "INSERT INTO videos(id,url,title,duration_sec,storage_bytes,channel,"
        "language,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
        ("abc12345678", "https://x", "V1", 120.0, 500, "Ch-A", "en",
         "2026-01-01", "2026-01-01"),
    )
    c.execute(
        "INSERT INTO videos(id,url,title,duration_sec,storage_bytes,channel,"
        "language,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
        ("def12345678", "https://z", "V3 longest", 3600.0, 900, "Ch-A", "en",
         "2026-01-03", "2026-01-03"),
    )
    c.execute(
        "INSERT INTO videos(id,url,title,duration_sec,storage_bytes,channel,"
        "language,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
        ("ghi12345678", "https://w", "V4", 240.0, 200, "Ch-B", "fr",
         "2026-01-04", "2026-01-04"),
    )
    c.execute(
        "INSERT INTO videos(id,url,title,duration_sec,storage_bytes,archived,"
        "channel,language,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
        ("zzz12345678", "https://y", "V2 archived", 60.0, 100, 1, "Ch-ARCH",
         "es", "2026-01-02", "2026-01-02"),
    )
    # Tags on live videos only (one shared, one unique).
    c.execute("INSERT INTO video_tags(video_id,tag) VALUES(?,?)", ("abc12345678", "ml"))
    c.execute("INSERT INTO video_tags(video_id,tag) VALUES(?,?)", ("def12345678", "ml"))
    c.execute("INSERT INTO video_tags(video_id,tag) VALUES(?,?)", ("ghi12345678", "news"))
    # Tag on archived video — must be excluded.
    c.execute("INSERT INTO video_tags(video_id,tag) VALUES(?,?)", ("zzz12345678", "ghost"))
    # One finished analysis on a live video, one on an archived video.
    c.execute(
        "INSERT INTO analyses(video_id,provider,model,status,started_at,finished_at) "
        "VALUES(?,?,?,?,?,?)",
        ("abc12345678", "claude_cli", "sonnet", "done", "2026-01-05", "2026-01-06"),
    )
    c.execute(
        "INSERT INTO analyses(video_id,provider,model,status,started_at,finished_at) "
        "VALUES(?,?,?,?,?,?)",
        ("zzz12345678", "ollama", "llama3", "done", "2026-01-06", "2026-01-07"),
    )
    c.execute(
        "INSERT INTO projects(id,name,created_at,updated_at) VALUES(?,?,?,?)",
        ("p1", "P1", "2026-01-01", "2026-01-01"),
    )
    c.close()

    d = client.get("/api/stats").json()
    assert d["video_count"] == 3
    assert d["project_count"] == 1
    assert d["total_seconds"] == pytest.approx(120.0 + 3600.0 + 240.0)
    assert d["storage_bytes"] == 500 + 900 + 200
    # latest_videos: newest first, archived excluded.
    assert [v["id"] for v in d["latest_videos"]] == [
        "ghi12345678", "def12345678", "abc12345678",
    ]

    # by_channel: Ch-A has 2 videos (total 3720s), Ch-B has 1 (240s).
    assert d["by_channel"] == [
        {"channel": "Ch-A", "count": 2, "total_seconds": 120.0 + 3600.0},
        {"channel": "Ch-B", "count": 1, "total_seconds": 240.0},
    ]
    # Archived channel must not appear.
    assert not any(c["channel"] == "Ch-ARCH" for c in d["by_channel"])

    # by_language: en=2, fr=1 (archived 'es' excluded).
    assert d["by_language"] == [
        {"language": "en", "count": 2},
        {"language": "fr", "count": 1},
    ]

    # top_tags: ml=2 first, news=1. Archived-only tag 'ghost' excluded.
    assert d["top_tags"] == [
        {"tag": "ml", "count": 2},
        {"tag": "news", "count": 1},
    ]
    assert not any(t["tag"] == "ghost" for t in d["top_tags"])

    # longest_videos: sorted desc by duration.
    assert [v["id"] for v in d["longest_videos"]] == [
        "def12345678", "ghi12345678", "abc12345678",
    ]
    assert d["longest_videos"][0]["duration_sec"] == pytest.approx(3600.0)

    # recently_analyzed: only the one on a live video.
    assert len(d["recently_analyzed"]) == 1
    assert d["recently_analyzed"][0]["id"] == "abc12345678"
    assert d["recently_analyzed"][0]["provider"] == "claude_cli"
