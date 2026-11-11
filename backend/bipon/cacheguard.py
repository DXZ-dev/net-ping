"""Guardián de cachés: si la caché de Unbound o la de Bipon llegan al umbral (90 % por defecto),
se limpian solas para que nunca se saturen.

Unbound (por orden, comprobando entre pasos):
  1. flush_negative + flush_bogus + flush_requestlist  (respuestas negativas, falsas y peticiones atascadas)
  2. flush_zone .                                      (vaciado completo)
Después se vuelve a precalentar el DNS de juegos y streaming para no perder velocidad.
La caché propia de Bipon descarta primero lo caducado y luego lo menos usado hasta el 70 %.
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from collections import deque

log = logging.getLogger("bipon.cache")


def parse_size(v) -> int:
    m = re.match(r"^\s*(\d+(?:\.\d+)?)\s*([kmgKMG]?)", str(v or ""))
    if not m:
        return 0
    return int(float(m.group(1)) * {"": 1, "k": 1024, "m": 1024 ** 2, "g": 1024 ** 3}[m.group(2).lower()])


class CacheGuard:
    def __init__(self, ctx):
        self.ctx = ctx
        self.history: deque = deque(maxlen=50)
        self.last: dict = {}

    def cfg(self) -> dict:
        return {"enabled": True, "threshold": 90, **(self.ctx.settings.get("cache_guard") or {})}

    async def unbound_usage(self) -> dict | None:
        ub = self.ctx.unbound
        if not ub.running:
            return None
        st = await ub.stats()
        if not st:
            return None
        u = self.ctx.settings.get("unbound") or {}
        msg_max, rr_max = parse_size(u.get("msg_cache_size", "64m")), parse_size(u.get("rrset_cache_size", "128m"))
        msg = st.get("mem_cache_msg", 0) / msg_max * 100 if msg_max else 0
        rr = st.get("mem_cache_rrset", 0) / rr_max * 100 if rr_max else 0
        return {"pct": round(max(msg, rr), 1), "msg_pct": round(msg, 1), "rrset_pct": round(rr, 1),
                "msg_bytes": st.get("mem_cache_msg", 0), "rrset_bytes": st.get("mem_cache_rrset", 0), "msg_max": msg_max, "rrset_max": rr_max}

    async def check(self, force: bool = False) -> dict:
        c = self.cfg()
        thr = float(c["threshold"])
        out = {"ts": time.time(), "bipon": round(self.ctx.dns.cache.usage(), 1), "unbound": await self.unbound_usage()}
        # caché de Bipon
        if force or out["bipon"] >= thr:
            n = self.ctx.dns.cache.trim(70)
            self._record("Bipon", out["bipon"], round(self.ctx.dns.cache.usage(), 1), f"{n} entradas liberadas")
        # caché de Unbound
        u = out["unbound"]
        if u and (force or u["pct"] >= thr):
            before = u["pct"]
            ub = self.ctx.unbound
            for cmd in ("flush_negative", "flush_bogus", "flush_requestlist"):
                await ub.control(cmd)
            after = await self.unbound_usage()
            step = "limpieza ligera (negativas, falsas y atascadas)"
            if force or (after and after["pct"] >= thr - 5):
                await ub.control("flush_zone", ".")
                after = await self.unbound_usage()
                step = "vaciado completo"
            self._record("Unbound", before, after["pct"] if after else 0, step)
            try:
                await self.ctx.gaming.warm()  # juegos y streaming vuelven a estar en caché al momento
            except Exception:
                pass
        self.last = out
        return out

    def _record(self, which: str, before: float, after: float, detail: str) -> None:
        self.history.appendleft({"ts": time.time(), "cache": which, "before": before, "after": after, "detail": detail})
        msg = f"Caché de {which} al {before:.0f} %: {detail} → {after:.0f} %"
        log.info(msg)
        self.ctx.db.event("info", "caché", msg)

    async def loop(self) -> None:
        await asyncio.sleep(90)
        while True:
            try:
                if self.cfg()["enabled"]:
                    await self.check()
            except Exception as exc:  # pragma: no cover
                log.warning("guardián de caché: %s", exc)
            await asyncio.sleep(60)

    def status(self) -> dict:
        return {"config": self.cfg(), "last": self.last, "history": list(self.history)[:20]}
