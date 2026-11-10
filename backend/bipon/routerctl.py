"""Control del router desde Bipon Net.

Sólo se tocan ajustes «seguros» de la LAN: DNS que reparte el DHCP, canal
Wi-Fi, reinicio y copia de seguridad. NUNCA la WAN/PPPoE, VLAN de servicio,
VoIP ni credenciales del proveedor. Cada cambio guarda el valor anterior y
puede deshacerse.

Compatibles: OpenWrt (ubus), MikroTik RouterOS 7 (REST), AVM FRITZ!Box (TR-064).
Para routers cerrados del ISP se ofrece estado vía UPnP y el acceso a su panel.
"""
from __future__ import annotations

import ipaddress
import json
import logging
import re
import time
import xml.etree.ElementTree as ET

import httpx

from .config import DATA_DIR

log = logging.getLogger("bipon.routerctl")
BACKUP_DIR = DATA_DIR / "router_backups"
DRIVERS = {"openwrt": "OpenWrt (LuCI/ubus)", "mikrotik": "MikroTik RouterOS 7", "fritzbox": "AVM FRITZ!Box (TR-064)"}


def _ips(values: list[str]) -> list[str]:
    out = []
    for v in values:
        out.append(str(ipaddress.ip_address(v.strip())))
    if not out or len(out) > 3:
        raise ValueError("Indica entre 1 y 3 servidores DNS")
    return out


# ------------------------------------------------------------------ OpenWrt
class OpenWrt:
    def __init__(self, cfg):
        self.cfg = cfg
        self.url = cfg["url"].rstrip("/") + "/ubus"
        self.sid = None

    async def _call(self, h, obj, method, args=None):
        if self.sid is None and obj != "session":
            res = await self._call(h, "session", "login", {"username": self.cfg.get("username") or "root", "password": self.cfg.get("password", "")})
            self.sid = res["ubus_rpc_session"]
        r = await h.post(self.url, json={"jsonrpc": "2.0", "id": 1, "method": "call",
                                         "params": [self.sid or "0" * 32, obj, method, args or {}]})
        res = r.json().get("result", [1])
        if res[0] != 0:
            raise RuntimeError(f"OpenWrt rechazó {obj}.{method} (código {res[0]})")
        return res[1] if len(res) > 1 else {}

    async def status(self, h):
        board = await self._call(h, "system", "board")
        info = await self._call(h, "system", "info")
        wan = {}
        try:
            wan = await self._call(h, "network.interface.wan", "status")
        except Exception:
            pass
        dhcp = await self._call(h, "uci", "get", {"config": "dhcp", "section": "lan"})
        opts = dhcp.get("values", {}).get("dhcp_option", []) or []
        dns = next((o.split(",", 1)[1].split(",") for o in opts if o.startswith(("6,", "option:dns-server,"))), [])
        wl = (await self._call(h, "uci", "get", {"config": "wireless"})).get("values", {})
        radios = [{"id": k, "band": v.get("band") or v.get("hwmode"), "channel": v.get("channel"), "htmode": v.get("htmode")}
                  for k, v in wl.items() if v.get(".type") == "wifi-device"]
        addr = (wan.get("ipv4-address") or [{}])[0].get("address")
        return {"model": board.get("model"), "firmware": (board.get("release") or {}).get("description"), "uptime": info.get("uptime"),
                "wan_ip": addr, "wan_up": wan.get("up"), "dhcp_dns": dns, "radios": radios}

    async def set_dhcp_dns(self, h, servers):
        cur = (await self._call(h, "uci", "get", {"config": "dhcp", "section": "lan"})).get("values", {}).get("dhcp_option", []) or []
        keep = [o for o in cur if not o.startswith(("6,", "option:dns-server,"))]
        new = keep + (["6," + ",".join(servers)] if servers else [])
        await self._call(h, "uci", "set", {"config": "dhcp", "section": "lan", "values": {"dhcp_option": new}})
        await self._call(h, "uci", "commit", {"config": "dhcp"})
        await self._call(h, "file", "exec", {"command": "/etc/init.d/dnsmasq", "params": ["reload"]})
        return cur

    async def set_channel(self, h, radio, channel):
        prev = (await self._call(h, "uci", "get", {"config": "wireless", "section": radio})).get("values", {}).get("channel")
        await self._call(h, "uci", "set", {"config": "wireless", "section": radio, "values": {"channel": str(channel)}})
        await self._call(h, "uci", "commit", {"config": "wireless"})
        await self._call(h, "file", "exec", {"command": "/sbin/wifi", "params": ["reload"]})
        return prev

    async def reboot(self, h):
        await self._call(h, "system", "reboot")

    async def backup(self, h) -> bytes:
        r = await self._call(h, "file", "exec", {"command": "/sbin/sysupgrade", "params": ["-b", "/tmp/bipon-backup.tar.gz"]})
        data = await self._call(h, "file", "read", {"path": "/tmp/bipon-backup.tar.gz", "base64": True})
        import base64
        return base64.b64decode(data.get("data", ""))


# ------------------------------------------------------------------ MikroTik
class MikroTik:
    def __init__(self, cfg):
        self.cfg = cfg
        self.base = cfg["url"].rstrip("/") + "/rest"

    async def status(self, h):
        res = (await h.get(f"{self.base}/system/resource")).json()
        ident = (await h.get(f"{self.base}/system/identity")).json()
        nets = (await h.get(f"{self.base}/ip/dhcp-server/network")).json()
        radios = []
        for path in ("interface/wifi", "interface/wireless"):
            r = await h.get(f"{self.base}/{path}")
            if r.status_code == 200:
                radios += [{"id": i.get(".id"), "name": i.get("name"), "band": i.get("band") or i.get("configuration.band"),
                            "channel": i.get("frequency") or i.get("channel.frequency"), "path": path} for i in r.json()]
        return {"model": res.get("board-name"), "firmware": res.get("version"), "uptime": res.get("uptime"), "name": ident.get("name"),
                "dhcp_dns": (nets[0].get("dns-server") or "").split(",") if nets else [], "dhcp_networks": nets, "radios": radios}

    async def set_dhcp_dns(self, h, servers):
        nets = (await h.get(f"{self.base}/ip/dhcp-server/network")).json()
        prev = {n[".id"]: n.get("dns-server", "") for n in nets}
        for n in nets:
            r = await h.patch(f"{self.base}/ip/dhcp-server/network/{n['.id']}", json={"dns-server": ",".join(servers)})
            if r.status_code >= 300:
                raise RuntimeError(f"MikroTik: {r.text[:150]}")
        return prev

    async def set_channel(self, h, radio, channel):
        path = "interface/wifi" if radio.startswith("wifi") else "interface/wireless"
        field = "channel.frequency" if path == "interface/wifi" else "frequency"
        cur = (await h.get(f"{self.base}/{path}/{radio}")).json()
        r = await h.patch(f"{self.base}/{path}/{radio}", json={field: str(channel)})
        if r.status_code >= 300:
            raise RuntimeError(f"MikroTik: {r.text[:150]}")
        return cur.get(field)

    async def reboot(self, h):
        await h.post(f"{self.base}/system/reboot", json={})

    async def backup(self, h) -> bytes:
        r = await h.post(f"{self.base}/export", json={})
        if r.status_code >= 300:
            raise RuntimeError("La exportación de configuración requiere RouterOS ≥ 7.9")
        return r.content


# ------------------------------------------------------------------ FRITZ!Box (TR-064)
class FritzBox:
    SVC = {"lan": ("urn:dslforum-org:service:LANHostConfigManagement:1", "/upnp/control/lanhostconfigmgm"),
           "dev": ("urn:dslforum-org:service:DeviceConfig:1", "/upnp/control/deviceconfig"),
           "info": ("urn:dslforum-org:service:DeviceInfo:1", "/upnp/control/deviceinfo"),
           "wan": ("urn:dslforum-org:service:WANIPConnection:1", "/upnp/control/wanipconnection1"),
           "wlan1": ("urn:dslforum-org:service:WLANConfiguration:1", "/upnp/control/wlanconfig1"),
           "wlan2": ("urn:dslforum-org:service:WLANConfiguration:2", "/upnp/control/wlanconfig2")}

    def __init__(self, cfg):
        self.cfg = cfg
        m = re.match(r"https?://([^/:]+)", cfg["url"])
        self.base = f"http://{m.group(1) if m else cfg['url']}:49000"

    async def soap(self, h, svc, action, args=None):
        st, path = self.SVC[svc]
        body = "".join(f"<{k}>{v}</{k}>" for k, v in (args or {}).items())
        env = (f'<?xml version="1.0"?><s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" s:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/">'
               f'<s:Body><u:{action} xmlns:u="{st}">{body}</u:{action}></s:Body></s:Envelope>')
        r = await h.post(self.base + path, content=env, headers={"Content-Type": 'text/xml; charset="utf-8"', "SOAPAction": f'"{st}#{action}"'},
                         auth=httpx.DigestAuth(self.cfg.get("username") or "", self.cfg.get("password", "")))
        if r.status_code != 200:
            raise RuntimeError(f"FRITZ!Box rechazó {action} (HTTP {r.status_code})")
        root = ET.fromstring(r.content)
        resp = next((el for el in root.iter() if el.tag.endswith(f"{action}Response")), None)
        return {child.tag: child.text for child in (resp if resp is not None else [])}

    async def status(self, h):
        info = await self.soap(h, "info", "GetInfo")
        dns = (await self.soap(h, "lan", "GetDNSServers")).get("NewDNSServers", "")
        wan = {}
        try:
            wan = await self.soap(h, "wan", "GetExternalIPAddress")
        except Exception:
            pass
        radios = []
        for k in ("wlan1", "wlan2"):
            try:
                w = await self.soap(h, k, "GetInfo")
                radios.append({"id": k, "band": "2,4 GHz" if k == "wlan1" else "5 GHz", "channel": w.get("NewChannel"), "ssid": w.get("NewSSID")})
            except Exception:
                pass
        return {"model": info.get("NewModelName"), "firmware": info.get("NewSoftwareVersion"), "uptime": int(info.get("NewUpTime") or 0),
                "wan_ip": wan.get("NewExternalIPAddress"), "dhcp_dns": [d.strip() for d in dns.split(",") if d.strip()], "radios": radios}

    async def set_dhcp_dns(self, h, servers):
        prev = (await self.soap(h, "lan", "GetDNSServers")).get("NewDNSServers", "")
        await self.soap(h, "lan", "SetDNSServers", {"NewDNSServers": ",".join(servers)})
        return prev.split(",") if prev else []

    async def set_channel(self, h, radio, channel):
        prev = (await self.soap(h, radio, "GetInfo")).get("NewChannel")
        await self.soap(h, radio, "SetChannel", {"NewChannel": int(channel)})
        return prev

    async def reboot(self, h):
        await self.soap(h, "dev", "Reboot")

    async def backup(self, h) -> bytes:
        res = await self.soap(h, "dev", "X_AVM-DE_GetConfigFile", {"NewX_AVM-DE_Password": self.cfg.get("backup_password") or "bipon-backup"})
        url = res.get("NewX_AVM-DE_ConfigFileUrl")
        r = await h.get(url, auth=httpx.DigestAuth(self.cfg.get("username") or "", self.cfg.get("password", "")))
        return r.content


CLASSES = {"openwrt": OpenWrt, "mikrotik": MikroTik, "fritzbox": FritzBox}


class RouterControl:
    def __init__(self, ctx):
        self.ctx = ctx

    def cfg(self) -> dict:
        return (self.ctx.settings.get("integrations") or {}).get("router_control") or {}

    def save_cfg(self, body: dict) -> dict:
        integ = self.ctx.settings.get("integrations") or {}
        old = integ.get("router_control") or {}
        if body.get("type") not in CLASSES:
            raise ValueError("Tipo de router no compatible")
        if not str(body.get("url", "")).startswith(("http://", "https://")):
            raise ValueError("URL inválida")
        new = {k: body.get(k, old.get(k)) for k in ("type", "url", "username", "password", "verify_tls", "backup_password")}
        if new.get("password") in (None, "", "••••••"):
            new["password"] = old.get("password")
        integ["router_control"] = new
        self.ctx.settings.set("integrations", integ, notify=False)
        return new

    def _drv(self):
        c = self.cfg()
        if not c.get("type"):
            raise ValueError("Configura primero el acceso al router")
        return CLASSES[c["type"]](c)

    def _client(self):
        c = self.cfg()
        auth = (c.get("username") or "admin", c.get("password", "")) if c.get("type") == "mikrotik" else None
        return httpx.AsyncClient(verify=bool(c.get("verify_tls")), timeout=20, auth=auth)

    def _log_change(self, op: str, prev, new) -> None:
        hist = self.ctx.settings.get("router_changes") or []
        hist.insert(0, {"ts": time.time(), "op": op, "prev": prev, "new": new})
        self.ctx.settings.set("router_changes", hist[:30], notify=False)

    async def status(self) -> dict:
        async with self._client() as h:
            return await self._drv().status(h)

    async def set_dns(self, servers: list[str]) -> dict:
        servers = _ips(servers)
        async with self._client() as h:
            prev = await self._drv().set_dhcp_dns(h, servers)
        self._log_change("dhcp_dns", prev, servers)
        return {"ok": True, "previous": prev}

    async def set_channel(self, radio: str, channel: str) -> dict:
        if not re.match(r"^[\w*.-]{1,40}$", radio) or not re.match(r"^(auto|\d{1,4})$", str(channel)):
            raise ValueError("Radio o canal inválidos")
        async with self._client() as h:
            prev = await self._drv().set_channel(h, radio, channel)
        self._log_change("wifi_channel", {"radio": radio, "channel": prev}, {"radio": radio, "channel": channel})
        return {"ok": True, "previous": prev}

    async def undo(self, index: int = 0) -> dict:
        hist = self.ctx.settings.get("router_changes") or []
        if index >= len(hist):
            raise ValueError("No hay cambios que deshacer")
        ch = hist[index]
        async with self._client() as h:
            drv = self._drv()
            if ch["op"] == "dhcp_dns":
                prev = ch["prev"]
                if isinstance(prev, dict):  # MikroTik: por red
                    for nid, dns in prev.items():
                        await h.patch(f"{drv.base}/ip/dhcp-server/network/{nid}", json={"dns-server": dns})
                elif self.cfg().get("type") == "openwrt":
                    await drv._call(h, "uci", "set", {"config": "dhcp", "section": "lan", "values": {"dhcp_option": prev}})
                    await drv._call(h, "uci", "commit", {"config": "dhcp"})
                else:
                    await drv.set_dhcp_dns(h, prev)
            elif ch["op"] == "wifi_channel" and ch["prev"].get("channel"):
                await drv.set_channel(h, ch["prev"]["radio"], ch["prev"]["channel"])
        hist.pop(index)
        self.ctx.settings.set("router_changes", hist, notify=False)
        return {"ok": True}

    async def reboot(self) -> dict:
        async with self._client() as h:
            await self._drv().reboot(h)
        self.ctx.db.event("warning", "router", "Reinicio del router solicitado desde Bipon Net")
        return {"ok": True}

    async def backup(self) -> dict:
        async with self._client() as h:
            data = await self._drv().backup(h)
        BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        name = f"router-{self.cfg().get('type')}-{time.strftime('%Y%m%d-%H%M%S')}.bak"
        (BACKUP_DIR / name).write_bytes(data)
        return {"ok": True, "file": name, "size": len(data)}

    def backups(self) -> list[dict]:
        if not BACKUP_DIR.exists():
            return []
        return sorted(({"file": p.name, "size": p.stat().st_size, "ts": p.stat().st_mtime} for p in BACKUP_DIR.iterdir()), key=lambda x: -x["ts"])
