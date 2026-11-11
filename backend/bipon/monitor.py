"""Monitor continuo de calidad de conexión y optimizador automático de DNS."""
from __future__ import annotations

import asyncio
import logging
import time

from dnslib import DNSRecord

from . import netinfo
from .catalog import DNS_PRESETS
from .pinger import ping
from .tools import dns_benchmark, optimizer_candidates

log = logging.getLogger("bipon.monitor")


class Monitor:
    def __init__(self, ctx):
        self.ctx = ctx
        self.last: dict = {}
        self.outage_since: float | None = None

    async def sample(self) -> dict:
        s = self.ctx.settings
        gw = netinfo.default_gateway()
        targets = [t for t in s.get("monitor.targets", ["1.1.1.1", "8.8.8.8"]) if t]
        jobs = {"gateway": ping(gw, 3)} if gw else {}
        for t in targets[:4]:
            jobs[t] = ping(t, 3)
        results = dict(zip(jobs.keys(), await asyncio.gather(*jobs.values(), return_exceptions=True)))
        # tiempo de resolución DNS real (a través de los upstream, sin caché)
        t0 = time.perf_counter()
        dns_ms = None
        try:
            await self.ctx.pool.resolve(DNSRecord.question(f"bipon-{int(time.time())}.example.com", "A").pack())
            dns_ms = round((time.perf_counter() - t0) * 1000, 2)
        except Exception:
            pass
        now = time.time()
        rows = []
        for k, r in results.items():
            if isinstance(r, Exception):
                continue
            rows.append((now, k, r["avg"], r["loss"]))
        rows.append((now, "dns", dns_ms, 0 if dns_ms is not None else 100))
        self.ctx.db.executemany("INSERT INTO monitor(ts,target,latency,loss) VALUES (?,?,?,?)", rows)
        inet = [r for k, r in results.items() if k != "gateway" and isinstance(r, dict)]
        down = bool(inet) and all(r["loss"] == 100 for r in inet)
        if down and self.outage_since is None:
            self.outage_since = now
            self.ctx.db.event("error", "conexión", "Se perdió la conexión a Internet")
            self.ctx.notify.emit("internet_down", "Sin conexión a Internet", "Bipon Net ha detectado una caída de Internet. Te avisará cuando vuelva.", key="inet")
        elif not down and self.outage_since is not None:
            dur = int(now - self.outage_since)
            self.ctx.db.event("warning", "conexión", f"Conexión a Internet restablecida tras {dur} s sin servicio")
            self.ctx.notify.emit("internet_up", "Internet restablecido", f"La conexión volvió tras {dur // 60} min {dur % 60} s sin servicio.", key="inet")
            self.outage_since = None
        self.last = {"ts": now, "results": {k: v for k, v in results.items() if isinstance(v, dict)}, "dns_ms": dns_ms,
                     "online": not down}
        return self.last

    async def loop(self) -> None:
        await asyncio.sleep(5)
        while True:
            if self.ctx.settings.get("monitor.enabled", True):
                try:
                    await self.sample()
                except Exception:  # pragma: no cover
                    log.exception("muestra de monitor falló")
            await asyncio.sleep(max(15, int(self.ctx.settings.get("monitor.interval", 60))))


async def optimize_dns(ctx, apply: bool = True) -> dict:
    """Mide los resolvers desde la ubicación del usuario y aplica los más rápidos."""
    cands = optimizer_candidates(ctx.unbound.running)
    results = await dns_benchmark(cands)
    ok = [r for r in results if r.get("median") is not None and r["reliability"] >= 95]
    chosen = None
    if ok and apply:
        best = ok[0]
        preset = best["id"]
        servers = list(DNS_PRESETS[preset]["servers"])
        # segundo mejor proveedor distinto como respaldo
        if len(ok) > 1:
            servers.append(DNS_PRESETS[ok[1]["id"]]["servers"][0])
        ctx.settings.update("dns", {"preset": "auto:" + preset, "upstreams": servers, "strategy": "fastest"})
        chosen = {"preset": preset, "name": best["name"], "servers": servers, "median": best["median"]}
        ctx.db.event("info", "dns", f"Optimizador: DNS más rápido desde tu ubicación → {best['name']} ({best['median']} ms)")
    auto = ctx.settings.get("dns.auto_optimize") or {}
    auto.update(last_run=time.time(), last_result=chosen)
    ctx.settings.set("dns.auto_optimize", auto, notify=False)
    ctx.apply_dns_settings()
    return {"results": results, "chosen": chosen}


async def optimizer_loop(ctx) -> None:
    await asyncio.sleep(60)
    while True:
        auto = ctx.settings.get("dns.auto_optimize") or {}
        if auto.get("enabled"):
            interval = max(1, int(auto.get("interval_hours", 24))) * 3600
            if time.time() - (auto.get("last_run") or 0) >= interval:
                try:
                    await optimize_dns(ctx)
                except Exception:  # pragma: no cover
                    log.exception("optimizador falló")
        await asyncio.sleep(600)
