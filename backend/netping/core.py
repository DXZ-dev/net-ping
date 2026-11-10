"""Contexto de la aplicación: crea, conecta y arranca todos los subsistemas."""
from __future__ import annotations

import asyncio
import logging
import time

from . import auth
from .advisor import Advisor
from .blocklists import BlocklistManager
from .catalog import DNS_PRESETS
from .config import DATA_DIR, init_settings
from .db import init_db
from .devices import DeviceManager
from .gaming import GamingManager
from .dns.cache import DNSCache
from .dns.filter import FilterEngine
from .dns.server import DNSServer
from .dns.upstream import UpstreamPool
from .monitor import Monitor, optimizer_loop
from .speedtest import SpeedTest
from .unbound import UnboundManager
from .vpn import VPNManager

log = logging.getLogger("netping")


class Context:
    def __init__(self):
        self.settings = init_settings()
        auth.apply_env_password()
        self.db = init_db(DATA_DIR / "netping.db")
        self.engine = FilterEngine()
        self.pool = UpstreamPool()
        self.cache = DNSCache()
        self.dns = DNSServer(self.engine, self.pool, self.cache)
        self.lists = BlocklistManager(self.db, self.engine)
        self.devices = DeviceManager(self.db, self.settings)
        self.unbound = UnboundManager(self.settings)
        self.monitor = Monitor(self)
        self.speedtest = SpeedTest()
        self.vpn = VPNManager()
        self.advisor = Advisor(self)
        self.gaming = GamingManager(self)
        self.dns_error: str | None = None
        self.upstream_fallback = False
        self.tasks: list[asyncio.Task] = []
        self.started = time.time()

        # cableado del servidor DNS
        self.dns.policy_for = self.devices.policy_for
        self.dns.ptr_name = self.devices.ptr_name
        self.dns.self_ip = lambda: self.devices.self_ip
        self.dns.on_query = self.db.log_query
        self.dns.on_client = self.devices.track_client
        self.settings.on_change(self._on_settings)

    # ---- aplicar configuración ----------------------------------------------
    def _on_settings(self, section: str) -> None:
        if section in ("dns", "*"):
            self.apply_dns_settings()
        if section in ("blocking", "blocked_services", "*"):
            self.apply_blocking()
        if section in ("gaming", "*"):
            self.gaming.apply()
        if section in ("profiles", "*"):
            self.devices.invalidate()

    def apply_dns_settings(self) -> None:
        d = self.settings.get("dns")
        ups = list(d.get("upstreams") or [])
        unbound_spec = f"127.0.0.1:{self.settings.get('unbound.port', 5335)}"
        self.upstream_fallback = False
        if any(u.startswith("127.0.0.1:5335") or u == unbound_spec for u in ups) and not self.unbound.available:
            # Unbound no está instalado: se usa Cloudflare para no dejar la red sin DNS
            ups = [u for u in ups if u != unbound_spec] or list(DNS_PRESETS["cloudflare"]["servers"])
            self.upstream_fallback = True
        self.pool.configure(ups, d.get("strategy", "fastest"), int(d.get("timeout_ms", 2000)))
        self.cache.configure(int(d.get("cache_size", 20000)), int(d.get("cache_min_ttl", 0)),
                             int(d.get("cache_max_ttl", 86400)), bool(d.get("optimistic_cache", True)))
        self.dns.blocking_mode = d.get("blocking_mode", "null")
        self.dns.block_ttl = int(d.get("block_ttl", 60))
        self.dns.log_queries = bool(d.get("log_queries", True))
        self.dns.block_canary = bool(d.get("block_doh_canary", True))
        self.dns.block_icloud = bool(d.get("block_icloud_relay", False))
        self.dns.prefetch = bool(d.get("prefetch", True))
        self.dns.cf = d.get("conditional_forwarding") or {}
        self.dns.set_local_records(d.get("local_records") or [])

    def apply_blocking(self) -> None:
        b = self.settings.get("blocking")
        self.dns.blocking_enabled = bool(b.get("enabled", True)) and (b.get("paused_until") or 0) < time.time()
        self.dns.global_services = set(self.settings.get("blocked_services") or [])

    # ---- ciclo de vida ------------------------------------------------------
    def _bg(self, coro, name: str):
        t = asyncio.create_task(coro, name=name)
        self.tasks.append(t)
        return t

    async def start(self) -> None:
        self.lists.seed_defaults()
        await self.unbound.start()
        self.apply_dns_settings()
        self.apply_blocking()
        self.gaming.apply()
        d = self.settings.get("dns")
        try:
            await self.dns.start(d.get("listen", "0.0.0.0"), int(d.get("port", 53)))
            self.dns_error = None
        except OSError as exc:
            self.dns_error = (f"No se pudo abrir el puerto DNS {d.get('port')}: {exc}. "
                              "¿Hay otro servicio usando el puerto 53 (systemd-resolved, dnsmasq, Pi-hole)?")
            log.error(self.dns_error)
            self.db.event("error", "dns", self.dns_error)
        self._bg(self._flush_loop(), "flush")
        self._bg(self._maintenance_loop(), "mantenimiento")
        self._bg(self._blocking_loop(), "bloqueo")
        self._bg(self.lists.scheduler(), "listas")
        self._bg(self.devices.scheduler(), "dispositivos")
        self._bg(self.monitor.loop(), "monitor")
        self._bg(optimizer_loop(self), "optimizador")
        self._bg(self.vpn.start(), "vpn")
        self._bg(self.gaming.loop(), "gaming")
        self.db.event("info", "sistema", "NetPing iniciado")

    async def stop(self) -> None:
        for t in self.tasks:
            t.cancel()
        await self.dns.stop()
        await self.unbound.stop()
        await self.vpn.shutdown()
        await self.pool.close()
        self.db.flush_queries()

    async def _flush_loop(self):
        while True:
            await asyncio.sleep(1)
            try:
                while await asyncio.to_thread(self.db.flush_queries) >= 5000:
                    pass
            except Exception:  # pragma: no cover
                log.exception("no se pudo guardar el registro de consultas")

    async def _maintenance_loop(self):
        while True:
            await asyncio.sleep(3600)
            await asyncio.to_thread(self.db.purge, int(self.settings.get("retention_days", 30)))

    async def _blocking_loop(self):
        """Reanuda el bloqueo al terminar una pausa y refresca políticas horarias."""
        last_min = -1
        while True:
            await asyncio.sleep(2)
            self.apply_blocking()
            m = time.localtime().tm_min
            if m != last_min:
                last_min = m
                self.devices.invalidate()
