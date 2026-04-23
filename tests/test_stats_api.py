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
    }


def test_populated_stats(client, tmp_path) -> None:
    from server.db import open_connection, run_migrations
    c = open_connection(tmp_path / "app.db")
    run_migrations(c)
    c.execute(
        "INSERT INTO videos(id,url,title,duration_sec,storage_bytes,created_at,updated_at) "
        "VALUES(?,?,?,?,?,?,?)",
        ("abc12345678", "https://x", "V1", 120.0, 500, "2026-01-01", "2026-01-01"),
    )
    c.execute(
        "INSERT INTO videos(id,url,title,duration_sec,storage_bytes,archived,created_at,updated_at) "
        "VALUES(?,?,?,?,?,?,?,?)",
        ("zzz12345678", "https://y", "V2 archived", 60.0, 100, 1, "2026-01-02", "2026-01-02"),
    )
    c.execute(
        "INSERT INTO projects(id,name,created_at,updated_at) VALUES(?,?,?,?)",
        ("p1", "P1", "2026-01-01", "2026-01-01"),
    )
    c.close()

    d = client.get("/api/stats").json()
    assert d["video_count"] == 1
    assert d["project_count"] == 1
    assert d["total_seconds"] == pytest.approx(120.0)
    assert d["storage_bytes"] == 500
    assert len(d["latest_videos"]) == 1
    assert d["latest_videos"][0]["id"] == "abc12345678"
