"""Integraciones: controladores Wi-Fi (UniFi, OpenWrt, MikroTik) y Home Assistant.

Wi-Fi: lee señal (dBm), banda, canal y velocidad de cada cliente, y el uso de
cada radio, para recomendar canales y detectar dispositivos con mala cobertura.
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections import Counter

import httpx

log = logging.getLogger("netping.integrations")

WIFI_TYPES = {"unifi": "UniFi Network", "openwrt": "OpenWrt (ubus)", "mikrotik": "MikroTik RouterOS 7 (REST)"}


def _band(channel: int | None, freq: int | None = None) -> str:
    if freq:
        return "6 GHz" if freq >= 5925 else "5 GHz" if freq >= 4900 else "2,4 GHz"
    if channel is None:
        return "?"
    return "2,4 GHz" if channel <= 14 else "5 GHz"


def signal_quality(dbm: float | None) -> str:
    if dbm is None:
        return "desconocida"
    return "excelente" if dbm >= -55 else "buena" if dbm >= -67 else "regular" if dbm >= -75 else "mala"


# ---------------------------------------------------------------- UniFi
async def unifi(cfg: dict) -> dict:
    base = cfg["url"].rstrip("/")
    site = cfg.get("site") or "default"
    async with httpx.AsyncClient(verify=bool(cfg.get("verify_tls")), timeout=15) as h:
        # UniFi OS (UDM/Cloud Key Gen2+) primero; si no, controlador clásico
        r = await h.post(f"{base}/api/auth/login", json={"username": cfg["username"], "password": cfg["password"]})
        prefix = "/proxy/network"
        headers = {}
        if r.status_code == 200:
            csrf = r.headers.get("x-csrf-token")
            if csrf:
                headers["x-csrf-token"] = csrf
        else:
            r = await h.post(f"{base}/api/login", json={"username": cfg["username"], "password": cfg["password"]})
            prefix = ""
            if r.status_code != 200:
                raise RuntimeError(f"UniFi rechazó el inicio de sesión (HTTP {r.status_code})")
        sta = (await h.get(f"{base}{prefix}/api/s/{site}/stat/sta", headers=headers)).json().get("data", [])
        dev = (await h.get(f"{base}{prefix}/api/s/{site}/stat/device", headers=headers)).json().get("data", [])
    aps = {d.get("mac"): d.get("name") or d.get("model") for d in dev}
    clients = [{"mac": c.get("mac"), "name": c.get("name") or c.get("hostname") or "", "ip": c.get("ip"), "ap": aps.get(c.get("ap_mac"), c.get("ap_mac")),
                "ssid": c.get("essid"), "channel": c.get("channel"), "band": _band(c.get("channel")), "signal": c.get("signal") or c.get("rssi"),
                "tx_rate": (c.get("tx_rate") or 0) / 1000, "rx_rate": (c.get("rx_rate") or 0) / 1000, "uptime": c.get("uptime")}
               for c in sta if not c.get("is_wired")]
    radios = []
    for d in dev:
        for rs in d.get("radio_table_stats") or []:
            radios.append({"ap": d.get("name") or d.get("model"), "band": _band(rs.get("channel")), "channel": rs.get("channel"),
                           "utilization": rs.get("cu_total"), "clients": rs.get("num_sta"), "tx_power": rs.get("tx_power")})
    return {"clients": clients, "radios": radios}


# ---------------------------------------------------------------- OpenWrt
async def openwrt(cfg: dict) -> dict:
    url = cfg["url"].rstrip("/") + "/ubus"
    async with httpx.AsyncClient(verify=bool(cfg.get("verify_tls")), timeout=15) as h:
        async def call(sid, obj, method, args=None):
            r = await h.post(url, json={"jsonrpc": "2.0", "id": 1, "method": "call", "params": [sid, obj, method, args or {}]})
            res = r.json().get("result", [1])
            if res[0] != 0:
                raise RuntimeError(f"ubus {obj}.{method} devolvió {res[0]} (¿permisos rpcd/iwinfo?)")
            return res[1] if len(res) > 1 else {}
        login = await call("0" * 32, "session", "login", {"username": cfg.get("username") or "root", "password": cfg["password"]})
        sid = login["ubus_rpc_session"]
        devs = (await call(sid, "iwinfo", "devices")).get("devices", [])
        clients, radios = [], []
        for d in devs:
            info = await call(sid, "iwinfo", "info", {"device": d})
            assoc = (await call(sid, "iwinfo", "assoclist", {"device": d})).get("results", [])
            band = _band(info.get("channel"), info.get("frequency"))
            radios.append({"ap": cfg.get("name") or "OpenWrt", "band": band, "channel": info.get("channel"), "ssid": info.get("ssid"),
                           "utilization": None, "clients": len(assoc), "tx_power": info.get("txpower"), "noise": info.get("noise")})
            for a in assoc:
                clients.append({"mac": (a.get("mac") or "").lower(), "name": "", "ap": cfg.get("name") or "OpenWrt", "ssid": info.get("ssid"),
                                "channel": info.get("channel"), "band": band, "signal": a.get("signal"),
                                "tx_rate": (a.get("tx") or {}).get("rate", 0) / 1000, "rx_rate": (a.get("rx") or {}).get("rate", 0) / 1000,
                                "uptime": a.get("connected_time")})
    return {"clients": clients, "radios": radios}


# ---------------------------------------------------------------- MikroTik
def _mk_rate(s: str | None) -> float:
    if not s:
        return 0
    try:
        num = float("".join(ch for ch in s.split()[0] if ch.isdigit() or ch == "."))
        return num / 1000 if "Kbps" in s else num
    except ValueError:
        return 0


async def mikrotik(cfg: dict) -> dict:
    base = cfg["url"].rstrip("/") + "/rest"
    auth = (cfg.get("username") or "admin", cfg.get("password", ""))
    async with httpx.AsyncClient(verify=bool(cfg.get("verify_tls")), timeout=15, auth=auth) as h:
        clients, radios = [], []
        for path in ("interface/wifi", "interface/wireless"):  # wifi (wave2) y wireless (clásico)
            r = await h.get(f"{base}/{path}/registration-table")
            if r.status_code != 200:
                continue
            ifaces = {i.get("name"): i for i in (await h.get(f"{base}/{path}")).json()}
            for c in r.json():
                itf = ifaces.get(c.get("interface"), {})
                ch = itf.get("channel") or itf.get("channel.frequency") or ""
                sig = c.get("signal") or c.get("signal-strength") or ""
                try:
                    sig = int(str(sig).split("@")[0].replace("dBm", ""))
                except ValueError:
                    sig = None
                clients.append({"mac": (c.get("mac-address") or "").lower(), "name": c.get("comment") or "", "ap": c.get("interface"),
                                "ssid": itf.get("ssid") or itf.get("configuration.ssid"), "channel": ch, "band": itf.get("band") or "?",
                                "signal": sig, "tx_rate": _mk_rate(c.get("tx-rate")), "rx_rate": _mk_rate(c.get("rx-rate")), "uptime": c.get("uptime")})
            for name, itf in ifaces.items():
                radios.append({"ap": name, "band": itf.get("band") or "?", "channel": itf.get("channel") or itf.get("frequency"),
                               "ssid": itf.get("ssid") or itf.get("configuration.ssid"), "utilization": None,
                               "clients": sum(1 for c in clients if c["ap"] == name)})
    return {"clients": clients, "radios": radios}


DRIVERS = {"unifi": unifi, "openwrt": openwrt, "mikrotik": mikrotik}


# ---------------------------------------------------------------- Proxmox VE
async def proxmox_inventory(cfg: dict) -> dict:
    """Lista VMs y contenedores con su MAC y el nodo (IP) que los aloja.
    Requiere un token de API de sólo lectura (rol PVEAuditor)."""
    import re as _re
    base = cfg["url"].rstrip("/") + "/api2/json"
    headers = {"Authorization": f"PVEAPIToken={cfg['token_id']}={cfg['secret']}"}
    out: dict[str, dict] = {}
    async with httpx.AsyncClient(verify=bool(cfg.get("verify_tls")), timeout=15, headers=headers) as h:
        r = await h.get(f"{base}/cluster/status")
        if r.status_code != 200:
            raise RuntimeError(f"Proxmox respondió HTTP {r.status_code} (revisa el token)")
        nodes = {n["name"]: n.get("ip") for n in r.json()["data"] if n.get("type") == "node"}
        vms = (await h.get(f"{base}/cluster/resources", params={"type": "vm"})).json()["data"]

        async def one(vm):
            kind = "lxc" if vm["type"] == "lxc" else "qemu"
            try:
                conf = (await h.get(f"{base}/nodes/{vm['node']}/{kind}/{vm['vmid']}/config")).json()["data"]
            except Exception:
                return
            for k, v in conf.items():
                if not _re.match(r"^net\d+$", k):
                    continue
                m = _re.search(r"([0-9A-Fa-f]{2}(?::[0-9A-Fa-f]{2}){5})", str(v))
                if m:
                    out[m.group(1).lower()] = {"node": vm["node"], "node_ip": nodes.get(vm["node"]), "vmid": vm["vmid"],
                                               "name": conf.get("name") or conf.get("hostname") or vm.get("name"), "type": kind,
                                               "status": vm.get("status")}
        await asyncio.gather(*(one(vm) for vm in vms))
    return out


class WifiManager:
    def __init__(self, ctx):
        self.ctx = ctx
        self.cache: dict = {}
        self.updated = 0.0
        self.errors: dict[str, str] = {}
        self.proxmox: dict = {}

    def controllers(self) -> list[dict]:
        return (self.ctx.settings.get("integrations") or {}).get("wifi", [])

    async def collect(self) -> dict:
        clients, radios = [], []
        self.errors = {}
        async def one(c):
            try:
                return c, await DRIVERS[c["type"]](c)
            except Exception as exc:
                self.errors[c.get("name") or c["type"]] = f"{type(exc).__name__}: {exc}"[:200]
                return c, None
        for c, res in await asyncio.gather(*(one(c) for c in self.controllers() if c.get("enabled", True) and c.get("type") in DRIVERS)):
            if res:
                clients += res["clients"]
                radios += res["radios"]
        devs = self.ctx.devices.devices
        for cl in clients:
            d = devs.get(cl["mac"])
            if d:
                cl["name"] = d.get("name") or d.get("hostname") or cl["name"]
                cl["ip"] = cl.get("ip") or d.get("ip")
            cl["quality"] = signal_quality(cl.get("signal"))
        self.cache = {"clients": sorted(clients, key=lambda c: c.get("signal") or -999), "radios": radios,
                      "recommendations": self.recommend(clients, radios), "errors": self.errors}
        self.updated = time.time()
        return self.cache

    @staticmethod
    def recommend(clients: list[dict], radios: list[dict]) -> list[str]:
        tips = []
        weak = [c for c in clients if c.get("signal") is not None and c["signal"] < -75]
        if weak:
            tips.append(f"{len(weak)} dispositivo(s) con señal mala (< -75 dBm): {', '.join((c['name'] or c['mac']) for c in weak[:4])}. "
                        "Acércalos al punto de acceso, añade un AP/mesh o usa cable para consolas y TV.")
        r24 = [r for r in radios if r["band"].startswith("2")]
        chans = Counter(r.get("channel") for r in r24)
        for r in r24:
            ch = r.get("channel")
            try:
                if ch and int(str(ch).split()[0]) not in (1, 6, 11):
                    tips.append(f"{r['ap']}: el canal 2,4 GHz {ch} se solapa con otros; usa 1, 6 u 11.")
            except ValueError:
                pass
        if any(n > 1 for ch, n in chans.items() if ch):
            free = [c for c in (1, 6, 11) if c not in chans]
            if free:
                tips.append(f"Varios AP comparten canal en 2,4 GHz; el canal {free[0]} está libre.")
        busy = [r for r in radios if (r.get("utilization") or 0) > 60]
        for r in busy:
            tips.append(f"{r['ap']} ({r['band']}, canal {r['channel']}) está saturado ({r['utilization']} % de uso). Cambia de canal o reduce la anchura.")
        slow = [c for c in clients if c.get("band") == "2,4 GHz" and (c.get("signal") or -99) > -60]
        if len(slow) >= 3:
            tips.append(f"{len(slow)} dispositivos con buena señal siguen en 2,4 GHz: activa «band steering» para moverlos a 5 GHz (más rápido y con menos interferencias).")
        if not tips and clients:
            tips.append("La red Wi-Fi está en buen estado.")
        return tips

    async def sync_proxmox(self) -> dict:
        integ = self.ctx.settings.get("integrations") or {}
        pc = integ.get("proxmox")
        if not pc or not pc.get("url"):
            return {}
        inv = await proxmox_inventory(pc)
        integ["proxmox_map"] = {mac: v["node_ip"] for mac, v in inv.items() if v.get("node_ip")}
        self.ctx.settings.set("integrations", integ, notify=False)
        db = self.ctx.db
        for mac, v in inv.items():
            label = f"{v['name']} ({'LXC' if v['type'] == 'lxc' else 'VM'} {v['vmid']})"
            db.execute("UPDATE devices SET friendly=?, hypervisor='proxmox' WHERE mac=?", (label, mac))
        self.ctx.devices.load()
        self.ctx.devices.compute_parents()
        self.proxmox = inv
        return inv

    async def loop(self) -> None:
        await asyncio.sleep(45)
        while True:
            if self.controllers():
                try:
                    await self.collect()
                except Exception:  # pragma: no cover
                    log.exception("wifi")
            try:
                await self.sync_proxmox()
            except Exception as exc:
                self.errors["Proxmox"] = str(exc)[:200]
            await asyncio.sleep(120)


# ---------------------------------------------------------------- Home Assistant
def ha_state(ctx) -> dict:
    """Estado compacto para sensores REST de Home Assistant."""
    d = ctx.dns.stats()
    mon = ctx.monitor.last.get("results", {}) if ctx.monitor.last else {}
    inet = next((r for k, r in mon.items() if k != "gateway"), {})
    last_speed = ctx.db.one("SELECT download, upload, ping, grade FROM speedtests ORDER BY ts DESC LIMIT 1") or {}
    devs = ctx.devices.devices.values()
    online = sum(1 for x in devs if x.get("online"))
    b = ctx.settings.get("blocking") or {}
    return {
        "blocking": ctx.dns.blocking_enabled, "blocking_enabled": b.get("enabled", True),
        "queries_total": d["total"], "queries_blocked": d["blocked"], "blocked_pct": round(d["blocked"] / d["total"] * 100, 1) if d["total"] else 0,
        "qps": d["qps"], "response_ms": d["avg_response_ms"], "cache_hit_ratio": d["cache"]["hit_ratio"],
        "internet_online": bool(ctx.monitor.last.get("online", True)) if ctx.monitor.last else None,
        "internet_latency_ms": inet.get("avg"), "internet_loss_pct": inet.get("loss"),
        "speed_down_mbps": last_speed.get("download"), "speed_up_mbps": last_speed.get("upload"), "speed_ping_ms": last_speed.get("ping"),
        "bufferbloat_grade": last_speed.get("grade"), "devices_total": len(ctx.devices.devices), "devices_online": online,
        "devices_paused": sum(1 for x in devs if x.get("paused") or (x.get("paused_until") or 0) > time.time()),
        "unbound_running": ctx.unbound.running, "anomalies_24h": ctx.db.scalar("SELECT COUNT(*) FROM anomalies WHERE ts>?", (time.time() - 86400,)) or 0,
    }


def ha_yaml(base_url: str, token: str = "TU_TOKEN_DE_NETPING") -> str:
    sensors = [("Consultas DNS", "queries_total", None), ("Consultas bloqueadas", "queries_blocked", None), ("Porcentaje bloqueado", "blocked_pct", "%"),
               ("Latencia Internet", "internet_latency_ms", "ms"), ("Respuesta DNS", "response_ms", "ms"), ("Velocidad bajada", "speed_down_mbps", "Mbit/s"),
               ("Velocidad subida", "speed_up_mbps", "Mbit/s"), ("Dispositivos en línea", "devices_online", None)]
    lines = ["# --- NetPing para Home Assistant (configuration.yaml) ---", "rest:", f"  - resource: {base_url}/api/integrations/ha/state",
             "    headers:", f"      Authorization: Bearer {token}", "    scan_interval: 30", "    verify_ssl: false", "    sensor:"]
    for name, key, unit in sensors:
        lines += [f"      - name: \"NetPing {name}\"", f"        value_template: \"{{{{ value_json.{key} }}}}\""]
        if unit:
            lines.append(f"        unit_of_measurement: \"{unit}\"")
    lines += ["    binary_sensor:", "      - name: \"NetPing protección\"", "        value_template: \"{{ value_json.blocking }}\"",
              "      - name: \"NetPing Internet\"", "        device_class: connectivity", "        value_template: \"{{ value_json.internet_online }}\"",
              "", "rest_command:", "  netping_pausar_bloqueo:", f"    url: {base_url}/api/blocking", "    method: POST",
              "    headers:", f"      Authorization: Bearer {token}", "    content_type: application/json", "    payload: '{\"pause_seconds\": 1800}'",
              "    verify_ssl: false", "  netping_reanudar_bloqueo:", f"    url: {base_url}/api/blocking", "    method: POST", "    headers:",
              f"      Authorization: Bearer {token}", "    content_type: application/json", "    payload: '{\"enabled\": true}'", "    verify_ssl: false",
              "  netping_pausa_familiar:", f"    url: {base_url}/api/family/pause", "    method: POST", "    headers:", f"      Authorization: Bearer {token}",
              "    content_type: application/json", "    payload: '{\"minutes\": {{ minutos | default(30) }}}'", "    verify_ssl: false",
              "  netping_pausar_dispositivo:", f"    url: \"{base_url}/api/devices/{{{{ mac }}}}\"", "    method: PATCH", "    headers:",
              f"      Authorization: Bearer {token}", "    content_type: application/json", "    payload: '{\"pause_minutes\": {{ minutos | default(60) }}}'",
              "    verify_ssl: false"]
    return "\n".join(lines) + "\n"
