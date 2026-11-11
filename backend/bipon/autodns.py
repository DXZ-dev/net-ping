"""Modo automático: los dispositivos usan Bipon Net como DNS sin tocar el router.

Funcionamiento (estándar, RFC 4861 + RFC 8106):
  * Bipon Net se asigna una dirección IPv6 privada (ULA, fdXX::53) en la LAN.
  * Envía cada 20 s un Router Advertisement con:
      - router lifetime = 0  → NO se anuncia como puerta de enlace (el tráfico
        sigue yendo por el router de siempre);
      - opción Prefix Information del prefijo ULA (los equipos se crean una
        dirección privada en él para poder hablar con Bipon Net);
      - opción RDNSS con la IP de Bipon Net y una vida corta (90 s).
  * Windows, Android, iOS, macOS y Linux añaden Bipon Net como servidor DNS.

Tolerancia a fallos:
  * Un vigilante consulta el propio DNS cada 10 s; si no responde, deja de
    anunciarse y envía la retirada (vida 0) → los equipos vuelven al DNS del router.
  * Al apagar Bipon Net se envía la retirada inmediatamente.
  * Si Bipon Net se cuelga, el anuncio caduca solo en ≤ 90 s.
"""
from __future__ import annotations

import asyncio
import ipaddress
import json
import logging
import os
import socket
import struct
import time

from dnslib import DNSRecord

from . import netinfo
from .config import DATA_DIR

log = logging.getLogger("bipon.autodns")

INTERVAL = 20
RDNSS_LIFETIME = 90
PREFIX_LIFETIME = 1800
ALL_NODES = "ff02::1"


def _ula_prefix() -> ipaddress.IPv6Network:
    path = DATA_DIR / "autodns.json"
    if path.exists():
        try:
            return ipaddress.IPv6Network(json.loads(path.read_text())["prefix"])
        except Exception:
            pass
    gid = os.urandom(5)  # RFC 4193: fd + 40 bits aleatorios
    net = ipaddress.IPv6Network((int.from_bytes(b"\xfd" + gid + b"\x00\x01", "big") << 64, 64))
    path.write_text(json.dumps({"prefix": str(net)}))
    return net


def build_ra(prefix: ipaddress.IPv6Network, dns: list[str], rdnss_lifetime: int, prefix_lifetime: int,
             mac: str | None, search: str | None) -> bytes:
    # cabecera RA: type, code, checksum (lo calcula el kernel), cur hop limit, flags, router lifetime, reachable, retrans
    pkt = struct.pack("!BBHBBHII", 134, 0, 0, 64, 0, 0, 0, 0)
    if mac:
        lla = bytes.fromhex(mac.replace(":", ""))
        pkt += struct.pack("!BB", 1, 1) + lla  # Source Link-Layer Address
    # Prefix Information: L (on-link) + A (SLAAC)
    pkt += struct.pack("!BBBBIII", 3, 4, prefix.prefixlen, 0xC0, prefix_lifetime, prefix_lifetime, 0) + prefix.network_address.packed
    # RDNSS (RFC 8106)
    addrs = b"".join(ipaddress.IPv6Address(a).packed for a in dns)
    pkt += struct.pack("!BBHI", 25, 1 + 2 * len(dns), 0, rdnss_lifetime) + addrs
    if search:
        labels = b"".join(bytes([len(p)]) + p.encode() for p in search.strip(".").split(".")) + b"\x00"
        labels += b"\x00" * (-(len(labels)) % 8)
        pkt += struct.pack("!BBHI", 31, 1 + len(labels) // 8, 0, rdnss_lifetime) + labels
    return pkt


class AutoDNS:
    def __init__(self, ctx):
        self.ctx = ctx
        self.prefix: ipaddress.IPv6Network | None = None
        self.address: str | None = None
        self.iface: str | None = None
        self.sock: socket.socket | None = None
        self.announcing = False
        self.healthy = False
        self.sent = 0
        self.last_sent = 0.0
        self.error = ""
        self._task: asyncio.Task | None = None
        self._added_addr = False
        self.mac: str | None = None

    def cfg(self) -> dict:
        c = self.ctx.settings.get("autodns") or {}
        c.setdefault("enabled", False)
        c.setdefault("interface", "")
        c.setdefault("search_domain", "lan")
        return c

    # ---------------------------------------------------------------- preparación
    async def _run(self, *args) -> tuple[int, str]:
        p = await asyncio.create_subprocess_exec(*args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
        out, _ = await p.communicate()
        return p.returncode or 0, out.decode(errors="ignore")

    async def _setup(self) -> None:
        ifs = netinfo.interfaces()
        want = self.cfg()["interface"]
        itf = next((i for i in ifs if i["name"] == want), None) if want else (ifs[0] if ifs else None)
        if not itf or itf["name"] == "?":
            raise RuntimeError("No se encontró la interfaz de red de la LAN")
        self.iface, self.mac = itf["name"], itf.get("mac")
        self.prefix = _ula_prefix()
        self.address = str(self.prefix.network_address + 0x53)
        code, out = await self._run("ip", "-6", "addr", "replace", f"{self.address}/64", "dev", self.iface, "nodad")
        if code != 0:
            raise RuntimeError(f"No se pudo asignar {self.address} a {self.iface}: {out.strip()} (¿falta NET_ADMIN o IPv6 desactivado?)")
        self._added_addr = True
        s = socket.socket(socket.AF_INET6, socket.SOCK_RAW, socket.IPPROTO_ICMPV6)
        s.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_MULTICAST_HOPS, 255)
        s.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_UNICAST_HOPS, 255)
        s.setsockopt(socket.SOL_SOCKET, 25, self.iface.encode())  # SO_BINDTODEVICE
        s.setblocking(False)
        self.sock = s

    def _send(self, rdnss_lifetime: int, prefix_lifetime: int) -> None:
        if not self.sock:
            return
        pkt = build_ra(self.prefix, [self.address], rdnss_lifetime, prefix_lifetime, self.mac, self.cfg().get("search_domain"))
        idx = socket.if_nametoindex(self.iface)
        self.sock.sendto(pkt, (ALL_NODES, 0, 0, idx))
        self.sent += 1
        self.last_sent = time.time()

    async def _dns_ok(self) -> bool:
        """El propio DNS debe responder (en ≤ 1,5 s) para seguir anunciándose."""
        port = int(self.ctx.settings.get("dns.port", 53))
        from .dns.upstream import query_server
        try:
            # 1) el servidor responde; 2) puede resolver nombres de Internet (si no, mejor que use el router)
            for name in ("bipon.lan", "www.google.com"):
                resp, _ = await query_server(f"127.0.0.1:{port}", DNSRecord.question(name, "A").pack(), timeout=2.5)
                if len(resp) < 4 or (resp[3] & 0x0F) not in (0, 3):
                    return False
            return True
        except Exception:
            return False

    # ---------------------------------------------------------------- ciclo
    async def loop(self) -> None:
        while True:
            if not self.cfg()["enabled"]:
                if self.announcing:
                    await self.withdraw()
                await asyncio.sleep(5)
                continue
            try:
                if self.sock is None:
                    await self._setup()
                    self.error = ""
                ok = await self._dns_ok() and not self.ctx.dns_error
                self.healthy = ok
                if ok:
                    self._send(RDNSS_LIFETIME, PREFIX_LIFETIME)
                    if not self.announcing:
                        self.ctx.db.event("info", "autodns", f"Modo automático activo: anunciando {self.address} como DNS en {self.iface}")
                    self.announcing = True
                elif self.announcing:
                    await self.withdraw(keep_socket=True)
                    self.ctx.db.event("warning", "autodns", "El DNS no responde: anuncio retirado, los equipos usan el DNS del router")
            except Exception as exc:
                self.error = str(exc)
                log.warning("modo automático: %s", exc)
                await asyncio.sleep(30)
                continue
            await asyncio.sleep(INTERVAL if self.healthy else 10)

    async def withdraw(self, keep_socket: bool = False) -> None:
        """Retira el anuncio (vida 0): los equipos dejan de usar Bipon Net al instante."""
        try:
            if self.sock:
                for _ in range(3):
                    self._send(0, PREFIX_LIFETIME if keep_socket else 0)
                    await asyncio.sleep(0.2)
        except Exception:
            pass
        self.announcing = False
        if not keep_socket:
            if self.sock:
                self.sock.close()
                self.sock = None
            if self._added_addr and self.iface and self.address:
                await self._run("ip", "-6", "addr", "del", f"{self.address}/64", "dev", self.iface)
                self._added_addr = False

    def status(self) -> dict:
        return {"config": self.cfg(), "announcing": self.announcing, "healthy": self.healthy, "address": self.address,
                "prefix": str(self.prefix) if self.prefix else None, "interface": self.iface, "sent": self.sent,
                "last_sent": self.last_sent, "error": self.error,
                "ipv6_clients": len({ip for ip in self.ctx.devices.activity if ":" in ip})}
