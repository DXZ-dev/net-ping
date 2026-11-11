"""Contexto de la aplicación: crea, conecta y arranca todos los subsistemas."""
from __future__ import annotations

import asyncio
import logging
import time

from . import auth, region, tls
from .advisor import Advisor
from .autodns import AutoDNS
from .blocklists import BlocklistManager
from .catalog import DNS_PRESETS
from .config import DATA_DIR, init_settings
from .db import init_db
from .devices import DeviceManager
from .dhcp import DHCPServer
from .dns.cache import DNSCache
from .dns.filter import FilterEngine
from .dns.server import DNSServer
from .dns.upstream import UpstreamPool
from .family import FamilyManager
from .gaming import GamingManager
from .integrations import WifiManager
from .monitor import Monitor, optimizer_loop
from .notify import Notifier
from .router import RouterManager
from .routerctl import RouterControl
from .configprofiles import ConfigProfiles
from .captive import CaptivePortal
from .access import AccessControl
from .packages import Installer
from .updater import Updater
from .scheduler import Scheduler
from .security import SecurityManager
from .speedtest import SpeedTest
from .sync import SyncManager
from .unbound import UnboundManager
from .vpn import VPNManager

log = logging.getLogger("bipon")


class Context:
    def __init__(self):
        self.settings = init_settings()
        region.apply_timezone((self.settings.get("region") or {}).get("timezone"))
        self.db = init_db(DATA_DIR / "netping.db")
        auth.migrate_legacy()
        auth.apply_env_password()
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
        self.notify = Notifier(self)
        self.advisor = Advisor(self)
        self.gaming = GamingManager(self)
        self.family = FamilyManager(self)
        self.security = SecurityManager(self)
        self.dhcp = DHCPServer(self)
        self.autodns = AutoDNS(self)
        self.scheduler = Scheduler(self)
        self.sync = SyncManager(self)
        self.wifi = WifiManager(self)
        self.router = RouterManager(self)
        from .liverates import LiveRates
        self.rates = LiveRates(self)
        from .speedctl import SpeedControl
        self.speedctl = SpeedControl(self)
        from .lagguard import LagGuard
        self.lag = LagGuard(self)
        from .cacheguard import CacheGuard
        self.cacheguard = CacheGuard(self)
        self.routerctl = RouterControl(self)
        self.configs = ConfigProfiles(self)
        self.updater = Updater(self)
        self.captive = CaptivePortal(self)
        self.access = AccessControl(self)
        self.installer = Installer(self)
        self.dns_error: str | None = None
        self.upstream_fallback = False
        self.anonymize = bool((self.settings.get("privacy") or {}).get("anonymize_clients"))
        self.tasks: list[asyncio.Task] = []
        self.started = time.time()

        # cableado del servidor DNS
        self.dns.policy_for = self.devices.policy_for
        self.dns.ptr_name = self.devices.ptr_name
        self.dns.self_ip = lambda: self.devices.self_ip
        self.dns.on_query = self._log_query
        self.dns.on_client = self.devices.track_client
        self.dns.on_usage = self.family.record
        self.dns.on_threat = self.security.threat_hit
        self.dns.name_lookup = self.dhcp.lookup_name
        self.devices.family = self.family
        self.devices.on_new_device = self._new_device
        self.devices.on_policy_change = self.apply_policy_now
        self.dns.captive_check = self.captive.accepted
        self.dns.access_check = self.access.allowed
        self.lists.on_failure = lambda name, msg: self.notify.emit("lists_failed", "No se pudo actualizar una lista",
                                                                   f"{name}: {msg}", key=name)
        self.unbound.on_crash = lambda msg: self.notify.emit("dns_down", "Unbound se detuvo", f"{msg}. Bipon Net lo está reiniciando.", key="unbound")
        self.settings.on_change(self._on_settings)

    # ---- eventos --------------------------------------------------------------
    def apply_policy_now(self, macs: list[str] | None) -> None:
        """Control de Internet inmediato: políticas, caché y firewall (modo router)."""
        self.devices.invalidate()
        try:
            asyncio.get_running_loop().create_task(self.router.enforce_now(macs))
        except RuntimeError:
            pass

    def _log_query(self, row: tuple) -> None:
        self.lag.observe(row[1], row[2])
        mac = self.devices.mac_for(row[1])  # el historial se guarda por MAC: si el equipo cambia de IP sigue siendo el mismo
        if self.anonymize:
            import hashlib
            h = lambda v: "anon-" + hashlib.sha256((self.settings.get("secret") + v).encode()).hexdigest()[:8]  # noqa: E731
            row = (row[0], h(row[1]), *row[2:8])
            mac = h(mac) if mac else None
        self.db.log_query((*row[:8], mac))

    def _new_device(self, d: dict) -> None:
        if not self.devices.last_scan:  # el primer escaneo descubre toda la red: no se avisa de cada equipo
            return
        name = d.get("name") or d.get("hostname") or d.get("vendor") or d.get("ip") or d.get("mac")
        detail = " · ".join(x for x in (d.get("ip"), d.get("vendor"), d.get("mac")) if x)
        self.notify.emit("device_new", "Nuevo dispositivo en tu red", f"{name}" + (f" ({detail})" if detail else "") +
                         ". Si no lo reconoces, puedes bloquearlo desde Bipon Net.", data=d, key=d.get("mac") or name)

    # ---- aplicar configuración ----------------------------------------------
    def _on_settings(self, section: str) -> None:
        if section in ("dns", "*"):
            self.apply_dns_settings()
        if section in ("blocking", "blocked_services", "*"):
            self.apply_blocking()
        if section in ("gaming", "*"):
            self.gaming.apply()
        if section in ("security", "*"):
            self.security.apply()
        if section in ("profiles", "*"):
            self.devices.invalidate()
        if section in ("access", "*"):
            self.access.reload()
        if section in ("privacy", "*"):
            self.anonymize = bool((self.settings.get("privacy") or {}).get("anonymize_clients"))

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
        self.dns.prefer_ipv4 = bool(d.get("prefer_ipv4", False))
        self.dns.prefetch = bool(d.get("prefetch", True))
        self.dns.cf = d.get("conditional_forwarding") or {}
        self.dns.set_local_records(d.get("local_records") or [])
        dom = ((self.settings.get("dhcp") or {}).get("domain") or "lan").lower()
        self.dns.local_tlds = {"lan", "home", "local", "internal", "localdomain", dom.rsplit(".", 1)[-1]}

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
        await self.security.start()
        d = self.settings.get("dns")
        try:
            await self.dns.start(d.get("listen", "0.0.0.0"), int(d.get("port", 53)))
            self.dns_error = None
        except OSError as exc:
            self.dns_error = (f"No se pudo abrir el puerto DNS {d.get('port')}: {exc}. "
                              "¿Hay otro servicio usando el puerto 53 (systemd-resolved, dnsmasq, Pi-hole)?")
            log.error(self.dns_error)
            self.db.event("error", "dns", self.dns_error)
        t = self.settings.get("tls") or {}
        if t.get("enabled", True) and t.get("dot", True):
            try:
                tls.generate_self_signed()
                dot_ctx = tls.server_context()
                tls.register_context(dot_ctx)  # se recarga en caliente al renovar el certificado
                await self.dns.start_dot(int(t.get("dot_port", 853)), dot_ctx)
            except Exception as exc:
                log.warning("DNS-over-TLS no disponible: %s", exc)
        self._bg(self._flush_loop(), "flush")
        self._bg(self._maintenance_loop(), "mantenimiento")
        self._bg(self._cert_loop(), "certificados")
        self._bg(self._blocking_loop(), "bloqueo")
        self._bg(self.lists.scheduler(), "listas")
        self._bg(self.devices.scheduler(), "dispositivos")
        self._bg(self.monitor.loop(), "monitor")
        self._bg(optimizer_loop(self), "optimizador")
        self._bg(self.vpn.start(), "vpn")
        self._bg(self.gaming.loop(), "gaming")
        self._bg(self.family.loop(), "tiempo de pantalla")
        self._bg(self.security.loop(), "seguridad")
        self._bg(self.dhcp.loop(), "dhcp")
        self._bg(self.autodns.loop(), "modo automático")
        self._bg(self.scheduler.loop(), "programador")
        self._bg(self.sync.loop(), "sincronización")
        self._bg(self.wifi.loop(), "wifi")
        self._bg(self.router.loop(), "router")
        self._bg(self.rates.loop(), "velocidad en vivo")
        self._bg(self.lag.loop(), "vigilante de lag")
        self._bg(self.cacheguard.loop(), "guardián de caché")
        self._bg(self.updater.loop(), "actualizaciones")
        self._bg(self.captive.loop(), "portal cautivo")
        self.db.event("info", "sistema", "Bipon Net iniciado")

    async def stop(self) -> None:
        # primero se retira el anuncio del modo automático: los equipos vuelven al DNS del router al instante
        try:
            await asyncio.wait_for(self.autodns.withdraw(), 5)
        except Exception:
            pass
        for t in self.tasks:
            t.cancel()
        await self.dhcp.stop()
        await self.dns.stop()
        await self.unbound.stop()
        await self.vpn.shutdown()
        await self.pool.close()
        await self.notify.close()
        self.family.flush()
        self.db.flush_queries()

    async def _flush_loop(self):
        while True:
            await asyncio.sleep(1)
            try:
                while await asyncio.to_thread(self.db.flush_queries) >= 5000:
                    pass
            except Exception:  # pragma: no cover
                log.exception("no se pudo guardar el registro de consultas")

    async def _cert_loop(self) -> None:
        """Renueva solo el certificado del panel firmado por la CA local (caducidad o IP nueva)."""
        await asyncio.sleep(60)
        while True:
            try:
                extra = (self.settings.get("tls") or {}).get("extra_names") or []
                if tls.needs_renewal(extra):
                    tls.issue_bipon(extra)
                    self.db.event("info", "seguridad", "Certificado HTTPS del panel renovado por la CA local")
                for c in tls.issued():
                    if c["days_left"] < 30 and not c.get("warned"):
                        self.db.event("warning", "seguridad", f"El certificado «{c['label']}» caduca en {c['days_left']} días: renuévalo en Usuarios y acceso")
            except Exception as exc:  # pragma: no cover
                log.warning("certificados: %s", exc)
            await asyncio.sleep(6 * 3600)

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
