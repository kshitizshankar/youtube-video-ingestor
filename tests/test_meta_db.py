from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from server.db import open_connection, run_migrations
from server import meta


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@pytest.fixture
def conn(tmp_path: Path):
    db_path = tmp_path / "app.db"
    c = open_connection(db_path)
    run_migrations(c)
    now = _iso_now()
    c.execute(
        "INSERT INTO videos(id, url, created_at, updated_at) VALUES(?, ?, ?, ?)",
        ("abc12345678", "https://youtu.be/abc12345678", now, now),
    )
    yield c
    c.close()


def test_read_empty_meta(tmp_path: Path, conn) -> None:
    r = meta.read_meta(tmp_path, "abc12345678", conn=conn)
    assert r == {"tags": [], "speaker_names": {}, "notes": "", "updated_at": None}


def test_write_then_read_meta(tmp_path: Path, conn) -> None:
    meta.write_meta(
        tmp_path, "abc12345678",
        {"tags": ["interview", "ai"], "notes": "Great."},
        conn=conn,
    )
    r = meta.read_meta(tmp_path, "abc12345678", conn=conn)
    assert r["tags"] == ["interview", "ai"]
    assert r["notes"] == "Great."
    assert r["updated_at"] is not None


def test_tags_dedupe_and_strip(tmp_path: Path, conn) -> None:
    meta.write_meta(
        tmp_path, "abc12345678",
        {"tags": [" Ai ", "ai", "Interview"]},
        conn=conn,
    )
    r = meta.read_meta(tmp_path, "abc12345678", conn=conn)
    assert r["tags"] == ["Ai", "Interview"]


def test_speaker_names_round_trip(tmp_path: Path, conn) -> None:
    meta.write_meta(
        tmp_path, "abc12345678",
        {"speaker_names": {"SPEAKER_00": "Alice", "SPEAKER_01": "  "}},
        conn=conn,
    )
    r = meta.read_meta(tmp_path, "abc12345678", conn=conn)
    assert r["speaker_names"] == {"SPEAKER_00": "Alice"}


def test_notes_only_update_preserves_tags(tmp_path: Path, conn) -> None:
    meta.write_meta(tmp_path, "abc12345678", {"tags": ["a"]}, conn=conn)
    meta.write_meta(tmp_path, "abc12345678", {"notes": "hi"}, conn=conn)
    r = meta.read_meta(tmp_path, "abc12345678", conn=conn)
    assert r["tags"] == ["a"]
    assert r["notes"] == "hi"
