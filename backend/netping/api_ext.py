"""API de los módulos ampliados: notificaciones, DHCP, tiempo de pantalla,
seguridad, modo automático, tareas programadas, informes, sincronización,
TLS, integraciones, modo router y configuración inicial."""
from __future__ import annotations

import json
import time

from fastapi import Body, HTTPException, Request
from fastapi.responses import PlainTextResponse, Response

from . import region, sync as syncmod, tls
from .api import api, ctx, public
from .family import ALL
from .integrations import WIFI_TYPES, ha_state, ha_yaml
from .scheduler import DEFAULT_QUALITY_TARGETS


def _bad(exc: Exception):
    raise HTTPException(400, str(exc).strip("'"))


# =====================================================================
# Configuración inicial (onboarding)
# =====================================================================
@public.get("/setup/info")
async def setup_info(request: Request):
    c = ctx(request)
    from . import auth
    done = (c.settings.get("setup") or {}).get("completed", False)
    if done and not auth.current_user(request):
        raise HTTPException(401, "No autenticado")
    return {"completed": done, "network": region.network_info(), "countries": region.countries_payload(),
            "timezones": region.available_timezones(), "detected_country": await region.detect_country(),
            "lists": [{"url": l["url"], "name": l["name"], "category": l["category"], "default": l["default"]}
                      for l in __import__("netping.catalog", fromlist=["RECOMMENDED_LISTS"]).RECOMMENDED_LISTS],
            "regional_lists": region.REGIONAL_LISTS}


@api.post("/setup/complete")
async def setup_complete(request: Request, body: dict = Body(...)):
    if request.state.user["role"] != "admin":
        raise HTTPException(403)
    return await region.complete_setup(ctx(request), body)


# =====================================================================
# Notificaciones
# =====================================================================
@api.get("/notify")
async def notify_get(request: Request):
    c = ctx(request)
    return {**c.notify.public_cfg(), "log": await c.db.aquery("SELECT * FROM notify_log ORDER BY ts DESC LIMIT 50")}


@api.post("/notify/channels")
async def notify_channel_save(request: Request, body: dict = Body(...)):
    try:
        return ctx(request).notify.save_channel(body)
    except ValueError as exc:
        _bad(exc)


@api.delete("/notify/channels/{cid}")
async def notify_channel_delete(request: Request, cid: str):
    ctx(request).notify.delete_channel(cid)
    return {"ok": True}


@api.post("/notify/channels/{cid}/test")
async def notify_channel_test(request: Request, cid: str):
    try:
        return await ctx(request).notify.test(cid)
    except KeyError as exc:
        _bad(exc)


@api.put("/notify/settings")
async def notify_settings(request: Request, body: dict = Body(...)):
    c = ctx(request)
    cfg = c.notify.cfg()
    for k in ("events", "quiet", "webpush"):
        if k in body:
            cfg[k] = body[k]
    c.settings.set("notify", cfg)
    return c.notify.public_cfg()


@api.post("/push/subscribe")
async def push_subscribe(request: Request, body: dict = Body(...)):
    if not body.get("endpoint", "").startswith("https://") or "keys" not in body:
        raise HTTPException(400, "Suscripción inválida")
    ctx(request).db.execute("INSERT OR REPLACE INTO push_subs(endpoint,data,user_id,created) VALUES (?,?,?,?)",
                            (body["endpoint"], json.dumps(body), request.state.user.get("id"), time.time()))
    return {"ok": True}


@api.post("/push/test")
async def push_test(request: Request):
    ctx(request).notify.emit("report", "Prueba de notificación", "Las notificaciones push funcionan ✅", force=True, key=str(time.time()))
    return {"ok": True}


# =====================================================================
# DHCP
# =====================================================================
@api.get("/dhcp")
async def dhcp_get(request: Request):
    return ctx(request).dhcp.status()


@api.put("/dhcp")
async def dhcp_put(request: Request, body: dict = Body(...)):
    c = ctx(request)
    cfg = {**c.dhcp.suggest(), **(c.settings.get("dhcp") or {}), **body}
    cfg.setdefault("static", [])
    try:
        c.dhcp.validate(cfg)
    except (ValueError, KeyError) as exc:
        _bad(exc)
    if cfg.get("enabled"):
        others = [o for o in await c.dhcp.probe() if not o.get("own") and not o.get("error")]
        if others and not body.get("force"):
            raise HTTPException(409, f"Hay otro servidor DHCP activo ({others[0]['server']}). Desactívalo en el router "
                                     "o confirma para continuar igualmente.")
    c.settings.set("dhcp", cfg)
    c.apply_dns_settings()
    await c.dhcp.restart()
    return c.dhcp.status()


@api.post("/dhcp/probe")
async def dhcp_probe(request: Request):
    return await ctx(request).dhcp.probe()


@api.delete("/dhcp/leases/{mac}")
async def dhcp_release(request: Request, mac: str):
    ctx(request).db.execute("DELETE FROM dhcp_leases WHERE mac=?", (mac,))
    return {"ok": True}


# =====================================================================
# Tiempo de pantalla y familia
# =====================================================================
@api.get("/screentime")
async def screentime(request: Request, mac: str | None = None):
    c = ctx(request)
    c.family.flush()
    return {"devices": c.family.summary(mac), "all_key": ALL}


@api.get("/screentime/{mac}/history")
async def screentime_history(request: Request, mac: str, days: int = 7):
    return ctx(request).family.history(mac, min(days, 60))


@api.post("/screentime/{mac}/bonus")
async def screentime_bonus(request: Request, mac: str, body: dict = Body(...)):
    minutes = int(body.get("minutes", 30))
    if not -600 <= minutes <= 600:
        raise HTTPException(400, "Entre -600 y 600 minutos")
    return {"bonus": ctx(request).family.grant(mac, body.get("service") or ALL, minutes)}


@api.post("/family/pause")
async def family_pause(request: Request, body: dict = Body(default={})):
    n = ctx(request).family.pause(body.get("profiles") or None, int(body.get("minutes", 30)))
    return {"devices": n}


@api.post("/family/resume")
async def family_resume(request: Request, body: dict = Body(default={})):
    return {"devices": ctx(request).family.resume(body.get("profiles") or None)}


# =====================================================================
# Seguridad
# =====================================================================
@api.get("/security")
async def security_get(request: Request):
    return ctx(request).security.status()


@api.put("/security")
async def security_put(request: Request, body: dict = Body(...)):
    c = ctx(request)
    cfg = c.security.cfg()
    for k in ("anomaly_detection", "blocked_tlds", "blocked_countries", "rebind_protection", "rebind_allow"):
        if k in body:
            cfg[k] = body[k]
    c.settings.set("security", cfg)
    if cfg["blocked_countries"] and not c.security.geo.loaded:
        if await c.security.geo.download():
            c.security.apply()
    c.cache.clear()
    return c.security.status()


@api.post("/security/scan")
async def security_scan(request: Request):
    import asyncio
    return await asyncio.to_thread(ctx(request).security.analyze)


@api.post("/security/anomalies/{aid}/ack")
async def anomaly_ack(request: Request, aid: int):
    ctx(request).db.execute("UPDATE anomalies SET acknowledged=1 WHERE id=?", (aid,))
    return {"ok": True}


# =====================================================================
# Modo automático (sin tocar el router)
# =====================================================================
@api.get("/autodns")
async def autodns_get(request: Request):
    return ctx(request).autodns.status()


@api.put("/autodns")
async def autodns_put(request: Request, body: dict = Body(...)):
    c = ctx(request)
    cfg = c.autodns.cfg()
    for k in ("enabled", "interface", "search_domain"):
        if k in body:
            cfg[k] = body[k]
    c.settings.set("autodns", cfg)
    if not cfg["enabled"]:
        await c.autodns.withdraw()
    return c.autodns.status()


# =====================================================================
# Tareas programadas, calidad e informes
# =====================================================================
@api.get("/schedule")
async def schedule_get(request: Request):
    s = ctx(request).settings
    return {"speed": s.get("speed_schedule") or {"enabled": False, "time": "04:13", "days": list(range(7))},
            "quality": s.get("quality") or {"enabled": True, "interval": 300, "targets": []},
            "reports": s.get("reports") or {"enabled": True, "days": [0], "time": "09:00"},
            "default_quality_targets": DEFAULT_QUALITY_TARGETS}


@api.put("/schedule")
async def schedule_put(request: Request, body: dict = Body(...)):
    s = ctx(request).settings
    if "speed" in body:
        s.set("speed_schedule", body["speed"], notify=False)
    if "quality" in body:
        for t in body["quality"].get("targets") or []:
            if not t.get("name") or not t.get("host"):
                raise HTTPException(400, "Cada destino necesita nombre y host")
        s.set("quality", body["quality"], notify=False)
    if "reports" in body:
        s.set("reports", body["reports"], notify=False)
    return await schedule_get(request)


@api.get("/quality")
async def quality_get(request: Request, hours: int = 24):
    c = ctx(request)
    since = time.time() - min(hours, 720) * 3600
    bucket = max(300, int(hours * 3600 / 150))
    rows = await c.db.aquery(f"SELECT target, CAST(ts/{bucket} AS INT)*{bucket} t, AVG(latency) latency, AVG(loss) loss "
                             "FROM monitor WHERE ts>=? AND target LIKE 'q:%' GROUP BY target, t ORDER BY t", (since,))
    series: dict = {}
    for r in rows:
        series.setdefault(r["target"][2:], []).append({"t": r["t"], "latency": r["latency"], "loss": r["loss"]})
    summary = await c.db.aquery("SELECT substr(target,3) name, AVG(latency) avg, MAX(latency) max, AVG(loss) loss, "
                                "SUM(loss>=100)*100.0/COUNT(*) down FROM monitor WHERE ts>=? AND target LIKE 'q:%' GROUP BY target", (since,))
    return {"series": series, "summary": summary}


@api.post("/quality/sample")
async def quality_sample(request: Request):
    return await ctx(request).scheduler.quality_sample()


@api.get("/reports")
async def reports_list(request: Request):
    rows = await ctx(request).db.aquery("SELECT id, ts, start, end FROM reports ORDER BY ts DESC LIMIT 52")
    return rows


@api.get("/reports/{rid}")
async def report_get(request: Request, rid: int):
    r = ctx(request).db.one("SELECT * FROM reports WHERE id=?", (rid,))
    if not r:
        raise HTTPException(404)
    return {**r, "data": json.loads(r["data"])}


@api.post("/reports/generate")
async def report_generate(request: Request, body: dict = Body(default={})):
    import asyncio
    c = ctx(request)
    data = await asyncio.to_thread(c.scheduler.build_report, int(body.get("days", 7)))
    if body.get("send"):
        c.scheduler.send_report(data)
    return data


# =====================================================================
# Usuarios: alta disponibilidad (sincronización)
# =====================================================================
@api.get("/sync")
async def sync_get(request: Request):
    return ctx(request).sync.status()


@api.put("/sync")
async def sync_put(request: Request, body: dict = Body(...)):
    c = ctx(request)
    cfg = c.sync.cfg()
    for k in ("role", "primary_url", "interval", "verify_tls"):
        if k in body:
            cfg[k] = body[k]
    if body.get("token") and body["token"] != "••••••":
        cfg["token"] = body["token"]
    if cfg["role"] not in ("standalone", "primary", "secondary"):
        raise HTTPException(400, "Rol inválido")
    c.settings.set("sync", cfg, notify=False)
    return c.sync.status()


@api.post("/sync/now")
async def sync_now(request: Request):
    try:
        return await ctx(request).sync.pull()
    except Exception as exc:
        _bad(exc)


@api.get("/sync/export")
async def sync_export(request: Request):
    return syncmod.export_state(ctx(request))


# =====================================================================
# HTTPS / certificados
# =====================================================================
@api.get("/tls")
async def tls_get(request: Request):
    return {"settings": ctx(request).settings.get("tls") or {"enabled": True, "port": 8443, "doh": True, "dot": True},
            "certificate": tls.info()}


@api.put("/tls")
async def tls_put(request: Request, body: dict = Body(...)):
    c = ctx(request)
    cfg = c.settings.get("tls") or {"enabled": True, "port": 8443, "doh": True, "dot": True}
    for k in ("enabled", "port", "redirect_http", "doh", "dot", "dot_port"):
        if k in body:
            cfg[k] = body[k]
    c.settings.set("tls", cfg, notify=False)
    return {"settings": cfg, "certificate": tls.info(), "restart_required": True}


@api.post("/tls/certificate")
async def tls_cert(request: Request, body: dict = Body(...)):
    try:
        if body.get("regenerate"):
            tls.generate_self_signed(force=True)
            return tls.info()
        return tls.install_custom(body.get("cert", ""), body.get("key", ""))
    except Exception as exc:
        _bad(exc)


# =====================================================================
# Integraciones: Wi-Fi y Home Assistant
# =====================================================================
def _integ(c) -> dict:
    i = c.settings.get("integrations") or {}
    i.setdefault("wifi", [])
    return i


@api.get("/integrations")
async def integrations_get(request: Request):
    c = ctx(request)
    i = _integ(c)
    wifi = [{**w, "password": "••••••" if w.get("password") else ""} for w in i["wifi"]]
    return {"wifi": wifi, "wifi_types": WIFI_TYPES, "wifi_state": c.wifi.cache, "wifi_updated": c.wifi.updated}


@api.post("/integrations/wifi")
async def wifi_save(request: Request, body: dict = Body(...)):
    c = ctx(request)
    i = _integ(c)
    if body.get("type") not in WIFI_TYPES or not str(body.get("url", "")).startswith(("http://", "https://")):
        raise HTTPException(400, "Tipo o URL inválidos")
    entry = {k: body.get(k) for k in ("id", "type", "name", "url", "username", "password", "site", "verify_tls", "enabled")}
    entry["id"] = entry.get("id") or f"w{int(time.time())}"
    old = next((w for w in i["wifi"] if w["id"] == entry["id"]), None)
    if old and entry.get("password") in (None, "", "••••••"):
        entry["password"] = old.get("password")
    i["wifi"] = [w for w in i["wifi"] if w["id"] != entry["id"]] + [entry]
    c.settings.set("integrations", i, notify=False)
    try:
        return await c.wifi.collect()
    except Exception as exc:
        _bad(exc)


@api.delete("/integrations/wifi/{wid}")
async def wifi_delete(request: Request, wid: str):
    c = ctx(request)
    i = _integ(c)
    i["wifi"] = [w for w in i["wifi"] if w["id"] != wid]
    c.settings.set("integrations", i, notify=False)
    return {"ok": True}


@api.post("/integrations/wifi/refresh")
async def wifi_refresh(request: Request):
    return await ctx(request).wifi.collect()


@api.get("/integrations/ha/state")
async def ha_state_api(request: Request):
    return ha_state(ctx(request))


@api.get("/integrations/config/ha.yaml", response_class=PlainTextResponse)
async def ha_config(request: Request):
    base = str(request.base_url).rstrip("/")
    return ha_yaml(base)


# =====================================================================
# Modo router: firewall, zonas, tráfico
# =====================================================================
@api.get("/router")
async def router_get(request: Request):
    return await ctx(request).router.status()


@api.put("/router")
async def router_put(request: Request, body: dict = Body(...)):
    c = ctx(request)
    cfg = {**c.router.cfg(), **{k: v for k, v in body.items() if k in ("enabled", "wan", "lan", "nat", "force_dns", "block_devices",
                                                                       "accounting", "zones", "port_forwards", "shaping")}}
    try:
        return await c.router.apply(cfg, confirm_required=True)
    except (ValueError, RuntimeError, KeyError) as exc:
        _bad(exc)


@api.post("/router/confirm")
async def router_confirm(request: Request):
    return {"ok": ctx(request).router.confirm()}


@api.get("/router/traffic")
async def router_traffic(request: Request, hours: int = 24):
    return ctx(request).router.usage(min(hours, 720))


@api.get("/router/preview", response_class=PlainTextResponse)
async def router_preview(request: Request):
    c = ctx(request)
    return c.router.ruleset(c.router.cfg())


@api.get("/audit")
async def audit_list(request: Request, limit: int = 200):
    return await ctx(request).db.aquery("SELECT * FROM audit ORDER BY ts DESC LIMIT ?", (min(limit, 2000),))


@api.get("/export/queries.csv")
async def export_queries(request: Request, hours: int = 24):
    import csv
    import io
    c = ctx(request)
    c.db.flush_queries()
    rows = await c.db.aquery("SELECT ts, client, domain, qtype, status, reason, upstream, rtt FROM queries WHERE ts>=? ORDER BY ts",
                             (time.time() - min(hours, 24 * 90) * 3600,))
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["fecha", "cliente", "dominio", "tipo", "estado", "motivo", "upstream", "ms"])
    for r in rows:
        w.writerow([time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(r["ts"])), r["client"], r["domain"], r["qtype"], r["status"],
                    r["reason"], r["upstream"], r["rtt"]])
    return Response(buf.getvalue(), media_type="text/csv",
                    headers={"content-disposition": f"attachment; filename=consultas-{time.strftime('%Y%m%d')}.csv"})


# =====================================================================
# Proxmox VE (inventario de VMs/LXC para la jerarquía de dispositivos)
# =====================================================================
@api.get("/integrations/proxmox")
async def proxmox_get(request: Request):
    c = ctx(request)
    pc = (c.settings.get("integrations") or {}).get("proxmox") or {}
    return {"config": {**pc, "secret": "••••••" if pc.get("secret") else ""}, "inventory": c.wifi.proxmox,
            "error": c.wifi.errors.get("Proxmox")}


@api.put("/integrations/proxmox")
async def proxmox_put(request: Request, body: dict = Body(...)):
    c = ctx(request)
    integ = c.settings.get("integrations") or {}
    old = integ.get("proxmox") or {}
    if body.get("url") and not str(body["url"]).startswith("https://"):
        raise HTTPException(400, "La URL de Proxmox debe ser https://IP:8006")
    new = {k: body.get(k, old.get(k)) for k in ("url", "token_id", "secret", "verify_tls")}
    if new.get("secret") in (None, "", "••••••"):
        new["secret"] = old.get("secret")
    integ["proxmox"] = new
    c.settings.set("integrations", integ, notify=False)
    if not new.get("url"):
        return {"inventory": {}}
    try:
        return {"inventory": await c.wifi.sync_proxmox()}
    except Exception as exc:
        _bad(exc)


@api.post("/router/shaping")
async def router_shaping(request: Request, body: dict = Body(...)):
    """Activa/desactiva los límites de velocidad y los totales de la línea."""
    c = ctx(request)
    cfg = c.router.cfg()
    cfg["shaping"] = {**(cfg.get("shaping") or {}), **{k: body[k] for k in ("enabled", "iface", "down_mbit", "up_mbit") if k in body}}
    c.settings.set("router", cfg, notify=False)
    try:
        return await c.router.apply_shaping(cfg)
    except (ValueError, RuntimeError) as exc:
        _bad(exc)


@api.patch("/devices/{mac}/limit")
async def device_limit(request: Request, mac: str, body: dict = Body(...)):
    """Límite de velocidad de un dispositivo en Mbps (0 o vacío = sin límite)."""
    c = ctx(request)
    def val(k):
        v = body.get(k)
        return float(v) if v not in (None, "", 0, "0") else None
    down, up = val("limit_down"), val("limit_up")
    for v in (down, up):
        if v is not None and not 0.1 <= v <= 10000:
            raise HTTPException(400, "Límite entre 0,1 y 10000 Mbps")
    c.db.execute("UPDATE devices SET limit_down=?, limit_up=? WHERE mac=?", (down, up, mac))
    c.devices.load()
    if (c.router.cfg().get("shaping") or {}).get("enabled"):
        try:
            res = await c.router.apply_shaping()
        except (ValueError, RuntimeError) as exc:
            _bad(exc)
        return {"ok": True, "applied": res}
    return {"ok": True, "applied": None, "message": "Guardado. Activa «Límites de velocidad» en Modo router para aplicarlo."}


# =====================================================================
# Control del router (LAN segura: DNS del DHCP, canal Wi-Fi, reinicio, copia)
# =====================================================================
@api.get("/routerctl")
async def routerctl_get(request: Request):
    from .routerctl import DRIVERS
    c = ctx(request)
    cfg = c.routerctl.cfg()
    out = {"config": {**cfg, "password": "••••••" if cfg.get("password") else ""}, "drivers": DRIVERS,
           "history": c.settings.get("router_changes") or [], "backups": c.routerctl.backups(), "status": None, "error": None,
           "gateway": c.devices.gateway}
    if cfg.get("type"):
        try:
            out["status"] = await c.routerctl.status()
        except Exception as exc:
            out["error"] = f"{type(exc).__name__}: {exc}"[:300]
    return out


@api.put("/routerctl")
async def routerctl_put(request: Request, body: dict = Body(...)):
    c = ctx(request)
    try:
        c.routerctl.save_cfg(body)
        return await c.routerctl.status()
    except Exception as exc:
        _bad(exc)


@api.post("/routerctl/{action}")
async def routerctl_action(request: Request, action: str, body: dict = Body(default={})):
    c = ctx(request)
    rc = c.routerctl
    try:
        if action == "dns":
            servers = body.get("servers") or [c.devices.self_ip]
            return await rc.set_dns(servers)
        if action == "channel":
            return await rc.set_channel(body.get("radio", ""), str(body.get("channel", "")))
        if action == "undo":
            return await rc.undo(int(body.get("index", 0)))
        if action == "reboot":
            return await rc.reboot()
        if action == "backup":
            return await rc.backup()
    except Exception as exc:
        _bad(exc)
    raise HTTPException(404)


# =====================================================================
# Perfiles de configuración
# =====================================================================
@api.get("/configs")
async def configs_list(request: Request):
    c = ctx(request)
    return {"profiles": c.configs.list(), "active": c.settings.get("active_config_profile")}


@api.post("/configs")
async def configs_save(request: Request, body: dict = Body(...)):
    try:
        return ctx(request).configs.save(body.get("name", ""), body.get("description", ""))
    except ValueError as exc:
        _bad(exc)


@api.get("/configs/{pid}/compare")
async def configs_compare(request: Request, pid: int):
    try:
        return ctx(request).configs.compare(pid)
    except KeyError as exc:
        _bad(exc)


@api.post("/configs/{pid}/apply")
async def configs_apply(request: Request, pid: int):
    try:
        return await ctx(request).configs.apply(pid)
    except KeyError as exc:
        _bad(exc)


@api.get("/configs/{pid}/export")
async def configs_export(request: Request, pid: int):
    try:
        p = ctx(request).configs.get(pid)
    except KeyError as exc:
        _bad(exc)
    return Response(json.dumps({"name": p["name"], "description": p["description"], "data": p["data"]}, ensure_ascii=False, indent=2),
                    media_type="application/json",
                    headers={"content-disposition": f"attachment; filename=bipon-config-{pid}.json"})


@api.post("/configs/import")
async def configs_import(request: Request, body: dict = Body(...)):
    if "data" not in body or "settings" not in body["data"]:
        raise HTTPException(400, "Archivo de perfil no válido")
    try:
        return ctx(request).configs.save(body.get("name") or "Importado", body.get("description", ""), data=body["data"])
    except ValueError as exc:
        _bad(exc)


@api.delete("/configs/{pid}")
async def configs_delete(request: Request, pid: int):
    ctx(request).configs.delete(pid)
    return {"ok": True}


# =====================================================================
# Edición, privacidad y preferencias de interfaz
# =====================================================================
@api.get("/system/prefs")
async def prefs_get(request: Request):
    c = ctx(request)
    s = c.settings
    user = request.state.user
    ui = (s.get("ui_prefs") or {}).get(str(user.get("id")), {})
    return {"edition": s.get("edition") or "home", "privacy": s.get("privacy") or {"anonymize_clients": False},
            "region": s.get("region") or {}, "ui": ui, "updates": s.get("updates") or {"auto": False, "channel": "main"}}


@api.put("/system/prefs")
async def prefs_put(request: Request, body: dict = Body(...)):
    c = ctx(request)
    s = c.settings
    user = request.state.user
    if "ui" in body:  # preferencias visuales por usuario (tema, idioma, panel)
        allp = s.get("ui_prefs") or {}
        allp[str(user.get("id"))] = {**allp.get(str(user.get("id")), {}), **body["ui"]}
        s.set("ui_prefs", allp, notify=False)
    if user["role"] == "admin":
        if body.get("edition") in ("home", "business"):
            s.set("edition", body["edition"], notify=False)
        if "privacy" in body:
            s.set("privacy", {"anonymize_clients": bool(body["privacy"].get("anonymize_clients"))})
        if "region" in body:
            reg = {**(s.get("region") or {}), **{k: v for k, v in body["region"].items() if k in ("country", "timezone", "lang")}}
            s.set("region", reg, notify=False)
            region.apply_timezone(reg.get("timezone"))
    return await prefs_get(request)


# =====================================================================
# Actualizaciones
# =====================================================================
@api.get("/updates")
async def updates_get(request: Request):
    return ctx(request).updater.status()


@api.post("/updates/check")
async def updates_check(request: Request):
    try:
        return await ctx(request).updater.check()
    except Exception as exc:
        _bad(exc)


@api.post("/updates/apply")
async def updates_apply(request: Request):
    try:
        return await ctx(request).updater.apply()
    except Exception as exc:
        _bad(exc)


@api.put("/updates")
async def updates_put(request: Request, body: dict = Body(...)):
    c = ctx(request)
    cfg = c.updater.cfg()
    for k in ("auto", "repo", "branch", "time"):
        if k in body:
            cfg[k] = body[k]
    if body.get("token") and body["token"] != "••••••":
        cfg["token"] = body["token"]
    if body.get("token") == "":
        cfg["token"] = ""
    import re as _re
    if not _re.match(r"^[\w.-]+/[\w.-]+$", cfg["repo"]):
        raise HTTPException(400, "Repositorio inválido (formato usuario/repositorio)")
    c.settings.set("updates", cfg, notify=False)
    return c.updater.status()
