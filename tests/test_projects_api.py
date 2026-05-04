"""Projects CRUD + membership via HTTP. Uses httpx.AsyncClient against a
fresh FastAPI app backed by a tmp_path output dir."""
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


def test_empty_projects_list(client) -> None:
    """A fresh DB has the system Inbox project automatically; no other
    projects until the user creates one."""
    r = client.get("/api/projects")
    assert r.status_code == 200
    projects = r.json()
    assert len(projects) == 1
    assert projects[0]["id"] == "inbox"
    assert projects[0]["system_kind"] == "inbox"
    assert projects[0]["video_count"] == 0


def test_create_project(client) -> None:
    r = client.post("/api/projects", json={"name": "My Project", "description": "D"})
    assert r.status_code == 201, r.text
    data = r.json()
    assert data["id"] == "my-project"
    assert data["name"] == "My Project"
    assert data["video_count"] == 0


def test_slug_collision_autosuffix(client) -> None:
    client.post("/api/projects", json={"name": "Dup"})
    r2 = client.post("/api/projects", json={"name": "Dup"})
    assert r2.status_code == 201
    assert r2.json()["id"] == "dup-2"
    r3 = client.post("/api/projects", json={"name": "Dup"})
    assert r3.json()["id"] == "dup-3"


def test_rename_and_delete(client) -> None:
    r = client.post("/api/projects", json={"name": "Alpha"})
    pid = r.json()["id"]
    client.patch(f"/api/projects/{pid}", json={"name": "Beta"})
    row = client.get(f"/api/projects/{pid}").json()
    assert row["project"]["id"] == pid
    assert row["project"]["name"] == "Beta"
    r = client.delete(f"/api/projects/{pid}")
    assert r.status_code == 200
    assert client.get(f"/api/projects/{pid}").status_code == 404


def test_add_and_remove_video(client, tmp_path) -> None:
    from server.db import open_connection, run_migrations
    from server.projects import ensure_inbox
    conn = open_connection(tmp_path / "app.db")
    run_migrations(conn)
    ensure_inbox(tmp_path, conn=conn)
    # Folder-per-project: every video must have a project_id + path.
    # The "add to a project" flow uses the move protocol, which refuses
    # NULL-path rows. Seed the video into Inbox with a path that doesn't
    # exist on disk -- move_video skips the FS step in that case and
    # just records the DB swap.
    inbox_path = tmp_path / "projects" / "inbox" / "abc12345678"
    inbox_path.mkdir(parents=True, exist_ok=True)
    conn.execute(
        "INSERT INTO videos(id,url,title,created_at,updated_at,project_id,path) "
        "VALUES(?,?,?,?,?,?,?)",
        ("abc12345678", "https://x", "Vid", "2026-01-01", "2026-01-01",
         "inbox", str(inbox_path)),
    )
    conn.close()

    pid = client.post("/api/projects", json={"name": "P"}).json()["id"]
    r = client.post(f"/api/projects/{pid}/videos", json={"video_ids": ["abc12345678"]})
    assert r.status_code == 200
    r = client.get(f"/api/projects/{pid}")
    assert r.json()["project"]["video_count"] == 1
    videos = r.json()["videos"]
    assert videos[0]["id"] == "abc12345678"

    r2 = client.post(f"/api/projects/{pid}/videos", json={"video_ids": ["abc12345678"]})
    assert r2.status_code == 200
    assert client.get(f"/api/projects/{pid}").json()["project"]["video_count"] == 1

    r = client.delete(f"/api/projects/{pid}/videos/abc12345678")
    assert r.status_code == 200
    assert client.get(f"/api/projects/{pid}").json()["project"]["video_count"] == 0


def test_unknown_project_404(client) -> None:
    assert client.get("/api/projects/nope").status_code == 404
    assert client.patch("/api/projects/nope", json={"name": "x"}).status_code == 404
    assert client.delete("/api/projects/nope").status_code == 404
