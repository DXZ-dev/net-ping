"""Botón FIX: diagnostica y repara lo que suele romperse, paso a paso, sin borrar configuración.

Cada paso devuelve {id, name, status, detail}: status = ok (estaba bien), fixed (se arregló),
warn (hay un problema que no se pudo arreglar solo) o skip (no aplica en este equipo).
"""
from __future__ import annotations

import asyncio
import logging
import shutil
import socket
import time

from dnslib import DNSRecord

from .config import DATA_DIR

log = logging.getLogger("bipon.repair")
_lock = asyncio.Lock()


def _step(sid: str, name: str, status: str, detail: str = "") -> dict:
    return {"id": sid, "name": name, "status": status, "detail": detail}


async def _dns_answers(port: int, timeout: float = 2.0) -> float | None:
    """Pregunta al propio servidor DNS de Bipon; devuelve ms o None si no responde."""
    q = DNSRecord.question("example.com").pack()
    loop = asyncio.get_running_loop()
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setblocking(False)
    try:
        t0 = time.perf_counter()
        await loop.sock_sendto(sock, q, ("127.0.0.1", port))
        await asyncio.wait_for(loop.sock_recv(sock, 4096), timeout)
        return (time.perf_counter() - t0) * 1000
    except (OSError, asyncio.TimeoutError):
        return None
    finally:
        sock.close()


async def check_database(c) -> dict:
    name = "Base de datos"
    try:
        res = c.db.scalar("PRAGMA quick_check")
    except Exception as exc:
        return _step("db", name, "warn", f"No se pudo comprobar: {exc}")
    try:
        c.db.flush_queries()
        c.db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    except Exception:
        pass
    if res == "ok":
        return _step("db", name, "ok", "Íntegra")
    return _step("db", name, "warn", f"Daños detectados ({str(res)[:120]}). Restaura una copia o usa «Restablecer de fábrica».")


async def unbound_healthy(ub, timeout: float = 4.0) -> bool:
    if not ub.running:
        return False
    try:
        return bool(await asyncio.wait_for(ub.stats(), timeout))
    except asyncio.TimeoutError:
        return False


async def _revive_unbound(ub, wait: float = 15.0) -> bool:
    """Mata un Unbound colgado (el supervisor lo vuelve a lanzar) o lo arranca si no hay supervisor."""
    if ub.running:
        try:
            ub.proc.kill()
        except ProcessLookupError:
            pass
    elif ub._supervisor is None or ub._supervisor.done():
        await ub.start()
    end = time.time() + wait
    while time.time() < end:
        await asyncio.sleep(1)
        if await unbound_healthy(ub, 2.0):
            return True
    return False


async def check_unbound(c) -> dict:
    name = "Unbound (resolvedor)"
    ub = c.unbound
    if not ub.available or not c.settings.get("unbound.enabled", True):
        return _step("unbound", name, "skip", "No instalado o desactivado")
    if await unbound_healthy(ub):
        return _step("unbound", name, "ok", "En marcha y respondiendo")
    was = "colgado" if ub.running else "parado"
    if await _revive_unbound(ub):
        return _step("unbound", name, "fixed", f"Estaba {was}: reiniciado")
    # la configuración personalizada suele ser la culpable: se reinicia sin ella
    cfg = c.settings.get("unbound") or {}
    if (cfg.get("custom") or "").strip():
        ok, _ = await ub.apply({**cfg, "custom": ""})
        if ok and await _revive_unbound(ub, 10):
            return _step("unbound", name, "fixed", "La configuración personalizada lo rompía: se quitó y Unbound arrancó")
    c.apply_dns_settings()
    return _step("unbound", name, "warn", f"No arranca ({ub.last_error or 'sin detalle'}). Revisa Registros → Unbound.")


async def watchdog(c) -> None:
    """FIX automático y silencioso: si Unbound se cuelga, el DNS de toda la casa se queda esperando (lag).
    Se comprueba cada 30 s y, tras dos fallos seguidos, se reinicia solo."""
    await asyncio.sleep(90)
    fails = 0
    while True:
        try:
            ub = c.unbound
            if ub.available and c.settings.get("unbound.enabled", True) and ub._supervisor and not ub._supervisor.done() and not _lock.locked():
                fails = 0 if await unbound_healthy(ub, 5.0) else fails + 1
                if fails >= 2:
                    fails = 0
                    ok = await _revive_unbound(ub)
                    msg = "Unbound no respondía: reiniciado automáticamente" + ("" if ok else " (sigue fallando)")
                    log.warning(msg)
                    c.db.event("warning" if ok else "error", "reparación", msg)
        except Exception as exc:  # pragma: no cover
            log.debug("vigilante: %s", exc)
        await asyncio.sleep(30)


async def check_upstreams(c) -> dict:
    name = "Servidores DNS de salida"
    stats = c.pool.stats()
    bad = [s for s in stats if s["queries"] >= 5 and s["failures"] / max(1, s["queries"]) > 0.5]
    if stats and len(bad) == len(stats):
        c.apply_dns_settings()  # reinicia las mediciones y aplica el respaldo si Unbound falta
        return _step("upstreams", name, "fixed", "Todos fallaban: se reiniciaron las conexiones")
    if bad:
        return _step("upstreams", name, "warn", "Fallan a menudo: " + ", ".join(s["server"] for s in bad))
    return _step("upstreams", name, "ok", f"{len(stats)} configurados")


async def check_dns_server(c) -> dict:
    name = "Servidor DNS de Bipon"
    d = c.settings.get("dns") or {}
    port = int(d.get("port", 53))
    ms = await _dns_answers(port)
    if ms is not None:
        return _step("dns", name, "ok", f"Responde en {ms:.1f} ms")
    try:
        await c.dns.stop()
        await c.dns.start(d.get("listen", "0.0.0.0"), port)
        c.dns_error = None
    except OSError as exc:
        return _step("dns", name, "warn", f"No puede abrir el puerto {port}: {exc}. Otro programa lo ocupa.")
    ms = await _dns_answers(port)
    if ms is not None:
        return _step("dns", name, "fixed", f"No respondía: reiniciado ({ms:.1f} ms)")
    return _step("dns", name, "warn", "Sigue sin responder tras reiniciarlo")


async def check_blocklists(c) -> dict:
    name = "Listas de bloqueo"
    enabled = c.db.scalar("SELECT COUNT(*) FROM blocklists WHERE enabled=1") or 0
    loaded = len(c.engine.d.exact) + len(c.engine.d.wild)
    if not enabled:
        return _step("lists", name, "skip", "Ninguna lista activada")
    if loaded:
        return _step("lists", name, "ok", f"{loaded:,} dominios cargados".replace(",", "."))
    res = await c.lists.rebuild()
    loaded = len(c.engine.d.exact) + len(c.engine.d.wild)
    if loaded:
        return _step("lists", name, "fixed", f"Estaban vacías: reconstruidas ({loaded} dominios)")
    asyncio.create_task(c.lists.update())
    return _step("lists", name, "fixed", f"Sin datos ({res}); descargándolas de nuevo en segundo plano")


async def check_cache(c) -> dict:
    name = "Caché de Bipon"
    pct = c.cache.usage()
    if pct >= 90:
        n = c.cache.trim(60)
        return _step("cache", name, "fixed", f"Estaba al {pct:.0f} %: se liberaron {n} entradas")
    return _step("cache", name, "ok", f"{pct:.1f} % en uso")


async def check_firewall(c) -> dict:
    name = "Firewall / puerta de enlace"
    r = c.router
    cfg = r.cfg()
    if not cfg.get("enabled"):
        return _step("firewall", name, "skip", "Modo router desactivado")
    if not r.available:
        return _step("firewall", name, "warn", "nftables no está instalado")
    if r.pending:
        r.confirm()
        return _step("firewall", name, "fixed", "Había un cambio sin confirmar: confirmado")
    fixed = []
    try:
        if open("/proc/sys/net/ipv4/ip_forward").read().strip() != "1":
            fixed.append("reenvío IP")
    except OSError:
        pass
    if not await r._current_table():
        fixed.append("reglas")
    if fixed:
        r._loaded_text = None
        res = await r.apply(cfg, confirm_required=False)
        if not res.get("ok"):
            return _step("firewall", name, "warn", f"No se pudieron cargar las reglas: {res.get('message')}")
        return _step("firewall", name, "fixed", "Faltaban: " + ", ".join(fixed) + " (recargado)")
    return _step("firewall", name, "ok", "Reglas cargadas")


async def check_shaping(c) -> dict:
    """Colas de velocidad olvidadas: sin límites activos solo añaden latencia."""
    name = "Colas de velocidad"
    cfg = c.router.cfg()
    lan = cfg.get("lan")
    if not lan or not shutil.which("tc"):
        return _step("shaping", name, "skip", "No aplica")
    limited = any(d.get("limit_down") or d.get("limit_up") for d in c.devices.devices.values())
    _, out = await c.router._tc("qdisc", "show", "dev", lan)
    if "htb" in out and not limited:
        await c.router._tc("qdisc", "del", "dev", lan, "root")
        await c.router._tc("qdisc", "del", "dev", lan, "ingress")
        return _step("shaping", name, "fixed", "Había colas sin ningún límite activo: eliminadas (menos latencia)")
    return _step("shaping", name, "ok", "Correctas")


async def check_disk(c) -> dict:
    name = "Espacio en disco"
    try:
        du = shutil.disk_usage(DATA_DIR)
    except OSError as exc:
        return _step("disk", name, "warn", str(exc))
    free_pct = du.free * 100 / du.total
    if free_pct >= 5:
        return _step("disk", name, "ok", f"{du.free / 1e9:.1f} GB libres")
    from . import backups
    removed = backups.prune(c)
    c.db.execute("DELETE FROM queries WHERE ts < ?", (time.time() - 7 * 86400,))
    return _step("disk", name, "fixed", f"Casi lleno ({free_pct:.1f} % libre): se borraron consultas de más de 7 días"
                                         + (f" y {removed} copias antiguas" if removed else ""))


async def check_tasks(c) -> dict:
    name = "Procesos internos"
    dead = [t for t in c.tasks if t.done() and not t.cancelled() and t.exception() is not None]
    if not dead:
        return _step("tasks", name, "ok", f"{len(c.tasks)} en marcha")
    names = [t.get_name() for t in dead]
    return _step("tasks", name, "warn", "Se detuvieron por un error: " + ", ".join(names) + ". Reinicia Bipon (Ajustes → Sistema).")


async def check_latency(c) -> dict:
    name = "Latencia"
    from .pinger import ping
    gw = c.devices.gateway
    r_gw, r_net = await asyncio.gather(ping(gw, 4) if gw else asyncio.sleep(0), ping("1.1.1.1", 4), return_exceptions=True)
    parts = []
    for label, r in (("router", r_gw), ("Internet", r_net)):
        if isinstance(r, dict) and r.get("avg") is not None:
            parts.append(f"{label} {r['avg']:.1f} ms (jitter {r.get('jitter') or 0:.1f} ms)")
    lan = r_gw.get("avg") if isinstance(r_gw, dict) else None
    if lan is not None and lan > 10:
        return _step("latency", name, "warn", "; ".join(parts) + ". El router tarda demasiado: revisa el cable o la carga del router.")
    return _step("latency", name, "ok", "; ".join(parts) or "sin medición")


STEPS = [check_database, check_unbound, check_upstreams, check_dns_server, check_blocklists, check_cache,
         check_firewall, check_shaping, check_disk, check_tasks, check_latency]


async def run(c) -> dict:
    if _lock.locked():
        raise RuntimeError("Ya hay una reparación en curso")
    async with _lock:
        t0 = time.time()
        steps = []
        for fn in STEPS:
            try:
                steps.append(await asyncio.wait_for(fn(c), 40))
            except Exception as exc:
                log.warning("reparación %s: %s", fn.__name__, exc)
                steps.append(_step(fn.__name__.replace("check_", ""), fn.__name__, "warn", f"Error al comprobar: {exc}"))
        fixed = sum(s["status"] == "fixed" for s in steps)
        warns = sum(s["status"] == "warn" for s in steps)
        summary = (f"{fixed} problema(s) reparado(s)" if fixed else "Todo estaba bien") + (f", {warns} aviso(s)" if warns else "")
        c.db.event("warning" if warns else "info", "reparación", f"FIX: {summary}")
        return {"ts": time.time(), "seconds": round(time.time() - t0, 1), "fixed": fixed, "warnings": warns,
                "summary": summary, "steps": steps}
