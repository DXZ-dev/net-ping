"""Modo router: firewall nftables, zonas/VLAN, bloqueo real por dispositivo,
DNS forzado, redirección de puertos y contabilidad de tráfico por dispositivo.

Sólo tiene efecto si el tráfico de la red PASA por este equipo (Bipon Net hace
de router/gateway, p. ej. en Proxmox con dos interfaces o con VLAN).
Bipon Net únicamente gestiona su propia tabla «inet bipon»: nunca toca reglas
de otros programas. Cada cambio se aplica con reversión automática a los
60 s salvo que el usuario lo confirme (evita quedarse sin acceso)."""
from __future__ import annotations

import asyncio
import ipaddress
import json
import logging
import re
import shutil
import time

from . import netinfo

log = logging.getLogger("bipon.router")

IFACE_RE = re.compile(r"^[a-zA-Z0-9_.@-]{1,15}$")
PRIVATE = "{ 10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16, 100.64.0.0/10 }"
ROLLBACK_SECONDS = 60


async def _run(*args, input: str | None = None, timeout: float = 20) -> tuple[int, str]:
    p = await asyncio.create_subprocess_exec(*args, stdin=asyncio.subprocess.PIPE if input else None,
                                             stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
    out, _ = await asyncio.wait_for(p.communicate(input.encode() if input else None), timeout)
    return p.returncode or 0, out.decode(errors="ignore")


class RouterManager:
    def __init__(self, ctx):
        self.ctx = ctx
        self.pending: dict | None = None
        self._rollback_task: asyncio.Task | None = None
        self.last_error = ""
        self.last_counters: dict[str, tuple[int, int]] = {}
        self.rates: dict[str, dict] = {}
        self.shaped: dict[str, dict] = {}
        self._pending_rows: dict[str, list[int]] = {}

    @property
    def available(self) -> bool:
        return shutil.which("nft") is not None

    def cfg(self) -> dict:
        c = self.ctx.settings.get("router") or {}
        c.setdefault("enabled", False)
        c.setdefault("wan", "")
        c.setdefault("lan", "")
        c.setdefault("nat", True)
        c.setdefault("force_dns", True)
        c.setdefault("block_devices", True)
        c.setdefault("accounting", True)
        c.setdefault("zones", [])
        c.setdefault("port_forwards", [])
        return c

    # ---------------------------------------------------------------- validación
    def validate(self, c: dict) -> dict:
        for k in ("wan", "lan"):
            if c.get(k) and not IFACE_RE.match(c[k]):
                raise ValueError(f"Interfaz {k} inválida")
        for z in c.get("zones", []):
            if not re.match(r"^[a-z0-9_-]{1,20}$", z.get("name", "")):
                raise ValueError("Nombre de zona inválido (minúsculas, números, - _)")
            ipaddress.ip_interface(z["address"])
            if z.get("vlan") is not None and z.get("vlan") != "":
                vid = int(z["vlan"])
                if not 1 <= vid <= 4094:
                    raise ValueError("VLAN entre 1 y 4094")
                if not IFACE_RE.match(z.get("parent") or c.get("lan") or ""):
                    raise ValueError("Indica la interfaz física de la VLAN")
        for f in c.get("port_forwards", []):
            if f.get("proto") not in ("tcp", "udp"):
                raise ValueError("Protocolo inválido")
            int(f["port"])
            int(f.get("to_port") or f["port"])
            ipaddress.ip_address(f["to_ip"])
        return c

    # ---------------------------------------------------------------- reglas
    def ruleset(self, c: dict) -> str:
        devices = self.ctx.devices
        blocked_macs = []
        if c.get("block_devices"):
            for mac, d in devices.devices.items():
                if mac.startswith("ip:") or not d.get("ip"):
                    continue
                if devices.policy_for(d["ip"]).block_all:
                    blocked_macs.append(mac)
        captive = getattr(self.ctx, "captive", None)
        if captive is not None:
            # portal cautivo: sin aceptar los términos no hay salida a Internet (sólo DNS/portal de Bipon)
            blocked_macs += [m for m in captive.pending_macs() if m not in blocked_macs]
        self_ip = devices.self_ip or netinfo.primary_ip() or "127.0.0.1"
        dns_port = int(self.ctx.settings.get("dns.port", 53))
        lan_ifs = [c["lan"]] if c.get("lan") else []
        for z in c.get("zones", []):
            lan_ifs.append(self._zone_iface(z, c))
        lines = ["table inet bipon {"]
        lines.append(f"  set blocked_macs {{ type ether_addr; {('elements = { ' + ', '.join(blocked_macs) + ' };') if blocked_macs else ''} }}")
        lines.append("  chain forward {")
        lines.append("    type filter hook forward priority -10; policy accept;")
        lines.append("    ether saddr @blocked_macs counter drop comment \"Internet bloqueado (control parental)\"")
        for z in c.get("zones", []):
            net = ipaddress.ip_interface(z["address"]).network
            if z.get("isolate", True):
                lines.append(f"    ip saddr {net} ip daddr {PRIVATE} ip daddr != {net} ct state new counter drop comment \"zona {z['name']} aislada\"")
            if not z.get("internet", True):
                lines.append(f"    ip saddr {net} ip daddr != {PRIVATE} counter drop comment \"zona {z['name']} sin Internet\"")
        if c.get("accounting"):
            for mac, d in devices.devices.items():
                ip = d.get("ip")
                if not ip or ip in (self_ip, devices.gateway):
                    continue
                lines.append(f"    ip saddr {ip} counter comment \"tx {mac}\"")
                lines.append(f"    ip daddr {ip} counter comment \"rx {mac}\"")
        lines.append("  }")
        lines.append("  chain prerouting {")
        lines.append("    type nat hook prerouting priority dstnat; policy accept;")
        if c.get("force_dns") and lan_ifs:
            ifs = "{ " + ", ".join(f'"{i}"' for i in lan_ifs) + " }"
            lines.append(f"    iifname {ifs} ip daddr != {self_ip} udp dport 53 counter dnat ip to {self_ip}:{dns_port} comment \"DNS forzado\"")
            lines.append(f"    iifname {ifs} ip daddr != {self_ip} tcp dport 53 counter dnat ip to {self_ip}:{dns_port} comment \"DNS forzado\"")
        if c.get("wan"):
            for f in c.get("port_forwards", []):
                to_port = int(f.get("to_port") or f["port"])
                lines.append(f"    iifname \"{c['wan']}\" {f['proto']} dport {int(f['port'])} counter dnat ip to {f['to_ip']}:{to_port} "
                             f"comment \"{re.sub(r'[^A-Za-z0-9 _.-]', '', f.get('name', 'redirección'))[:40]}\"")
        lines.append("  }")
        lines.append("  chain postrouting {")
        lines.append("    type nat hook postrouting priority srcnat; policy accept;")
        if c.get("nat") and c.get("wan"):
            lines.append(f"    oifname \"{c['wan']}\" masquerade")
        lines.append("  }")
        lines.append("}")
        return "\n".join(lines) + "\n"

    @staticmethod
    def _zone_iface(z: dict, c: dict) -> str:
        if z.get("vlan") not in (None, ""):
            return f"{z.get('parent') or c.get('lan')}.{int(z['vlan'])}"[:15]
        return z.get("parent") or c.get("lan")

    async def _current_table(self) -> str:
        code, out = await _run("nft", "list", "table", "inet", "bipon")
        return out if code == 0 else ""

    async def _load(self, text: str) -> tuple[bool, str]:
        cur = await self._current_table()
        script = ("delete table inet bipon\n" if cur else "") + text
        code, out = await _run("nft", "-c", "-f", "-", input=script)  # comprobación previa
        if code != 0:
            return False, out.strip()
        code, out = await _run("nft", "-f", "-", input=script)
        return code == 0, out.strip()

    async def _zones_up(self, c: dict) -> list[str]:
        notes = []
        for z in c.get("zones", []):
            ifname = self._zone_iface(z, c)
            if z.get("vlan") not in (None, ""):
                code, _ = await _run("ip", "link", "show", ifname)
                if code != 0:
                    code, out = await _run("ip", "link", "add", "link", z.get("parent") or c["lan"], "name", ifname, "type", "vlan", "id", str(int(z["vlan"])))
                    if code != 0:
                        notes.append(f"VLAN {ifname}: {out.strip()}")
                        continue
            await _run("ip", "addr", "replace", z["address"], "dev", ifname)
            await _run("ip", "link", "set", ifname, "up")
        return notes

    async def apply(self, c: dict | None = None, confirm_required: bool = True) -> dict:
        if not self.available:
            raise RuntimeError("nftables (nft) no está instalado")
        c = self.validate(c or self.cfg())
        previous = await self._current_table()
        if not c.get("enabled"):
            if previous:
                await _run("nft", "delete", "table", "inet", "bipon")
            self.ctx.settings.set("router", c, notify=False)
            return {"ok": True, "message": "Modo router desactivado (reglas eliminadas)"}
        try:
            with open("/proc/sys/net/ipv4/ip_forward", "w") as fh:
                fh.write("1")
        except OSError:
            pass
        notes = await self._zones_up(c)
        ok, out = await self._load(self.ruleset(c))
        if not ok:
            self.last_error = out
            return {"ok": False, "message": out}
        self.ctx.settings.set("router", c, notify=False)
        if confirm_required:
            self.pending = {"previous": previous, "deadline": time.time() + ROLLBACK_SECONDS}
            if self._rollback_task:
                self._rollback_task.cancel()
            self._rollback_task = asyncio.create_task(self._rollback_later())
        return {"ok": True, "message": "Reglas aplicadas" + (f". Confirma en {ROLLBACK_SECONDS} s o se revertirán." if confirm_required else ""),
                "notes": notes, "pending": confirm_required}

    async def _rollback_later(self):
        await asyncio.sleep(ROLLBACK_SECONDS)
        if self.pending:
            prev = self.pending["previous"]
            self.pending = None
            if prev:
                await _run("nft", "-f", "-", input="delete table inet bipon\n" + prev)
            else:
                await _run("nft", "delete", "table", "inet", "bipon")
            self.ctx.db.event("warning", "router", "Cambios del firewall revertidos automáticamente (no se confirmaron)")
            log.warning("firewall revertido (sin confirmación)")

    def confirm(self) -> bool:
        if not self.pending:
            return False
        self.pending = None
        if self._rollback_task:
            self._rollback_task.cancel()
        self.ctx.db.event("info", "router", "Cambios del firewall confirmados")
        return True

    # ---------------------------------------------------------------- límites de velocidad (tc)
    async def _tc(self, *args) -> tuple[int, str]:
        return await _run("tc", *args)

    async def apply_shaping(self, c: dict | None = None) -> dict:
        """Límite de bajada/subida por dispositivo.
        Bajada: HTB en la salida de la interfaz LAN (tráfico hacia el equipo).
        Subida: la entrada de la LAN se redirige a ifb0 y allí se aplica HTB."""
        c = c or self.cfg()
        sh = c.get("shaping") or {}
        lan = sh.get("iface") or c.get("lan")
        if not shutil.which("tc"):
            raise RuntimeError("tc (iproute2) no está instalado")
        if not lan or not IFACE_RE.match(lan):
            raise ValueError("Indica la interfaz LAN")
        # limpiar estado anterior
        await self._tc("qdisc", "del", "dev", lan, "root")
        await self._tc("qdisc", "del", "dev", lan, "ingress")
        await _run("ip", "link", "del", "ifb0")
        if not sh.get("enabled"):
            self.shaped = {}
            return {"ok": True, "message": "Límites de velocidad desactivados"}
        total_down = float(sh.get("down_mbit") or 1000)
        total_up = float(sh.get("up_mbit") or 1000)
        limited = [(m, d) for m, d in self.ctx.devices.devices.items() if d.get("ip") and (d.get("limit_down") or d.get("limit_up"))]
        errors = []

        async def build(dev: str, total: float, key: str, match: str):
            cmds = [["qdisc", "replace", "dev", dev, "root", "handle", "1:", "htb", "default", "9999", "r2q", "100"],
                    ["class", "replace", "dev", dev, "parent", "1:", "classid", "1:1", "htb", "rate", f"{total}mbit", "ceil", f"{total}mbit"],
                    ["class", "replace", "dev", dev, "parent", "1:1", "classid", "1:9999", "htb", "rate", f"{max(1.0, total * 0.5)}mbit",
                     "ceil", f"{total}mbit", "prio", "1"],
                    ["qdisc", "replace", "dev", dev, "parent", "1:9999", "fq_codel"]]
            for i, (mac, d) in enumerate(limited, start=10):
                lim = d.get(key)
                if not lim:
                    continue
                cid = f"1:{i}"
                cmds += [["class", "replace", "dev", dev, "parent", "1:1", "classid", cid, "htb", "rate", f"{float(lim)}mbit",
                          "ceil", f"{float(lim)}mbit", "prio", "2"],
                         ["qdisc", "replace", "dev", dev, "parent", cid, "fq_codel"],
                         ["filter", "add", "dev", dev, "parent", "1:", "protocol", "ip", "prio", "1", "u32", "match", "ip", match,
                          f"{d['ip']}/32", "flowid", cid]]
                self.shaped[mac] = {**self.shaped.get(mac, {}), key: float(lim), f"class_{key}": cid}
            for cmd in cmds:
                code, out = await self._tc(*cmd)
                if code != 0:
                    errors.append(f"tc {' '.join(cmd[:4])}: {out.strip()[:120]}")
        self.shaped = {}
        await build(lan, total_down, "limit_down", "dst")
        code, out = await _run("ip", "link", "add", "ifb0", "type", "ifb")
        if code != 0 and "exists" not in out:
            errors.append(f"ifb0: {out.strip()} (¿módulo ifb disponible?)")
        else:
            await _run("ip", "link", "set", "ifb0", "up")
            await self._tc("qdisc", "add", "dev", lan, "handle", "ffff:", "ingress")
            await self._tc("filter", "add", "dev", lan, "parent", "ffff:", "protocol", "ip", "u32", "match", "u32", "0", "0",
                           "action", "mirred", "egress", "redirect", "dev", "ifb0")
            await build("ifb0", total_up, "limit_up", "src")
        if errors:
            self.last_error = "; ".join(errors[:3])
        return {"ok": not errors, "message": "Límites aplicados" if not errors else self.last_error, "devices": len(limited)}

    async def enforce_now(self, macs: list[str] | None = None) -> None:
        """Aplica al instante el bloqueo de Internet y corta las conexiones abiertas de los dispositivos bloqueados."""
        c = self.cfg()
        if not (c.get("enabled") and c.get("block_devices") and self.available) or self.pending:
            return
        await self.apply(c, confirm_required=False)
        if not shutil.which("conntrack"):
            return
        devs = self.ctx.devices
        for mac, d in devs.devices.items():
            if macs and mac not in macs or not d.get("ip"):
                continue
            if devs.policy_for(d["ip"]).block_all:
                await _run("conntrack", "-D", "-s", d["ip"])
                await _run("conntrack", "-D", "-d", d["ip"])

    async def enforce_captive(self, cut_mac: str | None = None) -> None:
        """Portal cautivo: actualiza el firewall al aceptar o al volver a pedir los términos y,
        si se vuelve a pedir, corta las conexiones abiertas del dispositivo."""
        c = self.cfg()
        if not (c.get("enabled") and self.available) or self.pending:
            return
        await self.apply(c, confirm_required=False)
        if cut_mac is not None and shutil.which("conntrack"):
            devs = self.ctx.devices
            macs = [cut_mac] if cut_mac else self.ctx.captive.pending_macs()
            targets = [devs.devices.get(m, {}).get("ip") for m in macs]
            for ip in filter(None, targets):
                await _run("conntrack", "-D", "-s", ip)

    # ---------------------------------------------------------------- contabilidad
    async def read_counters(self) -> dict[str, tuple[int, int]]:
        code, out = await _run("nft", "-j", "list", "chain", "inet", "bipon", "forward")
        if code != 0:
            return {}
        res: dict[str, list[int]] = {}
        for item in json.loads(out).get("nftables", []):
            rule = item.get("rule")
            if not rule:
                continue
            comment = rule.get("comment", "")
            if not comment.startswith(("tx ", "rx ")):
                continue
            nbytes = next((e["counter"]["bytes"] for e in rule.get("expr", []) if isinstance(e, dict) and "counter" in e), 0)
            kind, mac = comment.split(" ", 1)
            res.setdefault(mac, [0, 0])[0 if kind == "rx" else 1] += nbytes
        return {m: (v[0], v[1]) for m, v in res.items()}

    async def loop(self) -> None:
        await asyncio.sleep(30)
        refreshed = 0.0
        while True:
            c = self.cfg()
            if c.get("enabled") and self.available and not self.pending:
                try:
                    # se regeneran las reglas cada 2 min (dispositivos nuevos, pausas y horarios)
                    if time.time() - refreshed > 120:
                        refreshed = time.time()
                        await self.apply(c, confirm_required=False)
                        if (c.get("shaping") or {}).get("enabled"):
                            sig = sorted((m, d.get("ip"), d.get("limit_down"), d.get("limit_up")) for m, d in self.ctx.devices.devices.items()
                                         if d.get("limit_down") or d.get("limit_up"))
                            if sig != getattr(self, "_shape_sig", None):
                                self._shape_sig = sig
                                await self.apply_shaping(c)
                    if c.get("accounting"):
                        now = time.time()
                        counters = await self.read_counters()
                        dt = max(1.0, now - getattr(self, "_last_read", now - 10))
                        self._last_read = now
                        for mac, (rx, tx) in counters.items():
                            prx, ptx = self.last_counters.get(mac, (rx, tx))
                            drx, dtx = max(0, rx - prx), max(0, tx - ptx)
                            acc = self._pending_rows.setdefault(mac, [0, 0])
                            acc[0] += drx
                            acc[1] += dtx
                            self.rates[mac] = {"rx_bps": drx * 8 / dt, "tx_bps": dtx * 8 / dt}
                        self.last_counters = counters
                        if now - getattr(self, "_last_write", 0) >= 60:
                            self._last_write = now
                            rows = [(now, m, v[0], v[1]) for m, v in self._pending_rows.items() if v[0] or v[1]]
                            self._pending_rows = {}
                            if rows:
                                self.ctx.db.executemany("INSERT INTO traffic(ts,mac,rx,tx) VALUES (?,?,?,?)", rows)
                except Exception as exc:  # pragma: no cover
                    self.last_error = str(exc)
                    log.warning("router: %s", exc)
            await asyncio.sleep(10)

    def usage(self, hours: int = 24) -> list[dict]:
        since = time.time() - hours * 3600
        rows = self.ctx.db.query("SELECT mac, SUM(rx) rx, SUM(tx) tx FROM traffic WHERE ts>=? GROUP BY mac ORDER BY SUM(rx)+SUM(tx) DESC", (since,))
        names = {m: d.get("name") or d.get("hostname") or d.get("ip") or m for m, d in self.ctx.devices.devices.items()}
        for r in rows:
            r["name"] = names.get(r["mac"], r["mac"])
            r.update(self.rates.get(r["mac"], {}))
        return rows

    async def status(self) -> dict:
        c = self.cfg()
        return {"available": self.available, "config": c, "pending": bool(self.pending),
                "deadline": self.pending["deadline"] if self.pending else None, "last_error": self.last_error,
                "interfaces": netinfo.interfaces(), "preview": self.ruleset(c) if c.get("enabled") else "",
                "active": bool(await self._current_table())}
