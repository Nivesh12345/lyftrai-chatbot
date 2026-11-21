"""SQLite storage. Plain sqlite3, one short-lived connection per operation."""
import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone

SCHEMA = """
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    metadata TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id, id);
CREATE TABLE IF NOT EXISTS sessions (
    session_id TEXT PRIMARY KEY,
    system_prompt TEXT,
    created_at TEXT NOT NULL
);
"""


def db_path(database_url: str) -> str:
    prefix = "sqlite:///"
    if not database_url.startswith(prefix):
        raise ValueError("DATABASE_URL must look like sqlite:////data/app.db")
    return database_url[len(prefix):]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def connect(database_url: str):
    conn = sqlite3.connect(db_path(database_url), timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db(database_url: str) -> None:
    path = db_path(database_url)
    folder = os.path.dirname(path)
    if folder:
        os.makedirs(folder, exist_ok=True)
    with connect(database_url) as conn:
        try:
            conn.execute("PRAGMA journal_mode=WAL")
        except sqlite3.DatabaseError:
            pass
        conn.executescript(SCHEMA)


def ping(database_url: str) -> bool:
    with connect(database_url) as conn:
        conn.execute("SELECT 1").fetchone()
    return True


def get_session_prompt(conn: sqlite3.Connection, session_id: str) -> str | None:
    row = conn.execute(
        "SELECT system_prompt FROM sessions WHERE session_id = ?", (session_id,)
    ).fetchone()
    return row["system_prompt"] if row else None


def get_history(conn: sqlite3.Connection, session_id: str) -> list[dict]:
    rows = conn.execute(
        "SELECT role, content, metadata FROM messages WHERE session_id = ? ORDER BY id",
        (session_id,),
    ).fetchall()
    out = []
    for r in rows:
        item = {"role": r["role"], "content": r["content"]}
        if r["metadata"]:
            item["metadata"] = json.loads(r["metadata"])
        out.append(item)
    return out


def save_exchange(
    database_url: str,
    session_id: str,
    system_prompt: str | None,
    user_message: str,
    reply: str,
    metadata: dict,
) -> None:
    """Write the user + assistant messages in ONE transaction (all or nothing)."""
    now = _now()
    with connect(database_url) as conn:
        if system_prompt is not None:
            conn.execute(
                "INSERT INTO sessions (session_id, system_prompt, created_at) VALUES (?, ?, ?) "
                "ON CONFLICT(session_id) DO UPDATE SET system_prompt = excluded.system_prompt",
                (session_id, system_prompt, now),
            )
        else:
            conn.execute(
                "INSERT OR IGNORE INTO sessions (session_id, system_prompt, created_at) "
                "VALUES (?, NULL, ?)",
                (session_id, now),
            )
        conn.execute(
            "INSERT INTO messages (session_id, role, content, metadata, created_at) "
            "VALUES (?, 'user', ?, NULL, ?)",
            (session_id, user_message, now),
        )
        conn.execute(
            "INSERT INTO messages (session_id, role, content, metadata, created_at) "
            "VALUES (?, 'assistant', ?, ?, ?)",
            (session_id, reply, json.dumps(metadata), _now()),
        )
