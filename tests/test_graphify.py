"""Graphify status + counter helpers. The subprocess wrapper itself
needs the `claude` binary, so we don't drive a full build in tests --
we cover the column reads / writes / 404 paths directly."""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from server.db import open_connection, run_migrations
from server.graphify import bump_events_since_build, get_graph_status
from server.projects import ensure_inbox


@pytest.fixture
def client(tmp_path: Path, monkeypatch) -> TestClient:
    monkeypatch.setenv("OUTPUT_DIR", str(tmp_path))
    monkeypatch.setenv("AUTH_DISABLED", "1")
    import importlib
    import server.main as main_mod
    importlib.reload(main_mod)
    return TestClient(main_mod.app)


@pytest.fixture
def db(tmp_path: Path):
    conn = open_connection(tmp_path / "app.db")
    run_migrations(conn)
    ensure_inbox(tmp_path, conn=conn)
    yield tmp_path, conn
    conn.close()


def test_get_graph_status_returns_defaults_for_fresh_project(db) -> None:
    out_dir, _conn = db
    s = get_graph_status(out_dir, "inbox")
    assert s is not None
    assert s["state"] == "never"
    assert s["built_at"] is None
    assert s["node_count"] is None
    assert s["edge_count"] is None
    assert s["events_since_build"] == 0
    assert s["has_index_html"] is False
    assert s["has_graph_json"] is False


def test_get_graph_status_returns_none_for_unknown_project(db) -> None:
    out_dir, _ = db
    assert get_graph_status(out_dir, "no-such-project") is None


def test_bump_events_increments_counter(db) -> None:
    out_dir, conn = db
    assert conn.execute(
        "SELECT events_since_build FROM projects WHERE id='inbox'"
    ).fetchone()["events_since_build"] == 0
    bump_events_since_build(out_dir, "inbox")
    bump_events_since_build(out_dir, "inbox")
    bump_events_since_build(out_dir, "inbox")
    assert conn.execute(
        "SELECT events_since_build FROM projects WHERE id='inbox'"
    ).fetchone()["events_since_build"] == 3


def test_bump_events_silent_for_none(db) -> None:
    out_dir, _ = db
    # Should not raise.
    bump_events_since_build(out_dir, None)


def test_status_endpoint_404s_unknown_project(client) -> None:
    r = client.get("/api/projects/no-such-project/graph/status")
    assert r.status_code == 404


def test_status_endpoint_returns_inbox_defaults(client) -> None:
    r = client.get("/api/projects/inbox/graph/status")
    assert r.status_code == 200
    body = r.json()
    assert body["state"] == "never"
    assert body["events_since_build"] == 0


def test_build_endpoint_rejects_unknown_mode(client) -> None:
    r = client.get("/api/projects/inbox/graph/build?mode=bogus")
    assert r.status_code == 400


def test_build_endpoint_404s_unknown_project(client) -> None:
    r = client.get("/api/projects/no-such-project/graph/build?mode=update")
    assert r.status_code == 404


def test_static_endpoint_404s_when_no_graph_built(client) -> None:
    r = client.get("/api/projects/inbox/graph/file/")
    assert r.status_code == 404
    assert r.json()["detail"] == "graph not built yet"


def test_static_endpoint_blocks_path_traversal(client, tmp_path) -> None:
    # Pre-create the graphify-out folder so the route gets past the
    # "no graph yet" check; then probe with a traversal payload.
    out_folder = tmp_path / "projects" / "inbox" / "graphify-out"
    out_folder.mkdir(parents=True)
    (out_folder / "index.html").write_text("ok", encoding="utf-8")
    r = client.get("/api/projects/inbox/graph/file/../../../app.db")
    # FastAPI normalises ../ in the URL itself before routing, so the
    # route gets a path that lands above the folder. Server returns
    # 403 (forbidden) or the request fails to even match the route.
    assert r.status_code in (403, 404)
