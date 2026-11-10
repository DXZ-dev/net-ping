"""Velocidad en tiempo real sin necesidad de modo router.

Bipon sólo «ve» el tráfico que pasa por él. Cuando no es la puerta de enlace,
la velocidad se obtiene de las fuentes que sí la conocen:

  * modo router (contadores nftables por MAC)          → todos los dispositivos
  * Proxmox (API: netin/netout de cada VM y LXC)        → máquinas virtuales
  * el propio equipo (/proc/net/dev)                    → Bipon
  * el router por UPnP-IGD (WANCommonInterfaceConfig)   → total de la línea
"""
from __future__ import annotations

import asyncio
import logging
import time
from urllib.parse import urljoin, urlparse
import xml.etree.ElementTree as ET

import httpx

from . import nat, netinfo

log = logging.getLogger("bipon.rates")

WAN_COMMON = "urn:schemas-upnp-org:service:WANCommonInterfaceConfig:1"
U32 = 2 ** 32


def _delta(now: int, prev: int, wrap: int | None = None) -> int:
    d = now - prev
    if d < 0 and wrap:
        d += wrap  # los contadores de 32 bits de los routers dan la vuelta cada 4 GB
    return max(0, d)


def iface_bytes() -> dict[str, tuple[int, int]]:
    out = {}
    try:
        with open("/proc/net/dev") as fh:
            for line in fh.readlines()[2:]:
                name, data = line.split(":", 1)
                f = data.split()
                out[name.strip()] = (int(f[0]), int(f[8]))
    except OSError:
        pass
    return out


class LiveRates:
    def __init__(self, ctx):
        self.ctx = ctx
        self.rates: dict[str, dict] = {}   # mac → {rx_bps, tx_bps, source, ts}
        self.line: dict = {}               # total de la línea (router por UPnP)
        self._prev: dict[str, tuple[float, int, int]] = {}
        self._wan_ctrl: str | None = None
        self._wan_search = 0.0
        self._pve_inv_ts = 0.0
        self._vm_macs: dict[str, list[str]] = {}

    def _set(self, key: str, macs: list[str], rx: int, tx: int, source: str, wrap: int | None = None) -> dict | None:
        now = time.monotonic()
        prev = self._prev.get(key)
        self._prev[key] = (now, rx, tx)
        if not prev or now - prev[0] < 0.5:
            return None
        dt = now - prev[0]
        r = {"rx_bps": _delta(rx, prev[1], wrap) * 8 / dt, "tx_bps": _delta(tx, prev[2], wrap) * 8 / dt, "source": source, "ts": time.time()}
        for m in macs:
            self.rates[m] = r
        return r

    # ---------------------------------------------------------------- fuentes
    def _self(self) -> None:
        devs = self.ctx.devices
        ip = devs.self_ip or netinfo.primary_ip()
        itf = next((i for i in netinfo.interfaces() if i.get("ip") == ip), None)
        if not itf:
            return
        # la clave es la que usa la lista de dispositivos para este equipo
        mac = next((m for m, d in devs.devices.items() if d.get("ip") == ip), None) or (itf.get("mac") or "").lower()
        if not mac:
            try:
                mac = open(f"/sys/class/net/{itf['name']}/address").read().strip().lower()
            except OSError:
                mac = None
        counters = iface_bytes()
        if mac and itf["name"] in counters:
            rx, tx = counters[itf["name"]]
            self._set("self", [mac], rx, tx, "local")

    async def _proxmox(self) -> None:
        integ = self.ctx.settings.get("integrations") or {}
        pc = integ.get("proxmox")
        if not pc or not pc.get("url"):
            return
        inv = getattr(getattr(self.ctx, "wifi", None), "proxmox", None) or {}
        vm_macs: dict[str, list[str]] = {}
        for mac, v in inv.items():
            vm_macs.setdefault(f"{v['node']}/{v['vmid']}", []).append(mac)
        base = pc["url"].rstrip("/") + "/api2/json"
        headers = {"Authorization": f"PVEAPIToken={pc['token_id']}={pc['secret']}"}
        async with httpx.AsyncClient(verify=bool(pc.get("verify_tls")), timeout=6, headers=headers) as h:
            r = await h.get(f"{base}/cluster/resources", params={"type": "vm"})
            if r.status_code != 200:
                return
            for vm in r.json().get("data", []):
                key = f"{vm.get('node')}/{vm.get('vmid')}"
                if vm.get("status") != "running":
                    for m in vm_macs.get(key, []):
                        self.rates[m] = {"rx_bps": 0, "tx_bps": 0, "source": "proxmox", "ts": time.time()}
                    continue
                # netin = lo que recibe la VM (su bajada); netout = lo que envía (su subida)
                self._set(f"pve:{key}", vm_macs.get(key, []), int(vm.get("netin") or 0), int(vm.get("netout") or 0), "proxmox")

    async def _find_wan_common(self) -> str | None:
        for loc in await nat._ssdp_discover():
            try:
                async with httpx.AsyncClient(timeout=4) as c:
                    r = await c.get(loc)
                root = nat._strip_ns(ET.fromstring(r.content))
                base = root.findtext("URLBase") or loc
                for svc in root.iter("service"):
                    if (svc.findtext("serviceType") or "") == WAN_COMMON:
                        ctrl = urljoin(base, svc.findtext("controlURL") or "")
                        if urlparse(ctrl).hostname and netinfo.is_private(urlparse(ctrl).hostname):
                            return ctrl
            except Exception as exc:
                log.debug("IGD %s: %s", loc, exc)
        return None

    async def _line(self) -> None:
        if not self._wan_ctrl:
            if time.time() - self._wan_search < 600:
                return
            self._wan_search = time.time()
            self._wan_ctrl = await self._find_wan_common()
            if not self._wan_ctrl:
                return
        igd = nat.IGD(self._wan_ctrl, WAN_COMMON, "router")
        try:
            rx = int((await igd.soap("GetTotalBytesReceived")).get("NewTotalBytesReceived") or 0)
            tx = int((await igd.soap("GetTotalBytesSent")).get("NewTotalBytesSent") or 0)
        except Exception as exc:
            log.debug("UPnP contadores: %s", exc)
            self._wan_ctrl = None
            return
        r = self._set("line", [], rx, tx, "upnp", wrap=U32)
        if r:
            self.line = r

    # ---------------------------------------------------------------- bucle
    async def loop(self) -> None:
        await asyncio.sleep(10)
        tick = 0
        while True:
            try:
                self._self()
                if tick % 2 == 0:
                    await asyncio.gather(self._proxmox(), self._line(), return_exceptions=True)
            except Exception as exc:  # pragma: no cover
                log.debug("rates: %s", exc)
            tick += 1
            await asyncio.sleep(2.5)

    def snapshot(self) -> dict:
        """Velocidades actuales: modo router (prioritario) + Proxmox + el propio equipo."""
        now = time.time()
        out = {m: r for m, r in self.rates.items() if now - r["ts"] < 30}
        router_rates = getattr(self.ctx.router, "rates", {}) or {}
        if router_rates and (self.ctx.router.cfg().get("accounting")):
            for m, r in router_rates.items():
                out[m] = {**r, "source": "router"}
        return out

    def info(self) -> dict:
        router_on = bool(self.ctx.router.cfg().get("enabled") and self.ctx.router.cfg().get("accounting"))
        line = self.line if self.line and time.time() - self.line["ts"] < 30 else None
        pve = bool(((self.ctx.settings.get("integrations") or {}).get("proxmox") or {}).get("url"))
        return {"rates": self.snapshot(), "line": line, "router_mode": router_on, "proxmox": pve, "upnp": bool(self._wan_ctrl)}
