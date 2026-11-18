"""Interfaces de red: ver y configurar cada tarjeta de Bipon (LAN, WAN, módem USB, VLAN).

* Lista todas las interfaces con su tipo, velocidad del enlace, estado, MTU, IP, puerta de enlace, tráfico en vivo
  y errores; indica su función (LAN, WAN, línea del multi-WAN, la del panel).
* Configuración por interfaz: dejar como está el sistema, DHCP, IP fija (+ puerta de enlace), MTU, desactivar.
  Se guarda en Bipon y se vuelve a aplicar al arrancar.
* VLAN: crear y borrar subinterfaces 802.1Q.
* Seguridad: tocar la interfaz por la que entras al panel exige confirmar en 60 s; si no, se restaura sola.
* Proxmox: añadir una tarjeta de red a este mismo contenedor/VM con la API (p. ej. para una segunda línea).
"""
from __future__ import annotations

import asyncio
import ipaddress
import logging
import re
import shutil
import time
from pathlib import Path

from . import netinfo

log = logging.getLogger("bipon.ifaces")
IFACE_RE = re.compile(r"^[a-zA-Z0-9_.@-]{1,15}$")
SKIP = ("lo", "veth", "docker", "br-", "ifb", "bpcake", "dummy")
ROLLBACK_SECONDS = 60


async def _run(*args, timeout: float = 20) -> tuple[int, str]:
    p = await asyncio.create_subprocess_exec(*args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
    try:
        out, _ = await asyncio.wait_for(p.communicate(), timeout)
    except asyncio.TimeoutError:
        p.kill()
        return 124, "tiempo agotado"
    return p.returncode or 0, out.decode(errors="ignore")


def _sys(name: str, attr: str) -> str:
    try:
        return (Path("/sys/class/net") / name / attr).read_text().strip()
    except OSError:
        return ""


def kind_of(name: str, link: dict) -> str:
    info = (link.get("linkinfo") or {}).get("info_kind", "")
    if info == "vlan" or Path(f"/proc/net/vlan/{name}").exists():
        return "vlan"
    if info in ("bridge", "bond", "wireguard", "tun", "veth", "macvlan", "ipvlan"):
        return info
    if (Path("/sys/class/net") / name / "wireless").exists():
        return "wifi"
    try:
        dev = str((Path("/sys/class/net") / name / "device").resolve())
        if "/usb" in dev:
            return "usb"
    except OSError:
        pass
    if name.startswith(("wwan", "usb", "enx")):
        return "usb"
    return "ethernet"


def dhcp_client() -> list[str] | None:
    for cmd in (["dhcpcd", "-4", "-b"], ["dhclient", "-4", "-nw"], ["udhcpc", "-b", "-q", "-i"]):
        if shutil.which(cmd[0]):
            return cmd
    return None


class Interfaces:
    def __init__(self, ctx):
        self.ctx = ctx
        self._prev: dict[str, tuple[float, int, int]] = {}
        self.pending: dict | None = None
        self._rollback_task: asyncio.Task | None = None

    # ------------------------------------------------------------------ configuración
    def cfg(self) -> dict:
        c = self.ctx.settings.get("interfaces") or {}
        c.setdefault("ifaces", {})   # nombre -> {mode, address, gateway, mtu, description}
        c.setdefault("vlans", [])    # [{parent, vid}]
        return c

    def roles(self) -> dict[str, list[str]]:
        rc = self.ctx.router.cfg()
        out: dict[str, list[str]] = {}
        if rc.get("lan"):
            out.setdefault(rc["lan"], []).append("lan")
        if rc.get("wan"):
            out.setdefault(rc["wan"], []).append("wan")
        mw = getattr(self.ctx, "multiwan", None)
        if mw is not None:
            for w in mw.cfg().get("wans", []):
                out.setdefault(w["iface"], []).append(f"multiwan:{w['name']}")
        for z in rc.get("zones", []):
            name = self.ctx.router._zone_iface(z, rc)
            out.setdefault(name, []).append(f"zona:{z.get('name')}")
        mgmt = self.mgmt_iface()
        if mgmt:
            out.setdefault(mgmt, []).append("panel")
        return out

    def mgmt_iface(self) -> str | None:
        ip = self.ctx.devices.self_ip or netinfo.primary_ip()
        for i in netinfo.interfaces():
            if i.get("ip") == ip:
                return i["name"]
        return None

    # ------------------------------------------------------------------ listado
    def list(self) -> list[dict]:
        links = {x["ifname"]: x for x in (netinfo.run_json(["ip", "-j", "-d", "link", "show"]) or [])}
        addrs = {x["ifname"]: x for x in (netinfo.run_json(["ip", "-j", "addr", "show"]) or [])}
        routes = netinfo.run_json(["ip", "-j", "-4", "route", "show", "default"]) or []
        roles = self.roles()
        conf = self.cfg()["ifaces"]
        now = time.time()
        out = []
        for name, link in links.items():
            if name.startswith(SKIP) or name == "lo":
                continue
            a = addrs.get(name, {})
            v4 = [f"{x['local']}/{x['prefixlen']}" for x in a.get("addr_info", []) if x.get("family") == "inet"]
            v6 = [f"{x['local']}/{x['prefixlen']}" for x in a.get("addr_info", []) if x.get("family") == "inet6" and x.get("scope") == "global"]
            dyn = any(x.get("dynamic") for x in a.get("addr_info", []) if x.get("family") == "inet")
            rx, tx = int(_sys(name, "statistics/rx_bytes") or 0), int(_sys(name, "statistics/tx_bytes") or 0)
            prev = self._prev.get(name)
            rate = None
            if prev and now - prev[0] > 0.5:
                dt = now - prev[0]
                rate = {"rx_bps": max(0, rx - prev[1]) * 8 / dt, "tx_bps": max(0, tx - prev[2]) * 8 / dt}
            self._prev[name] = (now, rx, tx)
            speed = _sys(name, "speed")
            out.append({
                "name": name, "kind": kind_of(name, link), "mac": link.get("address", ""), "mtu": link.get("mtu"),
                "state": (link.get("operstate") or "").lower(), "carrier": _sys(name, "carrier") == "1",
                "speed": int(speed) if speed.lstrip("-").isdigit() and int(speed) > 0 else None, "duplex": _sys(name, "duplex") or None,
                "driver": Path(f"/sys/class/net/{name}/device/driver").resolve().name if Path(f"/sys/class/net/{name}/device/driver").exists() else "",
                "ipv4": v4, "ipv6": v6, "dhcp": dyn, "gateway": next((r.get("gateway") for r in routes if r.get("dev") == name), None),
                "parent": link.get("link"), "vlan_id": ((link.get("linkinfo") or {}).get("info_data") or {}).get("id"),
                "rx_bytes": rx, "tx_bytes": tx, "rate": rate,
                "rx_errors": int(_sys(name, "statistics/rx_errors") or 0), "tx_errors": int(_sys(name, "statistics/tx_errors") or 0),
                "rx_dropped": int(_sys(name, "statistics/rx_dropped") or 0),
                "roles": roles.get(name, []), "config": conf.get(name, {"mode": "keep"}),
            })
        out.sort(key=lambda x: (x["kind"] == "vlan", "panel" not in x["roles"], x["name"]))
        return out

    # ------------------------------------------------------------------ aplicar
    def validate(self, name: str, c: dict) -> dict:
        if not IFACE_RE.match(name):
            raise ValueError("Interfaz inválida")
        mode = c.get("mode", "keep")
        if mode not in ("keep", "dhcp", "static", "disabled"):
            raise ValueError("Modo inválido")
        out = {"mode": mode, "description": str(c.get("description") or "")[:60]}
        if mode == "static":
            itf = ipaddress.ip_interface(str(c.get("address") or ""))
            if itf.version != 4 or itf.network.prefixlen in (0, 32):
                raise ValueError("Indica la IP con su máscara, p. ej. 192.168.8.10/24")
            out["address"] = str(itf)
            gw = str(c.get("gateway") or "").strip()
            if gw:
                if ipaddress.ip_address(gw) not in itf.network:
                    raise ValueError("La puerta de enlace debe estar en la misma red")
                out["gateway"] = gw
        mtu = c.get("mtu")
        if mtu not in (None, "", 0):
            mtu = int(mtu)
            if not 576 <= mtu <= 9216:
                raise ValueError("MTU entre 576 y 9216")
            out["mtu"] = mtu
        return out

    async def _snapshot(self, name: str) -> dict:
        a = (netinfo.run_json(["ip", "-j", "addr", "show", "dev", name]) or [{}])[0]
        return {"addrs": [f"{x['local']}/{x['prefixlen']}" for x in a.get("addr_info", []) if x.get("family") == "inet"],
                "mtu": a.get("mtu"), "up": "UP" in a.get("flags", [])}

    async def _restore(self, name: str, snap: dict) -> None:
        await _run("ip", "-4", "addr", "flush", "dev", name)
        for ad in snap.get("addrs", []):
            await _run("ip", "addr", "add", ad, "dev", name)
        if snap.get("mtu"):
            await _run("ip", "link", "set", "dev", name, "mtu", str(snap["mtu"]))
        await _run("ip", "link", "set", "dev", name, "up" if snap.get("up", True) else "down")

    async def _apply_one(self, name: str, c: dict) -> list[str]:
        notes = []
        mode = c.get("mode", "keep")
        if c.get("mtu"):
            code, out = await _run("ip", "link", "set", "dev", name, "mtu", str(c["mtu"]))
            if code:
                notes.append(f"MTU: {out.strip()[:100]}")
        if mode == "disabled":
            await _run("ip", "link", "set", "dev", name, "down")
            return notes
        if mode in ("static", "dhcp"):
            await _run("ip", "link", "set", "dev", name, "up")
        if mode == "static":
            await _run("ip", "-4", "addr", "flush", "dev", name)
            code, out = await _run("ip", "addr", "add", c["address"], "dev", name)
            if code:
                notes.append(out.strip()[:120])
        elif mode == "dhcp":
            cli = dhcp_client()
            if not cli:
                notes.append("No hay cliente DHCP instalado (instala dhcpcd-base o isc-dhcp-client)")
            else:
                code, out = await _run(*cli, name, timeout=30)
                if code:
                    notes.append(f"DHCP: {out.strip()[:120]}")
        return notes

    async def configure(self, name: str, c: dict) -> dict:
        c = self.validate(name, c)
        if name not in {i["name"] for i in self.list()}:
            raise ValueError("Esa interfaz no existe")
        mgmt = name == self.mgmt_iface()
        if mgmt and c["mode"] == "disabled":
            raise ValueError("No se puede desactivar la interfaz por la que entras al panel")
        snap = await self._snapshot(name)
        allc = self.cfg()
        prev_cfg = dict(allc["ifaces"].get(name, {"mode": "keep"}))
        allc["ifaces"][name] = c
        self.ctx.settings.set("interfaces", allc, notify=False)
        notes = await self._apply_one(name, c)
        risky = mgmt and (c["mode"] in ("static", "dhcp") or c.get("mtu"))
        if risky:
            self.pending = {"name": name, "snap": snap, "prev_cfg": prev_cfg, "deadline": time.time() + ROLLBACK_SECONDS}
            if self._rollback_task:
                self._rollback_task.cancel()
            self._rollback_task = asyncio.create_task(self._rollback_later())
        mw = getattr(self.ctx, "multiwan", None)
        if mw is not None and mw.active and any(w["iface"] == name for w in mw.wans()):
            asyncio.create_task(mw.apply())
        self.ctx.db.event("info", "interfaces", f"Interfaz {name}: {c['mode']}" + (f" {c.get('address')}" if c.get("address") else ""))
        return {"ok": not notes, "notes": notes, "pending": bool(risky), "deadline": self.pending["deadline"] if risky else None}

    async def _rollback_later(self) -> None:
        await asyncio.sleep(ROLLBACK_SECONDS)
        p = self.pending
        if not p:
            return
        self.pending = None
        await self._restore(p["name"], p["snap"])
        allc = self.cfg()
        allc["ifaces"][p["name"]] = p["prev_cfg"]
        self.ctx.settings.set("interfaces", allc, notify=False)
        self.ctx.db.event("warning", "interfaces", f"Cambios en {p['name']} revertidos (no se confirmaron)")

    def confirm(self) -> bool:
        if not self.pending:
            return False
        self.pending = None
        if self._rollback_task:
            self._rollback_task.cancel()
        return True

    # ------------------------------------------------------------------ VLAN
    async def add_vlan(self, parent: str, vid: int) -> dict:
        if not IFACE_RE.match(parent):
            raise ValueError("Interfaz inválida")
        vid = int(vid)
        if not 1 <= vid <= 4094:
            raise ValueError("VLAN entre 1 y 4094")
        name = f"{parent}.{vid}"[:15]
        code, out = await _run("ip", "link", "add", "link", parent, "name", name, "type", "vlan", "id", str(vid))
        if code and "exists" not in out:
            raise RuntimeError(out.strip()[:160])
        await _run("ip", "link", "set", "dev", name, "up")
        allc = self.cfg()
        if not any(v["parent"] == parent and int(v["vid"]) == vid for v in allc["vlans"]):
            allc["vlans"].append({"parent": parent, "vid": vid})
            self.ctx.settings.set("interfaces", allc, notify=False)
        return {"ok": True, "name": name}

    async def del_vlan(self, name: str) -> dict:
        if not IFACE_RE.match(name) or "." not in name:
            raise ValueError("Sólo se pueden borrar VLAN")
        if name == self.mgmt_iface():
            raise ValueError("Es la interfaz por la que entras al panel")
        await _run("ip", "link", "del", name)
        allc = self.cfg()
        parent, vid = name.rsplit(".", 1)
        allc["vlans"] = [v for v in allc["vlans"] if not (v["parent"] == parent and str(v["vid"]) == vid)]
        allc["ifaces"].pop(name, None)
        self.ctx.settings.set("interfaces", allc, notify=False)
        return {"ok": True}

    async def apply_all(self) -> None:
        """Al arrancar: recrea las VLAN y vuelve a aplicar la configuración guardada."""
        c = self.cfg()
        for v in c["vlans"]:
            try:
                await self.add_vlan(v["parent"], v["vid"])
            except Exception as exc:  # pragma: no cover
                log.warning("VLAN %s.%s: %s", v.get("parent"), v.get("vid"), exc)
        for name, ic in c["ifaces"].items():
            if ic.get("mode", "keep") != "keep" or ic.get("mtu"):
                try:
                    await self._apply_one(name, ic)
                except Exception as exc:  # pragma: no cover
                    log.warning("interfaz %s: %s", name, exc)

    # ------------------------------------------------------------------ Proxmox: añadir tarjeta a este contenedor
    def _pve(self) -> dict:
        return (self.ctx.settings.get("integrations") or {}).get("proxmox") or {}

    async def proxmox_self(self) -> dict:
        import httpx

        from .integrations import proxmox_inventory
        pc = self._pve()
        if not pc.get("url"):
            raise RuntimeError("Conecta Proxmox en Integraciones")
        inv = await proxmox_inventory(pc)
        mine = None
        for i in self.list():
            g = inv.get((i.get("mac") or "").lower())
            if g:
                mine = g
                break
        if not mine:
            raise RuntimeError("No encuentro este equipo en Proxmox (¿es un contenedor/VM de ese servidor?)")
        base = pc["url"].rstrip("/") + "/api2/json"
        headers = {"Authorization": f"PVEAPIToken={pc['token_id']}={pc['secret']}"}
        async with httpx.AsyncClient(verify=bool(pc.get("verify_tls")), timeout=15, headers=headers) as h:
            nets = (await h.get(f"{base}/nodes/{mine['node']}/network")).json().get("data") or []
            conf = (await h.get(f"{base}/nodes/{mine['node']}/{mine['type']}/{mine['vmid']}/config")).json().get("data") or {}
        bridges = sorted({n["iface"] for n in nets if n.get("type") in ("bridge", "OVSBridge")})
        used = {k: v for k, v in conf.items() if re.match(r"^net\d+$", k)}
        return {**mine, "bridges": bridges, "nets": used}

    async def proxmox_add_nic(self, bridge: str, ip: str = "dhcp", gw: str = "", vlan: int | None = None) -> dict:
        import httpx
        me = await self.proxmox_self()
        if bridge not in me["bridges"]:
            raise ValueError("Puente desconocido en ese nodo")
        idx = next(i for i in range(0, 10) if f"net{i}" not in me["nets"])
        if me["type"] == "lxc":
            val = f"name=eth{idx},bridge={bridge},firewall=0"
            if ip == "dhcp":
                val += ",ip=dhcp"
            elif ip:
                ipaddress.ip_interface(ip)
                val += f",ip={ip}" + (f",gw={ipaddress.ip_address(gw)}" if gw else "")
        else:
            val = f"virtio,bridge={bridge},firewall=0"
        if vlan:
            if not 1 <= int(vlan) <= 4094:
                raise ValueError("VLAN entre 1 y 4094")
            val += f",tag={int(vlan)}"
        pc = self._pve()
        base = pc["url"].rstrip("/") + "/api2/json"
        headers = {"Authorization": f"PVEAPIToken={pc['token_id']}={pc['secret']}"}
        async with httpx.AsyncClient(verify=bool(pc.get("verify_tls")), timeout=30, headers=headers) as h:
            r = await h.put(f"{base}/nodes/{me['node']}/{me['type']}/{me['vmid']}/config", data={f"net{idx}": val})
        if r.status_code != 200:
            raise RuntimeError(f"Proxmox HTTP {r.status_code}: {r.text[:160]}"
                               + (" — el token necesita el permiso VM.Config.Network" if r.status_code == 403 else ""))
        self.ctx.db.event("info", "interfaces", f"Tarjeta net{idx} añadida en Proxmox ({bridge})")
        return {"ok": True, "key": f"net{idx}", "value": val,
                "message": f"Tarjeta añadida como eth{idx}." + ("" if me["type"] == "lxc" else " En una VM aparece tras reiniciarla.")}
