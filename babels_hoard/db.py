"""SQLite storage layer. WAL mode, FTS5 search index, no ORM.

Every text column is UTF-8. Paths are stored as posix strings but read back
through ``pathlib.Path`` so the module works unchanged on Windows and Linux.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Iterable

SCHEMA_VERSION = 2

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS environments (
    id TEXT PRIMARY KEY,
    label TEXT NOT NULL,
    project_path TEXT,
    python_path TEXT,
    python_version TEXT,
    node_modules_path TEXT,
    node_version TEXT,
    is_builtin INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS libraries (
    id TEXT PRIMARY KEY,
    ecosystem TEXT NOT NULL,       -- python | js | docset | markdown
    name TEXT NOT NULL,
    version TEXT NOT NULL,
    source TEXT NOT NULL,          -- e.g. env:<id>, node_modules:<id>, devdocs, folder
    env_id TEXT,
    status TEXT NOT NULL DEFAULT 'pending',  -- pending | indexing | done | partial | error
    entry_count INTEGER NOT NULL DEFAULT 0,
    note TEXT,
    indexed_at TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_libraries_lookup ON libraries(ecosystem, name, version, source);

CREATE TABLE IF NOT EXISTS entries (
    id TEXT PRIMARY KEY,
    library_id TEXT NOT NULL REFERENCES libraries(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    qualname TEXT NOT NULL,
    kind TEXT NOT NULL,             -- module|class|function|method|attribute|property|section
    signature TEXT,
    params_json TEXT,
    returns TEXT,
    summary TEXT,
    doc TEXT,
    deprecated INTEGER NOT NULL DEFAULT 0,
    deprecated_note TEXT,
    source_path TEXT,
    source_line INTEGER,
    parent_id TEXT,
    target TEXT,                    -- canonical (definition) path, python only
    meta_json TEXT                  -- completeness / call-shape facts for the checker
);
CREATE INDEX IF NOT EXISTS idx_entries_library ON entries(library_id);
CREATE INDEX IF NOT EXISTS idx_entries_qualname ON entries(qualname);
CREATE INDEX IF NOT EXISTS idx_entries_name ON entries(name);
CREATE INDEX IF NOT EXISTS idx_libraries_env ON libraries(env_id, ecosystem, status);

CREATE VIRTUAL TABLE IF NOT EXISTS entries_fts USING fts5(
    name, qualname, signature, summary, doc,
    content='entries', content_rowid='rowid'
);

CREATE TRIGGER IF NOT EXISTS entries_ai AFTER INSERT ON entries BEGIN
    INSERT INTO entries_fts(rowid, name, qualname, signature, summary, doc)
    VALUES (new.rowid, new.name, new.qualname, new.signature, new.summary, new.doc);
END;
CREATE TRIGGER IF NOT EXISTS entries_ad AFTER DELETE ON entries BEGIN
    INSERT INTO entries_fts(entries_fts, rowid, name, qualname, signature, summary, doc)
    VALUES ('delete', old.rowid, old.name, old.qualname, old.signature, old.summary, old.doc);
END;
CREATE TRIGGER IF NOT EXISTS entries_au AFTER UPDATE ON entries BEGIN
    INSERT INTO entries_fts(entries_fts, rowid, name, qualname, signature, summary, doc)
    VALUES ('delete', old.rowid, old.name, old.qualname, old.signature, old.summary, old.doc);
    INSERT INTO entries_fts(rowid, name, qualname, signature, summary, doc)
    VALUES (new.rowid, new.name, new.qualname, new.signature, new.summary, new.doc);
END;

CREATE VIRTUAL TABLE IF NOT EXISTS names_trigram USING fts5(
    name, entry_id UNINDEXED, tokenize='trigram'
);
CREATE TRIGGER IF NOT EXISTS entries_trigram_ai AFTER INSERT ON entries BEGIN
    INSERT INTO names_trigram(rowid, name, entry_id) VALUES (new.rowid, new.name, new.id);
END;
CREATE TRIGGER IF NOT EXISTS entries_trigram_ad AFTER DELETE ON entries BEGIN
    DELETE FROM names_trigram WHERE rowid = old.rowid;
END;

CREATE TABLE IF NOT EXISTS docsets (
    slug TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    version TEXT,
    library_id TEXT REFERENCES libraries(id) ON DELETE CASCADE,
    installed_at TEXT NOT NULL,
    entry_count INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'queued',  -- queued|running|done|error
    progress REAL NOT NULL DEFAULT 0.0,
    message TEXT,
    result_json TEXT,
    error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS agent_calls (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    tool TEXT NOT NULL,
    args_summary TEXT,
    duration_ms REAL,
    ok INTEGER NOT NULL,
    error TEXT
);
CREATE INDEX IF NOT EXISTS idx_agent_calls_ts ON agent_calls(ts);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT
);
"""


def _open(db_path: Path) -> sqlite3.Connection:
    # check_same_thread=False only so that close_all() may close a
    # connection from another thread; each connection is otherwise used by
    # exactly one thread (see Database).
    conn = sqlite3.connect(str(db_path), check_same_thread=False, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


def connect(db_path: Path) -> sqlite3.Connection:
    """Open one connection and make sure the schema exists. Use it from a
    single thread; multi-threaded code (the HTTP app, the job worker) goes
    through :class:`Database` instead."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = _open(db_path)
    conn.executescript(_SCHEMA)
    _migrate(conn)
    conn.execute(
        "INSERT OR IGNORE INTO meta(key, value) VALUES ('schema_version', ?)",
        (str(SCHEMA_VERSION),),
    )
    conn.commit()
    return conn


class Database:
    """One SQLite connection per thread over the same WAL database file.

    A single ``sqlite3.Connection`` shared by the request threads and the
    background job worker interleaves their transactions (a commit from one
    thread commits the other's half-written work). WAL mode lets readers
    run while one writer writes, and ``busy_timeout`` makes concurrent
    writers wait instead of failing, so per-thread connections are both
    safe and non-blocking for reads such as ``/api/health``.
    """

    def __init__(self, db_path: Path):
        self.path = Path(db_path)
        self._local = threading.local()
        self._lock = threading.Lock()
        self._all: list[sqlite3.Connection] = []
        first = connect(self.path)
        self._local.conn = first
        self._all.append(first)

    def conn(self) -> sqlite3.Connection:
        c = getattr(self._local, "conn", None)
        if c is None:
            c = _open(self.path)
            self._local.conn = c
            with self._lock:
                self._all.append(c)
        return c

    def close_all(self) -> None:
        with self._lock:
            conns, self._all = self._all, []
        for c in conns:
            try:
                c.close()
            except Exception:
                pass
        self._local = threading.local()


def _migrate(conn: sqlite3.Connection) -> None:
    """Bring an older database up to the current schema in place."""
    row = conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
    current = int(row["value"]) if row else SCHEMA_VERSION
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(entries)").fetchall()}
    if "target" not in cols:
        conn.execute("ALTER TABLE entries ADD COLUMN target TEXT")
    if "meta_json" not in cols:
        conn.execute("ALTER TABLE entries ADD COLUMN meta_json TEXT")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_entries_target ON entries(target)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_entries_parent ON entries(parent_id)")
    if current < 2:
        # v1 Python/JS indexes lack the completeness metadata the checker
        # relies on to stay silent; drop them so they are rebuilt lazily.
        conn.execute("DELETE FROM libraries WHERE ecosystem IN ('python', 'js')")
        conn.execute("DELETE FROM names_trigram")
        conn.execute("INSERT INTO names_trigram(rowid, name, entry_id) SELECT rowid, name, id FROM entries")
    conn.execute(
        "INSERT OR REPLACE INTO meta(key, value) VALUES ('schema_version', ?)", (str(SCHEMA_VERSION),)
    )


def now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def dump(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return dict(row)


def dump_all(rows: Iterable[sqlite3.Row]) -> list[dict[str, Any]]:
    return [dict(r) for r in rows]


def to_json(value: Any) -> str | None:
    if value is None:
        return None
    return json.dumps(value, ensure_ascii=False)


def from_json(value: str | None) -> Any:
    if not value:
        return None
    return json.loads(value)
