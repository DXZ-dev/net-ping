"""Controlador de velocidad por dispositivo, sin necesidad de que Bipon sea el router.

Para limitar la velocidad hay que actuar en el punto por el que pasa el tráfico
del dispositivo. Bipon elige, para cada equipo, el primer método disponible:

  1. proxmox  – VM / LXC de Proxmox: límite «rate» de su tarjeta de red virtual (API).
  2. router   – Bipon en modo router: colas HTB + fq_codel (tc).
  3. mikrotik – Router MikroTik conectado en «Mi router»: /queue/simple por IP.
  4. openwrt  – Router OpenWrt con el paquete nft-qos: límite estático por IP (UCI).

Si ninguno está disponible (p. ej. un router del proveedor sin API), el límite se
guarda y se aplicará en cuanto haya un método (o al activar el modo router).
"""
from __future__ import annotations

import logging
import re

import httpx

log = logging.getLogger("bipon.speedctl")

METHOD_LABEL = {"proxmox": "Proxmox (tarjeta virtual)", "router": "Bipon (modo router)", "mikrotik": "MikroTik (colas)",
                "openwrt": "OpenWrt (nft-qos)", None: "Sin método disponible"}


class SpeedControl:
    def __init__(self, ctx):
        self.ctx = ctx

    # ---------------------------------------------------------------- elección del método
    def _pve(self) -> dict:
        return ((self.ctx.settings.get("integrations") or {}).get("proxmox") or {})

    def _guest(self, mac: str) -> dict | None:
        inv = getattr(getattr(self.ctx, "wifi", None), "proxmox", None) or {}
        return inv.get(mac.lower())

    def method_for(self, mac: str) -> str | None:
        if self._guest(mac) and self._pve().get("url"):
            return "proxmox"
        rc = self.ctx.router.cfg()
        if rc.get("enabled") and (rc.get("shaping") or {}).get("enabled"):
            return "router"
        t = (self.ctx.routerctl.cfg() or {}).get("type")
        if t in ("mikrotik", "openwrt"):
            return t
        return None

    def overview(self) -> dict:
        devs = self.ctx.devices.devices
        methods = {m: self.method_for(m) for m in devs}
        t = (self.ctx.routerctl.cfg() or {}).get("type")
        return {"methods": methods, "labels": METHOD_LABEL, "router_type": t,
                "router_mode": bool((self.ctx.router.cfg().get("shaping") or {}).get("enabled")),
                "proxmox": bool(self._pve().get("url"))}

    # ---------------------------------------------------------------- aplicar
    async def apply(self, mac: str, down: float | None, up: float | None) -> dict:
        d = self.ctx.devices.devices.get(mac) or {}
        method = self.method_for(mac)
        try:
            if method == "proxmox":
                await self._apply_proxmox(mac, down, up)
            elif method == "router":
                await self.ctx.router.apply_shaping()
            elif method == "mikrotik":
                await self._apply_mikrotik(mac, d.get("ip"), down, up)
            elif method == "openwrt":
                await self._apply_openwrt(mac, d.get("ip"), down, up)
            else:
                return {"ok": True, "applied": False, "method": None,
                        "message": "Guardado. Para aplicarlo hace falta un punto de control: conecta un router MikroTik u OpenWrt "
                                   "en «Mi router», o activa el modo router. Las VMs de Proxmox se limitan directamente."}
        except Exception as exc:
            log.warning("velocidad %s: %s", mac, exc)
            return {"ok": False, "applied": False, "method": method, "message": f"{METHOD_LABEL[method]}: {exc}"}
        return {"ok": True, "applied": True, "method": method,
                "message": ("Límite quitado" if not (down or up) else "Límite aplicado") + f" · {METHOD_LABEL[method]}"}

    # ---- Proxmox: parámetro rate (MB/s) de la tarjeta de red de la VM/LXC (mismo valor en ambos sentidos)
    async def _apply_proxmox(self, mac: str, down, up) -> None:
        g = self._guest(mac)
        pc = self._pve()
        base = pc["url"].rstrip("/") + "/api2/json"
        path = f"{base}/nodes/{g['node']}/{g['type']}/{g['vmid']}/config"
        headers = {"Authorization": f"PVEAPIToken={pc['token_id']}={pc['secret']}"}
        async with httpx.AsyncClient(verify=bool(pc.get("verify_tls")), timeout=20, headers=headers) as h:
            conf = (await h.get(path)).json()["data"]
            key = next((k for k, v in conf.items() if re.match(r"^net\d+$", k) and mac.lower() in str(v).lower()), None)
            if not key:
                raise RuntimeError("no encuentro la tarjeta de red con esa MAC")
            parts = [p for p in str(conf[key]).split(",") if not p.startswith("rate=")]
            limit = max(v for v in (down, up, 0) if v is not None) if (down or up) else None
            if limit:
                parts.append(f"rate={max(0.1, round(limit / 8, 2))}")  # Mbps → MB/s
            r = await h.put(path, data={key: ",".join(parts)})
            if r.status_code != 200:
                raise RuntimeError(f"HTTP {r.status_code}: {r.text[:160]}"
                                   + (" — el token necesita permiso VM.Config.Network" if r.status_code == 403 else ""))

    # ---- MikroTik: una cola simple por dispositivo (max-limit = subida/bajada)
    async def _apply_mikrotik(self, mac: str, ip: str | None, down, up) -> None:
        if not ip:
            raise RuntimeError("el dispositivo no tiene IP")
        rc = self.ctx.routerctl
        base = rc.cfg()["url"].rstrip("/") + "/rest/queue/simple"
        name = "bipon-" + mac.replace(":", "")
        async with rc._client() as h:
            existing = [q for q in (await h.get(base)).json() if q.get("name") == name]
            if not (down or up):
                for q in existing:
                    await h.delete(f"{base}/{q['.id']}")
                return
            lim = f"{int((up or 0) * 1_000_000)}/{int((down or 0) * 1_000_000)}"  # 0 = sin límite en ese sentido
            body = {"name": name, "target": f"{ip}/32", "max-limit": lim, "comment": f"Bipon Net · {mac}"}
            r = await (h.patch(f"{base}/{existing[0]['.id']}", json=body) if existing else h.put(base, json=body))
            if r.status_code >= 300:
                raise RuntimeError(r.text[:160])

    # ---- OpenWrt: nft-qos (límite estático por IP) mediante UCI por ubus
    async def _apply_openwrt(self, mac: str, ip: str | None, down, up) -> None:
        if not ip:
            raise RuntimeError("el dispositivo no tiene IP")
        rc = self.ctx.routerctl
        drv = rc._drv()
        async with rc._client() as h:
            try:
                cur = await drv._call(h, "uci", "get", {"config": "nft-qos"})
            except Exception:
                raise RuntimeError("instala el paquete nft-qos en el router (opkg install nft-qos luci-app-nft-qos)") from None
            for sec, v in (cur.get("values") or {}).items():
                if v.get(".type") in ("download", "upload") and v.get("bipon") == mac:
                    await drv._call(h, "uci", "delete", {"config": "nft-qos", "section": sec})
            if down or up:
                await drv._call(h, "uci", "set", {"config": "nft-qos", "section": "default",
                                                  "values": {"limit_enable": "1", "limit_type": "static"}})
                for typ, val in (("download", down), ("upload", up)):
                    if val:
                        await drv._call(h, "uci", "add", {"config": "nft-qos", "type": typ, "values": {
                            "hostname": mac.replace(":", ""), "ipaddr": ip, "macaddr": mac, "rate": str(int(val * 125)),  # Mbps → kB/s
                            "unit": "kbytes", "bipon": mac}})
            await drv._call(h, "uci", "commit", {"config": "nft-qos"})
