"""Herramientas de diagnóstico de red."""
from __future__ import annotations

import asyncio
import ipaddress
import random
import shutil
import socket
import ssl
import statistics
import string
import time
from datetime import datetime, timezone
from typing import AsyncIterator
from urllib.parse import urlparse

import httpx
from dnslib import EDNS0, DNSRecord, QTYPE, RCODE

from . import netinfo
from .catalog import DNS_PRESETS, OPTIMIZER_CANDIDATES
from .dns.upstream import query_server
from .pinger import ping, tcp_ping, valid_host

COMMON_PORTS = {
    21: "FTP", 22: "SSH", 23: "Telnet", 25: "SMTP", 53: "DNS", 80: "HTTP", 110: "POP3", 111: "RPC",
    135: "MS-RPC", 139: "NetBIOS", 143: "IMAP", 161: "SNMP", 443: "HTTPS", 445: "SMB", 465: "SMTPS",
    515: "LPD", 548: "AFP", 554: "RTSP", 587: "Submission", 631: "IPP/CUPS", 853: "DNS-over-TLS",
    993: "IMAPS", 995: "POP3S", 1080: "SOCKS", 1194: "OpenVPN", 1433: "MSSQL", 1883: "MQTT",
    1900: "UPnP", 2049: "NFS", 3000: "Web (dev)", 3306: "MySQL", 3389: "RDP", 5000: "UPnP/Synology",
    5001: "Synology HTTPS", 5353: "mDNS", 5432: "PostgreSQL", 5900: "VNC", 6379: "Redis",
    7000: "AirPlay", 8000: "HTTP alt", 8006: "Proxmox", 8080: "HTTP proxy", 8081: "HTTP alt",
    8123: "Home Assistant", 8443: "HTTPS alt", 8883: "MQTT TLS", 8888: "HTTP alt", 9000: "Portainer",
    9090: "Cockpit", 9100: "Impresora RAW", 9443: "Portainer HTTPS", 10000: "Webmin",
    32400: "Plex", 51820: "WireGuard", 62078: "iPhone sync",
}
WEB_PORTS = {80: "http", 443: "https", 8000: "http", 8006: "https", 8080: "http", 8081: "http", 8123: "http",
             8443: "https", 8888: "http", 9000: "http", 9090: "https", 9443: "https", 10000: "https",
             32400: "http", 5000: "http", 5001: "https", 3000: "http"}

BENCH_DOMAINS = [
    "google.com", "youtube.com", "facebook.com", "instagram.com", "whatsapp.net", "wikipedia.org",
    "amazon.com", "netflix.com", "microsoft.com", "apple.com", "cloudflare.com", "github.com",
    "tiktok.com", "spotify.com", "twitch.tv", "reddit.com", "linkedin.com", "office.com",
    "zoom.us", "mercadolibre.com", "bbc.co.uk", "yahoo.com", "bing.com", "live.com",
]


def _usable(resp: bytes) -> bool:
    """Una respuesta SERVFAIL/REFUSED cuenta como fallo del resolver."""
    return len(resp) > 3 and (resp[3] & 0x0F) not in (2, 5)


def _rand_label(n=10):
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=n))


# ------------------------------------------------------------------ DNS lookup
async def dns_lookup(name: str, qtype: str, server: str) -> dict:
    qtype = qtype.upper()
    if not hasattr(QTYPE, qtype):
        raise ValueError("tipo de registro no soportado")
    if qtype == "PTR":
        try:
            name = ipaddress.ip_address(name).reverse_pointer
        except ValueError:
            pass
    q = DNSRecord.question(name, qtype)
    q.header.rd = 1
    q.add_ar(EDNS0(udp_len=1232, flags="do"))
    resp, rtt = await query_server(server, q.pack(), timeout=4)
    rec = DNSRecord.parse(resp)
    return {
        "server": server, "rtt_ms": round(rtt, 2), "rcode": RCODE.get(rec.header.rcode),
        "flags": {"aa": bool(rec.header.aa), "tc": bool(rec.header.tc), "rd": bool(rec.header.rd),
                  "ra": bool(rec.header.ra), "ad": bool(rec.header.ad)},
        "answers": [{"name": str(r.rname), "type": QTYPE.get(r.rtype), "ttl": r.ttl, "data": str(r.rdata)} for r in rec.rr],
        "authority": [{"name": str(r.rname), "type": QTYPE.get(r.rtype), "ttl": r.ttl, "data": str(r.rdata)} for r in rec.auth],
    }


# ------------------------------------------------------------ DNS benchmark
async def _bench_one(server: str, domains: list[str], http: httpx.AsyncClient) -> dict:
    cached, uncached, fails = [], [], 0
    for d in domains:
        wire = DNSRecord.question(d, "A").pack()
        try:
            resp, rtt = await query_server(server, wire, timeout=2.0, http=http)
            if not _usable(resp):
                fails += 1
                continue
            cached.append(rtt)  # dominio popular: casi siempre en caché del resolver
        except Exception:
            fails += 1
    for _ in range(4):
        wire = DNSRecord.question(f"{_rand_label()}.{random.choice(['com', 'net', 'org'])}", "A").pack()
        try:
            resp, rtt = await query_server(server, wire, timeout=3.0, http=http)
            # NXDOMAIN es la respuesta correcta para un nombre aleatorio
            if not _usable(resp):
                fails += 1
                continue
            uncached.append(rtt)
        except Exception:
            fails += 1
    total = len(domains) + 4
    out = {"server": server, "fails": fails, "reliability": round((total - fails) / total * 100, 1)}
    if cached:
        out.update(median=round(statistics.median(cached), 2), avg=round(statistics.fmean(cached), 2),
                   min=round(min(cached), 2), max=round(max(cached), 2))
    if uncached:
        out["uncached"] = round(statistics.median(uncached), 2)
    return out


async def dns_benchmark(servers: list[dict], rounds: int = 1) -> list[dict]:
    """servers = [{"id","name","server"}]. Mide latencia (caché) y resolución en frío."""
    domains = BENCH_DOMAINS * max(1, min(rounds, 3))
    async with httpx.AsyncClient() as http:
        async def run(s):
            # calentamiento (establece rutas/TLS) y medición
            try:
                await query_server(s["server"], DNSRecord.question("example.com", "A").pack(), timeout=2, http=http)
            except Exception:
                pass
            r = await _bench_one(s["server"], domains, http)
            return {**s, **r}
        results = await asyncio.gather(*(run(s) for s in servers))
    def score(r):
        if "median" not in r:
            return 1e9
        return r["median"] * 0.7 + r.get("uncached", r["median"] * 4) * 0.3 / 4 + (100 - r["reliability"]) * 20
    results.sort(key=score)
    for i, r in enumerate(results):
        r["rank"] = i + 1
        r["score"] = round(score(r), 2) if "median" in r else None
    return results


def optimizer_candidates(unbound_running: bool) -> list[dict]:
    out = []
    for pid in OPTIMIZER_CANDIDATES:
        if pid == "unbound" and not unbound_running:
            continue
        p = DNS_PRESETS[pid]
        out.append({"id": pid, "name": p["name"], "server": p["servers"][0]})
    return out


# -------------------------------------------------------------- Puertos
async def port_scan(host: str, ports: list[int], timeout: float = 0.8) -> list[dict]:
    if not valid_host(host):
        raise ValueError("host inválido")
    sem = asyncio.Semaphore(256)
    async def probe(p):
        async with sem:
            t0 = time.perf_counter()
            try:
                _, w = await asyncio.wait_for(asyncio.open_connection(host, p), timeout)
                w.close()
                return {"port": p, "open": True, "service": COMMON_PORTS.get(p, ""), "ms": round((time.perf_counter() - t0) * 1000, 1),
                        "web": WEB_PORTS.get(p)}
            except Exception:
                return None
    res = await asyncio.gather(*(probe(p) for p in ports))
    return [r for r in res if r]


def parse_ports(spec: str) -> list[int]:
    if not spec or spec == "common":
        return sorted(COMMON_PORTS)
    ports: set[int] = set()
    for part in spec.split(","):
        part = part.strip()
        if "-" in part:
            a, b = part.split("-", 1)
            ports.update(range(max(1, int(a)), min(65535, int(b)) + 1))
        elif part:
            ports.add(int(part))
    if len(ports) > 5000:
        raise ValueError("máximo 5000 puertos por escaneo")
    return sorted(p for p in ports if 0 < p < 65536)


# -------------------------------------------------------------- Streaming
async def stream_cmd(args: list[str], timeout: float = 90) -> AsyncIterator[str]:
    proc = await asyncio.create_subprocess_exec(*args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
    deadline = time.monotonic() + timeout
    assert proc.stdout
    try:
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                yield "… tiempo agotado"
                break
            try:
                line = await asyncio.wait_for(proc.stdout.readline(), remaining)
            except asyncio.TimeoutError:
                yield "… tiempo agotado"
                break
            if not line:
                break
            yield line.decode(errors="ignore").rstrip()
    finally:
        if proc.returncode is None:
            proc.kill()
            await proc.wait()


def traceroute_cmd(host: str, max_hops: int = 30) -> list[str]:
    if not valid_host(host):
        raise ValueError("host inválido")
    if shutil.which("traceroute"):
        return ["traceroute", "-n", "-q", "1", "-w", "2", "-m", str(max_hops), host]
    if shutil.which("tracepath"):
        return ["tracepath", "-n", "-m", str(max_hops), host]
    raise RuntimeError("traceroute no está instalado")


def ping_cmd(host: str, count: int) -> list[str]:
    if not valid_host(host):
        raise ValueError("host inválido")
    return ["ping", "-n", "-c", str(max(1, min(count, 100))), "-i", "0.5", "-W", "2", host]


async def whois(query: str) -> str:
    if not valid_host(query):
        raise ValueError("consulta inválida")
    if shutil.which("whois"):
        proc = await asyncio.create_subprocess_exec("whois", query, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
        try:
            out, _ = await asyncio.wait_for(proc.communicate(), 20)
            return out.decode(errors="ignore")[:60000]
        except asyncio.TimeoutError:
            proc.kill()
            return "tiempo agotado"
    async with httpx.AsyncClient(follow_redirects=True, timeout=15) as c:
        kind = "ip" if _is_ip(query) else "domain"
        r = await c.get(f"https://rdap.org/{kind}/{query}")
        return r.text[:60000]


def _is_ip(s: str) -> bool:
    try:
        ipaddress.ip_address(s)
        return True
    except ValueError:
        return False


# -------------------------------------------------------------- HTTP / TLS
async def tls_info(host: str, port: int = 443) -> dict:
    ctx = ssl.create_default_context()
    try:
        _, w = await asyncio.wait_for(asyncio.open_connection(host, port, ssl=ctx, server_hostname=host), 6)
        sslobj = w.get_extra_info("ssl_object")
        cert = sslobj.getpeercert()
        proto = sslobj.version()
        w.close()
        exp = datetime.strptime(cert["notAfter"], "%b %d %H:%M:%S %Y %Z").replace(tzinfo=timezone.utc)
        subject = dict(x[0] for x in cert.get("subject", []))
        issuer = dict(x[0] for x in cert.get("issuer", []))
        return {"valid": True, "protocol": proto, "subject": subject.get("commonName"), "issuer": issuer.get("organizationName") or issuer.get("commonName"),
                "expires": exp.isoformat(), "days_left": (exp - datetime.now(timezone.utc)).days,
                "san": [v for k, v in cert.get("subjectAltName", [])][:20]}
    except ssl.SSLCertVerificationError as exc:
        return {"valid": False, "error": exc.verify_message or str(exc)}
    except Exception as exc:
        return {"valid": False, "error": str(exc)}


async def http_check(url: str) -> dict:
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    u = urlparse(url)
    if not u.hostname or not valid_host(u.hostname):
        raise ValueError("URL inválida")
    t0 = time.perf_counter()
    t_dns = None
    try:
        await asyncio.get_running_loop().getaddrinfo(u.hostname, None)
        t_dns = round((time.perf_counter() - t0) * 1000, 1)
    except Exception:
        pass
    t1 = time.perf_counter()
    async with httpx.AsyncClient(follow_redirects=True, timeout=15, verify=True) as c:
        try:
            r = await c.get(url, headers={"user-agent": "Mozilla/5.0 NetPing"})
        except Exception as exc:
            return {"url": url, "ok": False, "error": f"{type(exc).__name__}: {exc}", "dns_ms": t_dns}
    total = round((time.perf_counter() - t1) * 1000, 1)
    out = {
        "url": url, "final_url": str(r.url), "ok": r.status_code < 400, "status": r.status_code,
        "http_version": r.http_version, "total_ms": total, "dns_ms": t_dns,
        "redirects": [str(h.url) for h in r.history], "size": len(r.content),
        "headers": {k: v for k, v in r.headers.items() if k.lower() in (
            "server", "content-type", "cache-control", "strict-transport-security", "content-security-policy",
            "x-frame-options", "x-content-type-options", "via", "cf-ray", "alt-svc")},
    }
    if urlparse(str(r.url)).scheme == "https":
        out["tls"] = await tls_info(urlparse(str(r.url)).hostname, urlparse(str(r.url)).port or 443)
    return out


# ------------------------------------------------------------ Diagnóstico
async def public_info() -> dict:
    info: dict = {}
    async with httpx.AsyncClient(timeout=6) as c:
        try:
            r = await c.get("https://1.1.1.1/cdn-cgi/trace")
            kv = dict(line.split("=", 1) for line in r.text.splitlines() if "=" in line)
            info.update(ip=kv.get("ip"), colo=kv.get("colo"), country=kv.get("loc"), http=kv.get("http"), tls=kv.get("tls"),
                        warp=kv.get("warp"))
        except Exception:
            pass
        try:
            r = await c.get("https://ipinfo.io/json")
            if r.status_code == 200:
                j = r.json()
                info.update(isp=j.get("org"), city=j.get("city"), region=j.get("region"), country=j.get("country") or info.get("country"),
                            ip=info.get("ip") or j.get("ip"), timezone=j.get("timezone"), loc=j.get("loc"))
        except Exception:
            pass
    return info


async def diagnose(local_dns_port: int, unbound_port: int | None) -> dict:
    checks: list[dict] = []
    ifaces = netinfo.interfaces()
    gw = netinfo.default_gateway()

    def add(cid, name, status, detail, value=None):
        checks.append({"id": cid, "name": name, "status": status, "detail": detail, "value": value})

    add("iface", "Interfaz de red", "ok" if ifaces else "fail",
        ", ".join(f"{i['name']} {i['ip']}/{i['prefix']}" for i in ifaces) or "Sin IP en la LAN")

    async def t_gw():
        if not gw:
            add("gateway", "Router (puerta de enlace)", "fail", "No hay ruta por defecto")
            return
        r = await ping(gw, count=5)
        st = "ok" if r["loss"] == 0 and (r["avg"] or 99) < 10 else ("warn" if r["received"] else "fail")
        add("gateway", "Router (puerta de enlace)", st, f"{gw} · {r['avg']} ms · pérdida {r['loss']}%" if r["received"] else f"{gw} no responde a ping", r)

    async def t_inet():
        res = await asyncio.gather(ping("1.1.1.1", 5), ping("8.8.8.8", 5))
        best = min((r for r in res if r["received"]), key=lambda r: r["avg"], default=None)
        if not best:
            tp = await tcp_ping("1.1.1.1", 443, 3)
            if tp["received"]:
                add("internet", "Conectividad a Internet", "warn", f"ICMP bloqueado, TCP OK ({tp['avg']} ms)", tp)
            else:
                add("internet", "Conectividad a Internet", "fail", "Sin respuesta de 1.1.1.1 ni 8.8.8.8")
            return
        loss = max(r["loss"] for r in res)
        st = "ok" if best["avg"] < 60 and loss < 5 else "warn"
        add("internet", "Latencia a Internet", st,
            f"Cloudflare {res[0]['avg']} ms · Google {res[1]['avg']} ms · jitter {best['jitter']} ms · pérdida {loss}%", res)

    async def t_dns(cid, name, server):
        try:
            d = "www.google.com"
            _, rtt = await query_server(server, DNSRecord.question(d, "A").pack(), timeout=3)
            add(cid, name, "ok" if rtt < 80 else "warn", f"{server} respondió en {rtt:.1f} ms", round(rtt, 1))
        except Exception as exc:
            add(cid, name, "fail", f"{server}: {exc or 'sin respuesta'}")

    async def t_http():
        try:
            async with httpx.AsyncClient(timeout=6) as c:
                t0 = time.perf_counter()
                r = await c.get("https://www.google.com/generate_204")
                ms = (time.perf_counter() - t0) * 1000
            add("http", "Navegación web (HTTPS)", "ok" if r.status_code == 204 else "warn", f"HTTP {r.status_code} en {ms:.0f} ms", round(ms))
        except Exception as exc:
            add("http", "Navegación web (HTTPS)", "fail", str(exc)[:150])

    async def t_ipv6():
        try:
            _, w = await asyncio.wait_for(asyncio.open_connection("2606:4700:4700::1111", 443), 3)
            w.close()
            add("ipv6", "IPv6", "ok", "Conectividad IPv6 disponible")
        except Exception:
            add("ipv6", "IPv6", "info", "Sin IPv6 (no es un problema para la mayoría de redes)")

    async def t_public():
        info = await public_info()
        if info.get("ip"):
            add("public", "IP pública / proveedor", "ok",
                f"{info.get('ip')} · {info.get('isp') or ''} · {info.get('city') or ''} {info.get('country') or ''} · nodo Cloudflare {info.get('colo')}", info)
        else:
            add("public", "IP pública / proveedor", "warn", "No se pudo obtener")

    tasks = [t_gw(), t_inet(), t_dns("dns_local", "DNS de NetPing", f"127.0.0.1:{local_dns_port}"),
             t_dns("dns_public", "DNS público (Cloudflare)", "1.1.1.1"), t_http(), t_ipv6(), t_public()]
    if unbound_port:
        tasks.append(t_dns("dns_unbound", "Unbound (recursivo)", f"127.0.0.1:{unbound_port}"))
    await asyncio.gather(*tasks)
    order = ["iface", "gateway", "internet", "dns_local", "dns_unbound", "dns_public", "http", "ipv6", "public"]
    checks.sort(key=lambda c: order.index(c["id"]) if c["id"] in order else 99)
    worst = "ok"
    for c in checks:
        if c["status"] == "fail":
            worst = "fail"
            break
        if c["status"] == "warn":
            worst = "warn"
    return {"status": worst, "checks": checks, "ts": time.time()}


async def latency_targets() -> list[dict]:
    """Latencia a servicios populares (útil para gaming/streaming)."""
    targets = [
        ("Cloudflare", "1.1.1.1"), ("Google", "8.8.8.8"), ("Quad9", "9.9.9.9"), ("OpenDNS", "208.67.222.222"),
        ("AdGuard", "94.140.14.14"), ("Control D", "76.76.2.0"),
    ]
    res = await asyncio.gather(*(ping(ip, 4) for _, ip in targets))
    return sorted(
        [{"name": n, "host": ip, **r} for (n, ip), r in zip(targets, res)],
        key=lambda x: x["avg"] if x["avg"] is not None else 1e9,
    )


def local_ip_for(host: str) -> str | None:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect((host, 9))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except OSError:
        return None
