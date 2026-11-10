"""Test de velocidad de Internet (red global de Cloudflare: nodo anycast más cercano)
y prueba de velocidad LAN (navegador <-> NetPing)."""
from __future__ import annotations

import asyncio
import os
import statistics
import time
from typing import AsyncIterator

import httpx

from .pinger import ping as icmp_ping
from .tools import public_info

BASE = "https://speed.cloudflare.com"
CHUNK = os.urandom(256 * 1024)
_LAN_BLOCK = os.urandom(1024 * 1024)


class SpeedTest:
    def __init__(self):
        self.lock = asyncio.Lock()

    @property
    def running(self) -> bool:
        return self.lock.locked()

    async def run(self, duration: float = 8.0, streams: int = 6) -> AsyncIterator[dict]:
        async with self.lock:
            async for ev in self._run(duration, streams):
                yield ev

    async def _run(self, duration: float, streams: int) -> AsyncIterator[dict]:
        limits = httpx.Limits(max_connections=streams + 2, max_keepalive_connections=streams + 2)
        headers = {"user-agent": "Mozilla/5.0 NetPing-Speedtest", "referer": "https://speed.cloudflare.com/"}
        async with httpx.AsyncClient(limits=limits, headers=headers, timeout=30) as c:
            yield {"phase": "start"}
            info = await public_info()
            # servidor (colo) más cercano
            try:
                r = await c.get(f"{BASE}/cdn-cgi/trace")
                kv = dict(line.split("=", 1) for line in r.text.splitlines() if "=" in line)
                info["colo"] = kv.get("colo", info.get("colo"))
            except Exception:
                pass
            yield {"phase": "info", "info": info}

            # ---- latencia ----
            lat: list[float] = []
            for i in range(21):
                t0 = time.perf_counter()
                try:
                    r = await c.get(f"{BASE}/__down", params={"bytes": 0})
                    total = (time.perf_counter() - t0) * 1000
                    # descontamos el tiempo de proceso del servidor (cabecera server-timing)
                    st = r.headers.get("server-timing", "")
                    srv = 0.0
                    if "dur=" in st:
                        try:
                            srv = float(st.split("dur=")[1].split(",")[0].split(";")[0])
                        except ValueError:
                            srv = 0.0
                    if i > 0:
                        lat.append(max(0.1, total - srv))
                        yield {"phase": "ping", "value": round(lat[-1], 2)}
                except Exception:
                    pass
            ping = round(statistics.median(lat), 2) if lat else None
            jitter = round(statistics.fmean(abs(lat[i] - lat[i - 1]) for i in range(1, len(lat))), 2) if len(lat) > 1 else None
            loss = round((20 - len(lat)) / 20 * 100, 1)
            yield {"phase": "ping_done", "ping": ping, "jitter": jitter}

            # latencia en reposo con ICMP (referencia para el bufferbloat)
            idle = await icmp_ping("1.1.1.1", count=5)
            idle_ms = idle["avg"] or ping

            # ---- descarga (midiendo latencia bajo carga) ----
            loaded: list[float] = []
            dl = await self._measure(c, "download", duration, streams, loaded)
            async for ev in dl:
                yield ev
            download = self._last
            # ---- subida ----
            ul = await self._measure(c, "upload", duration, max(2, streams // 2), loaded)
            async for ev in ul:
                yield ev
            upload = self._last
            bloat = None
            loaded_ms = None
            if loaded and idle_ms:
                loaded_ms = round(statistics.median(loaded), 2)
                bloat = round(max(0.0, loaded_ms - idle_ms), 2)
            yield {"phase": "done", "result": {
                "download": download, "upload": upload, "ping": ping, "jitter": jitter, "loss": loss,
                "idle_ping": idle_ms, "loaded_ping": loaded_ms, "bufferbloat": bloat, "grade": bloat_grade(bloat),
                "server": f"Cloudflare {info.get('colo') or ''}".strip(), "ip": info.get("ip"), "isp": info.get("isp"),
            }}

    async def _measure(self, c: httpx.AsyncClient, kind: str, duration: float, streams: int, loaded: list):
        counter = {"bytes": 0}
        stop = asyncio.Event()
        warmup = 1.0

        async def latency_worker():
            await asyncio.sleep(warmup)
            while not stop.is_set():
                r = await icmp_ping("1.1.1.1", count=1, timeout=1)
                if r["avg"] is not None:
                    loaded.append(r["avg"])
                await asyncio.sleep(0.3)

        async def down_worker():
            while not stop.is_set():
                try:
                    async with c.stream("GET", f"{BASE}/__down", params={"bytes": 50_000_000}) as r:
                        async for chunk in r.aiter_raw(65536):
                            counter["bytes"] += len(chunk)
                            if stop.is_set():
                                break
                except Exception:
                    await asyncio.sleep(0.2)

        async def up_worker():
            async def body():
                sent = 0
                while not stop.is_set() and sent < 50_000_000:
                    counter["bytes"] += len(CHUNK)
                    sent += len(CHUNK)
                    yield CHUNK
            while not stop.is_set():
                try:
                    await c.post(f"{BASE}/__up", content=body(), headers={"content-type": "application/octet-stream"})
                except Exception:
                    await asyncio.sleep(0.2)

        async def gen():
            worker = down_worker if kind == "download" else up_worker
            tasks = [asyncio.create_task(worker()) for _ in range(streams)]
            tasks.append(asyncio.create_task(latency_worker()))
            t0 = time.perf_counter()
            base_bytes, base_t = 0, None
            samples = []
            try:
                while True:
                    await asyncio.sleep(0.25)
                    el = time.perf_counter() - t0
                    if base_t is None and el >= warmup:
                        base_bytes, base_t = counter["bytes"], el
                    if base_t is not None and el > base_t:
                        mbps = (counter["bytes"] - base_bytes) * 8 / (el - base_t) / 1e6
                        samples.append(mbps)
                        yield {"phase": kind, "value": round(mbps, 2), "progress": min(1.0, el / (duration + warmup))}
                    if el >= duration + warmup:
                        break
            finally:
                stop.set()
                for t in tasks:
                    t.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
            self._last = round(samples[-1], 2) if samples else 0.0
            yield {"phase": f"{kind}_done", "value": self._last}
        return gen()


def bloat_grade(bloat: float | None) -> str | None:
    """Nota de bufferbloat (aumento de latencia bajo carga)."""
    if bloat is None:
        return None
    for limit, grade in ((5, "A+"), (30, "A"), (60, "B"), (200, "C"), (400, "D")):
        if bloat < limit:
            return grade
    return "F"


def lan_payload(size: int):
    size = max(1, min(size, 1024 * 1024 * 1024))

    async def gen():
        sent = 0
        while sent < size:
            n = min(len(_LAN_BLOCK), size - sent)
            yield _LAN_BLOCK[:n]
            sent += n
    return gen()
