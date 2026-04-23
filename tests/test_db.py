from pathlib import Path

from server.db import open_connection


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
