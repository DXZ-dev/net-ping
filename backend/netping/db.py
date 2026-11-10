"""Persistencia SQLite (WAL) con escritura por lotes para el registro de consultas."""
from __future__ import annotations

import asyncio
import logging
import sqlite3
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any, Iterable

log = logging.getLogger("netping.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS queries (
    id INTEGER PRIMARY KEY,
    ts REAL NOT NULL,
    client TEXT NOT NULL,
    domain TEXT NOT NULL,
    qtype TEXT NOT NULL,
    status INTEGER NOT NULL,
    reason TEXT,
    upstream TEXT,
    rtt REAL
);
CREATE INDEX IF NOT EXISTS idx_queries_ts ON queries(ts);
CREATE INDEX IF NOT EXISTS idx_queries_client_ts ON queries(client, ts);

CREATE TABLE IF NOT EXISTS domain_rules (
    id INTEGER PRIMARY KEY,
    value TEXT NOT NULL,
    kind TEXT NOT NULL,          -- allow | deny | regex_allow | regex_deny
    comment TEXT DEFAULT '',
    enabled INTEGER DEFAULT 1,
    created REAL NOT NULL,
    UNIQUE(value, kind)
);

CREATE TABLE IF NOT EXISTS blocklists (
    id INTEGER PRIMARY KEY,
    url TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    category TEXT DEFAULT 'ads',  -- ads | security | adult | custom
    enabled INTEGER DEFAULT 1,
    domains INTEGER DEFAULT 0,
    last_updated REAL DEFAULT 0,
    last_status TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS devices (
    mac TEXT PRIMARY KEY,
    ip TEXT,
    hostname TEXT DEFAULT '',
    vendor TEXT DEFAULT '',
    name TEXT DEFAULT '',
    kind TEXT DEFAULT '',
    profile TEXT DEFAULT 'default',
    paused INTEGER DEFAULT 0,          -- internet pausado manualmente
    paused_until REAL DEFAULT 0,
    notes TEXT DEFAULT '',
    first_seen REAL,
    last_seen REAL,
    online INTEGER DEFAULT 0,
    latency REAL
);

CREATE TABLE IF NOT EXISTS speedtests (
    id INTEGER PRIMARY KEY,
    ts REAL NOT NULL,
    download REAL, upload REAL, ping REAL, jitter REAL, loss REAL,
    server TEXT, ip TEXT, isp TEXT
);

CREATE TABLE IF NOT EXISTS monitor (
    ts REAL NOT NULL,
    target TEXT NOT NULL,
    latency REAL,
    loss REAL
);
CREATE INDEX IF NOT EXISTS idx_monitor_ts ON monitor(ts);

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY,
    ts REAL NOT NULL,
    level TEXT NOT NULL,
    category TEXT NOT NULL,
    message TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts);
"""


class Database:
    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.Lock()
        self.conn = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self.conn.execute("PRAGMA temp_store=MEMORY")
        self.conn.execute("PRAGMA cache_size=-20000")
        self.conn.executescript(SCHEMA)
        self._migrate()
        self._pending: deque[tuple] = deque()

    def _migrate(self) -> None:
        cols = {r[1] for r in self.conn.execute("PRAGMA table_info(speedtests)")}
        for col, typ in (("bufferbloat", "REAL"), ("grade", "TEXT"), ("loaded_ping", "REAL")):
            if col not in cols:
                self.conn.execute(f"ALTER TABLE speedtests ADD COLUMN {col} {typ}")

    # ---- primitivas -------------------------------------------------
    def execute(self, sql: str, params: Iterable[Any] = ()) -> sqlite3.Cursor:
        with self._lock:
            return self.conn.execute(sql, tuple(params))

    def executemany(self, sql: str, rows: list) -> None:
        with self._lock:
            self.conn.executemany(sql, rows)

    def query(self, sql: str, params: Iterable[Any] = ()) -> list[dict]:
        with self._lock:
            return [dict(r) for r in self.conn.execute(sql, tuple(params)).fetchall()]

    def one(self, sql: str, params: Iterable[Any] = ()) -> dict | None:
        rows = self.query(sql, params)
        return rows[0] if rows else None

    def scalar(self, sql: str, params: Iterable[Any] = ()) -> Any:
        with self._lock:
            row = self.conn.execute(sql, tuple(params)).fetchone()
        return row[0] if row else None

    async def aquery(self, sql: str, params: Iterable[Any] = ()) -> list[dict]:
        return await asyncio.to_thread(self.query, sql, params)

    async def aexecute(self, sql: str, params: Iterable[Any] = ()) -> int:
        def run():
            cur = self.execute(sql, params)
            return cur.lastrowid or cur.rowcount
        return await asyncio.to_thread(run)

    # ---- registro de consultas (por lotes) ---------------------------
    def log_query(self, row: tuple) -> None:
        self._pending.append(row)

    def flush_queries(self) -> int:
        if not self._pending:
            return 0
        batch = []
        while self._pending and len(batch) < 5000:
            batch.append(self._pending.popleft())
        with self._lock:
            self.conn.execute("BEGIN")
            self.conn.executemany(
                "INSERT INTO queries(ts,client,domain,qtype,status,reason,upstream,rtt) VALUES (?,?,?,?,?,?,?,?)",
                batch,
            )
            self.conn.execute("COMMIT")
        return len(batch)

    def event(self, level: str, category: str, message: str) -> None:
        try:
            self.execute(
                "INSERT INTO events(ts,level,category,message) VALUES (?,?,?,?)",
                (time.time(), level, category, message),
            )
        except Exception:  # pragma: no cover
            log.exception("no se pudo registrar el evento")

    def purge(self, retention_days: int) -> None:
        cutoff = time.time() - retention_days * 86400
        with self._lock:
            self.conn.execute("DELETE FROM queries WHERE ts < ?", (cutoff,))
            self.conn.execute("DELETE FROM monitor WHERE ts < ?", (cutoff,))
            self.conn.execute("DELETE FROM events WHERE ts < ?", (cutoff,))
        try:
            with self._lock:
                self.conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        except sqlite3.Error:
            pass


db: Database | None = None


def init_db(path: Path) -> Database:
    global db
    db = Database(path)
    return db


def get_db() -> Database:
    assert db is not None
    return db
