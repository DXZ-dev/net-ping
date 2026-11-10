"""Gestión de listas de bloqueo: descarga, actualización y recarga del motor."""
from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path

import httpx

from .catalog import RECOMMENDED_LISTS
from .config import DATA_DIR
from .db import Database
from .dns.filter import FilterEngine, parse_list_line

log = logging.getLogger("bipon.lists")

MAX_LIST_BYTES = 150 * 1024 * 1024
UPDATE_INTERVAL = 24 * 3600


class BlocklistManager:
    def __init__(self, db: Database, engine: FilterEngine):
        self.db = db
        self.engine = engine
        self.dir = DATA_DIR / "lists"
        self.dir.mkdir(parents=True, exist_ok=True)
        self._lock = asyncio.Lock()
        self.last_build: dict = {}
        self.updating = False
        self.on_failure = None

    def path_for(self, list_id: int) -> Path:
        return self.dir / f"{list_id}.txt"

    def seed_defaults(self) -> None:
        if self.db.scalar("SELECT COUNT(*) FROM blocklists"):
            return
        # todas las listas recomendadas aparecen DESACTIVADAS: el usuario elige en el registro cuáles activar
        for item in RECOMMENDED_LISTS:
            self.db.execute(
                "INSERT OR IGNORE INTO blocklists(url,name,category,enabled) VALUES (?,?,?,0)",
                (item["url"], item["name"], item["category"]),
            )

    async def rebuild(self) -> dict:
        lists = await self.db.aquery("SELECT id, category FROM blocklists WHERE enabled=1")
        rules = await self.db.aquery("SELECT value, kind, enabled FROM domain_rules")
        items = [(self.path_for(r["id"]), r["category"]) for r in lists]
        t0 = time.time()
        counts = await asyncio.to_thread(self.engine.build, items, rules)
        self.last_build = {**{k: v for k, v in counts.items() if k != "per_list"}, "ts": time.time(),
                           "seconds": round(time.time() - t0, 2)}
        log.info("motor de filtrado: %s dominios en %.2fs", counts["total"], time.time() - t0)
        return self.last_build

    async def download(self, row: dict, client: httpx.AsyncClient) -> tuple[bool, str, int]:
        url = row["url"]
        dest = self.path_for(row["id"])
        try:
            if url.startswith("file://"):
                src = Path(url[7:])
                data = await asyncio.to_thread(src.read_bytes)
            else:
                async with client.stream("GET", url, follow_redirects=True, timeout=90) as r:
                    if r.status_code != 200:
                        return False, f"HTTP {r.status_code}", 0
                    chunks, size = [], 0
                    async for chunk in r.aiter_bytes():
                        size += len(chunk)
                        if size > MAX_LIST_BYTES:
                            return False, "lista demasiado grande", 0
                        chunks.append(chunk)
                    data = b"".join(chunks)
            text = data.decode("utf-8", errors="ignore")
            count = sum(1 for line in text.splitlines() if parse_list_line(line))
            if count == 0:
                return False, "no contiene dominios válidos", 0
            tmp = dest.with_suffix(".tmp")
            await asyncio.to_thread(tmp.write_text, text, "utf-8")
            tmp.replace(dest)
            return True, "OK", count
        except Exception as exc:
            return False, f"{type(exc).__name__}: {exc}"[:200], 0

    async def update(self, list_id: int | None = None, rebuild: bool = True) -> list[dict]:
        async with self._lock:
            self.updating = True
            try:
                if list_id is None:
                    rows = await self.db.aquery("SELECT * FROM blocklists WHERE enabled=1")
                else:
                    rows = await self.db.aquery("SELECT * FROM blocklists WHERE id=?", (list_id,))
                results = []
                async with httpx.AsyncClient(headers={"user-agent": "Bipon Net/1.0 (+home network filter)"}) as client:
                    outs = await asyncio.gather(*(self.download(r, client) for r in rows))
                for row, (ok, msg, count) in zip(rows, outs):
                    if ok:
                        await self.db.aexecute(
                            "UPDATE blocklists SET domains=?, last_updated=?, last_status=? WHERE id=?",
                            (count, time.time(), "OK", row["id"]),
                        )
                    else:
                        await self.db.aexecute("UPDATE blocklists SET last_status=? WHERE id=?", (msg, row["id"]))
                        self.db.event("warning", "listas", f"No se pudo actualizar «{row['name']}»: {msg}")
                        if self.on_failure:
                            self.on_failure(row["name"], msg)
                    results.append({"id": row["id"], "name": row["name"], "ok": ok, "message": msg, "domains": count})
                if rebuild:
                    await self.rebuild()
                return results
            finally:
                self.updating = False

    async def scheduler(self) -> None:
        # primera carga desde disco (arranque instantáneo) y descarga si faltan ficheros
        await self.rebuild()
        missing = [r for r in self.db.query("SELECT id FROM blocklists WHERE enabled=1") if not self.path_for(r["id"]).exists()]
        if missing:
            await self.update()
        while True:
            oldest = self.db.scalar("SELECT MIN(last_updated) FROM blocklists WHERE enabled=1") or 0
            wait = max(60, UPDATE_INTERVAL - (time.time() - oldest))
            await asyncio.sleep(wait)
            try:
                await self.update()
            except Exception:  # pragma: no cover
                log.exception("actualización programada falló")

    def delete_file(self, list_id: int) -> None:
        try:
            self.path_for(list_id).unlink()
        except FileNotFoundError:
            pass
