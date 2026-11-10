"""Servidor DNS asíncrono (UDP + TCP)."""
from __future__ import annotations

import asyncio
import ipaddress
import logging
import socket
import struct
import time
from collections import deque
from typing import Callable

from dnslib import AAAA, CNAME, PTR, QTYPE, RCODE, RR, A, DNSRecord, DNSQuestion

from ..catalog import DOH_CANARY, ICLOUD_RELAY
from .cache import DNSCache
from .filter import (BLOCKED_STATUSES, ST_CACHE, ST_DEVICE, ST_ERROR, ST_GEO, ST_LIST, ST_LOCAL, ST_OK, ST_REBIND, ST_SAFE,
                     ST_STALE, Decision, FilterEngine, Policy)
from .upstream import UpstreamPool, query_server

log = logging.getLogger("netping.dns")

_PRIVATE_V4 = [ipaddress.ip_network(n) for n in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "169.254.0.0/16", "100.64.0.0/10")]
SELF_NAMES = {"netping", "netping.lan", "netping.home", "netping.local", "pi.hole"}


def _ptr_to_ip(name: str) -> str | None:
    if not name.endswith(".in-addr.arpa"):
        return None
    parts = name[: -len(".in-addr.arpa")].split(".")
    if len(parts) != 4:
        return None
    try:
        return str(ipaddress.ip_address(".".join(reversed(parts))))
    except ValueError:
        return None


def _is_private_reverse(name: str) -> bool:
    ip = _ptr_to_ip(name)
    if not ip:
        return False
    a = ipaddress.ip_address(ip)
    return any(a in n for n in _PRIVATE_V4)


def _do_bit(rec: DNSRecord) -> bool:
    for rr in rec.ar:
        if rr.rtype == QTYPE.OPT:
            return bool(rr.ttl & 0x8000)
    return False


def _max_udp(rec: DNSRecord) -> int:
    for rr in rec.ar:
        if rr.rtype == QTYPE.OPT:
            return max(512, min(int(rr.rclass), 4096))
    return 512


class DNSServer:
    def __init__(self, engine: FilterEngine, pool: UpstreamPool, cache: DNSCache):
        self.engine = engine
        self.pool = pool
        self.cache = cache
        # inyectados por la aplicación
        self.policy_for: Callable[[str], Policy] = lambda ip: Policy()
        self.ptr_name: Callable[[str], str | None] = lambda ip: None
        self.self_ip: Callable[[], str | None] = lambda: None
        self.on_query: Callable[[tuple], None] = lambda row: None
        self.on_client: Callable[[str], None] = lambda ip: None
        self.on_usage: Callable[[str, str, bool], None] = lambda ip, name, blocked: None
        self.on_threat: Callable[[str, str], None] = lambda ip, name: None
        self.name_lookup: Callable[[str], str | None] = lambda name: None
        # protección por país / rebinding
        self.geo = None
        self.blocked_countries: set = set()
        self.rebind_protection = True
        self.rebind_allow: set = set()
        self.listening: list[str] = []
        self.local_tlds: set = {"lan", "home", "local", "internal", "localdomain"}
        # configuración dinámica
        self.blocking_enabled = True
        self.global_services: set = set()
        self.blocking_mode = "null"
        self.block_ttl = 60
        self.log_queries = True
        self.block_canary = True
        self.block_icloud = False
        self.prefetch = True
        self.local: dict[tuple[str, int], list[str]] = {}
        self.cf = {"enabled": False, "domain": "", "server": ""}
        # métricas en memoria
        self.started = time.time()
        self.total = 0
        self.blocked = 0
        self.cached = 0
        self.errors = 0
        self.resp_ewma = 0.0
        self._sec_counts: deque = deque(maxlen=120)
        self._cur_sec = int(time.time())
        self._cur_count = 0
        self._transports: list = []
        self._servers: list = []
        self._bg: set = set()

    # ---- configuración ----------------------------------------------------
    def set_local_records(self, records: list[dict]) -> None:
        local: dict[tuple[str, int], list[str]] = {}
        for r in records:
            name = r.get("name", "").lower().rstrip(".")
            typ = r.get("type", "A").upper()
            val = r.get("value", "").strip()
            if not name or not val or typ not in ("A", "AAAA", "CNAME"):
                continue
            local.setdefault((name, getattr(QTYPE, typ)), []).append(val)
        self.local = local

    # ---- arranque -----------------------------------------------------------
    async def start(self, host: str, port: int, ipv6: bool = True) -> None:
        loop = asyncio.get_running_loop()
        hosts = [host]
        if ipv6 and host in ("0.0.0.0", "") and socket.has_ipv6:
            hosts.append("::")
        for h in hosts:
            v6 = ":" in h
            family = socket.AF_INET6 if v6 else socket.AF_INET
            try:
                usock = socket.socket(family, socket.SOCK_DGRAM)
                tsock = socket.socket(family, socket.SOCK_STREAM)
                for s_ in (usock, tsock):
                    s_.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                    if v6:
                        s_.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
                try:
                    usock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4 * 1024 * 1024)
                except OSError:
                    pass
                usock.bind((h, port))
                tsock.bind((h, port))
                tsock.listen(128)
                usock.setblocking(False)
                tsock.setblocking(False)
            except OSError:
                if not v6:
                    raise
                log.info("IPv6 no disponible para el DNS; sólo IPv4")
                continue
            transport, _ = await loop.create_datagram_endpoint(lambda: _UDPServer(self), sock=usock)
            self._transports.append(transport)
            self._servers.append(await asyncio.start_server(self._tcp_client, sock=tsock))
            self.listening.append(f"[{h}]:{port}" if v6 else f"{h}:{port}")
        log.info("DNS escuchando en %s (UDP/TCP)", ", ".join(self.listening))
        self._ticker = asyncio.create_task(self._tick())

    async def start_dot(self, port: int, ssl_ctx) -> None:
        """DNS-over-TLS (RFC 7858) para dispositivos con «DNS privado»."""
        srv = await asyncio.start_server(self._tcp_client, "0.0.0.0", port, ssl=ssl_ctx, reuse_address=True)
        self._servers.append(srv)
        self.listening.append(f"DoT 0.0.0.0:{port}")
        log.info("DNS-over-TLS escuchando en el puerto %s", port)

    async def stop(self) -> None:
        for t in self._transports:
            t.close()
        for s in self._servers:
            s.close()
        self._transports.clear()
        self._servers.clear()
        if getattr(self, "_ticker", None):
            self._ticker.cancel()

    async def _tick(self):
        while True:
            await asyncio.sleep(1)
            self._roll()

    def _roll(self):
        now = int(time.time())
        while self._cur_sec < now:
            self._sec_counts.append(self._cur_count)
            self._cur_count = 0
            self._cur_sec += 1
            if now - self._cur_sec > 120:
                self._cur_sec = now

    def qps(self) -> float:
        vals = list(self._sec_counts)[-10:]
        return round(sum(vals) / len(vals), 1) if vals else 0.0

    # ---- TCP ----------------------------------------------------------------
    async def _tcp_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        peer = writer.get_extra_info("peername")
        ip = peer[0] if peer else "?"
        try:
            while True:
                hdr = await asyncio.wait_for(reader.readexactly(2), 30)
                (ln,) = struct.unpack("!H", hdr)
                data = await asyncio.wait_for(reader.readexactly(ln), 10)
                resp = await self.handle(data, ip, tcp=True)
                if resp is None:
                    break
                writer.write(struct.pack("!H", len(resp)) + resp)
                await writer.drain()
        except (asyncio.IncompleteReadError, asyncio.TimeoutError, ConnectionError):
            pass
        except Exception:  # pragma: no cover
            log.exception("error TCP")
        finally:
            writer.close()

    # ---- núcleo -------------------------------------------------------------
    async def handle(self, data: bytes, client_ip: str, tcp: bool = False) -> bytes | None:
        t0 = time.perf_counter()
        if client_ip.startswith("::ffff:"):
            client_ip = client_ip[7:]
        try:
            req = DNSRecord.parse(data)
        except Exception:
            return None
        if req.header.qr or not req.questions:
            return None
        q = req.q
        name = str(q.qname).lower().rstrip(".")
        qtype = q.qtype
        qtype_name = QTYPE.get(qtype, str(qtype))
        self.on_client(client_ip)

        status, reason, upstream_label = ST_OK, "", ""
        try:
            reply, status, reason, upstream_label = await self._resolve(req, name, qtype, client_ip)
        except Exception as exc:
            log.debug("fallo resolviendo %s: %s", name, exc)
            reply = req.reply()
            reply.header.rcode = RCODE.SERVFAIL
            status, reason = ST_ERROR, str(exc)[:120]
            self.errors += 1

        out = reply.pack() if isinstance(reply, DNSRecord) else reply
        if not tcp:
            limit = _max_udp(req)
            if len(out) > limit:
                trunc = req.reply()
                trunc.header.tc = 1
                out = trunc.pack()

        elapsed = (time.perf_counter() - t0) * 1000
        self.total += 1
        self._cur_count += 1
        try:
            self.on_usage(client_ip, name, status in BLOCKED_STATUSES)
        except Exception:  # pragma: no cover
            pass
        if status in BLOCKED_STATUSES:
            self.blocked += 1
        elif status in (ST_CACHE, ST_STALE):
            self.cached += 1
        self.resp_ewma = elapsed if self.total == 1 else 0.05 * elapsed + 0.95 * self.resp_ewma
        if self.log_queries:
            self.on_query((time.time(), client_ip, name, qtype_name, status, reason, upstream_label, round(elapsed, 2)))
        return out

    async def _resolve(self, req: DNSRecord, name: str, qtype: int, client_ip: str):
        # 1) nombres propios y registros locales
        if name in SELF_NAMES and qtype in (QTYPE.A, QTYPE.ANY):
            ip = self.self_ip()
            if ip:
                r = req.reply()
                r.add_answer(RR(req.q.qname, QTYPE.A, rdata=A(ip), ttl=300))
                return r, ST_LOCAL, "NetPing", "local"
        if qtype == QTYPE.A and ("." not in name or name.rsplit(".", 1)[-1] in self.local_tlds):
            dyn = self.name_lookup(name)
            if dyn:
                r = req.reply()
                r.add_answer(RR(req.q.qname, QTYPE.A, rdata=A(dyn), ttl=60))
                return r, ST_LOCAL, "Equipo DHCP", "local"
        local = self.local.get((name, qtype)) or self.local.get((name, QTYPE.CNAME))
        if local:
            r = req.reply()
            for v in local:
                if (name, QTYPE.CNAME) in self.local and (name, qtype) not in self.local:
                    r.add_answer(RR(req.q.qname, QTYPE.CNAME, rdata=CNAME(v), ttl=300))
                elif qtype == QTYPE.A:
                    r.add_answer(RR(req.q.qname, QTYPE.A, rdata=A(v), ttl=300))
                elif qtype == QTYPE.AAAA:
                    r.add_answer(RR(req.q.qname, QTYPE.AAAA, rdata=AAAA(v), ttl=300))
            return r, ST_LOCAL, "Registro local", "local"

        # 2) canarios de navegadores / iCloud Private Relay
        if (self.block_canary and name == DOH_CANARY) or (self.block_icloud and name in ICLOUD_RELAY):
            r = req.reply()
            r.header.rcode = RCODE.NXDOMAIN
            return r, ST_LOCAL, "Canario DoH / Private Relay", "local"

        # 3) PTR de la red local
        if qtype == QTYPE.PTR and _is_private_reverse(name):
            ip = _ptr_to_ip(name)
            host = self.ptr_name(ip) if ip else None
            if host:
                r = req.reply()
                r.add_answer(RR(req.q.qname, QTYPE.PTR, rdata=PTR(host + "."), ttl=300))
                return r, ST_LOCAL, "Dispositivo local", "local"
            if self.cf.get("enabled") and self.cf.get("server"):
                return await self._forward_to(req, self.cf["server"])
            r = req.reply()
            r.header.rcode = RCODE.NXDOMAIN
            return r, ST_LOCAL, "Red privada (no se reenvía)", "local"

        # 4) reenvío condicional (nombres de la red local)
        cf_domain = (self.cf.get("domain") or "").lower().strip(".")
        if self.cf.get("enabled") and self.cf.get("server") and cf_domain and (name == cf_domain or name.endswith("." + cf_domain)):
            return await self._forward_to(req, self.cf["server"])

        # 5) filtrado
        policy = self.policy_for(client_ip)
        decision = self.engine.check(name, policy, self.blocking_enabled, self.global_services)
        if decision is not None:
            if decision.blocked:
                if decision.status == ST_LIST and "seguridad" in decision.reason:
                    self.on_threat(client_ip, name)
                return self._blocked_reply(req, qtype), decision.status, decision.reason, ""
            if decision.cname:
                return await self._cname_reply(req, decision, qtype)

        # 6) caché + upstream, y comprobación de las IP devueltas (país / rebinding)
        result = await self._cached_or_forward(req, name, qtype)
        if qtype in (QTYPE.A, QTYPE.AAAA) and (self.rebind_protection or self.blocked_countries):
            post = self._post_check(name, result[0])
            if post:
                return self._blocked_reply(req, qtype), post[0], post[1], result[3]
        return result

    _REBIND_SAFE = ("lan", "local", "home.arpa", "internal", "localdomain", "plex.direct", "nip.io", "sslip.io", "fritz.box")

    def _post_check(self, name: str, reply) -> tuple[int, str] | None:
        try:
            rec = reply if isinstance(reply, DNSRecord) else DNSRecord.parse(reply)
        except Exception:
            return None
        ips = [str(rr.rdata) for rr in rec.rr if rr.rtype in (QTYPE.A, QTYPE.AAAA)]
        if not ips:
            return None
        if self.rebind_protection:
            safe = any(name == s or name.endswith("." + s) for s in (*self._REBIND_SAFE, *self.rebind_allow))
            cf = (self.cf.get("domain") or "").strip(".")
            if cf and (name == cf or name.endswith("." + cf)):
                safe = True
            if not safe:
                for ip in ips:
                    try:
                        a = ipaddress.ip_address(ip)
                    except ValueError:
                        continue
                    if (a.is_private or a.is_loopback or a.is_link_local) and not a.is_unspecified:
                        return ST_REBIND, f"Dominio público apunta a IP interna ({ip})"
        if self.blocked_countries and self.geo is not None:
            for ip in ips:
                cc = self.geo.country(ip)
                if cc and cc in self.blocked_countries:
                    return ST_GEO, f"Servidor en país bloqueado ({cc})"
        return None

    def _blocked_reply(self, req: DNSRecord, qtype: int) -> DNSRecord:
        r = req.reply()
        if self.blocking_mode == "nxdomain":
            r.header.rcode = RCODE.NXDOMAIN
        elif qtype == QTYPE.A:
            r.add_answer(RR(req.q.qname, QTYPE.A, rdata=A("0.0.0.0"), ttl=self.block_ttl))
        elif qtype == QTYPE.AAAA:
            r.add_answer(RR(req.q.qname, QTYPE.AAAA, rdata=AAAA("::"), ttl=self.block_ttl))
        return r

    async def _cname_reply(self, req: DNSRecord, decision: Decision, qtype: int):
        target = decision.cname
        sub = DNSRecord(q=DNSQuestion(target, qtype))
        sub_reply, _, _, up = await self._cached_or_forward(sub, target, qtype)
        if not isinstance(sub_reply, DNSRecord):
            sub_reply = DNSRecord.parse(sub_reply)
        r = req.reply()
        r.add_answer(RR(req.q.qname, QTYPE.CNAME, rdata=CNAME(target + "."), ttl=300))
        for rr in sub_reply.rr:
            r.add_answer(rr)
        return r, ST_SAFE, decision.reason, up

    async def _forward_to(self, req: DNSRecord, server: str):
        resp, _ = await query_server(server, req.pack(), timeout=2.0)
        rec = DNSRecord.parse(resp)
        rec.header.id = req.header.id
        return rec, ST_OK, "Reenvío condicional", server

    async def _cached_or_forward(self, req: DNSRecord, name: str, qtype: int):
        key = DNSCache.key(name, qtype, "do" if _do_bit(req) else "")
        cached, state = self.cache.get(key, req.header.id)
        if cached is not None:
            if state in ("stale", "prefetch") and self.cache.mark_refreshing(key):
                self._spawn(self._refresh(key, name, qtype, _do_bit(req)))
            cached.questions = req.questions
            return cached, (ST_STALE if state == "stale" else ST_CACHE), "", "caché"
        wire = req.pack()
        resp, up, _rtt = await self.pool.resolve(wire)
        rec = DNSRecord.parse(resp)
        self.cache.put(key, rec, resp)
        # la respuesta upstream ya lleva el ID del cliente: se devuelve tal cual (sin re-serializar)
        return resp, ST_OK, "", up.label

    async def _refresh(self, key, name, qtype, do: bool):
        try:
            q = DNSRecord(q=DNSQuestion(name, qtype))
            if do:
                q.add_ar(RR(rtype=QTYPE.OPT, rclass=1232, ttl=0x8000))
            resp, _, _ = await self.pool.resolve(q.pack())
            rec = DNSRecord.parse(resp)
            self.cache.put(key, rec, resp)
        except Exception:
            e = self.cache._d.get(key)
            if e:
                e.refreshing = False

    def _spawn(self, coro):
        t = asyncio.create_task(coro)
        self._bg.add(t)
        t.add_done_callback(self._bg.discard)

    def stats(self) -> dict:
        return {
            "total": self.total,
            "blocked": self.blocked,
            "cached": self.cached,
            "errors": self.errors,
            "qps": self.qps(),
            "avg_response_ms": round(self.resp_ewma, 2),
            "uptime": int(time.time() - self.started),
            "cache": self.cache.stats(),
            "upstreams": self.pool.stats(),
        }


class _UDPServer(asyncio.DatagramProtocol):
    def __init__(self, server: DNSServer):
        self.server = server
        self.transport = None

    def connection_made(self, transport):
        self.transport = transport

    def datagram_received(self, data, addr):
        self.server._spawn(self._process(data, addr))

    async def _process(self, data, addr):
        resp = await self.server.handle(data, addr[0])
        if resp is not None and self.transport is not None:
            self.transport.sendto(resp, addr)

    def error_received(self, exc):  # pragma: no cover
        log.debug("error UDP: %s", exc)
