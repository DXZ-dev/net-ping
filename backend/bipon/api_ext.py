"""API de los módulos ampliados: notificaciones, DHCP, tiempo de pantalla,
seguridad, modo automático, tareas programadas, informes, sincronización,
TLS, integraciones, modo router y configuración inicial."""
from __future__ import annotations

import re
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
                      for l in __import__("bipon.catalog", fromlist=["RECOMMENDED_LISTS"]).RECOMMENDED_LISTS],
            "regional_lists": region.REGIONAL_LISTS}


@api.post("/setup/complete")
async def setup_complete(request: Request, body: dict = Body(...)):
    if request.state.user["role"] != "admin":
        raise HTTPException(403)
    return await region.complete_setup(ctx(request), body)


# =====================================================================
# Autoridad certificadora local
# =====================================================================
def _ca_state(c) -> dict:
    tcfg = c.settings.get("tls") or {}
    names, ips = tls.default_names()
    from .wireguard import qr_svg
    url = f"http://{c.devices.self_ip or '127.0.0.1'}:{int(c.settings.get('web.port', 8080) or 8080)}/ca.crt"
    return {"ca": tls.ca_info(), "certificate": tls.info(), "issued": tls.issued(), "extra_names": tcfg.get("extra_names") or [],
            "ca_url": url, "qr": qr_svg(url) if tls.CA_CERT.exists() else None,
            "panel_names": names + ips, "download": {"crt": "/ca.crt", "pem": "/ca.pem", "mobileconfig": "/ca.mobileconfig"},
            "self_ip": c.devices.self_ip, "web_port": int(c.settings.get("web.port", 8080) or 8080)}


@api.get("/tls/ca")
async def ca_get(request: Request):
    return _ca_state(ctx(request))


@api.post("/tls/ca")
async def ca_create(request: Request, body: dict = Body(default={})):
    """Crea la CA local y firma con ella el certificado del panel (HTTPS, DoH y DoT sin avisos)."""
    c = ctx(request)
    try:
        if body.get("force") and tls.CA_CERT.exists():
            import shutil
            shutil.copy(tls.CA_CERT, tls.TLS_DIR / f"ca-anterior-{int(time.time())}.pem")
        tls.create_ca(body.get("extra_domains") or [], bool(body.get("constrained", True)), bool(body.get("force")))
        tls.issue_bipon((c.settings.get("tls") or {}).get("extra_names") or [])
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    c.db.event("info", "seguridad", "Autoridad certificadora local creada; certificado del panel firmado")
    return _ca_state(c)


@api.post("/tls/ca/panel")
async def ca_panel(request: Request, body: dict = Body(default={})):
    """Vuelve a firmar el certificado del panel (p. ej. con nombres extra como bipon.midominio.lan)."""
    c = ctx(request)
    cfg = c.settings.get("tls") or {}
    if "extra_names" in body:
        cfg["extra_names"] = [str(x).strip() for x in body["extra_names"] if str(x).strip()][:30]
        c.settings.set("tls", cfg, notify=False)
    try:
        tls.issue_bipon(cfg.get("extra_names") or [])
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return _ca_state(c)


def _add_local_record(c, name: str, ip: str) -> None:
    d = c.settings.get("dns") or {}
    recs = [r for r in (d.get("local_records") or []) if r.get("name") != name]
    recs.append({"name": name, "type": "AAAA" if ":" in ip else "A", "value": ip})
    d["local_records"] = recs
    c.settings.set("dns", d, notify=False)
    c.dns.set_local_records(recs)


@api.post("/tls/issued")
async def ca_issue(request: Request, body: dict = Body(...)):
    c = ctx(request)
    raw = [x for x in re.split(r"[\s,;]+", str(body.get("names", ""))) if x]
    try:
        cert = tls.issue(str(body.get("label") or raw[0] if raw else "equipo"), raw, [], int(body.get("days") or 825),
                         "rsa" if body.get("key_type") == "rsa" else "ec")
    except (ValueError, IndexError) as exc:
        raise HTTPException(400, str(exc) or "Indica al menos un nombre o una IP")
    if body.get("dns_record"):
        names = [n for n in cert["names"] if not tls._is_ip(n) and not n.startswith("*.")]
        ips = [n for n in cert["names"] if tls._is_ip(n) and not n.startswith("127.")]
        if names and ips:
            for n in names:
                _add_local_record(c, n, ips[0])
    c.db.event("info", "seguridad", f"Certificado emitido para {cert['label']} ({', '.join(cert['names'])})")
    return cert


@api.post("/tls/issued/{cid}/renew")
async def ca_renew(request: Request, cid: str):
    try:
        return tls.renew_issued(cid)
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(400, str(exc))


@api.delete("/tls/issued/{cid}")
async def ca_delete(request: Request, cid: str):
    try:
        tls.delete_issued(cid)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return {"ok": True}


@api.post("/tls/issued/{cid}/download")
async def ca_download(request: Request, cid: str, body: dict = Body(default={})):
    try:
        data, name, mime = tls.export_issued(cid, str(body.get("format", "pem")), str(body.get("password", "")))
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(400, str(exc))
    return Response(data, media_type=mime, headers={"content-disposition": f'attachment; filename="{name}"'})


@api.post("/tls/proxmox")
async def ca_proxmox(request: Request):
    """Firma y coloca un certificado en cada nodo de Proxmox (https://nodo:8006 sin avisos).
    Requiere el token de Integraciones con permiso Sys.Modify (root sin «Privilege Separation»)."""
    import httpx
    c = ctx(request)
    pc = (c.settings.get("integrations") or {}).get("proxmox") or {}
    if not pc.get("url"):
        raise HTTPException(400, "Conecta primero Proxmox en Integraciones")
    base = pc["url"].rstrip("/") + "/api2/json"
    headers = {"Authorization": f"PVEAPIToken={pc['token_id']}={pc['secret']}"}
    host_in_url = (re.match(r"https?://\[?([^\]/:]+)", pc["url"]) or [None, None])[1]
    out = []
    async with httpx.AsyncClient(verify=bool(pc.get("verify_tls")), timeout=30, headers=headers) as h:
        r = await h.get(f"{base}/cluster/status")
        if r.status_code != 200:
            raise HTTPException(400, f"Proxmox respondió HTTP {r.status_code}")
        nodes = [n for n in r.json()["data"] if n.get("type") == "node"]
        for n in nodes:
            names = [n["name"].lower(), f"{n['name'].lower()}.lan"]
            ips = [n.get("ip")] if n.get("ip") else []
            if host_in_url and tls._is_ip(host_in_url) and len(nodes) == 1 and host_in_url not in ips:
                ips.append(host_in_url)
            try:
                cert = tls.issue(f"Proxmox {n['name']}", names + ips, [], 825, "rsa")
                pem, _, _ = tls.export_issued(cert["id"], "pem")
                key, _, _ = tls.export_issued(cert["id"], "key")
                rr = await h.post(f"{base}/nodes/{n['name']}/certificates/custom",
                                  data={"certificates": pem.decode(), "key": key.decode(), "force": 1, "restart": 1})
                if rr.status_code != 200:
                    raise RuntimeError(f"HTTP {rr.status_code}: {rr.text[:160]}"
                                       + (" — el token necesita permiso Sys.Modify (desmarca «Privilege Separation»)" if rr.status_code == 403 else ""))
                if ips:
                    _add_local_record(c, f"{n['name'].lower()}.lan", ips[0])
                out.append({"node": n["name"], "ok": True, "names": cert["names"]})
            except Exception as exc:
                out.append({"node": n["name"], "ok": False, "error": str(exc)[:240]})
    c.db.event("info", "seguridad", "Certificados enviados a Proxmox: " + ", ".join(f"{o['node']} {'✓' if o['ok'] else '✗'}" for o in out))
    return {"nodes": out}


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


@api.post("/integrations/proxmox/reveal")
async def proxmox_reveal(request: Request, body: dict = Body(...)):
    """Muestra el token guardado de Proxmox. Exige volver a escribir la contraseña de un administrador
    (sólo con sesión iniciada: los tokens de API no pueden leerlo)."""
    from . import auth
    c = ctx(request)
    user = request.state.user
    ip = request.client.host if request.client else ""
    if user.get("role") != "admin" or request.headers.get("authorization", "").lower().startswith("bearer"):
        raise HTTPException(403, "Sólo un administrador con sesión iniciada puede ver el token")
    auth.throttle(ip)
    full = auth.get_user(uid=user.get("id")) if user.get("id") is not None else auth.get_user(username=user.get("username"))
    if not full or not auth.verify_password(str(body.get("password", "")), full["password_hash"]):
        auth.record_failure(ip)
        c.db.event("warning", "seguridad", f"Contraseña incorrecta al intentar ver el token de Proxmox ({user.get('username')}, {ip})")
        raise HTTPException(401, "Contraseña incorrecta")
    pc = (c.settings.get("integrations") or {}).get("proxmox") or {}
    c.db.event("info", "seguridad", f"{user.get('username')} vio el token de Proxmox desde {ip}")
    return {"token_id": pc.get("token_id") or "", "secret": pc.get("secret") or "", "url": pc.get("url") or ""}


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
    new["token_id"] = str(new.get("token_id") or "").strip()
    new["secret"] = str(new.get("secret") or "").strip()
    if new.get("url"):
        if "np_" in new["token_id"] or str(new["secret"]).startswith("np_"):
            raise HTTPException(400, "Eso es un token de Bipon (np_…), no de Proxmox. Token ID debe ser como root@pam!bipon y el "
                                     "Secreto el código que muestra Proxmox al crear el token (formato xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx).")
        if not re.match(r"^[\w.-]+@[\w.-]+![\w.-]+$", new["token_id"]):
            raise HTTPException(400, "Token ID no válido: debe ser usuario@realm!nombre, por ejemplo root@pam!bipon")
        if not re.match(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$", new["secret"]):
            raise HTTPException(400, "El Secreto de Proxmox tiene el formato xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx (se muestra una sola vez al crear el token)")
    integ["proxmox"] = new
    c.settings.set("integrations", integ, notify=False)
    if not new.get("url"):
        return {"inventory": {}}
    try:
        return {"inventory": await c.wifi.sync_proxmox()}
    except Exception as exc:
        msg = str(exc)
        if "CERTIFICATE_VERIFY_FAILED" in msg or "certificate verify failed" in msg:
            msg = "Proxmox usa un certificado autofirmado: desactiva «Verificar certificado» y vuelve a conectar."
        elif "401" in msg:
            msg = "Proxmox rechazó el token (HTTP 401): revisa el Token ID (root@pam!bipon) y el Secreto."
        elif "403" in msg:
            msg = "El token no tiene permisos (HTTP 403): desmarca «Privilege Separation» o dale el rol PVEAuditor en Datacenter → Permissions."
        elif "ConnectError" in type(exc).__name__ or "connect" in msg.lower() or "timed out" in msg.lower():
            msg = f"No se pudo conectar con {new['url']}: revisa la IP y que el puerto 8006 esté accesible." + (f" ({msg[:120]})" if msg else "")
        c.wifi.errors["Proxmox"] = msg
        raise HTTPException(400, msg)


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
    return await c.speedctl.apply(mac, down, up)


@api.get("/lag")
async def lag_get(request: Request):
    """Ping en vivo, base, jitter, picos recientes, posibles culpables y estado de «Proteger mi partida»."""
    return ctx(request).lag.status()


@api.post("/lag/protect")
async def lag_protect(request: Request, body: dict = Body(default={})):
    c = ctx(request)
    macs = [str(m).lower() for m in (body.get("macs") or [])]
    if not macs and request.client:
        mac = c.devices.mac_for(request.client.host)
        macs = [mac] if mac else []
    res = await c.lag.start_protect(macs, int(body.get("minutes") or 60), float(body.get("limit_mbps") or 5))
    return {**res, "status": c.lag.status()}


@api.delete("/lag/protect")
async def lag_unprotect(request: Request):
    c = ctx(request)
    await c.lag.stop_protect()
    return c.lag.status()


@api.get("/system/logs")
async def system_logs(request: Request, source: str = "events", level: str = "", q: str = "", limit: int = 300, category: str = ""):
    """Registros para diagnosticar problemas: eventos, aplicación o Unbound."""
    from . import applog
    c = ctx(request)
    limit = max(10, min(int(limit), 2000))
    lv = {"info": ("info", "warning", "error"), "warning": ("warning", "error"), "error": ("error",)}.get(level)
    if source == "app":
        return {"source": "app", "rows": applog.read(level, q, limit)}
    if source == "unbound":
        lines = list(c.unbound.logs)[::-1]
        rows = [{"ts": None, "level": "error" if re.search(r"error|fail|fatal", ln, re.I) else "warning" if "warning" in ln.lower() else "info",
                 "source": "unbound", "message": ln} for ln in lines if not q or q.lower() in ln.lower()]
        if lv:
            rows = [r for r in rows if r["level"] in lv]
        return {"source": "unbound", "rows": rows[:limit]}
    where, params = ["1=1"], []
    if lv:
        where.append(f"level IN ({','.join('?' * len(lv))})")
        params += list(lv)
    if category:
        where.append("category=?")
        params.append(category)
    if q:
        where.append("message LIKE ?")
        params.append(f"%{q}%")
    rows = await c.db.aquery(f"SELECT ts, level, category source, message FROM events WHERE {' AND '.join(where)} ORDER BY id DESC LIMIT ?", (*params, limit))
    cats = [r["category"] for r in await c.db.aquery("SELECT DISTINCT category FROM events ORDER BY category")]
    counts = {r["level"]: r["n"] for r in await c.db.aquery("SELECT level, COUNT(*) n FROM events WHERE ts>=? GROUP BY level", (time.time() - 86400,))}
    return {"source": "events", "rows": rows, "categories": cats, "counts_24h": counts}


@api.get("/cacheguard")
async def cacheguard_get(request: Request):
    c = ctx(request)
    st = c.cacheguard.status()
    if not st["last"]:
        st["last"] = {"bipon": round(c.dns.cache.usage(), 1), "unbound": await c.cacheguard.unbound_usage()}
    return st


@api.put("/cacheguard")
async def cacheguard_put(request: Request, body: dict = Body(...)):
    c = ctx(request)
    cfg = c.cacheguard.cfg()
    if "enabled" in body:
        cfg["enabled"] = bool(body["enabled"])
    if "threshold" in body:
        cfg["threshold"] = max(50, min(int(body["threshold"]), 99))
    c.settings.set("cache_guard", cfg, notify=False)
    return c.cacheguard.status()


@api.post("/cacheguard/clean")
async def cacheguard_clean(request: Request):
    c = ctx(request)
    await c.cacheguard.check(force=True)
    return c.cacheguard.status()


@api.get("/speedctl")
async def speedctl_get(request: Request):
    """Método con el que se limitaría la velocidad de cada dispositivo."""
    return ctx(request).speedctl.overview()


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


# =====================================================================
# Portal cautivo (términos y condiciones)
# =====================================================================
@api.get("/captive")
async def captive_get(request: Request):
    return ctx(request).captive.status()


@api.put("/captive")
async def captive_put(request: Request, body: dict = Body(...)):
    from .captive import DEFAULTS
    c = ctx(request)
    cfg = c.captive.cfg()
    was = cfg["enabled"]
    for k in DEFAULTS:
        if k in body:
            cfg[k] = body[k]
    if not 1 <= int(cfg["port"]) <= 65535:
        raise HTTPException(400, "Puerto inválido")
    c.settings.set("captive", cfg, notify=False)
    c.captive.enabled = bool(cfg["enabled"])
    if cfg["enabled"] and not was and body.get("accept_existing", True):
        c.captive.accept_existing()  # los dispositivos ya conocidos no tienen que aceptar
    c.captive._accepted_cache.clear()
    return c.captive.status()


@api.get("/captive/preview", response_class=Response)
async def captive_preview(request: Request):
    return Response(ctx(request).captive.page("", preview=True), media_type="text/html")


@api.post("/captive/allow")
async def captive_allow(request: Request, body: dict = Body(...)):
    """Aprueba un dispositivo pendiente sin pasar por la página (y opcionalmente lo registra)."""
    c = ctx(request)
    mac = str(body.get("mac", "")).lower()
    if mac not in c.devices.devices:
        raise HTTPException(404, "Dispositivo no encontrado")
    c.captive.allow(mac, trusted=bool(body.get("trusted")))
    return c.captive.status()


@api.post("/captive/trust")
async def captive_trust(request: Request, body: dict = Body(...)):
    """Dispositivos registrados: no se les vuelve a pedir aceptar (ni al pedir a todos de nuevo ni al caducar)."""
    c = ctx(request)
    c.captive.trust(str(body.get("mac", "")).lower(), bool(body.get("trusted", True)))
    return c.captive.status()


@api.delete("/captive/accepts")
async def captive_reset(request: Request, mac: str | None = None):
    ctx(request).captive.reset(mac)
    return {"ok": True}


# =====================================================================
# Actividad por dispositivo (qué apps y webs abre, cuándo y cuánto)
# =====================================================================
def _site(domain: str) -> str:
    parts = domain.split(".")
    if len(parts) >= 3 and parts[-2] in ("co", "com", "org", "net", "gob", "gov", "edu", "ac") and len(parts[-1]) == 2:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


NOISE = ("in-addr.arpa", "ip6.arpa", "local", "lan", "akadns.net", "akamaiedge.net", "edgekey.net", "cloudfront.net", "gstatic.com",
         "googleapis.com", "msftncsi.com", "msftconnecttest.com", "apple-dns.net", "icloud-content.com", "ntp.org", "push.apple.com")


@api.get("/devices/{mac}/activity")
async def device_activity(request: Request, mac: str, hours: int = 24):
    c = ctx(request)
    d = c.devices.devices.get(mac)
    if not d or not d.get("ip"):
        raise HTTPException(404, "Dispositivo sin IP")
    c.db.flush_queries()
    since = time.time() - min(hours, 24 * 30) * 3600
    rows = await c.db.aquery("SELECT ts, domain, status FROM queries WHERE (mac=? OR (mac IS NULL AND client=?)) AND ts>=? ORDER BY ts", (mac, d["ip"], since))
    from .catalog import SERVICE_MAP
    from .dns.filter import BLOCKED_STATUSES
    groups: dict[str, dict] = {}
    for r in rows:
        dom = r["domain"]
        svc = sorted(c.engine.services_for(dom))
        if svc:
            key, label, kind = svc[0], SERVICE_MAP[svc[0]]["name"], SERVICE_MAP[svc[0]]["category"]
        else:
            key = _site(dom)
            if any(key == n or key.endswith("." + n) for n in NOISE):
                continue
            label, kind = key, "Web"
        g = groups.setdefault(key, {"key": key, "name": label, "category": kind, "app": bool(svc), "first": r["ts"], "last": r["ts"],
                                    "queries": 0, "blocked": 0, "minutes": set(), "domains": set()})
        g["last"] = r["ts"]
        g["queries"] += 1
        g["minutes"].add(int(r["ts"] // 60))
        if len(g["domains"]) < 12:
            g["domains"].add(dom)
        if r["status"] in BLOCKED_STATUSES:
            g["blocked"] += 1
    out = []
    for g in groups.values():
        mins = sorted(g.pop("minutes"))
        # sesiones: minutos consecutivos (huecos < 5 min) = uso continuado
        sessions, start, prev = [], None, None
        for m in mins:
            if start is None:
                start = prev = m
            elif m - prev > 5:
                sessions.append((start * 60, (prev + 1) * 60))
                start = m
            prev = m
        if start is not None:
            sessions.append((start * 60, (prev + 1) * 60))
        g["active_minutes"] = len(mins)
        g["sessions"] = sessions[-30:]
        g["domains"] = sorted(g["domains"])
        out.append(g)
    out.sort(key=lambda x: -x["active_minutes"])
    return {"device": d.get("name") or d.get("hostname") or d["ip"], "hours": hours, "items": out[:150], "total_queries": len(rows)}


# =====================================================================
# Infraestructura: Proxmox (recursos y acciones) y procesos de Bipon
# =====================================================================
@api.get("/infra")
async def infra_get(request: Request):
    import httpx
    import os
    c = ctx(request)
    pc = (c.settings.get("integrations") or {}).get("proxmox") or {}
    out = {"proxmox": None, "error": None, "bipon": {}}
    if pc.get("url"):
        try:
            async with httpx.AsyncClient(verify=bool(pc.get("verify_tls")), timeout=12,
                                         headers={"Authorization": f"PVEAPIToken={pc['token_id']}={pc['secret']}"}) as h:
                res = (await h.get(pc["url"].rstrip("/") + "/api2/json/cluster/resources")).json()["data"]
            out["proxmox"] = [{k: r.get(k) for k in ("id", "type", "node", "vmid", "name", "status", "cpu", "maxcpu", "mem", "maxmem",
                                                       "disk", "maxdisk", "uptime", "storage", "template")} for r in res]
        except Exception as exc:
            out["error"] = f"{type(exc).__name__}: {exc}"[:200]
    # procesos y tareas de Bipon
    tasks = [{"name": t.get_name(), "running": not t.done(), "error": (str(t.exception())[:150] if t.done() and not t.cancelled() and t.exception() else None)}
             for t in c.tasks]
    procs = []
    me = os.getpid()
    for pid in os.listdir("/proc"):
        if not pid.isdigit():
            continue
        try:
            with open(f"/proc/{pid}/stat") as fh:
                st = fh.read().split()
            if int(st[3]) != me:
                continue
            with open(f"/proc/{pid}/cmdline", "rb") as fh:
                cmd = fh.read().replace(b"\0", b" ").decode(errors="ignore").strip()
            with open(f"/proc/{pid}/statm") as fh:
                rss = int(fh.read().split()[1]) * os.sysconf("SC_PAGE_SIZE")
            procs.append({"pid": int(pid), "cmd": cmd[:160], "rss": rss})
        except Exception:
            continue
    with open(f"/proc/{me}/statm") as fh:
        my_rss = int(fh.read().split()[1]) * os.sysconf("SC_PAGE_SIZE")
    out["bipon"] = {"pid": me, "rss": my_rss, "tasks": tasks, "children": procs, "unbound": c.unbound.status()}
    return out


@api.post("/infra/proxmox/{node}/{kind}/{vmid}/{action}")
async def infra_action(request: Request, node: str, kind: str, vmid: int, action: str):
    import httpx
    import re as _re
    c = ctx(request)
    if kind not in ("qemu", "lxc") or action not in ("start", "shutdown", "stop", "reboot", "suspend", "resume") or not _re.match(r"^[\w.-]+$", node):
        raise HTTPException(400, "Acción no válida")
    pc = (c.settings.get("integrations") or {}).get("proxmox") or {}
    if not pc.get("url"):
        raise HTTPException(400, "Configura la integración con Proxmox")
    async with httpx.AsyncClient(verify=bool(pc.get("verify_tls")), timeout=20,
                                 headers={"Authorization": f"PVEAPIToken={pc['token_id']}={pc['secret']}"}) as h:
        r = await h.post(f"{pc['url'].rstrip('/')}/api2/json/nodes/{node}/{kind}/{vmid}/status/{action}")
    if r.status_code != 200:
        raise HTTPException(400, f"Proxmox: HTTP {r.status_code} — el token necesita el permiso VM.PowerMgmt")
    c.db.event("info", "infraestructura", f"Proxmox: {action} {kind} {vmid} en {node}")
    return {"ok": True, "task": r.json().get("data")}


# =====================================================================
# Puertos TCP/UDP en uso (servidor) y flujos por dispositivo (modo router)
# =====================================================================
def _svc_name(port: int, proto: str) -> str:
    import socket as _s
    from .tools import COMMON_PORTS
    known = {53: "DNS", 67: "DHCP", 68: "DHCP cliente", 123: "NTP", 137: "NetBIOS", 1900: "SSDP/UPnP", 5353: "mDNS", 5335: "Unbound",
             853: "DNS-over-TLS", 8080: "Panel Bipon", 8443: "Panel Bipon (HTTPS)", 8953: "Control de Unbound", 41641: "Tailscale",
             51820: "WireGuard", 1194: "OpenVPN"}
    if port in known:
        return known[port]
    if port in COMMON_PORTS:
        return COMMON_PORTS[port]
    try:
        return _s.getservbyport(port, proto)
    except OSError:
        return ""


def _split_addr(a: str) -> tuple[str, int]:
    host, _, port = a.rpartition(":")
    try:
        return host.strip("[]"), int(port)
    except ValueError:
        return host, 0


@api.get("/ports")
async def ports_get(request: Request):
    import asyncio
    import re as _re
    import shutil
    c = ctx(request)
    out = {"listening": [], "connections": [], "flows": [], "notes": []}
    if not shutil.which("ss"):
        out["notes"].append("La herramienta «ss» (iproute2) no está instalada.")
        return out

    async def run(*args):
        p = await asyncio.create_subprocess_exec(*args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
        o, _ = await p.communicate()
        return o.decode(errors="ignore")

    listen_txt, est_txt = await asyncio.gather(run("ss", "-H", "-tulpn"), run("ss", "-H", "-tnp", "state", "established"))
    for line in listen_txt.splitlines():
        f = line.split()
        if len(f) < 5:
            continue
        proto = f[0]
        host, port = _split_addr(f[4])
        procs = _re.findall(r'\("([^"]+)",pid=(\d+)', line)
        out["listening"].append({"proto": proto.upper(), "address": host, "port": port, "service": _svc_name(port, "udp" if proto == "udp" else "tcp"),
                                 "process": ", ".join(sorted({p for p, _ in procs})) or "—", "pids": sorted({int(x) for _, x in procs}),
                                 "exposed": host not in ("127.0.0.1", "::1", "127.0.0.53", "127.0.0.54")})
    out["listening"].sort(key=lambda x: (x["port"], x["proto"]))
    names = {d.get("ip"): (d.get("name") or d.get("friendly") or d.get("hostname") or d.get("ip")) for d in c.devices.devices.values() if d.get("ip")}
    for line in est_txt.splitlines()[:300]:
        f = line.split()
        if len(f) < 4:
            continue
        lh, lp = _split_addr(f[2])
        rh, rp = _split_addr(f[3])
        procs = _re.findall(r'\("([^"]+)",pid=(\d+)', line)
        out["connections"].append({"local_port": lp, "remote": rh, "remote_port": rp, "remote_name": names.get(rh, ""),
                                   "service": _svc_name(lp, "tcp") or _svc_name(rp, "tcp"), "process": ", ".join(sorted({p for p, _ in procs})) or "—"})
    # flujos de los dispositivos (sólo si el tráfico pasa por Bipon: modo router)
    if shutil.which("conntrack"):
        txt = await run("conntrack", "-L", "-o", "extended")
        agg: dict = {}
        for line in txt.splitlines()[:20000]:
            m = _re.search(r"\b(tcp|udp)\b.*?src=(\S+) dst=(\S+) sport=(\d+) dport=(\d+)", line)
            if not m:
                continue
            proto, src, dst, _, dport = m.groups()
            if src not in names:
                continue
            k = (src, proto, int(dport))
            a = agg.setdefault(k, {"device": names[src], "ip": src, "proto": proto.upper(), "port": int(dport),
                                   "service": _svc_name(int(dport), proto), "connections": 0, "destinations": set()})
            a["connections"] += 1
            if len(a["destinations"]) < 5:
                a["destinations"].add(dst)
        out["flows"] = sorted(({**v, "destinations": sorted(v["destinations"])} for v in agg.values()), key=lambda x: -x["connections"])[:300]
        if not out["flows"]:
            out["notes"].append("No hay flujos de dispositivos: sólo se ven cuando el tráfico de la red pasa por Bipon (modo router).")
    else:
        out["notes"].append("Instala «conntrack» para ver los puertos que usa cada dispositivo (en modo router).")
    return out


# =====================================================================
# Acceso por origen (red local / externo)
# =====================================================================
@api.get("/access")
async def access_get(request: Request):
    c = ctx(request)
    return {**c.access.status(), "your_ip": request.client.host if request.client else None}


@api.put("/access")
async def access_put(request: Request, body: dict = Body(...)):
    import ipaddress as _ip
    from .access import MODES
    c = ctx(request)
    cfg = c.access.cfg()
    for k in ("web", "dns"):
        if k in body:
            if body[k] not in MODES:
                raise HTTPException(400, "Modo inválido")
            cfg[k] = body[k]
    for k in ("web_allow", "dns_allow"):
        if k in body:
            nets = [n.strip() for n in body[k] if n.strip()]
            for n in nets:
                try:
                    _ip.ip_network(n, strict=False)
                except ValueError:
                    raise HTTPException(400, f"Red inválida: {n}")
            cfg[k] = nets
    # no dejar fuera a quien está haciendo el cambio
    me = request.client.host if request.client else ""
    c.settings.set("access", cfg)
    if me and not c.access.allowed("web", me):
        cfg["web_allow"] = cfg.get("web_allow", []) + [me]
        if cfg["web"] != "custom":
            cfg["web"] = "custom"
        c.settings.set("access", cfg)
        return {**c.access.status(), "warning": f"Se añadió tu IP ({me}) para no perder el acceso."}
    return c.access.status()


# =====================================================================
# Instalación de componentes (Tailscale, WireGuard, OpenVPN…)
# =====================================================================
@api.get("/packages")
async def packages_get(request: Request):
    return ctx(request).installer.status()


@api.post("/packages/{tool}/install")
async def packages_install(request: Request, tool: str):
    try:
        return await ctx(request).installer.install(tool)
    except (ValueError, RuntimeError) as exc:
        _bad(exc)


@api.post("/packages/enable-tun")
async def enable_tun(request: Request):
    """Activa /dev/net/tun en el LXC de Bipon vía API de Proxmox (dev0) y reinicia el contenedor."""
    import httpx
    import os
    c = ctx(request)
    if os.path.exists("/dev/net/tun"):
        return {"ok": True, "message": "TUN ya está disponible"}
    pc = (c.settings.get("integrations") or {}).get("proxmox") or {}
    if not pc.get("url"):
        raise HTTPException(400, "Conecta primero Bipon con Proxmox (Integraciones → Proxmox) usando un token de root@pam")
    from .netinfo import interfaces
    my_macs = {i.get("mac", "").lower() for i in interfaces() if i.get("mac")}
    inv = c.wifi.proxmox or await c.wifi.sync_proxmox()
    me = next((v for m, v in inv.items() if m in my_macs and v["type"] == "lxc"), None)
    if not me:
        raise HTTPException(400, "No encontré este contenedor en Proxmox (¿Bipon corre en un LXC de ese servidor?)")
    base = pc["url"].rstrip("/") + f"/api2/json/nodes/{me['node']}/lxc/{me['vmid']}"
    async with httpx.AsyncClient(verify=bool(pc.get("verify_tls")), timeout=20,
                                 headers={"Authorization": f"PVEAPIToken={pc['token_id']}={pc['secret']}"}) as h:
        conf = (await h.get(base + "/config")).json().get("data", {})
        slot = next(f"dev{i}" for i in range(10) if f"dev{i}" not in conf)
        r = await h.put(base + "/config", data={slot: "/dev/net/tun"})
        if r.status_code != 200:
            raise HTTPException(400, f"Proxmox rechazó el cambio (HTTP {r.status_code}). El paso de dispositivos requiere un token de "
                                     f"root@pam sin separación de privilegios. Alternativa en el host: pct set {me['vmid']} -dev0 /dev/net/tun && pct reboot {me['vmid']}")
        c.db.event("warning", "sistema", f"TUN activado en el LXC {me['vmid']}: reiniciando el contenedor")
        await h.post(base + "/status/reboot")
    return {"ok": True, "vmid": me["vmid"], "message": f"TUN activado en el contenedor {me['vmid']}. Bipon se reinicia; vuelve en ~30 s."}


@api.get("/packages/self")
async def packages_self(request: Request):
    """ID del contenedor de Bipon en Proxmox (si se conoce) para mostrar el comando exacto."""
    from .netinfo import interfaces
    c = ctx(request)
    my_macs = {i.get("mac", "").lower() for i in interfaces() if i.get("mac")}
    me = next((v for m, v in (c.wifi.proxmox or {}).items() if m in my_macs), None)
    return {"vmid": me["vmid"] if me else None, "node": me["node"] if me else None}
