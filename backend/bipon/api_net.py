"""API de red avanzada: multi-WAN, interfaces de red, anti-lag (SQM/DSCP) y ruta salto a salto."""
from __future__ import annotations

from fastapi import Body, HTTPException, Request

from . import qos
from .api import api, ctx


def _bad(exc: Exception):
    raise HTTPException(400, str(exc).strip("'"))


def ipv6_bypass(c) -> bool:
    """¿Hay IPv6 global en la red y los equipos no prefieren IPv4? Entonces su tráfico IPv6 va directo al router
    del proveedor y se salta a Bipon (sin anti-lag, prioridad ni multi-WAN)."""
    from . import netinfo
    if (c.settings.get("dns") or {}).get("prefer_ipv4"):
        return False
    for itf in netinfo.run_json(["ip", "-j", "-6", "addr", "show", "scope", "global"]) or []:
        if any(a.get("family") == "inet6" for a in itf.get("addr_info", [])):
            return True
    return False


@api.post("/net/prefer-ipv4")
async def prefer_ipv4(request: Request):
    c = ctx(request)
    d = c.settings.get("dns") or {}
    d["prefer_ipv4"] = True
    c.settings.set("dns", d)
    c.db.event("info", "dns", "Preferencia IPv4 activada para que todo el tráfico pase por Bipon")
    return {"ok": True}


# ============================================================ multi-WAN
@api.get("/multiwan")
async def multiwan_get(request: Request):
    c = ctx(request)
    return {**c.multiwan.status(), "ipv6_bypass": ipv6_bypass(c)}


@api.put("/multiwan")
async def multiwan_put(request: Request, body: dict = Body(...)):
    c = ctx(request)
    mw = c.multiwan
    cfg = {**mw.cfg(), **{k: body[k] for k in ("enabled", "mode", "balance_by", "smart_gaming", "wans", "device_rules", "targets",
                                                "interval", "fail_after", "recover_after", "max_loss", "max_latency") if k in body}}
    try:
        cfg = mw.validate(cfg)
    except (ValueError, TypeError) as exc:
        _bad(exc)
    was = mw.active
    c.settings.set("multiwan", cfg, notify=False)
    if mw.active or was:
        res = await mw.apply()
    else:
        res = {"ok": True, "notes": []}
    c.db.event("info", "multi-WAN", f"Configuración guardada ({cfg['mode']}, {len(cfg['wans'])} líneas)")
    return {**mw.status(), "result": res}


@api.post("/multiwan/{wid}/simulate")
async def multiwan_simulate(request: Request, wid: str, body: dict = Body(default={})):
    try:
        return await ctx(request).multiwan.simulate_down(wid, int(body.get("seconds") or 60))
    except ValueError as exc:
        _bad(exc)


@api.post("/multiwan/{wid}/restore")
async def multiwan_restore(request: Request, wid: str):
    mw = ctx(request).multiwan
    mw.forced_down.pop(wid, None)
    await mw.apply()
    return {"ok": True}


# ============================================================ interfaces de red
@api.get("/interfaces")
async def ifaces_get(request: Request):
    c = ctx(request)
    from .ifaces import dhcp_client
    return {"interfaces": c.ifaces.list(), "pending": bool(c.ifaces.pending),
            "deadline": c.ifaces.pending["deadline"] if c.ifaces.pending else None,
            "mgmt": c.ifaces.mgmt_iface(), "dhcp_client": (dhcp_client() or [None])[0],
            "proxmox": bool(((c.settings.get("integrations") or {}).get("proxmox") or {}).get("url"))}


@api.put("/interfaces/{name}")
async def ifaces_put(request: Request, name: str, body: dict = Body(...)):
    try:
        return await ctx(request).ifaces.configure(name, body)
    except (ValueError, RuntimeError) as exc:
        _bad(exc)


@api.post("/interfaces/confirm")
async def ifaces_confirm(request: Request):
    return {"ok": ctx(request).ifaces.confirm()}


@api.post("/interfaces/vlan")
async def ifaces_vlan_add(request: Request, body: dict = Body(...)):
    try:
        return await ctx(request).ifaces.add_vlan(str(body.get("parent", "")), int(body.get("vid") or 0))
    except (ValueError, RuntimeError) as exc:
        _bad(exc)


@api.delete("/interfaces/vlan/{name}")
async def ifaces_vlan_del(request: Request, name: str):
    try:
        return await ctx(request).ifaces.del_vlan(name)
    except (ValueError, RuntimeError) as exc:
        _bad(exc)


@api.get("/interfaces/proxmox")
async def ifaces_pve(request: Request):
    try:
        return await ctx(request).ifaces.proxmox_self()
    except Exception as exc:
        _bad(exc)


@api.post("/interfaces/proxmox")
async def ifaces_pve_add(request: Request, body: dict = Body(...)):
    try:
        return await ctx(request).ifaces.proxmox_add_nic(str(body.get("bridge", "")), str(body.get("ip") or "dhcp"),
                                                          str(body.get("gw") or ""), body.get("vlan") or None)
    except Exception as exc:
        _bad(exc)


# ============================================================ anti-lag (SQM + prioridad)
@api.get("/sqm")
async def sqm_get(request: Request):
    c = ctx(request)
    cfg = qos.cfg(c)
    down, up, src = qos.rates(c, cfg)
    rc = c.router.cfg()
    last = c.db.one("SELECT ts, download, upload, ping, loaded_ping, bufferbloat, grade FROM speedtests ORDER BY ts DESC LIMIT 1")
    return {"config": cfg, "down": down, "up": up, "source": src, "cake": await qos.has_cake(), "router_enabled": bool(rc.get("enabled")),
            "one_arm": bool(rc.get("wan")) and rc.get("wan") == rc.get("lan"), "last_test": last,
            "classified": c.classifier.counts, "shaping_enabled": bool((rc.get("shaping") or {}).get("enabled")),
            "ipv6_bypass": ipv6_bypass(c)}


@api.put("/sqm")
async def sqm_put(request: Request, body: dict = Body(...)):
    c = ctx(request)
    cfg = qos.cfg(c)
    for k in ("enabled", "auto", "dscp", "interactive_heuristic"):
        if k in body:
            cfg[k] = bool(body[k])
    try:
        for k in ("down_mbit", "up_mbit"):
            if k in body:
                cfg[k] = max(0.0, min(100000.0, float(body[k] or 0)))
        if "factor" in body:
            cfg["factor"] = max(0.5, min(1.0, float(body["factor"])))
        if "bulk_mb" in body:
            cfg["bulk_mb"] = max(1, min(10000, int(body["bulk_mb"])))
    except (TypeError, ValueError) as exc:
        _bad(exc)
    c.settings.set("sqm", cfg, notify=False)
    res = {"ok": True, "message": "Guardado"}
    rc = c.router.cfg()
    if rc.get("enabled") and c.router.available:
        c.router._loaded_text = None
        await c.router.apply(rc, confirm_required=False)  # prioridad DSCP en el firewall
        res = await qos.apply(c)
        c.router._shape_sig = c.router._shape_signature()
    return res


@api.post("/sqm/autotune")
async def sqm_autotune(request: Request):
    c = ctx(request)
    if not c.router.cfg().get("enabled"):
        _bad(ValueError("El anti-lag necesita que Bipon sea la puerta de enlace (Router y firewall)"))
    res = await qos.autotune(c)
    c.router._shape_sig = c.router._shape_signature()
    return res


# ============================================================ ruta salto a salto
@api.get("/quality/trace")
async def trace_get(request: Request):
    t = ctx(request).tracer
    return {"available": t.available, "running": t.running, "latest": t.latest(), "history": t.history(24), "summary": t.summary(7),
            "targets": [{"host": h, "label": label} for h, label in t.targets()]}


@api.post("/quality/trace")
async def trace_run(request: Request, body: dict = Body(default={})):
    t = ctx(request).tracer
    if not t.available:
        _bad(RuntimeError("Falta mtr (instala el paquete mtr-tiny o actualiza Bipon)"))
    try:
        if body.get("target"):
            return {"latest": [await t.run_one(str(body["target"]), str(body["target"]))]}
        return {"latest": await t.run_all()}
    except (ValueError, RuntimeError) as exc:
        _bad(exc)


# ============================================================ guardián ARP
@api.get("/security/arp")
async def arp_status(request: Request):
    return ctx(request).arpguard.status()


@api.post("/security/arp/accept")
async def arp_accept(request: Request, body: dict = Body(...)):
    ok = ctx(request).arpguard.accept(str(body.get("ip", "")))
    if not ok:
        _bad(ValueError("No hay ningún cambio pendiente para esa IP"))
    return {"ok": True}
