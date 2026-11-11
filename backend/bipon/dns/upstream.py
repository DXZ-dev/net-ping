"""Clientes DNS upstream: UDP, TCP, DNS-over-TLS y DNS-over-HTTPS.

Formatos de servidor admitidos:
    1.1.1.1                 UDP (puerto 53) con reintento por TCP si la respuesta viene truncada
    1.1.1.1:5335            UDP en otro puerto
    [2606:4700::1111]:53    IPv6
    tcp://1.1.1.1           TCP
    tls://1.1.1.1#cloudflare-dns.com   DNS-over-TLS (puerto 853, SNI opcional)
    https://1.1.1.1/dns-query          DNS-over-HTTPS (RFC 8484)
"""
from __future__ import annotations

import asyncio
import random
import ssl
import struct
import time
from dataclasses import dataclass, field
from urllib.parse import urlparse

import httpx


class UpstreamError(Exception):
    pass


def _split_host_port(s: str, default_port: int) -> tuple[str, int]:
    if s.startswith("["):
        host, _, rest = s[1:].partition("]")
        port = int(rest[1:]) if rest.startswith(":") else default_port
        return host, port
    if s.count(":") == 1:
        host, port = s.split(":")
        return host, int(port)
    return s, default_port


@dataclass
class Upstream:
    spec: str
    proto: str = "udp"
    host: str = ""
    port: int = 53
    sni: str | None = None
    url: str | None = None
    # métricas de latencia (EWMA) para la estrategia "fastest"
    ewma: float = 0.0
    samples: int = 0
    failures: int = 0
    last_fail: float = 0.0
    total: int = 0
    streak: int = 0  # fallos seguidos
    _tls_ctx: ssl.SSLContext | None = field(default=None, repr=False)

    @classmethod
    def parse(cls, spec: str) -> "Upstream":
        spec = spec.strip()
        if not spec:
            raise ValueError("servidor vacío")
        if spec.startswith("https://"):
            u = urlparse(spec)
            return cls(spec=spec, proto="doh", host=u.hostname or "", port=u.port or 443, url=spec)
        if spec.startswith("tls://"):
            rest = spec[6:]
            rest, _, sni = rest.partition("#")
            host, port = _split_host_port(rest, 853)
            return cls(spec=spec, proto="dot", host=host, port=port, sni=sni or host)
        if spec.startswith("tcp://"):
            host, port = _split_host_port(spec[6:], 53)
            return cls(spec=spec, proto="tcp", host=host, port=port)
        if spec.startswith("udp://"):
            spec_body = spec[6:]
        else:
            spec_body = spec
        host, port = _split_host_port(spec_body, 53)
        return cls(spec=spec, proto="udp", host=host, port=port)

    @property
    def label(self) -> str:
        return self.spec

    def record(self, rtt_ms: float | None) -> None:
        self.total += 1
        if rtt_ms is None:
            self.streak += 1
            self.failures += 1
            self.last_fail = time.monotonic()
            # penalización para que la estrategia "fastest" lo evite durante un tiempo
            self.ewma = max(self.ewma, 1000.0) if self.samples else 1000.0
            return
        self.streak = 0
        self.samples += 1
        alpha = 0.3 if self.samples > 5 else 0.6
        self.ewma = rtt_ms if self.samples == 1 else (alpha * rtt_ms + (1 - alpha) * self.ewma)

    @property
    def down(self) -> bool:
        """Tras 3 fallos seguidos se da por caído 15 s: no se le hace esperar a cada consulta."""
        return self.streak >= 3 and time.monotonic() - self.last_fail < 15

    def score(self) -> float:
        if self.samples == 0:
            return 50.0  # desconocido: le damos oportunidad
        penalty = 0.0
        if self.last_fail and time.monotonic() - self.last_fail < 30:
            penalty = 500.0
        return self.ewma + penalty

    # ---- transporte -----------------------------------------------------
    async def query(self, wire: bytes, timeout: float, http: httpx.AsyncClient | None = None) -> bytes:
        if self.proto == "udp":
            resp = await _udp_query(self.host, self.port, wire, timeout)
            if len(resp) > 2 and resp[2] & 0x02:  # TC: truncada -> TCP
                resp = await _tcp_query(self.host, self.port, wire, timeout)
            return resp
        if self.proto == "tcp":
            return await _tcp_query(self.host, self.port, wire, timeout)
        if self.proto == "dot":
            if self._tls_ctx is None:
                self._tls_ctx = ssl.create_default_context()
                self._tls_ctx.check_hostname = bool(self.sni)
            return await _tcp_query(self.host, self.port, wire, timeout, ssl_ctx=self._tls_ctx, sni=self.sni)
        if self.proto == "doh":
            if http is None:
                raise UpstreamError("cliente HTTP no disponible")
            r = await http.post(
                self.url,
                content=wire,
                headers={"content-type": "application/dns-message", "accept": "application/dns-message"},
                timeout=timeout,
            )
            if r.status_code != 200:
                raise UpstreamError(f"DoH HTTP {r.status_code}")
            return r.content
        raise UpstreamError(f"protocolo no soportado: {self.proto}")


class _UDPClient(asyncio.DatagramProtocol):
    def __init__(self, expect_id: bytes):
        self.expect_id = expect_id
        self.fut: asyncio.Future = asyncio.get_running_loop().create_future()

    def datagram_received(self, data, addr):
        if data[:2] == self.expect_id and not self.fut.done():
            self.fut.set_result(data)

    def error_received(self, exc):
        if not self.fut.done():
            self.fut.set_exception(exc)

    def connection_lost(self, exc):
        if not self.fut.done():
            self.fut.set_exception(exc or UpstreamError("conexión cerrada"))


async def _udp_query(host: str, port: int, wire: bytes, timeout: float) -> bytes:
    loop = asyncio.get_running_loop()
    transport, proto = await loop.create_datagram_endpoint(
        lambda: _UDPClient(wire[:2]), remote_addr=(host, port)
    )
    try:
        transport.sendto(wire)
        return await asyncio.wait_for(proto.fut, timeout)
    finally:
        transport.close()


async def _tcp_query(host, port, wire, timeout, ssl_ctx=None, sni=None) -> bytes:
    async def run():
        reader, writer = await asyncio.open_connection(
            host, port, ssl=ssl_ctx, server_hostname=sni if ssl_ctx else None
        )
        try:
            writer.write(struct.pack("!H", len(wire)) + wire)
            await writer.drain()
            hdr = await reader.readexactly(2)
            (ln,) = struct.unpack("!H", hdr)
            return await reader.readexactly(ln)
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass
    return await asyncio.wait_for(run(), timeout)


class UpstreamPool:
    """Gestiona un conjunto de upstreams con estrategia failover / fastest / parallel."""

    def __init__(self):
        self.upstreams: list[Upstream] = []
        self.strategy = "fastest"
        self.timeout = 2.0
        # respaldo de emergencia: sólo si TODOS los configurados fallan (p. ej. Unbound colgado), para que la casa
        # siga teniendo DNS en vez de esperar y fallar
        self.emergency = [Upstream.parse("1.1.1.1"), Upstream.parse("9.9.9.9")]
        self.emergency_used = 0
        self._http: httpx.AsyncClient | None = None

    def configure(self, specs: list[str], strategy: str, timeout_ms: int) -> None:
        old = {u.spec: u for u in self.upstreams}
        ups = []
        for s in specs:
            try:
                ups.append(old.get(s) or Upstream.parse(s))
            except Exception:
                continue
        self.upstreams = ups
        self.strategy = strategy
        self.timeout = max(0.3, timeout_ms / 1000)

    def http(self) -> httpx.AsyncClient:
        if self._http is None:
            self._http = httpx.AsyncClient(http2=False, limits=httpx.Limits(max_keepalive_connections=8))
        return self._http

    async def close(self):
        if self._http:
            await self._http.aclose()

    def ordered(self) -> list[Upstream]:
        ups = list(self.upstreams)
        if self.strategy == "fastest" and len(ups) > 1:
            ups.sort(key=lambda u: u.score())
            # exploración ocasional para refrescar métricas de los demás
            if random.random() < 0.05:
                ups[0], ups[1] = ups[1], ups[0]
        return ups

    async def _one(self, up: Upstream, wire: bytes) -> tuple[bytes, Upstream, float]:
        t0 = time.perf_counter()
        try:
            resp = await up.query(wire, self.timeout, self.http())
        except Exception:
            up.record(None)
            raise
        rtt = (time.perf_counter() - t0) * 1000
        up.record(rtt)
        return resp, up, rtt

    async def _probe(self, up: Upstream, wire: bytes) -> None:
        try:
            await self._one(up, wire)
        except Exception:
            pass

    async def resolve(self, wire: bytes) -> tuple[bytes, Upstream, float]:
        if not self.upstreams:
            raise UpstreamError("no hay servidores upstream configurados")
        if self.strategy == "parallel" and len(self.upstreams) > 1:
            tasks = [asyncio.create_task(self._one(u, wire)) for u in self.upstreams]
            try:
                for fut in asyncio.as_completed(tasks):
                    try:
                        return await fut
                    except Exception:
                        continue
            finally:
                for t in tasks:
                    t.cancel()
            raise UpstreamError("todos los upstream fallaron")
        last: Exception | None = None
        ups = self.ordered()
        alive = [u for u in ups if not u.down]
        if not alive and ups:
            # todos caídos: se responde ya con el respaldo y se comprueba en segundo plano si el principal volvió
            self._probe_task = asyncio.ensure_future(self._probe(ups[0], wire))
        for up in alive:
            try:
                return await self._one(up, wire)
            except Exception as exc:
                last = exc
        for up in self.emergency:
            if up.spec in {u.spec for u in ups}:
                continue
            try:
                t0 = time.perf_counter()
                resp = await up.query(wire, min(self.timeout, 1.0), self.http())
                self.emergency_used += 1
                return resp, up, (time.perf_counter() - t0) * 1000
            except Exception as exc:
                last = exc
        raise UpstreamError(f"todos los upstream fallaron: {last!r}")

    def stats(self) -> list[dict]:
        return [
            {
                "server": u.spec,
                "proto": u.proto,
                "avg_ms": round(u.ewma, 2) if u.samples else None,
                "queries": u.total,
                "failures": u.failures,
            }
            for u in self.upstreams
        ]


async def query_server(spec: str, wire: bytes, timeout: float = 2.0, http: httpx.AsyncClient | None = None) -> tuple[bytes, float]:
    """Consulta única a un servidor concreto (herramientas/benchmark)."""
    up = Upstream.parse(spec)
    own = None
    if up.proto == "doh" and http is None:
        own = http = httpx.AsyncClient()
    try:
        t0 = time.perf_counter()
        resp = await up.query(wire, timeout, http)
        return resp, (time.perf_counter() - t0) * 1000
    finally:
        if own:
            await own.aclose()
