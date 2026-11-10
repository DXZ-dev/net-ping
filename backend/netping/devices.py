"""Descubrimiento de dispositivos, perfiles y controles parentales por dispositivo."""
from __future__ import annotations

import asyncio
import ipaddress
import logging
import socket
import time
from datetime import datetime

from dnslib import DNSRecord, QTYPE

from . import netinfo
from .config import DEFAULT_PROFILE_ID, Settings
from .db import Database
from .dns.filter import Policy
from .dns.upstream import query_server
from .pinger import ping

log = logging.getLogger("netping.devices")

ONLINE_WINDOW = 600  # s sin verse -> desconectado
MAX_HOSTS_PER_SUBNET = 1024


def _hhmm(s: str) -> int:
    h, m = s.split(":")
    return int(h) * 60 + int(m)


def schedule_active(entry: dict, now: datetime | None = None) -> bool:
    """Ventana horaria activa; soporta ventanas que cruzan la medianoche (22:00-07:00)."""
    now = now or datetime.now()
    try:
        start, end = _hhmm(entry["start"]), _hhmm(entry["end"])
    except Exception:
        return False
    days = entry.get("days") or list(range(7))
    minute = now.hour * 60 + now.minute
    wd = now.weekday()
    if start <= end:
        return wd in days and start <= minute < end
    # cruza medianoche: la parte de después de medianoche pertenece al día anterior
    if minute >= start:
        return wd in days
    if minute < end:
        return (wd - 1) % 7 in days
    return False


class DeviceManager:
    def __init__(self, db: Database, settings: Settings):
        self.db = db
        self.settings = settings
        self.oui = netinfo.OUIDatabase()
        self.ip_to_mac: dict[str, str] = {}
        self.devices: dict[str, dict] = {}
        self.activity: dict[str, float] = {}
        self._policy_cache: dict[str, tuple[float, Policy]] = {}
        self.scanning = False
        self.last_scan = 0.0
        self.gateway: str | None = None
        self.self_ip: str | None = None
        self.load()

    # ---- estado ---------------------------------------------------------------
    def load(self) -> None:
        rows = self.db.query("SELECT * FROM devices")
        self.devices = {r["mac"]: r for r in rows}
        self.ip_to_mac = {r["ip"]: r["mac"] for r in rows if r["ip"]}
        self._policy_cache.clear()

    def invalidate(self) -> None:
        self._policy_cache.clear()

    def track_client(self, ip: str) -> None:
        self.activity[ip] = time.time()

    def ptr_name(self, ip: str) -> str | None:
        mac = self.ip_to_mac.get(ip)
        d = self.devices.get(mac) if mac else None
        if not d:
            return None
        name = d.get("name") or d.get("hostname")
        if not name:
            return None
        slug = "".join(c if c.isalnum() or c == "-" else "-" for c in name.lower()).strip("-")
        return f"{slug}.lan" if slug and "." not in slug else name

    # ---- políticas ------------------------------------------------------------
    def profiles(self) -> dict[str, dict]:
        return {p["id"]: p for p in self.settings.get("profiles", [])}

    def policy_for(self, ip: str) -> Policy:
        now = time.time()
        hit = self._policy_cache.get(ip)
        if hit and now - hit[0] < 15:
            return hit[1]
        mac = self.ip_to_mac.get(ip)
        dev = self.devices.get(mac) if mac else None
        profiles = self.profiles()
        prof = profiles.get((dev or {}).get("profile") or DEFAULT_PROFILE_ID) or profiles.get(DEFAULT_PROFILE_ID) or {}
        services = set(prof.get("blocked_services", []))
        block_all, reason = bool(prof.get("block_all")), "Perfil sin Internet" if prof.get("block_all") else ""
        for sch in prof.get("schedules", []):
            if not sch.get("enabled", True) or not schedule_active(sch):
                continue
            if sch.get("action", "block_all") == "block_all":
                block_all, reason = True, f"Horario: {sch.get('label') or sch['start'] + '–' + sch['end']}"
            else:
                services |= set(sch.get("services", []))
        if dev:
            if dev.get("paused") or (dev.get("paused_until") or 0) > now:
                block_all, reason = True, "Internet pausado para este dispositivo"
        pol = Policy(
            profile_id=prof.get("id", DEFAULT_PROFILE_ID), profile_name=prof.get("name", "Predeterminado"),
            filtering=prof.get("filtering", True), threat=prof.get("threat_protection", True),
            adult=prof.get("adult_filter", False), safesearch=prof.get("safesearch", False),
            youtube=prof.get("youtube_restricted", False), services=frozenset(services),
            block_all=block_all, block_reason=reason, device=(dev or {}).get("name") or "",
        )
        self._policy_cache[ip] = (now, pol)
        return pol

    # ---- descubrimiento -------------------------------------------------------
    async def _probe(self, hosts: list[str]) -> None:
        """Envía un datagrama UDP a cada IP: obliga al kernel a resolver ARP (barrido sin privilegios)."""
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setblocking(False)
        try:
            for i, h in enumerate(hosts):
                try:
                    sock.sendto(b"\x00", (h, 9))
                except OSError:
                    pass
                if i % 64 == 63:
                    await asyncio.sleep(0.02)
        finally:
            sock.close()

    async def _ptr_lookup(self, ip: str, servers: list[str]) -> str:
        name = ipaddress.ip_address(ip).reverse_pointer
        q = DNSRecord.question(name, "PTR").pack()
        for srv in servers:
            try:
                resp, _ = await query_server(srv, q, timeout=1.0)
                rec = DNSRecord.parse(resp)
                for rr in rec.rr:
                    if rr.rtype == QTYPE.PTR:
                        return str(rr.rdata).rstrip(".")
            except Exception:
                continue
        return ""

    async def scan(self) -> dict:
        if self.scanning:
            return {"status": "en curso"}
        self.scanning = True
        t0 = time.time()
        try:
            ifaces = await asyncio.to_thread(netinfo.interfaces)
            self.gateway = netinfo.default_gateway()
            self.self_ip = ifaces[0]["ip"] if ifaces else netinfo.primary_ip()
            subnets = self.settings.get("devices.subnets") or [i["network"] for i in ifaces]
            hosts: list[str] = []
            for sn in subnets:
                try:
                    net = ipaddress.ip_network(sn, strict=False)
                except ValueError:
                    continue
                if net.num_addresses > MAX_HOSTS_PER_SUBNET + 2:
                    own = next((ipaddress.ip_address(i["ip"]) for i in ifaces if ipaddress.ip_address(i["ip"]) in net), None)
                    net = ipaddress.ip_network(f"{own}/24", strict=False) if own else None
                if net:
                    hosts += [str(h) for h in net.hosts()]
            await self._probe(hosts)
            await asyncio.sleep(2.5)
            neigh = await asyncio.to_thread(netinfo.neighbors)
            now = time.time()
            seen: dict[str, str] = {}
            for n in neigh:
                if n["state"] in ("FAILED", "INCOMPLETE"):
                    continue
                if n["state"] == "STALE" and now - self.activity.get(n["ip"], 0) > ONLINE_WINDOW:
                    # entrada antigua de la caché ARP que no respondió al sondeo
                    continue
                seen[n["mac"]] = n["ip"]
            # host propio
            for itf in ifaces:
                if itf.get("mac"):
                    seen.setdefault(itf["mac"].lower(), itf["ip"])

            dns_servers = []
            if self.settings.get("dns.conditional_forwarding.enabled") and self.settings.get("dns.conditional_forwarding.server"):
                dns_servers.append(self.settings.get("dns.conditional_forwarding.server"))
            if self.gateway:
                dns_servers.append(self.gateway)
            resolve = self.settings.get("devices.resolve_names", True)

            async def enrich(mac: str, ip: str):
                existing = self.devices.get(mac) or {}
                hostname = existing.get("hostname") or ""
                if resolve and (not hostname or existing.get("ip") != ip):
                    hostname = await self._ptr_lookup(ip, dns_servers) or hostname
                pr = await ping(ip, count=1, timeout=1)
                return mac, ip, hostname, pr["avg"]

            results = await asyncio.gather(*(enrich(m, ip) for m, ip in seen.items()))
            new_devices = []
            for mac, ip, hostname, lat in results:
                vendor = self.oui.lookup(mac)
                existing = self.devices.get(mac)
                if existing is None:
                    kind = netinfo.guess_kind(vendor, hostname)
                    if ip == self.gateway:
                        kind = "router"
                    if ip == self.self_ip:
                        kind = "server"
                    self.db.execute(
                        "INSERT INTO devices(mac,ip,hostname,vendor,kind,profile,first_seen,last_seen,online,latency) VALUES (?,?,?,?,?,?,?,?,1,?)",
                        (mac, ip, hostname, vendor, kind, DEFAULT_PROFILE_ID, now, now, lat),
                    )
                    new_devices.append(hostname or ip)
                else:
                    self.db.execute(
                        "UPDATE devices SET ip=?, hostname=?, vendor=COALESCE(NULLIF(?,''),vendor), last_seen=?, online=1, latency=? WHERE mac=?",
                        (ip, hostname, vendor, now, lat, mac),
                    )
                # una IP sólo pertenece a un dispositivo
                self.db.execute("UPDATE devices SET ip=NULL WHERE ip=? AND mac<>?", (ip, mac))
            # clientes DNS activos que no aparecen en ARP siguen considerándose conectados
            active_ips = {ip for ip, ts in self.activity.items() if now - ts < ONLINE_WINDOW}
            for mac, d in self.devices.items():
                if mac not in seen and d.get("ip") not in active_ips and d.get("online"):
                    self.db.execute("UPDATE devices SET online=0 WHERE mac=?", (mac,))
            for ip in active_ips:
                self.db.execute("UPDATE devices SET last_seen=?, online=1 WHERE ip=?", (self.activity[ip], ip))
            for name in new_devices:
                self.db.event("info", "dispositivos", f"Nuevo dispositivo detectado: {name}")
            self.load()
            self.last_scan = time.time()
            return {"found": len(seen), "new": len(new_devices), "seconds": round(time.time() - t0, 1)}
        finally:
            self.scanning = False

    async def scheduler(self) -> None:
        await self.oui.ensure()
        while True:
            try:
                await self.scan()
            except Exception:  # pragma: no cover
                log.exception("escaneo de red falló")
            await asyncio.sleep(max(60, int(self.settings.get("devices.scan_interval", 300))))

    # ---- acciones -------------------------------------------------------------
    def update(self, mac: str, patch: dict) -> dict | None:
        allowed = {"name", "kind", "profile", "paused", "paused_until", "notes"}
        fields = {k: v for k, v in patch.items() if k in allowed}
        if fields:
            sets = ", ".join(f"{k}=?" for k in fields)
            self.db.execute(f"UPDATE devices SET {sets} WHERE mac=?", (*fields.values(), mac))
            self.load()
        return self.devices.get(mac)

    def delete(self, mac: str) -> None:
        self.db.execute("DELETE FROM devices WHERE mac=?", (mac,))
        self.load()

    @staticmethod
    def wake_on_lan(mac: str, broadcast: str = "255.255.255.255") -> None:
        h = mac.replace(":", "").replace("-", "")
        if len(h) != 12:
            raise ValueError("MAC inválida")
        packet = b"\xff" * 6 + bytes.fromhex(h) * 16
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        try:
            for port in (9, 7):
                s.sendto(packet, (broadcast, port))
        finally:
            s.close()
