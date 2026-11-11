"""Autoconfiguración de Unbound según tu ubicación y tu equipo.

1. Mide desde TU red los resolvers cifrados (DNS-over-TLS) de los grandes proveedores anycast.
   Anycast = te contesta el nodo más cercano a ti, así que la medición ya refleja tu geografía.
2. Elige el modo:
     · reenvío cifrado (DoT) a los 2 proveedores más rápidos y fiables (lo normal: < 20 ms, privado frente al ISP)
     · reenvío simple (puerto 53) si tu red bloquea el 853
     · recursivo (servidores raíz) si ninguno responde bien
   Los servidores se escriben ordenados por latencia (Unbound además prefiere el más rápido).
3. Combina el resultado con el ajuste por hardware (hilos y caché según CPU/RAM).
Se puede repetir solo cada día: si la ruta de tu proveedor cambia, Unbound se adapta.
"""
from __future__ import annotations

import asyncio
import logging
import os
import random
import statistics
import string
import time

from dnslib import DNSRecord

from .dns.upstream import query_server

log = logging.getLogger("bipon.unbound.auto")

# proveedor: (nombre, [(ip, nombre TLS)], filtra contenido)
DOT_PROVIDERS = {
    "cloudflare": ("Cloudflare", [("1.1.1.1", "cloudflare-dns.com"), ("1.0.0.1", "cloudflare-dns.com")]),
    "google": ("Google", [("8.8.8.8", "dns.google"), ("8.8.4.4", "dns.google")]),
    "quad9": ("Quad9", [("9.9.9.9", "dns.quad9.net"), ("149.112.112.112", "dns.quad9.net")]),
    "adguard": ("AdGuard (sin filtro)", [("94.140.14.140", "unfiltered.adguard-dns.com"), ("94.140.14.141", "unfiltered.adguard-dns.com")]),
    "controld": ("Control D (sin filtro)", [("76.76.2.0", "p0.freedns.controld.com"), ("76.76.10.0", "p0.freedns.controld.com")]),
    "mullvad": ("Mullvad", [("194.242.2.2", "dns.mullvad.net")]),
    "dnssb": ("DNS.SB", [("185.222.222.222", "dot.sb"), ("45.11.45.11", "dot.sb")]),
}
POPULAR = ["google.com", "youtube.com", "facebook.com", "whatsapp.net", "netflix.com", "microsoft.com", "apple.com",
           "amazon.com", "instagram.com", "cloudflare.com"]


def _rand() -> str:
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=12)) + ".com"


async def _measure(spec: str, n_popular: int = 8, n_cold: int = 3, timeout: float = 2.5) -> dict:
    """Latencia de consultas populares (caché del proveedor) y en frío (nombre aleatorio)."""
    def usable(resp: bytes) -> bool:
        return len(resp) > 3 and (resp[3] & 0x0F) not in (2, 5)
    times, cold, fails = [], [], 0
    try:  # calentamiento: abre la conexión TLS para no medir el apretón de manos
        await query_server(spec, DNSRecord.question("example.com", "A").pack(), timeout=timeout)
    except Exception:
        pass
    for d in random.sample(POPULAR, min(n_popular, len(POPULAR))):
        try:
            resp, rtt = await query_server(spec, DNSRecord.question(d, "A").pack(), timeout=timeout)
            if usable(resp):
                times.append(rtt)
            else:
                fails += 1
        except Exception:
            fails += 1
    for _ in range(n_cold):
        try:
            resp, rtt = await query_server(spec, DNSRecord.question(_rand(), "A").pack(), timeout=timeout + 1)
            if usable(resp):
                cold.append(rtt)
            else:
                fails += 1
        except Exception:
            fails += 1
    total = n_popular + n_cold
    out = {"reliability": round((total - fails) / total * 100, 1)}
    if times:
        out["median"] = round(statistics.median(times), 2)
        out["min"] = round(min(times), 2)
    if cold:
        out["cold"] = round(statistics.median(cold), 2)
    return out


async def measure_all(proto: str = "dot") -> list[dict]:
    """Mide todos los proveedores (cada IP por separado) en paralelo."""
    jobs = []
    for pid, (name, addrs) in DOT_PROVIDERS.items():
        for ip, tls_name in addrs:
            spec = f"tls://{ip}#{tls_name}" if proto == "dot" else ip
            jobs.append((pid, name, ip, tls_name, spec))
    sem = asyncio.Semaphore(6)

    async def one(j):
        async with sem:
            r = await _measure(j[4])
        return {"provider": j[0], "name": j[1], "ip": j[2], "tls_name": j[3], "proto": proto, **r}
    res = await asyncio.gather(*(one(j) for j in jobs))
    return sorted(res, key=score)


def score(r: dict) -> float:
    if "median" not in r or r.get("reliability", 0) < 85:
        return 1e9
    # lo que más pesa es la consulta normal; la fría y la fiabilidad desempatan
    return r["median"] * 0.75 + r.get("cold", r["median"] * 3) * 0.25 / 3 + (100 - r["reliability"]) * 5


def choose(dot: list[dict], plain: list[dict] | None) -> dict:
    """Decide modo y servidores. Devuelve {"mode", "forward_servers", "chosen", "reason"}."""
    def top_providers(rows, n=2):
        best: dict[str, list[dict]] = {}
        for r in rows:
            if score(r) >= 1e9:
                continue
            best.setdefault(r["provider"], []).append(r)
        ranked = sorted(best.values(), key=lambda lst: min(score(x) for x in lst))[:n]
        return [sorted(lst, key=score) for lst in ranked]
    tops = top_providers(dot)
    if tops:
        servers = [f"{r['ip']}@853#{r['tls_name']}" for lst in tops for r in lst]
        names = ", ".join(f"{lst[0]['name']} ({lst[0]['median']} ms)" for lst in tops)
        return {"mode": "forward-tls", "forward_servers": servers, "chosen": [lst[0]["provider"] for lst in tops],
                "reason": f"Reenvío cifrado (DoT) a los más rápidos desde tu ubicación: {names}"}
    tops = top_providers(plain or [])
    if tops:
        servers = [r["ip"] for lst in tops for r in lst]
        names = ", ".join(f"{lst[0]['name']} ({lst[0]['median']} ms)" for lst in tops)
        return {"mode": "forward", "forward_servers": servers, "chosen": [lst[0]["provider"] for lst in tops],
                "reason": f"Tu red bloquea DNS cifrado (puerto 853): reenvío simple a {names}"}
    return {"mode": "recursive", "forward_servers": [], "chosen": [],
            "reason": "Ningún proveedor respondió bien: modo recursivo (pregunta directamente a los servidores raíz)"}


def _mem_total() -> int:
    try:
        with open("/proc/meminfo") as fh:
            for line in fh:
                if line.startswith("MemTotal:"):
                    return int(line.split()[1]) * 1024
    except OSError:
        pass
    return 0


async def auto_configure(ctx, apply: bool = True) -> dict:
    from .unbound import recommend_config
    dot = await measure_all("dot")
    plain = None if any(score(r) < 1e9 for r in dot) else await measure_all("udp")
    pick = choose(dot, plain)
    base = recommend_config(ctx.settings.get("unbound"), os.cpu_count() or 1, _mem_total())["config"]
    cfg = {**base, "mode": pick["mode"], "forward_servers": pick["forward_servers"]}
    result = {"ts": time.time(), "dot": dot, "plain": plain, **pick, "applied": False}
    if apply:
        ok, msg = await ctx.unbound.apply(cfg)
        if not ok:
            result["error"] = msg
        else:
            ctx.settings.set("unbound", cfg, notify=False)
            ctx.apply_dns_settings()
            result["applied"] = True
            ctx.db.event("info", "unbound", "Unbound autoconfigurado: " + pick["reason"])
    state = {**(ctx.settings.get("unbound_auto") or {}), "last": {k: v for k, v in result.items() if k != "plain"}}
    ctx.settings.set("unbound_auto", state, notify=False)
    return result


async def prewarm(ctx, limit: int = 300) -> int:
    """Precalienta Unbound con los dominios que de verdad usa esta casa (los más consultados en 7 días),
    para que respondan desde su caché en < 1 ms."""
    from .dns.filter import BLOCKED_STATUSES
    if not ctx.unbound.running:
        return 0
    port = int((ctx.settings.get("unbound") or {}).get("port", 5335))
    blocked = ",".join(str(x) for x in BLOCKED_STATUSES) or "-1"
    ctx.db.flush_queries()
    rows = ctx.db.query(f"SELECT domain, COUNT(*) n FROM queries WHERE ts>=? AND status NOT IN ({blocked}) "
                        "GROUP BY domain ORDER BY n DESC LIMIT ?", (time.time() - 7 * 86400, limit))
    sem = asyncio.Semaphore(20)
    ok = 0

    async def one(name, qtype):
        nonlocal ok
        async with sem:
            try:
                await query_server(f"127.0.0.1:{port}", DNSRecord.question(name, qtype).pack(), timeout=3)
                ok += 1
            except Exception:
                pass
    await asyncio.gather(*(one(r["domain"], q) for r in rows for q in ("A", "AAAA")))
    return ok


async def loop(ctx) -> None:
    """Re-ajuste diario si está activado: si la ruta del proveedor cambia, Unbound se adapta solo."""
    await asyncio.sleep(60)
    last_warm = 0.0
    while True:
        if time.time() - last_warm > 6 * 3600 and ctx.unbound.running:
            try:
                n = await prewarm(ctx)
                last_warm = time.time()
                log.info("Unbound precalentado con %s respuestas de los dominios más usados", n)
            except Exception as exc:  # pragma: no cover
                log.debug("precalentado: %s", exc)
        st = ctx.settings.get("unbound_auto") or {}
        last = (st.get("last") or {}).get("ts", 0)
        if st.get("enabled") and time.time() - last > 24 * 3600 and ctx.unbound.available:
            try:
                await auto_configure(ctx, apply=True)
            except Exception as exc:  # pragma: no cover
                log.warning("autoconfiguración de Unbound: %s", exc)
        await asyncio.sleep(3600)
