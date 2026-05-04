from pathlib import Path

from server.db import open_connection, run_migrations


def _pragma(conn, name: str):
    row = conn.execute(f"PRAGMA {name}").fetchone()
    return row[0] if row else None


def test_connection_has_required_pragmas(tmp_path: Path) -> None:
    db_path = tmp_path / "app.db"
    conn = open_connection(db_path)
    try:
        assert _pragma(conn, "journal_mode").lower() == "wal"
        # synchronous: 1 = NORMAL
        assert int(_pragma(conn, "synchronous")) == 1
        # foreign_keys: 1 = ON
        assert int(_pragma(conn, "foreign_keys")) == 1
        # busy_timeout: milliseconds, we set 5000
        assert int(_pragma(conn, "busy_timeout")) == 5000
    finally:
        conn.close()


def test_open_connection_creates_parent_dir(tmp_path: Path) -> None:
    db_path = tmp_path / "nested" / "dir" / "app.db"
    conn = open_connection(db_path)
    try:
        assert db_path.parent.is_dir()
        assert db_path.exists()
    finally:
        conn.close()


def test_run_migrations_creates_tables(tmp_path: Path) -> None:
    db_path = tmp_path / "app.db"
    conn = open_connection(db_path)
    try:
        run_migrations(conn)
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )}
        required = {
            "videos", "video_tags", "video_speakers",
            "projects",
            "ingests", "analyses",
            "schema_migrations",
            # Folder-per-project tables (migration 004); migration 005
            # drops project_videos as part of this layout change.
            "move_log",
        }
        missing = required - tables
        assert not missing, f"missing tables: {missing}"
        # project_videos must be GONE -- the m:m table was replaced by
        # videos.project_id (migration 005).
        assert "project_videos" not in tables, "project_videos should be dropped"
    finally:
        conn.close()


def test_run_migrations_is_idempotent(tmp_path: Path) -> None:
    db_path = tmp_path / "app.db"
    conn = open_connection(db_path)
    try:
        run_migrations(conn)
        first = [r[0] for r in conn.execute("SELECT name FROM schema_migrations ORDER BY name")]
        run_migrations(conn)
        second = [r[0] for r in conn.execute("SELECT name FROM schema_migrations ORDER BY name")]
        # Second run records nothing new, and at minimum the initial migration is present.
        assert first == second
        assert "001_initial" in first
    finally:
        conn.close()


def test_foreign_keys_cascade(tmp_path: Path) -> None:
    db_path = tmp_path / "app.db"
    conn = open_connection(db_path)
    try:
        run_migrations(conn)
        conn.execute(
            "INSERT INTO videos(id, url, created_at, updated_at) "
            "VALUES('abc12345678', 'https://x', '2026-01-01', '2026-01-01')"
        )
        conn.execute("INSERT INTO video_tags(video_id, tag) VALUES('abc12345678', 'tag1')")
        conn.execute("DELETE FROM videos WHERE id='abc12345678'")
        count = conn.execute("SELECT COUNT(*) FROM video_tags").fetchone()[0]
        assert count == 0
    finally:
        conn.close()
