from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from server.db import open_connection, run_migrations
from server import transcripts


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@pytest.fixture
def conn(tmp_path: Path):
    db_path = tmp_path / "app.db"
    c = open_connection(db_path)
    run_migrations(c)
    yield c
    c.close()


def _seed_video(conn, vid: str, *, archived: int = 0, title: str = "t") -> None:
    now = _iso_now()
    conn.execute(
        "INSERT INTO videos(id, url, title, archived, created_at, updated_at) "
        "VALUES(?, ?, ?, ?, ?, ?)",
        (vid, f"https://youtu.be/{vid}", title, archived, now, now),
    )


def test_list_transcripts_excludes_archived(tmp_path: Path, conn) -> None:
    _seed_video(conn, "live000abcde", archived=0, title="Live")
    _seed_video(conn, "arch0000abcd", archived=1, title="Archived")
    out = transcripts.list_transcripts(tmp_path, conn=conn)
    ids = [r["id"] for r in out]
    assert ids == ["live000abcde"]
    assert out[0]["title"] == "Live"


def test_list_archived_returns_only_archived(tmp_path: Path, conn) -> None:
    _seed_video(conn, "live000abcde", archived=0)
    _seed_video(conn, "arch0000abcd", archived=1)
    out = transcripts.list_archived(tmp_path, conn=conn)
    assert [r["id"] for r in out] == ["arch0000abcd"]


def test_set_archived_flips_flag(tmp_path: Path, conn) -> None:
    _seed_video(conn, "live000abcde")
    assert transcripts.set_archived(tmp_path, "live000abcde", True, conn=conn)
    row = conn.execute("SELECT archived FROM videos WHERE id='live000abcde'").fetchone()
    assert row["archived"] == 1


def test_set_archived_returns_false_for_unknown(tmp_path: Path, conn) -> None:
    assert transcripts.set_archived(tmp_path, "nonexistent", True, conn=conn) is False


def test_read_transcript_prefers_disk(tmp_path: Path, conn) -> None:
    """Segments are still canonical on disk; read_transcript merges the DB
    row metadata with the on-disk segments."""
    video_id = "live000abcde"
    folder = tmp_path / video_id
    folder.mkdir()
    (folder / "transcript.json").write_text(json.dumps({
        "id": video_id, "url": "https://x",
        "title": "On disk", "segments": [{"id": 1, "start": 0, "end": 1, "text": "hi"}],
    }), encoding="utf-8")
    _seed_video(conn, video_id, title="Also in DB")
    r = transcripts.read_transcript(tmp_path, video_id, conn=conn)
    assert r is not None
    assert len(r["segments"]) == 1
    assert r["segments"][0]["text"] == "hi"


def test_delete_video_removes_row_and_folder(tmp_path: Path, conn) -> None:
    video_id = "live000abcde"
    folder = tmp_path / video_id
    folder.mkdir()
    (folder / "transcript.json").write_text("{}", encoding="utf-8")
    _seed_video(conn, video_id, archived=1)
    assert transcripts.delete_video(tmp_path, video_id, conn=conn)
    assert conn.execute(
        "SELECT 1 FROM videos WHERE id=?", (video_id,)
    ).fetchone() is None
    assert not folder.exists()
