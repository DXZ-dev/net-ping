"""Asistente de puerta de enlace: Bipon controla TODO el tráfico de la casa con un solo cable.

    Internet ── Huawei (módem + Wi-Fi + switch, sin tocar nada del proveedor)
                   │
         equipos ──┴── Bipon (puerta de enlace que reparte el DHCP)

1. El DHCP de Bipon (modo principal) entrega a cada equipo «puerta de enlace = Bipon».
2. Bipon reenvía el tráfico al Huawei y lo enmascara con su IP, así la respuesta también vuelve por
   Bipon: se puede limitar la bajada y la subida, pausar Internet y aplicar el firewall a cada equipo.
3. Se desactivan las redirecciones ICMP para que los equipos no «se salten» a Bipon.
Seguridad: el firewall se aplica con reversión automática a los 60 s si no confirmas; si Bipon se apaga,
los equipos recuperan Internet con el DHCP del router cuando caduca su concesión (30 min).
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import time

from . import netinfo

LEASE = 1800


def _ip_dynamic(iface: str) -> bool | None:
    try:
        out = subprocess.run(["ip", "-j", "-4", "addr", "show", "dev", iface], capture_output=True, text=True, timeout=3).stdout
        for itf in json.loads(out or "[]"):
            for a in itf.get("addr_info", []):
                if a.get("family") == "inet":
                    return bool(a.get("dynamic"))
    except Exception:
        return None
    return None


def _virt() -> str:
    try:
        env = open("/proc/1/environ", "rb").read().decode(errors="ignore")
        if "container=lxc" in env:
            return "lxc"
        if "container=docker" in env or os.path.exists("/.dockerenv"):
            return "docker"
    except OSError:
        pass
    return ""


class Gateway:
    def __init__(self, ctx):
        self.ctx = ctx

    def _iface(self) -> dict | None:
        ifs = netinfo.interfaces()
        ip = self.ctx.devices.self_ip
        return next((i for i in ifs if i.get("ip") == ip), ifs[0] if ifs else None)

    # ---------------------------------------------------------------- comprobaciones
    async def precheck(self) -> dict:
        itf = self._iface()
        gw = netinfo.default_gateway()
        checks = []

        def add(key, ok, title, detail, level=None):
            checks.append({"key": key, "status": level or ("ok" if ok else "fail"), "title": title, "detail": detail})

        add("iface", bool(itf), "Interfaz de red", f"{itf['name']} · {itf['ip']}/{itf['prefix']}" if itf else "No se encontró una interfaz con IPv4")
        add("gateway", bool(gw), "Router del proveedor", f"{gw}" if gw else "No hay puerta de enlace: Bipon no tiene salida a Internet")
        dyn = _ip_dynamic(itf["name"]) if itf else None
        add("static", dyn is False, "IP fija de Bipon",
            "Fija" if dyn is False else "Bipon recibe su IP por DHCP: reserva su IP en el router o ponle IP fija antes de continuar (los equipos apuntarán a ella).",
            None if dyn is False else "warn")
        add("nft", bool(shutil.which("nft")), "nftables (firewall)", "Disponible" if shutil.which("nft") else "Instala nftables (el instalador lo hace)")
        add("conntrack", bool(shutil.which("conntrack")), "conntrack", "Disponible: los cortes de Internet son inmediatos" if shutil.which("conntrack")
            else "Sin conntrack: las conexiones abiertas tardan en cortarse", None if shutil.which("conntrack") else "warn")
        add("tc", bool(shutil.which("tc")), "Control de tráfico (tc)", "Disponible: límites de velocidad" if shutil.which("tc") else "Sin tc: no habrá límites de velocidad",
            None if shutil.which("tc") else "warn")
        fwd_ok = os.access("/proc/sys/net/ipv4/ip_forward", os.W_OK)
        add("forward", fwd_ok, "Reenvío de paquetes", "Se puede activar" if fwd_ok else
            "Este sistema no permite activar ip_forward (en Docker usa network_mode: host y cap NET_ADMIN; en LXC actívalo en el host)")
        virt = _virt()
        if virt == "lxc":
            add("lxc", True, "Contenedor LXC", "Funciona; el script de Proxmox ya carga en el host los módulos necesarios (nftables, conntrack, HTB, CAKE).", "warn")
        elif virt == "docker":
            add("docker", True, "Docker", "Necesita network_mode: host y NET_ADMIN (ya vienen en docker-compose.yml).", "warn")
        others = [o for o in await self.ctx.dhcp.probe() if not o.get("own") and not o.get("error")]
        add("dhcp", True, "DHCP del router", f"El router reparte IP ({others[0]['server']}): quedará como respaldo y Bipon será el principal."
            if others else "No hay otro DHCP: Bipon será el único.", "warn" if others else "ok")
        ready = all(c["status"] != "fail" for c in checks)
        return {"ready": ready, "checks": checks, "iface": itf, "router_ip": gw, "self_ip": itf["ip"] if itf else None, "virt": virt}

    # ---------------------------------------------------------------- paso 1: firewall/NAT con un solo cable
    async def enable_routing(self) -> dict:
        itf = self._iface()
        if not itf:
            raise ValueError("No hay interfaz de red")
        r = self.ctx.router
        cfg = {**r.cfg(), "enabled": True, "wan": itf["name"], "lan": itf["name"], "nat": True, "force_dns": True,
               "block_devices": True, "accounting": True}
        res = await r.apply(cfg, confirm_required=True)
        self._persist_sysctl(itf["name"])
        return res

    @staticmethod
    def _persist_sysctl(iface: str) -> None:
        try:
            with open("/etc/sysctl.d/99-bipon-gateway.conf", "w") as fh:
                fh.write("# Bipon Net como puerta de enlace\nnet.ipv4.ip_forward=1\nnet.ipv4.conf.all.send_redirects=0\n"
                         f"net.ipv4.conf.{iface}.send_redirects=0\n")
        except OSError:
            pass

    # ---------------------------------------------------------------- paso 2: DHCP con Bipon como puerta de enlace
    async def enable_dhcp(self) -> dict:
        if not (self.ctx.router.cfg().get("enabled") and not self.ctx.router.pending):
            raise ValueError("Primero activa y confirma el paso 1 (firewall)")
        itf = self._iface()
        d = self.ctx.dhcp
        cfg = {**d.suggest(), **(self.ctx.settings.get("dhcp") or {})}
        cfg.update({"enabled": True, "mode": "primary", "authoritative": True, "router": itf["ip"], "server_ip": itf["ip"],
                    "dns": [itf["ip"]], "lease_time": LEASE, "gateway_mode": True, "isp_router": netinfo.default_gateway()})
        cfg.setdefault("static", [])
        d.validate(cfg)
        self.ctx.settings.set("dhcp", cfg)
        self.ctx.apply_dns_settings()
        await d.restart()
        self.ctx.db.event("info", "gateway", f"Bipon es ahora la puerta de enlace de la red ({itf['ip']})")
        return self.status()

    # ---------------------------------------------------------------- estado / desactivar
    def status(self) -> dict:
        r, dc = self.ctx.router.cfg(), self.ctx.settings.get("dhcp") or {}
        itf = self._iface()
        routing = bool(r.get("enabled") and r.get("wan") and r.get("wan") == r.get("lan"))
        dhcp = bool(dc.get("enabled") and itf and dc.get("router") == itf.get("ip"))
        rates = getattr(self.ctx.router, "rates", {}) or {}
        through = [m for m, v in rates.items() if (v.get("rx_bps") or 0) + (v.get("tx_bps") or 0) > 0]
        online = [m for m, dv in self.ctx.devices.devices.items() if dv.get("online") and dv.get("ip") not in (self.ctx.devices.self_ip, self.ctx.devices.gateway)]
        return {"routing": routing, "dhcp": dhcp, "active": routing and dhcp, "pending": bool(self.ctx.router.pending),
                "deadline": (self.ctx.router.pending or {}).get("deadline"), "through": len(through), "online": len(online),
                "takeover": self.ctx.dhcp.takeover() if dhcp else {}, "ts": time.time()}

    async def disable(self) -> dict:
        dc = self.ctx.settings.get("dhcp") or {}
        if dc.get("gateway_mode"):
            dc.update({"router": dc.get("isp_router") or netinfo.default_gateway(), "gateway_mode": False})
            self.ctx.settings.set("dhcp", dc)
            await self.ctx.dhcp.restart()
        r = self.ctx.router
        await r.apply({**r.cfg(), "enabled": False}, confirm_required=False)
        self.ctx.db.event("warning", "gateway", "Bipon dejó de ser la puerta de enlace (los equipos vuelven al router al renovar su IP)")
        return self.status()
