"""SQLite connection factory. Every caller opens their own short-lived
connection; the connection is not thread-safe under concurrent writes
without per-thread isolation, which matches how FastAPI handles requests.

PRAGMAs applied on every open:
- journal_mode=WAL so readers don't block writers (SSE polling + writes).
- synchronous=NORMAL so commits don't fsync every time (crash-safe enough).
- foreign_keys=ON so CASCADE clauses in the schema actually fire.
- busy_timeout=5000 so concurrent writers wait up to 5s before raising.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path


MIGRATIONS_DIR = Path(__file__).parent / "migrations"


def open_connection(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), isolation_level=None, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def run_migrations(conn: sqlite3.Connection) -> list[str]:
    """Apply every `.sql` file in server/migrations/ that hasn't been
    applied yet. Ordered by filename (lexicographic). Returns the names
    that were newly applied. Idempotent."""
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations "
        "(name TEXT PRIMARY KEY, applied_at TEXT NOT NULL)"
    )
    applied = {
        r[0] for r in conn.execute("SELECT name FROM schema_migrations")
    }

    newly: list[str] = []
    for sql_path in sorted(MIGRATIONS_DIR.glob("*.sql")):
        name = sql_path.stem
        if name in applied:
            continue
        # executescript with isolation_level=None auto-commits at start,
        # so we wrap the migration + bookkeeping insert inside a single
        # BEGIN/COMMIT embedded in the script text. That way the whole
        # migration is one transaction and rolls back on any error.
        body = sql_path.read_text(encoding="utf-8")
        applied_at = _iso_now().replace("'", "''")
        script = (
            "BEGIN;\n"
            f"{body}\n"
            "INSERT OR IGNORE INTO schema_migrations(name, applied_at) "
            f"VALUES('{name}', '{applied_at}');\n"
            "COMMIT;\n"
        )
        try:
            conn.executescript(script)
        except Exception:
            try:
                conn.execute("ROLLBACK")
            except sqlite3.OperationalError:
                pass
            raise
        newly.append(name)
    return newly
