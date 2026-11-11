"""API REST de Bipon Net."""
from __future__ import annotations

import asyncio
import json
import os
import platform
import re
import shutil
import socket
import time
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request, Response
from fastapi.responses import StreamingResponse

from . import __version__, auth, nat, netinfo, tools, wireguard
from .catalog import DNS_PRESETS, RECOMMENDED_LISTS, SERVICES
from .config import DEFAULT_PROFILE_ID
from .dns.filter import BLOCKED_STATUSES, STATUS_NAMES
from .monitor import optimize_dns
from .pinger import ping, valid_host
from .speedtest import lan_payload

public = APIRouter(prefix="/api")
api = APIRouter(prefix="/api", dependencies=[Depends(auth.require_auth)])

BLOCKED_SQL = "(" + ",".join(str(s) for s in BLOCKED_STATUSES) + ")"


def ctx(request: Request):
    return request.app.state.ctx


def sse(gen):
    async def wrap():
        try:
            async for ev in gen:
                yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"
        except ValueError as exc:
            yield f"data: {json.dumps({'error': str(exc)})}\n\n"
        except Exception as exc:  # pragma: no cover
            yield f"data: {json.dumps({'error': f'{type(exc).__name__}: {exc}'})}\n\n"
        yield "data: {\"end\": true}\n\n"
    return StreamingResponse(wrap(), media_type="text/event-stream", headers={"cache-control": "no-cache", "x-accel-buffering": "no"})


def client_ip(request: Request) -> str:
    return request.client.host if request.client else "?"


# =====================================================================
# Autenticación
# =====================================================================
@public.get("/auth/status")
async def auth_status(request: Request):
    u = auth.current_user(request) if auth.password_set() else None
    c = ctx(request)
    return {"setup_required": not auth.password_set(), "authenticated": u is not None, "version": __version__,
            "setup_completed": bool((c.settings.get("setup") or {}).get("completed")),
            "user": auth.public_user(u) if u and not u.get("token") else (u if u else None)}


def _set_cookie(resp: Response, request: Request, user: dict):
    resp.set_cookie(auth.COOKIE, auth.make_token(user), max_age=auth.SESSION_TTL, httponly=True, samesite="strict",
                    secure=request.url.scheme == "https")


def me(request: Request) -> dict:
    return request.state.user


@public.post("/auth/setup")
async def auth_setup(request: Request, response: Response, body: dict = Body(...)):
    if auth.password_set():
        raise HTTPException(409, "El administrador ya está configurado")
    try:
        auth.create_user((body.get("username") or "admin").strip(), body.get("password", ""), "admin")
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    u = auth.get_user(username=(body.get("username") or "admin").strip())
    _set_cookie(response, request, u)
    return {"ok": True}


@public.post("/auth/login")
async def auth_login(request: Request, response: Response, body: dict = Body(...)):
    ip = client_ip(request)
    auth.throttle(ip)
    u = auth.get_user(username=(body.get("username") or "admin").strip())
    if not u or not auth.verify_password(body.get("password", ""), u["password_hash"]):
        auth.record_failure(ip)
        await asyncio.sleep(0.8)
        raise HTTPException(401, "Usuario o contraseña incorrectos")
    if u["totp_enabled"]:
        code = (body.get("code") or "").strip()
        if not code:
            return {"ok": False, "totp_required": True}
        if not auth.totp_verify(u["totp_secret"], code):
            auth.record_failure(ip)
            raise HTTPException(401, "Código de verificación incorrecto")
    ctx(request).db.execute("UPDATE users SET last_login=? WHERE id=?", (time.time(), u["id"]))
    _set_cookie(response, request, u)
    return {"ok": True}


@public.post("/auth/logout")
async def auth_logout(response: Response):
    response.delete_cookie(auth.COOKIE)
    return {"ok": True}


def _real_user(request: Request) -> dict:
    u = me(request)
    if u.get("token"):
        raise HTTPException(400, "Acción no disponible con un token de API")
    return auth.get_user(uid=u["id"])


@api.post("/auth/password")
async def change_password(request: Request, response: Response, body: dict = Body(...)):
    u = _real_user(request)
    if not auth.verify_password(body.get("current", ""), u["password_hash"]):
        raise HTTPException(400, "La contraseña actual no es correcta")
    if len(body.get("new", "")) < 8:
        raise HTTPException(400, "Mínimo 8 caracteres")
    ctx(request).db.execute("UPDATE users SET password_hash=? WHERE id=?", (auth.hash_password(body["new"]), u["id"]))
    _set_cookie(response, request, auth.get_user(uid=u["id"]))
    return {"ok": True}


@api.post("/auth/2fa/setup")
async def totp_setup(request: Request):
    u = _real_user(request)
    secret = auth.totp_secret()
    ctx(request).db.execute("UPDATE users SET totp_secret=? WHERE id=? AND totp_enabled=0", (secret, u["id"]))
    from .wireguard import qr_svg
    uri = auth.totp_uri(secret, u["username"])
    return {"secret": secret, "uri": uri, "qr": qr_svg(uri)}


@api.post("/auth/2fa/enable")
async def totp_enable(request: Request, response: Response, body: dict = Body(...)):
    u = _real_user(request)
    if not u["totp_secret"] or not auth.totp_verify(u["totp_secret"], body.get("code", "")):
        raise HTTPException(400, "Código incorrecto. Comprueba la hora del móvil.")
    ctx(request).db.execute("UPDATE users SET totp_enabled=1 WHERE id=?", (u["id"],))
    _set_cookie(response, request, auth.get_user(uid=u["id"]))
    return {"ok": True}


@api.post("/auth/2fa/disable")
async def totp_disable(request: Request, response: Response, body: dict = Body(...)):
    u = _real_user(request)
    if not auth.verify_password(body.get("password", ""), u["password_hash"]):
        raise HTTPException(400, "Contraseña incorrecta")
    ctx(request).db.execute("UPDATE users SET totp_enabled=0, totp_secret=NULL WHERE id=?", (u["id"],))
    _set_cookie(response, request, auth.get_user(uid=u["id"]))
    return {"ok": True}


# ---------------------------------------------------------------- usuarios y tokens (admin)
@api.get("/users")
async def users_list(request: Request):
    rows = await ctx(request).db.aquery("SELECT * FROM users ORDER BY id")
    return [auth.public_user(r) for r in rows]


@api.post("/users")
async def users_create(request: Request, body: dict = Body(...)):
    try:
        return auth.create_user((body.get("username") or "").strip(), body.get("password", ""), body.get("role", "parent"))
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@api.patch("/users/{uid}")
async def users_update(request: Request, uid: int, body: dict = Body(...)):
    c = ctx(request)
    u = auth.get_user(uid=uid)
    if not u:
        raise HTTPException(404, "Usuario no encontrado")
    if "role" in body:
        if body["role"] not in auth.ROLES:
            raise HTTPException(400, "Rol inválido")
        if u["role"] == "admin" and body["role"] != "admin" and c.db.scalar("SELECT COUNT(*) FROM users WHERE role='admin'") <= 1:
            raise HTTPException(400, "Debe quedar al menos un administrador")
        c.db.execute("UPDATE users SET role=? WHERE id=?", (body["role"], uid))
    if body.get("password"):
        if len(body["password"]) < 8:
            raise HTTPException(400, "Mínimo 8 caracteres")
        c.db.execute("UPDATE users SET password_hash=? WHERE id=?", (auth.hash_password(body["password"]), uid))
    if body.get("reset_2fa"):
        c.db.execute("UPDATE users SET totp_enabled=0, totp_secret=NULL WHERE id=?", (uid,))
    return auth.public_user(auth.get_user(uid=uid))


@api.delete("/users/{uid}")
async def users_delete(request: Request, uid: int):
    c = ctx(request)
    u = auth.get_user(uid=uid)
    if not u:
        raise HTTPException(404)
    if u["id"] == me(request).get("id"):
        raise HTTPException(400, "No puedes eliminar tu propio usuario")
    if u["role"] == "admin" and c.db.scalar("SELECT COUNT(*) FROM users WHERE role='admin'") <= 1:
        raise HTTPException(400, "Debe quedar al menos un administrador")
    c.db.execute("DELETE FROM users WHERE id=?", (uid,))
    return {"ok": True}


@api.get("/tokens")
async def tokens_list(request: Request):
    return await ctx(request).db.aquery("SELECT id,name,role,created,last_used FROM api_tokens ORDER BY id")


@api.post("/tokens")
async def tokens_create(request: Request, body: dict = Body(...)):
    try:
        token = auth.create_api_token((body.get("name") or "token").strip(), body.get("role", "viewer"))
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return {"token": token}


@api.delete("/tokens/{tid}")
async def tokens_delete(request: Request, tid: int):
    ctx(request).db.execute("DELETE FROM api_tokens WHERE id=?", (tid,))
    return {"ok": True}


# =====================================================================
# Panel y estadísticas
# =====================================================================
def _device_names(c) -> dict[str, str]:
    """Nombre por IP y por MAC (las consultas se agrupan por MAC cuando se conoce)."""
    out = {}
    for m, d in c.devices.devices.items():
        name = d.get("name") or d.get("friendly") or d.get("hostname") or d.get("ip") or m
        out[m] = name
        if d.get("ip"):
            out[d["ip"]] = name
    return out


@api.get("/stats/summary")
async def stats_summary(request: Request, hours: int = Query(24, ge=1, le=24 * 90)):
    c = ctx(request)
    since = time.time() - hours * 3600
    db = c.db

    def compute():
        t = db.one(f"""SELECT COUNT(*) total, SUM(status IN {BLOCKED_SQL}) blocked, SUM(status IN (1,10)) cached,
                       COUNT(DISTINCT COALESCE(mac, client)) clients, AVG(CASE WHEN status=0 THEN rtt END) fwd_rtt, AVG(rtt) avg_rtt
                       FROM queries WHERE ts>=?""", (since,))
        bucket = max(60, int(hours * 3600 / 96))
        timeline = db.query(f"""SELECT CAST(ts/{bucket} AS INT)*{bucket} AS t, COUNT(*) total,
                                SUM(status IN {BLOCKED_SQL}) blocked, SUM(status IN (1,10)) cached
                                FROM queries WHERE ts>=? GROUP BY t ORDER BY t""", (since,))
        top_domains = db.query(f"SELECT domain, COUNT(*) n FROM queries WHERE ts>=? AND status NOT IN {BLOCKED_SQL} GROUP BY domain ORDER BY n DESC LIMIT 10", (since,))
        top_blocked = db.query(f"SELECT domain, COUNT(*) n FROM queries WHERE ts>=? AND status IN {BLOCKED_SQL} GROUP BY domain ORDER BY n DESC LIMIT 10", (since,))
        top_clients = db.query(f"""SELECT COALESCE(mac, client) who, MAX(mac) mac, MAX(client) client,
                                    COUNT(*) n, SUM(status IN {BLOCKED_SQL}) blocked FROM queries WHERE ts>=? GROUP BY who ORDER BY n DESC LIMIT 10""", (since,))
        qtypes = db.query("SELECT qtype, COUNT(*) n FROM queries WHERE ts>=? GROUP BY qtype ORDER BY n DESC LIMIT 8", (since,))
        reasons = db.query(f"SELECT status, COUNT(*) n FROM queries WHERE ts>=? GROUP BY status ORDER BY n DESC", (since,))
        upstreams = db.query("SELECT upstream, COUNT(*) n, AVG(rtt) rtt FROM queries WHERE ts>=? AND status=0 AND upstream<>'' GROUP BY upstream ORDER BY n DESC LIMIT 6", (since,))
        return t, bucket, timeline, top_domains, top_blocked, top_clients, qtypes, reasons, upstreams

    t, bucket, timeline, top_domains, top_blocked, top_clients, qtypes, reasons, upstreams = await asyncio.to_thread(compute)
    names = _device_names(c)
    for cl in top_clients:
        cl["name"] = names.get(cl.get("mac") or "", "") or names.get(cl["client"], cl["client"])
    total = t["total"] or 0
    return {
        "hours": hours, "bucket": bucket, "total": total, "blocked": t["blocked"] or 0, "cached": t["cached"] or 0,
        "blocked_pct": round((t["blocked"] or 0) / total * 100, 1) if total else 0,
        "clients": t["clients"] or 0, "avg_upstream_ms": round(t["fwd_rtt"] or 0, 1), "avg_response_ms": round(t["avg_rtt"] or 0, 2),
        "timeline": timeline, "top_domains": top_domains, "top_blocked": top_blocked, "top_clients": top_clients,
        "qtypes": qtypes, "statuses": [{"status": r["status"], "name": STATUS_NAMES.get(r["status"], "?"), "n": r["n"]} for r in reasons],
        "upstreams": [{"upstream": u["upstream"], "n": u["n"], "rtt": round(u["rtt"] or 0, 1)} for u in upstreams],
        "domains_blocked": c.lists.last_build.get("total", 0),
    }


def _read_proc(path: str) -> str:
    try:
        with open(path) as fh:
            return fh.read()
    except Exception:
        return ""


@api.get("/system")
async def system_info(request: Request):
    c = ctx(request)
    mem = {}
    for line in _read_proc("/proc/meminfo").splitlines():
        k, _, v = line.partition(":")
        mem[k] = int(v.split()[0]) * 1024 if v.split() else 0
    load = _read_proc("/proc/loadavg").split()[:3]
    temp = None
    for zone in ("/sys/class/thermal/thermal_zone0/temp", "/sys/class/hwmon/hwmon0/temp1_input"):
        v = _read_proc(zone).strip()
        if v.isdigit():
            temp = round(int(v) / 1000, 1)
            break
    du = shutil.disk_usage(str(c.settings.path.parent))
    b = c.settings.get("blocking")
    return {
        "version": __version__, "hostname": socket.gethostname(), "python": platform.python_version(),
        "platform": f"{platform.system()} {platform.release()} {platform.machine()}",
        "uptime": int(float((_read_proc("/proc/uptime") or "0").split()[0])), "app_uptime": int(time.time() - c.started),
        "load": [float(x) for x in load] if load else [], "cpus": os.cpu_count(),
        "memory": {"total": mem.get("MemTotal", 0), "available": mem.get("MemAvailable", 0)},
        "disk": {"total": du.total, "free": du.free}, "temperature": temp,
        "interfaces": netinfo.interfaces(), "gateway": netinfo.default_gateway(), "ip": c.devices.self_ip or netinfo.primary_ip(),
        "dns": {**c.dns.stats(), "port": c.settings.get("dns.port"), "error": c.dns_error, "fallback": c.upstream_fallback},
        "blocking": {"enabled": b.get("enabled", True), "paused_until": b.get("paused_until", 0), "active": c.dns.blocking_enabled},
        "unbound": c.unbound.status(), "monitor": c.monitor.last,
        "lists": {**c.lists.last_build, "updating": c.lists.updating},
    }


@api.get("/events")
async def events(request: Request, limit: int = 50):
    return await ctx(request).db.aquery("SELECT * FROM events ORDER BY ts DESC LIMIT ?", (min(limit, 500),))


# =====================================================================
# Registro de consultas
# =====================================================================
@api.get("/queries")
async def queries(request: Request, limit: int = Query(100, le=1000), offset: int = 0, client: str = "",
                  domain: str = "", status: str = "", since: float = 0):
    c = ctx(request)
    where, params = ["1=1"], []
    if client:  # IP o MAC del dispositivo
        where.append("(client=? OR mac=?)")
        params += [client, client.lower()]
    if domain:
        where.append("domain LIKE ?")
        params.append(f"%{domain.lower()}%")
    if status == "blocked":
        where.append(f"status IN {BLOCKED_SQL}")
    elif status == "allowed":
        where.append(f"status NOT IN {BLOCKED_SQL}")
    elif status == "cached":
        where.append("status IN (1,10)")
    elif status.isdigit():
        where.append("status=?")
        params.append(int(status))
    if since:
        where.append("ts>?")
        params.append(since)
    c.db.flush_queries()
    rows = await c.db.aquery(f"SELECT * FROM queries WHERE {' AND '.join(where)} ORDER BY id DESC LIMIT ? OFFSET ?",
                             (*params, limit, offset))
    names = _device_names(c)
    for r in rows:
        r["client_name"] = names.get(r.get("mac") or "", "") or names.get(r["client"], "")
        r["status_name"] = STATUS_NAMES.get(r["status"], "?")
        r["blocked"] = r["status"] in BLOCKED_STATUSES
    return rows


# =====================================================================
# Bloqueo, reglas, listas, apps
# =====================================================================
@api.get("/blocking")
async def blocking_get(request: Request):
    c = ctx(request)
    return {**c.settings.get("blocking"), "active": c.dns.blocking_enabled}


@api.post("/blocking")
async def blocking_set(request: Request, body: dict = Body(...)):
    c = ctx(request)
    b = c.settings.get("blocking")
    if "enabled" in body:
        b["enabled"] = bool(body["enabled"])
        b["paused_until"] = 0
    if "pause_seconds" in body:
        secs = int(body["pause_seconds"])
        b["paused_until"] = time.time() + secs if secs > 0 else 0
    c.settings.set("blocking", b)
    c.apply_blocking()
    return {**b, "active": c.dns.blocking_enabled}


RULE_KINDS = ("allow", "deny", "regex_allow", "regex_deny")


@api.get("/rules")
async def rules_list(request: Request):
    return await ctx(request).db.aquery("SELECT * FROM domain_rules ORDER BY created DESC")


@api.post("/rules")
async def rules_add(request: Request, body: dict = Body(...)):
    c = ctx(request)
    kind = body.get("kind")
    values = body.get("values") or [body.get("value", "")]
    if kind not in RULE_KINDS:
        raise HTTPException(400, "tipo de regla inválido")
    added = 0
    for v in values:
        v = (v or "").strip()
        if not v:
            continue
        if kind.startswith("regex"):
            try:
                re.compile(v)
            except re.error as exc:
                raise HTTPException(400, f"Regex inválida: {exc}")
        else:
            v = v.lower().removeprefix("http://").removeprefix("https://").split("/")[0].lstrip("*.").rstrip(".")
            if not re.match(r"^[a-z0-9_.-]+$", v):
                raise HTTPException(400, f"Dominio inválido: {v}")
            # una regla de permitir anula una de denegar sobre el mismo dominio y viceversa
            opposite = "deny" if kind == "allow" else "allow"
            await c.db.aexecute("DELETE FROM domain_rules WHERE value=? AND kind=?", (v, opposite))
        await c.db.aexecute("INSERT OR REPLACE INTO domain_rules(value,kind,comment,enabled,created) VALUES (?,?,?,1,?)",
                            (v, kind, body.get("comment", ""), time.time()))
        added += 1
    await c.lists.rebuild()
    c.cache.clear()
    return {"added": added}


@api.patch("/rules/{rid}")
async def rules_update(request: Request, rid: int, body: dict = Body(...)):
    c = ctx(request)
    if "enabled" in body:
        await c.db.aexecute("UPDATE domain_rules SET enabled=? WHERE id=?", (1 if body["enabled"] else 0, rid))
    if "comment" in body:
        await c.db.aexecute("UPDATE domain_rules SET comment=? WHERE id=?", (body["comment"], rid))
    await c.lists.rebuild()
    c.cache.clear()
    return {"ok": True}


@api.delete("/rules/{rid}")
async def rules_delete(request: Request, rid: int):
    c = ctx(request)
    await c.db.aexecute("DELETE FROM domain_rules WHERE id=?", (rid,))
    await c.lists.rebuild()
    c.cache.clear()
    return {"ok": True}


@api.get("/check")
async def check_domain(request: Request, domain: str, client: str = ""):
    c = ctx(request)
    domain = domain.strip().lower().rstrip(".")
    pol = c.devices.policy_for(client) if client else c.devices.policy_for("0.0.0.0")
    d = c.engine.check(domain, pol, c.dns.blocking_enabled, c.dns.global_services)
    return {**c.engine.explain(domain), "decision": None if d is None else {"status": STATUS_NAMES[d.status], "reason": d.reason,
                                                                             "blocked": d.blocked, "cname": d.cname},
            "profile": pol.profile_name}


@api.get("/lists")
async def lists_get(request: Request):
    c = ctx(request)
    rows = await c.db.aquery("SELECT * FROM blocklists ORDER BY category, id")
    have = {r["url"] for r in rows}
    return {"lists": rows, "recommended": [r for r in RECOMMENDED_LISTS if r["url"] not in have],
            "build": c.lists.last_build, "updating": c.lists.updating}


@api.post("/lists")
async def lists_add(request: Request, body: dict = Body(...)):
    c = ctx(request)
    url = (body.get("url") or "").strip()
    if not re.match(r"^https?://\S+$", url):
        raise HTTPException(400, "URL inválida (debe empezar por http:// o https://)")
    cat = body.get("category", "ads")
    if cat not in ("ads", "security", "adult", "custom"):
        cat = "custom"
    try:
        lid = await c.db.aexecute("INSERT INTO blocklists(url,name,category,enabled) VALUES (?,?,?,1)",
                                  (url, body.get("name") or url.split("/")[2], cat))
    except Exception:
        raise HTTPException(409, "Esa lista ya existe")
    res = await c.lists.update(lid)
    return {"id": lid, "result": res[0] if res else None}


@api.patch("/lists/{lid}")
async def lists_update(request: Request, lid: int, body: dict = Body(...)):
    c = ctx(request)
    if "enabled" in body:
        await c.db.aexecute("UPDATE blocklists SET enabled=? WHERE id=?", (1 if body["enabled"] else 0, lid))
    if "name" in body:
        await c.db.aexecute("UPDATE blocklists SET name=? WHERE id=?", (body["name"], lid))
    if "category" in body and body["category"] in ("ads", "security", "adult", "custom"):
        await c.db.aexecute("UPDATE blocklists SET category=? WHERE id=?", (body["category"], lid))
    if body.get("enabled") and not c.lists.path_for(lid).exists():
        await c.lists.update(lid)
    else:
        await c.lists.rebuild()
    c.cache.clear()
    return {"ok": True}


@api.delete("/lists/{lid}")
async def lists_delete(request: Request, lid: int):
    c = ctx(request)
    await c.db.aexecute("DELETE FROM blocklists WHERE id=?", (lid,))
    c.lists.delete_file(lid)
    await c.lists.rebuild()
    c.cache.clear()
    return {"ok": True}


@api.post("/lists/update")
async def lists_refresh(request: Request, body: dict = Body(default={})):
    c = ctx(request)
    res = await c.lists.update(body.get("id"))
    c.cache.clear()
    return {"results": res, "build": c.lists.last_build}


@api.get("/services")
async def services_get(request: Request):
    c = ctx(request)
    blocked = set(c.settings.get("blocked_services") or [])
    return {"services": [{"id": s["id"], "name": s["name"], "category": s["category"], "domains": s["domains"],
                          "blocked": s["id"] in blocked} for s in SERVICES]}


@api.put("/services")
async def services_set(request: Request, body: dict = Body(...)):
    c = ctx(request)
    ids = [s for s in body.get("blocked", []) if any(x["id"] == s for x in SERVICES)]
    c.settings.set("blocked_services", ids)
    c.apply_blocking()
    c.cache.clear()
    return {"blocked": ids}


# =====================================================================
# Perfiles parentales
# =====================================================================
PROFILE_FIELDS = {"name", "color", "filtering", "safesearch", "youtube_restricted", "threat_protection", "adult_filter",
                  "blocked_services", "schedules", "block_all", "icon", "limits"}


@api.get("/profiles")
async def profiles_get(request: Request):
    c = ctx(request)
    counts: dict[str, int] = {}
    for d in c.devices.devices.values():
        counts[d.get("profile") or DEFAULT_PROFILE_ID] = counts.get(d.get("profile") or DEFAULT_PROFILE_ID, 0) + 1
    return [{**p, "devices": counts.get(p["id"], 0)} for p in c.settings.get("profiles")]


def _validate_schedules(scheds: list) -> list:
    out = []
    for s in scheds or []:
        if not re.match(r"^\d{2}:\d{2}$", s.get("start", "")) or not re.match(r"^\d{2}:\d{2}$", s.get("end", "")):
            raise HTTPException(400, "Horario inválido (HH:MM)")
        out.append({"label": str(s.get("label", ""))[:40], "days": [int(d) for d in s.get("days", list(range(7))) if 0 <= int(d) <= 6],
                    "start": s["start"], "end": s["end"], "action": s.get("action", "block_all") if s.get("action") in ("block_all", "block_services") else "block_all",
                    "services": list(s.get("services", [])), "enabled": bool(s.get("enabled", True))})
    return out


@api.post("/profiles")
async def profiles_create(request: Request, body: dict = Body(...)):
    return create_profile(ctx(request), body)


def create_profile(c, body: dict) -> dict:
    """Crea un perfil (opcionalmente desde plantilla: kids, teen, guest, iot)."""
    profiles = c.settings.get("profiles")
    pid = re.sub(r"[^a-z0-9]+", "-", (body.get("name") or "perfil").lower()).strip("-")[:20] or "perfil"
    base = pid
    i = 2
    while any(p["id"] == pid for p in profiles):
        pid = f"{base}-{i}"
        i += 1
    tpl = body.get("template")
    prof = {"id": pid, "name": body.get("name") or "Perfil", "color": body.get("color", "#8b5cf6"), "filtering": True,
            "safesearch": False, "youtube_restricted": False, "threat_protection": True, "adult_filter": False,
            "blocked_services": [], "schedules": [], "block_all": False}
    if tpl == "kids":
        prof.update(safesearch=True, youtube_restricted=True, adult_filter=True,
                    blocked_services=["tiktok", "tinder", "gambling", "vpn_proxy", "public_doh"],
                    schedules=[{"label": "Hora de dormir", "days": list(range(7)), "start": "22:00", "end": "07:00", "action": "block_all", "services": [], "enabled": True}])
    elif tpl == "teen":
        prof.update(safesearch=True, adult_filter=True, blocked_services=["gambling", "tinder", "vpn_proxy", "public_doh"],
                    schedules=[{"label": "Deberes", "days": [0, 1, 2, 3, 4], "start": "16:00", "end": "19:00", "action": "block_services",
                                "services": ["tiktok", "instagram", "fortnite", "roblox", "youtube", "twitch"], "enabled": True},
                               {"label": "Noche", "days": list(range(7)), "start": "23:30", "end": "07:00", "action": "block_all", "services": [], "enabled": True}])
    elif tpl == "work":
        prof.update(color="#0ea5e9", adult_filter=True, blocked_services=["gambling", "tinder", "vpn_proxy", "public_doh"],
                    schedules=[{"label": "Horario laboral", "days": [0, 1, 2, 3, 4], "start": "09:00", "end": "18:00", "action": "block_services",
                                "services": ["tiktok", "instagram", "facebook", "snapchat", "twitch", "netflix", "fortnite", "roblox", "steam",
                                             "disneyplus", "primevideo", "hbomax", "kick"], "enabled": True}])
    elif tpl == "gamer":
        prof.update(color="#22c55e", adult_filter=False, blocked_services=["vpn_proxy"], threat_protection=True)
    elif tpl == "guest":
        prof.update(adult_filter=True, blocked_services=["vpn_proxy"])
    elif tpl == "iot":
        prof.update(blocked_services=[])
    profiles.append(prof)
    c.settings.set("profiles", profiles)
    return prof


@api.put("/profiles/{pid}")
async def profiles_update(request: Request, pid: str, body: dict = Body(...)):
    c = ctx(request)
    profiles = c.settings.get("profiles")
    for p in profiles:
        if p["id"] == pid:
            for k, v in body.items():
                if k in PROFILE_FIELDS:
                    p[k] = _validate_schedules(v) if k == "schedules" else v
            c.settings.set("profiles", profiles)
            c.cache.clear()
            return p
    raise HTTPException(404, "Perfil no encontrado")


@api.delete("/profiles/{pid}")
async def profiles_delete(request: Request, pid: str):
    c = ctx(request)
    if pid == DEFAULT_PROFILE_ID:
        raise HTTPException(400, "El perfil predeterminado no se puede eliminar")
    c.settings.set("profiles", [p for p in c.settings.get("profiles") if p["id"] != pid])
    await c.db.aexecute("UPDATE devices SET profile=? WHERE profile=?", (DEFAULT_PROFILE_ID, pid))
    c.devices.load()
    return {"ok": True}


# =====================================================================
# Dispositivos
# =====================================================================
@api.get("/devices")
async def devices_list(request: Request):
    c = ctx(request)
    since = time.time() - 86400
    c.db.flush_queries()
    counts = {r["who"]: r for r in await c.db.aquery(
        f"SELECT COALESCE(mac, client) who, COUNT(*) n, SUM(status IN {BLOCKED_SQL}) blocked, MAX(ts) last FROM queries WHERE ts>=? GROUP BY who", (since,))}
    out = []
    now = time.time()
    rates = c.rates.snapshot()
    for d in c.devices.devices.values():
        q = counts.get(d["mac"]) or counts.get(d.get("ip") or "", {})
        pol = c.devices.policy_for(d["ip"]) if d.get("ip") else None
        out.append({**d, "queries": q.get("n", 0), "blocked": q.get("blocked", 0), "last_query": q.get("last"),
                    "parent_effective": d.get("parent") or d.get("auto_parent"),
                    "services": json.loads(d.get("services") or "[]"), "ports": json.loads(d.get("ports") or "[]"),
                    "rate": rates.get(d["mac"]),
                    "online": bool(d.get("online")) or (now - c.devices.activity.get(d.get("ip") or "", 0) < 600),
                    "internet_blocked": bool(pol and pol.block_all), "block_reason": pol.block_reason if pol else "",
                    "is_gateway": d.get("ip") == c.devices.gateway, "is_self": d.get("ip") == c.devices.self_ip})
    out.sort(key=lambda x: (not x["online"], tuple(int(p) for p in (x.get("ip") or "999.999.999.999").split(".")) if (x.get("ip") or "").count(".") == 3 else (999,)))
    return {"devices": out, "scanning": c.devices.scanning, "last_scan": c.devices.last_scan, "gateway": c.devices.gateway,
            "self_ip": c.devices.self_ip}


@api.get("/devices/rates")
async def devices_rates(request: Request):
    """Velocidad actual de cada dispositivo (modo router, Proxmox, este equipo) y total de la línea (UPnP)."""
    return ctx(request).rates.info()


@api.get("/devices/stream")
async def devices_stream(request: Request):
    """Eventos en tiempo real (SSE): dispositivo nuevo, conectado, desconectado, actualizado."""
    c = ctx(request)
    q = c.devices.subscribe()

    async def gen():
        try:
            yield ": conectado\n\n"
            while True:
                if await request.is_disconnected():
                    break
                try:
                    ev = await asyncio.wait_for(q.get(), 15)
                    yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"
                except asyncio.TimeoutError:
                    yield ": ping\n\n"
        finally:
            c.devices.unsubscribe(q)
    return StreamingResponse(gen(), media_type="text/event-stream", headers={"cache-control": "no-cache", "x-accel-buffering": "no"})


def _live_snapshot(c) -> dict:
    st = c.dns.stats()
    now = time.time()
    devs = c.devices.devices.values()
    online = sum(1 for d in devs if d.get("online") or now - c.devices.activity.get(d.get("ip") or "", 0) < 600)
    inet = c.rates.ping_ms
    line = c.rates.line if c.rates.line and now - c.rates.line["ts"] < 30 else None
    return {"ts": now, "total": st["total"], "blocked": st["blocked"], "cached": st["cached"], "qps": st["qps"],
            "avg_response_ms": st["avg_response_ms"], "hit_ratio": st["cache"].get("hit_ratio"), "online": online,
            "devices": len(c.devices.devices), "inet_ms": inet, "line": line, "rates": c.rates.snapshot()}


@api.get("/stats/live")
async def stats_live(request: Request):
    """Estadísticas en tiempo real (SSE, cada 2 s): contadores DNS, consultas/s, dispositivos, latencia y velocidad."""
    c = ctx(request)

    async def gen():
        while not await request.is_disconnected():
            yield f"data: {json.dumps(_live_snapshot(c), ensure_ascii=False)}\n\n"
            await asyncio.sleep(2)
    return StreamingResponse(gen(), media_type="text/event-stream", headers={"cache-control": "no-cache", "x-accel-buffering": "no"})


@api.post("/devices/scan")
async def devices_scan(request: Request):
    return await ctx(request).devices.scan()


@api.get("/devices/{mac}")
async def device_detail(request: Request, mac: str):
    c = ctx(request)
    d = c.devices.devices.get(mac)
    if not d:
        raise HTTPException(404, "Dispositivo no encontrado")
    since = time.time() - 86400
    ip = d.get("ip") or ""
    c.db.flush_queries()
    who = "(mac=? OR (mac IS NULL AND client=?))"  # por MAC (sigue al equipo aunque cambie de IP)
    top = await c.db.aquery(f"SELECT domain, COUNT(*) n, MAX(status IN {BLOCKED_SQL}) blocked FROM queries WHERE {who} AND ts>=? GROUP BY domain ORDER BY n DESC LIMIT 15", (mac, ip, since))
    hourly = await c.db.aquery(f"SELECT CAST(ts/3600 AS INT)*3600 t, COUNT(*) total, SUM(status IN {BLOCKED_SQL}) blocked FROM queries WHERE {who} AND ts>=? GROUP BY t ORDER BY t", (mac, ip, since))
    pol = c.devices.policy_for(ip) if ip else None
    return {**d, "top_domains": top, "hourly": hourly,
            "policy": pol.__dict__ | {"services": sorted(pol.services)} if pol else None}


@api.patch("/devices/{mac}")
async def device_update(request: Request, mac: str, body: dict = Body(...)):
    c = ctx(request)
    if "profile" in body and not any(p["id"] == body["profile"] for p in c.settings.get("profiles")):
        raise HTTPException(400, "Perfil inexistente")
    if "pause_minutes" in body:
        m = int(body.pop("pause_minutes"))
        body["paused_until"] = time.time() + m * 60 if m > 0 else 0
        body["paused"] = 0
    d = c.devices.update(mac, body)
    if not d:
        raise HTTPException(404, "Dispositivo no encontrado")
    c.cache.clear()
    return d


@api.delete("/devices/{mac}")
async def device_delete(request: Request, mac: str):
    ctx(request).devices.delete(mac)
    return {"ok": True}


@api.post("/devices/{mac}/wake")
async def device_wake(request: Request, mac: str):
    c = ctx(request)
    try:
        c.devices.wake_on_lan(mac)
        for itf in netinfo.interfaces():
            import ipaddress
            c.devices.wake_on_lan(mac, str(ipaddress.ip_network(itf["network"]).broadcast_address))
    except (ValueError, OSError) as exc:
        raise HTTPException(400, str(exc))
    return {"ok": True}


@api.post("/devices/{mac}/ping")
async def device_ping(request: Request, mac: str):
    c = ctx(request)
    d = c.devices.devices.get(mac)
    if not d or not d.get("ip"):
        raise HTTPException(404, "Dispositivo sin IP")
    return await ping(d["ip"], count=5)


@api.post("/devices/{mac}/ports")
async def device_ports(request: Request, mac: str):
    c = ctx(request)
    d = c.devices.devices.get(mac)
    if not d or not d.get("ip"):
        raise HTTPException(404, "Dispositivo sin IP")
    return {"ip": d["ip"], "ports": await tools.port_scan(d["ip"], tools.parse_ports("common"))}


# =====================================================================
# DNS
# =====================================================================
DNS_FIELDS = {"upstreams", "preset", "strategy", "timeout_ms", "cache_size", "cache_min_ttl", "cache_max_ttl", "optimistic_cache",
              "prefetch", "blocking_mode", "block_ttl", "log_queries", "block_doh_canary", "block_icloud_relay",
              "conditional_forwarding", "local_records", "auto_optimize", "prefer_ipv4"}


@api.get("/dns")
async def dns_get(request: Request):
    c = ctx(request)
    return {"settings": c.settings.get("dns"), "presets": DNS_PRESETS, "stats": c.dns.stats(), "fallback": c.upstream_fallback,
            "error": c.dns_error, "unbound_running": c.unbound.running}


@api.put("/dns")
async def dns_put(request: Request, body: dict = Body(...)):
    c = ctx(request)
    from .dns.upstream import Upstream
    patch = {k: v for k, v in body.items() if k in DNS_FIELDS}
    if "upstreams" in patch:
        ups = [u.strip() for u in patch["upstreams"] if u.strip()]
        if not ups:
            raise HTTPException(400, "Indica al menos un servidor DNS")
        for u in ups:
            try:
                Upstream.parse(u)
            except Exception:
                raise HTTPException(400, f"Servidor inválido: {u}")
        patch["upstreams"] = ups
    if patch.get("strategy") and patch["strategy"] not in ("failover", "fastest", "parallel"):
        raise HTTPException(400, "Estrategia inválida")
    c.settings.update("dns", patch)
    c.cache.clear()
    return c.settings.get("dns")


@api.post("/dns/cache/flush")
async def dns_flush(request: Request):
    c = ctx(request)
    n = c.cache.clear()
    if c.unbound.running:
        await c.unbound.control("flush_zone", ".")
    return {"cleared": n}


@api.post("/dns/test")
async def dns_test(request: Request, body: dict = Body(...)):
    servers = [s for s in body.get("servers", []) if s][:10]
    return await tools.dns_benchmark([{"id": s, "name": s, "server": s} for s in servers])


@api.post("/dns/optimize")
async def dns_optimize(request: Request, body: dict = Body(default={})):
    return await optimize_dns(ctx(request), apply=bool(body.get("apply", True)))


# =====================================================================
# Unbound
# =====================================================================
@api.get("/unbound")
async def unbound_get(request: Request):
    c = ctx(request)
    return {"status": c.unbound.status(), "settings": c.settings.get("unbound"), "stats": await c.unbound.stats(),
            "logs": list(c.unbound.logs)[-80:]}


@api.put("/unbound")
async def unbound_put(request: Request, body: dict = Body(...)):
    c = ctx(request)
    cfg = c.settings.get("unbound")
    cfg.update({k: v for k, v in body.items() if k in cfg})
    ok, msg = await c.unbound.apply(cfg)
    if not ok:
        raise HTTPException(400, msg)
    c.apply_dns_settings()
    return {"ok": True, "message": msg}


@api.get("/unbound/recommend")
async def unbound_recommend(request: Request):
    """Mejor configuración de Unbound para el hardware de este equipo."""
    from .unbound import recommend_config
    c = ctx(request)
    mem = {}
    for line in _read_proc("/proc/meminfo").splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[0].rstrip(":") == "MemTotal":
            mem["total"] = int(parts[1]) * 1024
    r = recommend_config(c.settings.get("unbound"), os.cpu_count() or 1, mem.get("total", 0))
    cur = c.settings.get("unbound")
    diff = {k: {"from": cur.get(k), "to": v} for k, v in r["config"].items() if cur.get(k) != v}
    return {"recommended": r["config"], "reasons": r["reasons"], "changes": diff,
            "hardware": {"cpus": os.cpu_count(), "mem_total": mem.get("total", 0)}}


@api.post("/unbound/optimize")
async def unbound_optimize(request: Request):
    """Aplica la configuración óptima de Unbound para el hardware."""
    from .unbound import recommend_config
    c = ctx(request)
    mem = {}
    for line in _read_proc("/proc/meminfo").splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[0].rstrip(":") == "MemTotal":
            mem["total"] = int(parts[1]) * 1024
    rec = recommend_config(c.settings.get("unbound"), os.cpu_count() or 1, mem.get("total", 0))["config"]
    ok, msg = await c.unbound.apply(rec)
    if not ok:
        raise HTTPException(400, msg)
    c.settings.set("unbound", rec, notify=False)
    c.apply_dns_settings()
    c.db.event("info", "unbound", "Unbound optimizado automáticamente para el hardware")
    return {"ok": True, "message": "Unbound optimizado: " + msg}


@api.get("/unbound/auto")
async def unbound_auto_get(request: Request):
    st = ctx(request).settings.get("unbound_auto") or {}
    return {"enabled": bool(st.get("enabled")), "last": st.get("last")}


@api.post("/unbound/auto")
async def unbound_auto_run(request: Request, body: dict = Body(default={})):
    """Mide los DNS más cercanos desde tu ubicación y autoconfigura Unbound (también por hardware)."""
    from . import unbound_auto
    c = ctx(request)
    if "enabled" in body:
        st = c.settings.get("unbound_auto") or {}
        st["enabled"] = bool(body["enabled"])
        c.settings.set("unbound_auto", st, notify=False)
    if body.get("run", True):
        r = await unbound_auto.auto_configure(c, apply=bool(body.get("apply", True)))
        if r.get("error"):
            raise HTTPException(400, r["error"])
        return {"enabled": bool((c.settings.get("unbound_auto") or {}).get("enabled")), "last": r}
    return await unbound_auto_get(request)


@api.post("/unbound/{action}")
async def unbound_action(request: Request, action: str):
    c = ctx(request)
    if action == "restart":
        await c.unbound.stop()
        await c.unbound.start()
        return {"ok": True}
    if action == "flush":
        code, out = await c.unbound.control("flush_zone", ".")
        await c.unbound.control("flush_negative")
        return {"ok": code == 0, "message": out.strip()}
    if action == "flush_infra":
        code, out = await c.unbound.control("flush_infra", "all")
        return {"ok": code == 0, "message": out.strip()}
    raise HTTPException(404)


@api.get("/unbound/config")
async def unbound_config(request: Request):
    p = ctx(request).unbound.conf
    return {"config": p.read_text() if p.exists() else ""}


# =====================================================================
# Herramientas
# =====================================================================
@api.get("/tools/ping")
async def tool_ping(host: str, count: int = 10):
    if not valid_host(host):
        raise HTTPException(400, "Host inválido")
    async def gen():
        async for line in tools.stream_cmd(tools.ping_cmd(host, count), timeout=count + 15):
            yield {"line": line}
    return sse(gen())


@api.get("/tools/traceroute")
async def tool_traceroute(host: str):
    if not valid_host(host):
        raise HTTPException(400, "Host inválido")
    async def gen():
        async for line in tools.stream_cmd(tools.traceroute_cmd(host), timeout=90):
            yield {"line": line}
    return sse(gen())


@api.post("/tools/lookup")
async def tool_lookup(request: Request, body: dict = Body(...)):
    c = ctx(request)
    server = body.get("server") or f"127.0.0.1:{c.settings.get('dns.port')}"
    name = (body.get("name") or "").strip()
    if not name or not valid_host(name):
        raise HTTPException(400, "Nombre inválido")
    try:
        return await tools.dns_lookup(name, body.get("type", "A"), server)
    except Exception as exc:
        raise HTTPException(400, f"{type(exc).__name__}: {exc}")


@api.post("/tools/ports")
async def tool_ports(body: dict = Body(...)):
    host = (body.get("host") or "").strip()
    try:
        ports = tools.parse_ports(body.get("ports", "common"))
        return {"host": host, "scanned": len(ports), "open": await tools.port_scan(host, ports)}
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@api.post("/tools/whois")
async def tool_whois(body: dict = Body(...)):
    try:
        return {"output": await tools.whois((body.get("query") or "").strip())}
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@api.post("/tools/http")
async def tool_http(body: dict = Body(...)):
    try:
        return await tools.http_check((body.get("url") or "").strip())
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@api.post("/tools/wol")
async def tool_wol(request: Request, body: dict = Body(...)):
    try:
        ctx(request).devices.wake_on_lan(body.get("mac", ""), body.get("broadcast") or "255.255.255.255")
    except (ValueError, OSError) as exc:
        raise HTTPException(400, str(exc))
    return {"ok": True}


@api.post("/tools/diagnose")
async def tool_diagnose(request: Request):
    c = ctx(request)
    return await tools.diagnose(int(c.settings.get("dns.port")), c.settings.get("unbound.port") if c.unbound.running else None)


@api.post("/tools/benchmark")
async def tool_benchmark(request: Request, body: dict = Body(default={})):
    c = ctx(request)
    servers = tools.optimizer_candidates(c.unbound.running)
    servers.insert(0, {"id": "netping", "name": "Bipon Net (con caché)", "server": f"127.0.0.1:{c.settings.get('dns.port')}"})
    for extra in ("cloudflare-doh", "quad9-dot", "cloudflare-family", "cleanbrowsing-family"):
        if body.get("extended"):
            servers.append({"id": extra, "name": DNS_PRESETS[extra]["name"], "server": DNS_PRESETS[extra]["servers"][0]})
    for s in body.get("custom", [])[:5]:
        servers.append({"id": s, "name": s, "server": s})
    return await tools.dns_benchmark(servers)


@api.post("/tools/latency")
async def tool_latency():
    return await tools.latency_targets()


@api.get("/monitor")
async def monitor_get(request: Request, hours: int = Query(24, le=24 * 30)):
    c = ctx(request)
    since = time.time() - hours * 3600
    bucket = max(60, int(hours * 3600 / 200))
    rows = await c.db.aquery(f"""SELECT target, CAST(ts/{bucket} AS INT)*{bucket} t, AVG(latency) latency, AVG(loss) loss
                                FROM monitor WHERE ts>=? GROUP BY target, t ORDER BY t""", (since,))
    series: dict[str, list] = {}
    for r in rows:
        series.setdefault(r["target"], []).append({"t": r["t"], "latency": round(r["latency"], 2) if r["latency"] is not None else None,
                                                   "loss": round(r["loss"] or 0, 1)})
    summary = await c.db.aquery("""SELECT target, AVG(latency) avg, MIN(latency) min, MAX(latency) max, AVG(loss) loss,
                                  SUM(loss>=100)*100.0/COUNT(*) down FROM monitor WHERE ts>=? GROUP BY target""", (since,))
    return {"series": series, "summary": summary, "last": c.monitor.last}


@api.post("/monitor/sample")
async def monitor_sample(request: Request):
    return await ctx(request).monitor.sample()


# =====================================================================
# Velocidad
# =====================================================================
@api.get("/speedtest/run")
async def speedtest_run(request: Request):
    c = ctx(request)
    if c.speedtest.running:
        raise HTTPException(409, "Ya hay un test en curso")

    async def gen():
        async for ev in c.speedtest.run():
            if ev.get("phase") == "done":
                r = ev["result"]
                await c.db.aexecute("INSERT INTO speedtests(ts,download,upload,ping,jitter,loss,server,ip,isp,bufferbloat,grade,loaded_ping) "
                                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                                    (time.time(), r["download"], r["upload"], r["ping"], r["jitter"], r["loss"], r["server"], r["ip"],
                                     r["isp"], r.get("bufferbloat"), r.get("grade"), r.get("loaded_ping")))
            yield ev
    return sse(gen())


@api.get("/speedtest/history")
async def speedtest_history(request: Request, limit: int = 50):
    return await ctx(request).db.aquery("SELECT * FROM speedtests ORDER BY ts DESC LIMIT ?", (min(limit, 500),))


@api.get("/lan/download")
async def lan_download(size: int = 25_000_000):
    return StreamingResponse(lan_payload(size), media_type="application/octet-stream",
                             headers={"cache-control": "no-store", "content-length": str(max(1, min(size, 1024 ** 3)))})


@api.post("/lan/upload")
async def lan_upload(request: Request):
    n = 0
    async for chunk in request.stream():
        n += len(chunk)
    return {"bytes": n}


@api.get("/lan/ping")
async def lan_ping():
    return {"t": time.time()}


# =====================================================================
# Conectividad / NAT / CGNAT
# =====================================================================
@api.post("/nat/analyze")
async def nat_analyze():
    return await nat.analyze()


@api.post("/nat/upnp")
async def nat_upnp(body: dict = Body(...)):
    igd = await nat.find_igd()
    if not igd:
        raise HTTPException(400, "No se encontró un router con UPnP-IGD activo")
    action = body.get("action")
    try:
        if action == "add":
            ip = body.get("internal_ip") or netinfo.primary_ip()
            await igd.add_mapping(int(body["external_port"]), body.get("protocol", "TCP"), ip,
                                  int(body.get("internal_port") or body["external_port"]), body.get("description", "Bipon Net"))
        elif action == "delete":
            await igd.delete_mapping(int(body["external_port"]), body.get("protocol", "TCP"))
        else:
            raise HTTPException(400, "acción inválida")
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(400, f"El router rechazó la operación: {exc}")
    return {"ok": True, "mappings": await igd.mappings()}


@api.post("/nat/mtu")
async def nat_mtu(body: dict = Body(default={})):
    host = body.get("host") or "1.1.1.1"
    if not valid_host(host):
        raise HTTPException(400, "host inválido")
    return await nat.path_mtu(host)


@api.post("/wireguard/generate")
async def wg_generate(body: dict = Body(...)):
    try:
        return wireguard.generate(body)
    except (ValueError, KeyError) as exc:
        raise HTTPException(400, str(exc))


# =====================================================================
# VPN
# =====================================================================
def _vpn_call(fn, *a, **kw):
    try:
        return fn(*a, **kw)
    except (ValueError, KeyError) as exc:
        raise HTTPException(400, str(exc).strip("'"))


@api.get("/vpn")
async def vpn_overview(request: Request):
    return await ctx(request).vpn.overview()


@api.post("/vpn/wireguard/servers")
async def vpn_wg_create(request: Request, body: dict = Body(...)):
    c = ctx(request)
    body.setdefault("dns", c.devices.self_ip or netinfo.primary_ip() or "")
    return _vpn_call(c.vpn.wg.create_server, body)


@api.patch("/vpn/wireguard/{name}")
async def vpn_wg_update(request: Request, name: str, body: dict = Body(...)):
    c = ctx(request)
    res = _vpn_call(c.vpn.wg.update_server, name, body)
    if name in await c.vpn.wg.interfaces_up():
        await c.vpn.wg.reload(name)
    return res


@api.delete("/vpn/wireguard/{name}")
async def vpn_wg_delete(request: Request, name: str):
    c = ctx(request)
    if name in await c.vpn.wg.interfaces_up():
        await c.vpn.wg.down(name)
    c.vpn.wg.delete(name)
    return {"ok": True}


@api.post("/vpn/wireguard/{name}/peers")
async def vpn_wg_peer_add(request: Request, name: str, body: dict = Body(...)):
    c = ctx(request)
    peer = _vpn_call(c.vpn.wg.add_peer, name, body)
    if name in await c.vpn.wg.interfaces_up():
        await c.vpn.wg.reload(name)
    conf = _vpn_call(c.vpn.wg.peer_conf, name, peer["id"]) if c.vpn.wg.state["servers"][name].get("endpoint") else None
    return {"peer": {k: v for k, v in peer.items() if k not in ("private_key", "psk")}, "config": conf}


@api.patch("/vpn/wireguard/{name}/peers/{pid}")
async def vpn_wg_peer_update(request: Request, name: str, pid: str, body: dict = Body(...)):
    c = ctx(request)
    _vpn_call(c.vpn.wg.update_peer, name, pid, body)
    if name in await c.vpn.wg.interfaces_up():
        await c.vpn.wg.reload(name)
    return {"ok": True}


@api.delete("/vpn/wireguard/{name}/peers/{pid}")
async def vpn_wg_peer_delete(request: Request, name: str, pid: str):
    c = ctx(request)
    _vpn_call(c.vpn.wg.remove_peer, name, pid)
    if name in await c.vpn.wg.interfaces_up():
        await c.vpn.wg.reload(name)
    return {"ok": True}


@api.get("/vpn/wireguard/{name}/peers/{pid}/config")
async def vpn_wg_peer_conf(request: Request, name: str, pid: str):
    return _vpn_call(ctx(request).vpn.wg.peer_conf, name, pid)


@api.post("/vpn/wireguard/import")
async def vpn_wg_import(request: Request, body: dict = Body(...)):
    return _vpn_call(ctx(request).vpn.wg.import_client, body.get("name", ""), body.get("config", ""), bool(body.get("autostart")))


@api.post("/vpn/wireguard/{name}/{action}")
async def vpn_wg_action(request: Request, name: str, action: str):
    c = ctx(request)
    try:
        if action == "up":
            ok, msg = await c.vpn.wg.up(name)
        elif action == "down":
            ok, msg = await c.vpn.wg.down(name)
        else:
            raise HTTPException(404)
    except KeyError as exc:
        raise HTTPException(404, str(exc))
    if not ok:
        raise HTTPException(400, msg or "falló")
    return {"ok": True, "message": msg}


@api.post("/vpn/openvpn/server")
async def vpn_ovpn_init(request: Request, body: dict = Body(...)):
    c = ctx(request)
    body.setdefault("dns", c.devices.self_ip or netinfo.primary_ip() or "")
    return _vpn_call(c.vpn.ovpn.init_server, body)


@api.post("/vpn/openvpn/server/{action}")
async def vpn_ovpn_server(request: Request, action: str):
    c = ctx(request)
    if action == "start":
        ok, msg = await c.vpn.ovpn.start_server()
        if not ok:
            raise HTTPException(400, msg)
        return {"ok": True, "message": msg}
    if action == "stop":
        await c.vpn.ovpn.stop_server()
        return {"ok": True}
    raise HTTPException(404)


@api.post("/vpn/openvpn/clients")
async def vpn_ovpn_client(request: Request, body: dict = Body(...)):
    c = ctx(request)
    _vpn_call(c.vpn.ovpn.add_client, body.get("name", ""))
    try:
        return c.vpn.ovpn.export(body["name"])
    except ValueError as exc:
        return {"warning": str(exc)}


@api.get("/vpn/openvpn/clients/{name}")
async def vpn_ovpn_export(request: Request, name: str):
    return _vpn_call(ctx(request).vpn.ovpn.export, name)


@api.delete("/vpn/openvpn/clients/{name}")
async def vpn_ovpn_revoke(request: Request, name: str):
    ctx(request).vpn.ovpn.revoke(name)
    return {"ok": True}


@api.post("/vpn/openvpn/profiles")
async def vpn_ovpn_profile(request: Request, body: dict = Body(...)):
    return _vpn_call(ctx(request).vpn.ovpn.import_profile, body.get("name", ""), body.get("config", ""),
                     body.get("username", ""), body.get("password", ""), bool(body.get("autostart")))


@api.post("/vpn/openvpn/profiles/{name}/{action}")
async def vpn_ovpn_profile_action(request: Request, name: str, action: str):
    o = ctx(request).vpn.ovpn
    if action == "connect":
        ok, msg = await o.connect_profile(name)
        if not ok:
            raise HTTPException(400, msg)
    elif action == "disconnect":
        await o.disconnect_profile(name)
    elif action == "delete":
        await o.disconnect_profile(name)
        o.delete_profile(name)
    else:
        raise HTTPException(404)
    return {"ok": True}


@api.post("/vpn/tailscale/up")
async def vpn_ts_up(request: Request, body: dict = Body(default={})):
    try:
        return await ctx(request).vpn.ts.up(body.pop("authkey", ""), body)
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(400, str(exc))


@api.post("/vpn/tailscale/set")
async def vpn_ts_set(request: Request, body: dict = Body(...)):
    try:
        ok, msg = await ctx(request).vpn.ts.set(body)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    if not ok:
        raise HTTPException(400, msg)
    return {"ok": True, "message": msg}


@api.post("/vpn/tailscale/{action}")
async def vpn_ts_action(request: Request, action: str, body: dict = Body(default={})):
    ts = ctx(request).vpn.ts
    try:
        if action == "down":
            code, out = await ts.down()
            return {"ok": code == 0, "message": out}
        if action == "logout":
            code, out = await ts.logout()
            return {"ok": code == 0, "message": out}
        if action == "netcheck":
            return await ts.netcheck()
        if action == "ping":
            return await ts.ping(body.get("target", ""))
        if action == "start":
            await ts.ensure_daemon()
            return {"ok": True}
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(400, str(exc))
    raise HTTPException(404)


# =====================================================================
# Ajustes generales y copias de seguridad
# =====================================================================
@api.get("/settings")
async def settings_get(request: Request):
    c = ctx(request)
    return {"devices": c.settings.get("devices"), "monitor": c.settings.get("monitor"), "retention_days": c.settings.get("retention_days")}


@api.put("/settings")
async def settings_put(request: Request, body: dict = Body(...)):
    c = ctx(request)
    if "devices" in body:
        c.settings.update("devices", {k: v for k, v in body["devices"].items() if k in ("scan_interval", "subnets", "resolve_names")})
    if "monitor" in body:
        c.settings.update("monitor", {k: v for k, v in body["monitor"].items() if k in ("enabled", "interval", "targets")})
    if "retention_days" in body:
        c.settings.set("retention_days", max(1, min(int(body["retention_days"]), 365)))
    return await settings_get(request)


@api.get("/backup")
async def backup(request: Request):
    c = ctx(request)
    data = {"version": __version__, "created": time.time(), "settings": c.settings.export(),
            "rules": await c.db.aquery("SELECT value,kind,comment,enabled FROM domain_rules"),
            "lists": await c.db.aquery("SELECT url,name,category,enabled FROM blocklists"),
            "devices": await c.db.aquery("SELECT mac,name,kind,profile,notes FROM devices WHERE name<>'' OR profile<>'default' OR notes<>''")}
    return Response(json.dumps(data, indent=2, ensure_ascii=False), media_type="application/json",
                    headers={"content-disposition": f"attachment; filename=bipon-backup-{time.strftime('%Y%m%d')}.json"})


@api.post("/restore")
async def restore(request: Request, body: dict = Body(...)):
    c = ctx(request)
    if "settings" not in body:
        raise HTTPException(400, "Copia de seguridad no válida")
    c.settings.import_(body["settings"])
    for r in body.get("rules", []):
        await c.db.aexecute("INSERT OR IGNORE INTO domain_rules(value,kind,comment,enabled,created) VALUES (?,?,?,?,?)",
                            (r["value"], r["kind"], r.get("comment", ""), r.get("enabled", 1), time.time()))
    for l in body.get("lists", []):
        await c.db.aexecute("INSERT OR IGNORE INTO blocklists(url,name,category,enabled) VALUES (?,?,?,?)",
                            (l["url"], l["name"], l.get("category", "ads"), l.get("enabled", 1)))
    for d in body.get("devices", []):
        await c.db.aexecute("INSERT INTO devices(mac,name,kind,profile,notes,first_seen,last_seen) VALUES (?,?,?,?,?,?,?) "
                            "ON CONFLICT(mac) DO UPDATE SET name=excluded.name, kind=excluded.kind, profile=excluded.profile, notes=excluded.notes",
                            (d["mac"], d.get("name", ""), d.get("kind", ""), d.get("profile", "default"), d.get("notes", ""), time.time(), 0))
    c.devices.load()
    asyncio.create_task(c.lists.update())
    return {"ok": True}


# =====================================================================
# Asistente inteligente
# =====================================================================
@api.post("/advisor/analyze")
async def advisor_analyze(request: Request):
    try:
        return await ctx(request).advisor.analyze()
    except RuntimeError as exc:
        raise HTTPException(409, str(exc))


@api.get("/advisor")
async def advisor_get(request: Request):
    a = ctx(request).advisor
    return {"running": a.running, "progress": a.progress, "last": a.last}


@api.post("/advisor/apply")
async def advisor_apply(request: Request, body: dict = Body(...)):
    try:
        return {"results": await ctx(request).advisor.apply(body.get("ids", []))}
    except RuntimeError as exc:
        raise HTTPException(400, str(exc))


# =====================================================================
# Gaming y streaming
# =====================================================================
@api.get("/gaming")
async def gaming_get(request: Request):
    return ctx(request).gaming.status()


@api.put("/gaming")
async def gaming_put(request: Request, body: dict = Body(...)):
    c = ctx(request)
    cfg = c.settings.get("gaming") or {}
    for k in ("enabled", "protect", "groups", "custom_domains"):
        if k in body:
            cfg[k] = body[k]
    c.settings.set("gaming", cfg)
    return c.gaming.status()


@api.post("/gaming/warm")
async def gaming_warm(request: Request):
    return await ctx(request).gaming.warm()


@api.post("/gaming/regions")
async def gaming_regions(request: Request):
    return await ctx(request).gaming.regions()


@api.post("/gaming/cdn")
async def gaming_cdn(request: Request):
    return await ctx(request).gaming.cdn()


@api.get("/qos")
async def qos_get(request: Request):
    return {**await ctx(request).gaming.qos_status(), "interfaces": netinfo.interfaces()}


@api.post("/qos")
async def qos_set(request: Request, body: dict = Body(...)):
    try:
        return await ctx(request).gaming.qos_apply(body.get("iface", ""), float(body.get("mbit") or 0), body.get("overhead", "ethernet"),
                                                   bool(body.get("remove")))
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(400, str(exc))
