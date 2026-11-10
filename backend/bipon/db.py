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

log = logging.getLogger("bipon.db")

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

CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY,
    username TEXT NOT NULL UNIQUE COLLATE NOCASE,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'admin',
    totp_secret TEXT,
    totp_enabled INTEGER DEFAULT 0,
    created REAL,
    last_login REAL
);

CREATE TABLE IF NOT EXISTS api_tokens (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    token_hash TEXT NOT NULL UNIQUE,
    role TEXT NOT NULL,
    created REAL,
    last_used REAL
);

CREATE TABLE IF NOT EXISTS dhcp_leases (
    mac TEXT PRIMARY KEY,
    ip TEXT NOT NULL,
    hostname TEXT DEFAULT '',
    expires REAL NOT NULL,
    scope TEXT DEFAULT 'lan'
);

CREATE TABLE IF NOT EXISTS usage (
    day TEXT NOT NULL,
    mac TEXT NOT NULL,
    service TEXT NOT NULL,
    minutes INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (day, mac, service)
);

CREATE TABLE IF NOT EXISTS traffic (
    ts REAL NOT NULL,
    mac TEXT NOT NULL,
    rx INTEGER NOT NULL,
    tx INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_traffic_ts ON traffic(ts);

CREATE TABLE IF NOT EXISTS notify_log (
    id INTEGER PRIMARY KEY,
    ts REAL NOT NULL,
    event TEXT NOT NULL,
    channel TEXT NOT NULL,
    title TEXT NOT NULL,
    ok INTEGER NOT NULL,
    error TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS reports (
    id INTEGER PRIMARY KEY,
    ts REAL NOT NULL,
    start REAL NOT NULL,
    end REAL NOT NULL,
    data TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS push_subs (
    id INTEGER PRIMARY KEY,
    endpoint TEXT NOT NULL UNIQUE,
    data TEXT NOT NULL,
    user_id INTEGER,
    created REAL
);

CREATE TABLE IF NOT EXISTS anomalies (
    id INTEGER PRIMARY KEY,
    ts REAL NOT NULL,
    mac TEXT,
    client TEXT,
    kind TEXT NOT NULL,
    detail TEXT NOT NULL,
    acknowledged INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_anomalies_ts ON anomalies(ts);

CREATE TABLE IF NOT EXISTS audit (
    id INTEGER PRIMARY KEY,
    ts REAL NOT NULL,
    username TEXT,
    ip TEXT,
    method TEXT,
    path TEXT,
    status INTEGER
);
CREATE INDEX IF NOT EXISTS idx_audit_ts ON audit(ts);

CREATE TABLE IF NOT EXISTS config_profiles (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    description TEXT DEFAULT '',
    created REAL NOT NULL,
    data TEXT NOT NULL,
    auto INTEGER DEFAULT 0
);
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
        dcols = {r[1] for r in self.conn.execute("PRAGMA table_info(devices)")}
        for col, typ in (("parent", "TEXT"), ("auto_parent", "TEXT"), ("brand", "TEXT"), ("brand_name", "TEXT"), ("model", "TEXT"),
                         ("friendly", "TEXT"), ("os", "TEXT"), ("services", "TEXT"), ("ports", "TEXT"), ("hypervisor", "TEXT"),
                         ("enriched", "REAL"), ("limit_down", "REAL"), ("limit_up", "REAL"), ("kind_manual", "INTEGER DEFAULT 0"),
                         ("dhcp_vendor", "TEXT")):
            if col not in dcols:
                self.conn.execute(f"ALTER TABLE devices ADD COLUMN {col} {typ}")
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
            self.conn.execute("DELETE FROM traffic WHERE ts < ?", (cutoff,))
            self.conn.execute("DELETE FROM notify_log WHERE ts < ?", (cutoff,))
            self.conn.execute("DELETE FROM anomalies WHERE ts < ?", (cutoff,))
            self.conn.execute("DELETE FROM audit WHERE ts < ?", (cutoff - 335 * 86400,))
            self.conn.execute("DELETE FROM usage WHERE day < date(?, 'unixepoch')", (cutoff,))
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
