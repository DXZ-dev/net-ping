"""Alta disponibilidad: sincronización principal → secundario.

Un segundo equipo con Bipon Net (otra Raspberry, otro LXC…) en modo «secundario»
copia periódicamente del principal: listas, reglas, apps bloqueadas, perfiles
parentales, DNS, Unbound, gaming y seguridad, y los nombres/perfiles de los
dispositivos. Así ambos filtran igual y la red tiene dos DNS:
  * con el DNS del router: DNS1 = principal, DNS2 = secundario;
  * sin tocar el router: activa el «Modo automático» en los dos.
El DHCP, la VPN y los usuarios NO se copian (dos DHCP en la misma red chocarían).
"""
from __future__ import annotations

import asyncio
import logging
import time

import httpx

log = logging.getLogger("bipon.sync")

SYNCED_SECTIONS = ("dns", "blocking", "profiles", "unbound", "gaming", "security", "blocked_services", "monitor", "notify")
NEVER_SYNC = ("secret", "auth", "sync", "dhcp", "autodns", "tls", "router", "scheduler_state", "family")


def export_state(ctx) -> dict:
    s = ctx.settings
    data = {k: s.get(k) for k in SYNCED_SECTIONS if s.get(k) is not None}
    # el puerto de escucha y el estado del optimizador son propios de cada nodo
    if "dns" in data:
        data["dns"] = {k: v for k, v in data["dns"].items() if k not in ("port", "listen")}
    return {
        "ts": time.time(), "settings": data,
        "rules": ctx.db.query("SELECT value, kind, comment, enabled FROM domain_rules"),
        "lists": ctx.db.query("SELECT url, name, category, enabled FROM blocklists"),
        "devices": ctx.db.query("SELECT mac, name, kind, profile, notes, paused, paused_until FROM devices"),
    }


async def import_state(ctx, data: dict) -> dict:
    s = ctx.settings
    for k, v in (data.get("settings") or {}).items():
        if k in NEVER_SYNC or k not in SYNCED_SECTIONS:
            continue
        if k == "dns":
            cur = s.get("dns") or {}
            v = {**v, "port": cur.get("port", 53), "listen": cur.get("listen", "0.0.0.0")}
        s.set(k, v)
    db = ctx.db
    rules = data.get("rules") or []
    db.execute("DELETE FROM domain_rules")
    db.executemany("INSERT OR IGNORE INTO domain_rules(value,kind,comment,enabled,created) VALUES (?,?,?,?,?)",
                   [(r["value"], r["kind"], r.get("comment", ""), r.get("enabled", 1), time.time()) for r in rules])
    remote_lists = {l["url"]: l for l in data.get("lists") or []}
    local = {l["url"]: l for l in db.query("SELECT * FROM blocklists")}
    changed_lists = False
    for url, l in remote_lists.items():
        if url not in local:
            db.execute("INSERT INTO blocklists(url,name,category,enabled) VALUES (?,?,?,?)", (url, l["name"], l["category"], l["enabled"]))
            changed_lists = True
        elif (local[url]["enabled"], local[url]["category"]) != (l["enabled"], l["category"]):
            db.execute("UPDATE blocklists SET enabled=?, category=?, name=? WHERE url=?", (l["enabled"], l["category"], l["name"], url))
            changed_lists = True
    for url, l in local.items():
        if url not in remote_lists:
            db.execute("DELETE FROM blocklists WHERE url=?", (url,))
            ctx.lists.delete_file(l["id"])
            changed_lists = True
    for d in data.get("devices") or []:
        db.execute("INSERT INTO devices(mac,name,kind,profile,notes,paused,paused_until,first_seen,last_seen) VALUES (?,?,?,?,?,?,?,?,0) "
                   "ON CONFLICT(mac) DO UPDATE SET name=excluded.name, kind=excluded.kind, profile=excluded.profile, notes=excluded.notes, "
                   "paused=excluded.paused, paused_until=excluded.paused_until",
                   (d["mac"], d.get("name", ""), d.get("kind", ""), d.get("profile", "default"), d.get("notes", ""),
                    d.get("paused", 0), d.get("paused_until", 0), time.time()))
    ctx.devices.load()
    if changed_lists:
        asyncio.create_task(ctx.lists.update())
    else:
        await ctx.lists.rebuild()
    ctx.cache.clear()
    return {"rules": len(rules), "lists": len(remote_lists), "devices": len(data.get("devices") or [])}


class SyncManager:
    def __init__(self, ctx):
        self.ctx = ctx
        self.last_ok = 0.0
        self.last_error = ""
        self.last_result: dict = {}

    def cfg(self) -> dict:
        c = self.ctx.settings.get("sync") or {}
        c.setdefault("role", "standalone")
        c.setdefault("primary_url", "")
        c.setdefault("token", "")
        c.setdefault("interval", 300)
        return c

    async def pull(self) -> dict:
        c = self.cfg()
        url = c["primary_url"].rstrip("/")
        if not url.startswith(("http://", "https://")):
            raise ValueError("URL del principal inválida")
        # el principal suele usar certificado autofirmado: la verificación es opcional
        async with httpx.AsyncClient(timeout=30, verify=bool(c.get("verify_tls"))) as h:
            r = await h.get(f"{url}/api/sync/export", headers={"authorization": f"Bearer {c['token']}"})
        if r.status_code != 200:
            raise RuntimeError(f"El principal respondió HTTP {r.status_code}: {r.text[:150]}")
        result = await import_state(self.ctx, r.json())
        self.last_ok, self.last_error, self.last_result = time.time(), "", result
        return result

    async def loop(self) -> None:
        await asyncio.sleep(20)
        while True:
            c = self.cfg()
            if c["role"] == "secondary" and c["primary_url"] and c["token"]:
                try:
                    await self.pull()
                except Exception as exc:
                    self.last_error = str(exc)
                    log.warning("sincronización: %s", exc)
                    self.ctx.notify.emit("sync", "No se pudo sincronizar con el principal", str(exc)[:200], key="sync")
            await asyncio.sleep(max(60, int(c.get("interval", 300))))

    def status(self) -> dict:
        c = self.cfg()
        return {"config": {**c, "token": "••••••" if c.get("token") else ""}, "last_ok": self.last_ok,
                "last_error": self.last_error, "last_result": self.last_result}
