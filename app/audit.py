"""SQLite audit log of every processing outcome (Redis streams get trimmed)."""
import os
import sqlite3
import time
from contextlib import closing

_SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    event_id TEXT NOT NULL,
    type TEXT,
    executor TEXT,
    status TEXT NOT NULL,
    attempts INTEGER DEFAULT 0,
    error TEXT,
    result TEXT
)
"""


def _connect(path: str) -> sqlite3.Connection:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute(_SCHEMA)
    # Databases created by an earlier version lack the result column.
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(events)")}
    if "result" not in columns:
        conn.execute("ALTER TABLE events ADD COLUMN result TEXT")
    return conn


def record(
    path, event_id, event_type, status, attempts=0, error=None, executor=None, result=None
):
    ts = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime())
    with closing(_connect(path)) as conn, conn:
        conn.execute(
            "INSERT INTO events (ts, event_id, type, executor, status, attempts, error, result)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (ts, event_id, event_type, executor, status, attempts, error, result),
        )


def recent(path: str, limit: int = 50) -> list[dict]:
    with closing(_connect(path)) as conn:
        rows = conn.execute(
            "SELECT ts, event_id, type, executor, status, attempts, error, result"
            " FROM events ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(row) for row in rows]