"""Exercise the one-shot JSON → SQLite migration against a canned fixture
tree. Every assertion here maps to a requirement in the spec §11."""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from server.db import open_connection, run_migrations
from server.migrate_data import migrate_data


FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "migration_tree" / "output"


@pytest.fixture
def migrated_db(tmp_path: Path):
    """Copy fixture tree to tmp_path, run schema + data migration, yield conn."""
    dst = tmp_path / "output"
    shutil.copytree(FIXTURE_ROOT, dst)
    db_path = dst / "app.db"
    conn = open_connection(db_path)
    run_migrations(conn)
    stats = migrate_data(conn, output_dir=dst)
    yield conn, dst, stats
    conn.close()


def test_migrates_every_video(migrated_db) -> None:
    conn, _, stats = migrated_db
    rows = conn.execute("SELECT id, title, archived FROM videos ORDER BY id").fetchall()
    ids = [r["id"] for r in rows]
    assert ids == ["aaaAAAaaa11", "bbbBBBbbb22"]
    archived = {r["id"]: r["archived"] for r in rows}
    assert archived["aaaAAAaaa11"] == 0
    assert archived["bbbBBBbbb22"] == 1
    assert stats["videos_migrated"] == 2


def test_migrates_all_video_columns(migrated_db) -> None:
    conn, _, _ = migrated_db
    row = conn.execute(
        "SELECT * FROM videos WHERE id='aaaAAAaaa11'"
    ).fetchone()
    assert row["title"] == "Diarized talk"
    assert row["channel"] == "Channel A"
    assert row["channel_follower_count"] == 50000
    assert row["upload_date"] == "20250101"
    assert row["language_probability"] == pytest.approx(0.98)
    assert row["diarized"] == 1
    assert row["speaker_count"] == 2
    assert row["model"] == "distil-large-v3"
    assert row["batched"] == 1
    assert row["batch_size"] == 16
    assert row["transcription_realtime_factor"] == pytest.approx(12.0)
    assert row["segment_count"] == 2
    import json
    assert json.loads(row["yt_tags"]) == ["ai", "research"]
    assert json.loads(row["categories"]) == ["Education"]


def test_migrates_tags_speakers_notes(migrated_db) -> None:
    conn, _, _ = migrated_db
    tags = {r["tag"] for r in conn.execute(
        "SELECT tag FROM video_tags WHERE video_id='aaaAAAaaa11'"
    )}
    assert tags == {"interview", "ai"}

    speakers = {
        r["label"]: r["name"] for r in conn.execute(
            "SELECT label, name FROM video_speakers WHERE video_id='aaaAAAaaa11'"
        )
    }
    assert speakers == {"SPEAKER_00": "Alice", "SPEAKER_01": "Bob"}

    notes = conn.execute(
        "SELECT notes FROM videos WHERE id='aaaAAAaaa11'"
    ).fetchone()["notes"]
    assert notes == "Worth re-listening."


def test_migrates_analysis_row_and_moves_file(migrated_db) -> None:
    conn, out_dir, _ = migrated_db
    rows = conn.execute(
        "SELECT provider, model, status, cost_usd, tokens_in, tokens_out, file_path "
        "FROM analyses WHERE video_id='aaaAAAaaa11'"
    ).fetchall()
    assert len(rows) == 1
    r = rows[0]
    assert r["provider"] == "claude_cli"
    assert r["model"] == "legacy"
    assert r["status"] == "done"
    assert r["cost_usd"] == pytest.approx(0.05)
    assert r["tokens_in"] == 500
    assert r["tokens_out"] == 100
    archived_path = out_dir / "aaaAAAaaa11" / r["file_path"]
    assert archived_path.exists()
    import json
    data = json.loads(archived_path.read_text())
    assert data["summary"] == "A summary."


def test_orphans_nondone_ingests(migrated_db) -> None:
    conn, _, _ = migrated_db
    rows = conn.execute(
        "SELECT video_id, done, error FROM ingests ORDER BY video_id"
    ).fetchall()
    assert len(rows) == 2
    by_vid = {r["video_id"]: r for r in rows}
    assert by_vid["aaaAAAaaa11"]["done"] == 1
    assert by_vid["ccc999PENDING"]["done"] == 1
    assert by_vid["ccc999PENDING"]["error"] is not None


def test_computes_storage_bytes(migrated_db) -> None:
    conn, _, _ = migrated_db
    row = conn.execute(
        "SELECT storage_bytes FROM videos WHERE id='aaaAAAaaa11'"
    ).fetchone()
    assert row["storage_bytes"] > 0


def test_migration_is_idempotent(migrated_db) -> None:
    conn, out_dir, _ = migrated_db
    stats2 = migrate_data(conn, output_dir=out_dir)
    count = conn.execute("SELECT COUNT(*) FROM videos").fetchone()[0]
    assert count == 2
    assert stats2["videos_migrated"] == 0


def test_source_files_not_deleted(migrated_db) -> None:
    _, out_dir, _ = migrated_db
    assert (out_dir / "aaaAAAaaa11" / "meta.json").exists()
    assert (out_dir / "aaaAAAaaa11" / "transcript.json").exists()
    assert (out_dir / "_ingests.json").exists()
