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
from pathlib import Path


def open_connection(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), isolation_level=None, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn
