"""Control de acceso por origen: sólo red local, local + VPN, cualquiera o lista propia.

Se aplica por separado al panel web y al servidor DNS (evita ser un
«resolver abierto» que se usa en ataques de amplificación)."""
from __future__ import annotations

import ipaddress
import logging
import time

from . import netinfo

log = logging.getLogger("bipon.access")

LOCAL_NETS = [ipaddress.ip_network(n) for n in (
    "127.0.0.0/8", "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "169.254.0.0/16", "::1/128", "fc00::/7", "fe80::/10")]
VPN_NETS = [ipaddress.ip_network(n) for n in ("100.64.0.0/10", "fd7a:115c:a1e0::/48")]  # Tailscale (las VPN propias usan rangos privados)
MODES = ("local", "local_vpn", "any", "custom")


class AccessControl:
    def __init__(self, ctx):
        self.ctx = ctx
        self._cache: dict[tuple[str, str], bool] = {}
        self._nets: dict[str, list] = {}
        self.denied: dict[str, dict] = {}  # origen → {count, last, target}
        self.reload()

    def cfg(self) -> dict:
        c = self.ctx.settings.get("access") or {}
        c.setdefault("web", "local_vpn")
        c.setdefault("dns", "local_vpn")
        c.setdefault("web_allow", [])
        c.setdefault("dns_allow", [])
        return c

    def reload(self) -> None:
        c = self.cfg()
        extra_local = []
        for i in netinfo.interfaces():
            try:
                extra_local.append(ipaddress.ip_network(i["network"]))
            except Exception:
                pass
        for target in ("web", "dns"):
            mode = c[target] if c[target] in MODES else "local_vpn"
            nets = []
            if mode in ("local", "local_vpn", "custom"):
                nets += LOCAL_NETS + extra_local
            if mode == "local_vpn":
                nets += VPN_NETS
            if mode == "custom":
                for n in c[f"{target}_allow"]:
                    try:
                        nets.append(ipaddress.ip_network(n.strip(), strict=False))
                    except ValueError:
                        log.warning("red de acceso inválida: %s", n)
            self._nets[target] = None if mode == "any" else nets
        self._cache.clear()

    def allowed(self, target: str, ip: str) -> bool:
        nets = self._nets.get(target)
        if nets is None:
            return True
        key = (target, ip)
        hit = self._cache.get(key)
        if hit is not None:
            return hit
        try:
            a = ipaddress.ip_address(ip.split("%")[0])
            if a.version == 6 and a.ipv4_mapped:
                a = a.ipv4_mapped
            ok = any(a in n for n in nets)
        except ValueError:
            ok = False
        if len(self._cache) > 50_000:
            self._cache.clear()
        self._cache[key] = ok
        if not ok:
            d = self.denied.setdefault(ip, {"count": 0, "last": 0, "target": target})
            d["count"] += 1
            d["last"] = time.time()
            if d["count"] == 1:
                self.ctx.db.event("warning", "acceso", f"Acceso externo rechazado al {'panel' if target == 'web' else 'DNS'} desde {ip}")
        return ok

    def status(self) -> dict:
        top = sorted(({"ip": k, **v} for k, v in self.denied.items()), key=lambda x: -x["last"])[:100]
        return {"config": self.cfg(), "denied": top}
