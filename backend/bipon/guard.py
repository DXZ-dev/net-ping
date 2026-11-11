"""Seguridad avanzada: equipos que se saltan Bipon, puertos abiertos por UPnP y cuarentena automática.

Bypass de DNS
  * «Sin DNS de Bipon»: equipos en línea varias horas que no hicieron ni una consulta a Bipon → tienen otro DNS
    puesto a mano (o usan DNS cifrado del sistema). Su tráfico no se filtra.
  * «DNS cifrado externo»: equipos que consultan nombres de servidores DoH/DoT públicos (dns.google, cloudflare-dns…)
    → el navegador o el sistema pueden estar resolviendo por fuera de Bipon.
Auditoría UPnP
  Lista lo que tu router ha abierto hacia Internet por UPnP (a menudo sin que lo sepas) y marca los puertos peligrosos.
Cuarentena
  Si un equipo muestra malware (dominios aleatorios, túnel DNS, accesos repetidos a dominios maliciosos), se le corta
  Internet por DNS durante unas horas y se avisa. Se puede liberar con un clic.
"""
from __future__ import annotations

import logging
import time

log = logging.getLogger("bipon.guard")

RISKY_PORTS = {
    21: "FTP (contraseñas sin cifrar)", 22: "SSH (ataques de fuerza bruta)", 23: "Telnet (sin cifrar, muy atacado)",
    25: "SMTP (envío de spam)", 53: "DNS (amplificación DDoS)", 135: "RPC de Windows", 137: "NetBIOS", 139: "NetBIOS/SMB",
    445: "SMB de Windows (ransomware)", 1433: "SQL Server", 1900: "UPnP (amplificación DDoS)", 2323: "Telnet alternativo",
    3306: "MySQL", 3389: "Escritorio remoto de Windows (muy atacado)", 5432: "PostgreSQL", 5555: "ADB de Android (control total)",
    5900: "VNC (control remoto)", 6379: "Redis", 7547: "TR-069 del proveedor", 8291: "Winbox de MikroTik", 9200: "Elasticsearch",
    11211: "Memcached (amplificación DDoS)", 27017: "MongoDB",
}
ADMIN_PORTS = {80: "web sin cifrar", 8080: "web/administración", 8443: "administración", 8006: "Proxmox", 8123: "Home Assistant",
               9090: "administración", 10000: "Webmin", 32400: "Plex"}
QUARANTINE_KINDS = {"dga", "tunnel", "threats"}


class Guard:
    def __init__(self, ctx):
        self.ctx = ctx

    def cfg(self) -> dict:
        return {"quarantine": True, "quarantine_hours": 6, **(self.ctx.settings.get("guard") or {})}

    # ---------------------------------------------------------------- bypass de DNS
    def bypass(self, hours: int = 24) -> dict:
        from .catalog import SERVICE_MAP
        db, devs = self.ctx.db, self.ctx.devices
        since = time.time() - hours * 3600
        db.flush_queries()
        counts = {r["who"]: r["n"] for r in db.query(
            "SELECT COALESCE(mac, client) who, COUNT(*) n FROM queries WHERE ts>=? GROUP BY who", (since,))}
        doh_domains = list((SERVICE_MAP.get("public_doh") or {}).get("domains") or [])
        doh = {}
        if doh_domains:
            ph = ",".join("?" * len(doh_domains))
            for r in db.query(f"SELECT COALESCE(mac, client) who, domain, COUNT(*) n, MAX(ts) last FROM queries "
                              f"WHERE ts>=? AND domain IN ({ph}) GROUP BY who, domain", (since, *doh_domains)):
                doh.setdefault(r["who"], []).append({"domain": r["domain"], "n": r["n"], "last": r["last"]})
        no_dns, enc = [], []
        now = time.time()
        for mac, d in devs.devices.items():
            ip = d.get("ip")
            if not ip or mac.startswith("ip:") or ip in (devs.gateway, devs.self_ip):
                continue
            name = d.get("name") or d.get("friendly") or d.get("hostname") or ip
            n = counts.get(mac, 0) or counts.get(ip, 0)
            seen_for = now - (d.get("first_seen") or now)
            if d.get("online") and n == 0 and seen_for > 2 * 3600 and d.get("kind") not in ("router", "server", "switch", "ap"):
                no_dns.append({"mac": mac, "ip": ip, "name": name, "brand": d.get("brand_name") or d.get("vendor") or ""})
            hits = doh.get(mac) or doh.get(ip)
            if hits:
                enc.append({"mac": mac, "ip": ip, "name": name, "domains": hits})
        blocked_global = "public_doh" in set(self.ctx.settings.get("blocked_services") or [])
        return {"no_dns": no_dns, "encrypted": enc, "doh_blocked": blocked_global, "hours": hours,
                "force_dns": bool((self.ctx.router.cfg() or {}).get("force_dns")) and bool(self.ctx.router.cfg().get("enabled"))}

    # ---------------------------------------------------------------- UPnP
    async def upnp_audit(self) -> dict:
        from . import nat
        igd = await nat.find_igd()
        if not igd:
            return {"available": False, "mappings": []}
        try:
            maps = await igd.mappings()
        except Exception as exc:
            return {"available": True, "error": str(exc)[:160], "mappings": []}
        devs = self.ctx.devices.devices
        by_ip = {d.get("ip"): (d.get("name") or d.get("friendly") or d.get("hostname") or d.get("ip")) for d in devs.values() if d.get("ip")}
        out = []
        for m in maps:
            port = m.get("internal_port") or m.get("external_port")
            ext = m.get("external_port")
            risk, why = "info", ""
            for p in (ext, port):
                if p in RISKY_PORTS:
                    risk, why = "high", RISKY_PORTS[p]
                    break
                if p in ADMIN_PORTS and risk != "high":
                    risk, why = "medium", f"Panel {ADMIN_PORTS[p]} expuesto a Internet"
            out.append({**m, "device": by_ip.get(m.get("internal_ip")) or m.get("internal_ip"), "risk": risk, "why": why})
        out.sort(key=lambda x: {"high": 0, "medium": 1, "info": 2}[x["risk"]])
        return {"available": True, "router": igd.name, "mappings": out,
                "high": sum(1 for x in out if x["risk"] == "high"), "medium": sum(1 for x in out if x["risk"] == "medium")}

    # ---------------------------------------------------------------- cuarentena
    def on_anomaly(self, mac: str | None, client: str, kind: str, detail: str) -> None:
        c = self.cfg()
        if not c["quarantine"] or kind not in QUARANTINE_KINDS or not mac:
            return
        d = self.ctx.devices.devices.get(mac)
        if not d or d.get("ip") in (self.ctx.devices.gateway, self.ctx.devices.self_ip):
            return
        until = time.time() + int(c["quarantine_hours"]) * 3600
        self.ctx.devices.update(mac, {"paused_until": until, "notes": ((d.get("notes") or "") + f"\n[cuarentena] {detail}").strip()[:500]})
        q = self.ctx.settings.get("quarantine") or {}
        q[mac] = {"ts": time.time(), "until": until, "kind": kind, "detail": detail[:200]}
        self.ctx.settings.set("quarantine", q, notify=False)
        try:
            self.ctx.apply_policy_now([mac])
        except Exception:  # pragma: no cover
            pass
        name = d.get("name") or d.get("hostname") or client
        msg = f"{name} en cuarentena {c['quarantine_hours']} h: {detail}. Internet cortado para que no se propague. Libéralo desde Seguridad."
        self.ctx.db.event("error", "seguridad", msg)
        self.ctx.notify.emit("quarantine", "Dispositivo en cuarentena", msg, key=mac)
        log.warning(msg)

    def quarantined(self) -> list[dict]:
        q = self.ctx.settings.get("quarantine") or {}
        now = time.time()
        out = []
        for mac, v in q.items():
            if v.get("until", 0) <= now:
                continue
            d = self.ctx.devices.devices.get(mac) or {}
            out.append({"mac": mac, "name": d.get("name") or d.get("hostname") or d.get("ip") or mac, "ip": d.get("ip"), **v})
        return out

    def release(self, mac: str) -> None:
        q = self.ctx.settings.get("quarantine") or {}
        q.pop(mac, None)
        self.ctx.settings.set("quarantine", q, notify=False)
        self.ctx.devices.update(mac, {"paused_until": 0})
        try:
            self.ctx.apply_policy_now([mac])
        except Exception:  # pragma: no cover
            pass
        self.ctx.db.event("info", "seguridad", f"Cuarentena levantada: {mac}")
