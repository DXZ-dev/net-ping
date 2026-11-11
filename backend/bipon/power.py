"""Encendido y apagado de dispositivos de la casa, por el método que cada uno soporta.

Encender:
  * Wake-on-LAN (paquete mágico) para PC/NAS/consolas con WoL activado.
  * Home Assistant: switch.turn_on / light.turn_on / etc. para enchufes y bombillas.

Apagar (ninguna red apaga un equipo cualquiera «por arte de magia»; se usa lo que el aparato ofrece):
  * Home Assistant: turn_off de un enchufe inteligente, bombilla o media_player (apaga de verdad lo enchufado).
  * PC/servidor con SSH: comando de apagado (requiere credenciales del equipo).
  * Consolas/TV con protocolo propio (p. ej. PS4/PS5 por el puerto 9295, Android TV) cuando esté disponible.

Cada dispositivo guarda su método en settings["power"][mac]; la interfaz lo configura.
"""
from __future__ import annotations

import asyncio
import logging

import httpx

log = logging.getLogger("bipon.power")

# marca / tipo  ->  qué suele admitir
BRAND_HINTS = {
    "tp-link": ["ha", "wol"], "kasa": ["ha"], "tapo": ["ha"], "sonoff": ["ha"], "shelly": ["ha", "http"],
    "philips hue": ["ha"], "lifx": ["ha"], "wiz": ["ha"], "meross": ["ha"], "amazon": ["ha"], "google": ["ha"],
    "sony": ["wol"], "playstation": ["wol", "playstation"], "microsoft": ["wol"], "xbox": ["wol"], "nintendo": ["wol"],
    "synology": ["wol", "ssh"], "qnap": ["wol", "ssh"], "intel": ["wol", "ssh"], "dell": ["wol", "ssh"],
    "apple": ["wol"], "samsung": ["wol", "ha"], "lg": ["wol", "ha"], "roku": ["ha"],
}
METHOD_LABEL = {"wol": "Wake-on-LAN (encender)", "ha": "Home Assistant (enchufe / bombilla)",
                "ssh": "SSH (apagar PC/servidor)", "http": "HTTP del dispositivo (Shelly)", "playstation": "PlayStation"}


class PowerControl:
    def __init__(self, ctx):
        self.ctx = ctx

    def _map(self) -> dict:
        return self.ctx.settings.get("power") or {}

    def config_for(self, mac: str) -> dict:
        return self._map().get(mac, {})

    def set_config(self, mac: str, cfg: dict) -> dict:
        allp = self._map()
        clean = {k: cfg.get(k) for k in ("method", "ha_entity", "ssh_host", "ssh_user", "ssh_port") if cfg.get(k)}
        if cfg.get("password"):
            clean["password"] = cfg["password"]
        elif allp.get(mac, {}).get("password"):
            clean["password"] = allp[mac]["password"]
        allp[mac] = clean
        self.ctx.settings.set("power", allp, notify=False)
        return {k: v for k, v in clean.items() if k != "password"}

    def suggest(self, mac: str) -> list[str]:
        d = self.ctx.devices.devices.get(mac) or {}
        brand = (d.get("brand_name") or d.get("brand") or d.get("vendor") or "").lower()
        hints = []
        for k, v in BRAND_HINTS.items():
            if k in brand:
                hints = v
                break
        if not hints:
            hints = ["wol"] if d.get("kind") in ("pc", "server", "nas", "console", "laptop") else ["ha"]
        if (self.ctx.settings.get("integrations") or {}).get("homeassistant", {}).get("url") and "ha" not in hints:
            hints.append("ha")
        return hints

    def capabilities(self, mac: str) -> dict:
        cfg = self.config_for(mac)
        method = cfg.get("method")
        can_on = method in ("wol", "ha", "http") or True  # WoL siempre se puede intentar
        can_off = method in ("ha", "ssh", "http", "playstation")
        return {"method": method, "configured": bool(method), "suggest": self.suggest(mac),
                "can_on": can_on, "can_off": can_off, "labels": METHOD_LABEL, "ha_entity": cfg.get("ha_entity")}

    # ---------------------------------------------------------------- acciones
    async def power(self, mac: str, on: bool) -> dict:
        d = self.ctx.devices.devices.get(mac)
        if not d:
            raise ValueError("Dispositivo no encontrado")
        cfg = self.config_for(mac)
        method = cfg.get("method") or (self.suggest(mac)[0] if self.suggest(mac) else "wol")
        if method == "wol":
            if not on:
                raise ValueError("Wake-on-LAN solo puede encender. Para apagar usa Home Assistant (enchufe) o SSH.")
            import ipaddress
            from . import netinfo
            bcast = "255.255.255.255"
            for itf in netinfo.interfaces():
                try:
                    if d.get("ip") and ipaddress.ip_address(d["ip"]) in ipaddress.ip_network(itf["network"], strict=False):
                        bcast = str(ipaddress.ip_network(itf["network"], strict=False).broadcast_address)
                except ValueError:
                    pass
            self.ctx.devices.wake_on_lan(mac, bcast)
            return {"ok": True, "message": "Paquete de encendido (Wake-on-LAN) enviado"}
        if method == "ha":
            return await self._ha(cfg.get("ha_entity"), on)
        if method == "http":
            return await self._shelly(d.get("ip"), on)
        if method == "ssh":
            if on:
                self.ctx.devices.wake_on_lan(mac)
                return {"ok": True, "message": "Encendido por Wake-on-LAN (SSH solo apaga)"}
            return await self._ssh_off(cfg, d.get("ip"))
        raise ValueError(f"Método «{method}» no soporta esta acción")

    async def _ha(self, entity: str | None, on: bool) -> dict:
        if not entity:
            raise ValueError("Indica la entidad de Home Assistant (p. ej. switch.enchufe_tv)")
        ha = (self.ctx.settings.get("integrations") or {}).get("homeassistant") or {}
        if not ha.get("url") or not ha.get("token"):
            raise ValueError("Conecta Home Assistant en Integraciones (URL y token)")
        domain = entity.split(".")[0]
        url = ha["url"].rstrip("/") + f"/api/services/{domain}/turn_{'on' if on else 'off'}"
        async with httpx.AsyncClient(verify=bool(ha.get("verify_tls", True)), timeout=12,
                                     headers={"Authorization": f"Bearer {ha['token']}"}) as h:
            r = await h.post(url, json={"entity_id": entity})
            if r.status_code >= 300:
                raise RuntimeError(f"Home Assistant respondió HTTP {r.status_code}")
        return {"ok": True, "message": f"{'Encendido' if on else 'Apagado'} enviado a {entity}"}

    async def _shelly(self, ip: str | None, on: bool) -> dict:
        if not ip:
            raise ValueError("el dispositivo no tiene IP")
        async with httpx.AsyncClient(timeout=6) as h:
            r = await h.get(f"http://{ip}/relay/0?turn={'on' if on else 'off'}")
            if r.status_code >= 300:
                raise RuntimeError(f"HTTP {r.status_code}")
        return {"ok": True, "message": f"Shelly {'encendido' if on else 'apagado'}"}

    async def _ssh_off(self, cfg: dict, ip: str | None) -> dict:
        host = cfg.get("ssh_host") or ip
        if not host:
            raise ValueError("falta la dirección SSH")
        if not shutil_which("ssh"):
            raise RuntimeError("el cliente ssh no está instalado en Bipon")
        user = cfg.get("ssh_user") or "root"
        port = str(cfg.get("ssh_port") or 22)
        # se usa la clave del sistema; para apagado con contraseña, configúralo con clave SSH en Bipon
        cmd = ["ssh", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=accept-new", "-o", "ConnectTimeout=6",
               "-p", port, f"{user}@{host}", "sudo -n poweroff || sudo -n shutdown -h now || poweroff"]
        proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
        try:
            out, _ = await asyncio.wait_for(proc.communicate(), 12)
        except asyncio.TimeoutError:
            proc.kill()
            return {"ok": True, "message": "Comando de apagado enviado (sin respuesta: el equipo pudo apagarse)"}
        if proc.returncode not in (0, 255):
            raise RuntimeError(out.decode(errors="ignore")[:200] or "el apagado SSH falló")
        return {"ok": True, "message": "Apagado por SSH enviado"}


def shutil_which(name: str):
    import shutil
    return shutil.which(name)
