"""Multi-WAN: varias conexiones a Internet trabajando juntas.

Modos
* Respaldo (failover): todo sale por la línea principal; si cae (o su ping/pérdida se disparan) Bipon pasa
  en segundos a la siguiente y vuelve cuando la principal se recupera de forma estable.
* Reparto de carga (balance): cada dispositivo (o cada conexión) sale por una línea según su peso.
  Por dispositivo es lo recomendable: ninguna web ve cambiar la IP en mitad de una sesión.
* Juegos y llamadas por la línea con menos ping (smart): las conexiones nuevas de juegos, llamadas y consolas
  van por la línea más rápida en ese momento (con histéresis para no saltar a cada rato).
* Reglas por dispositivo: un equipo siempre por una línea concreta (si esa línea cae, usa las demás).

Cómo funciona (Linux)
* Cada línea tiene una marca y una tabla de rutas propias (201, 202…): «default via <gw> table 201» +
  «ip rule fwmark 201 lookup 201».
* nftables marca la primera vez cada conexión que sale de la LAN hacia Internet y guarda la marca en conntrack
  (ct mark): toda la conexión sigue por la misma línea, y la NAT de la línea correspondiente la enmascara.
* La salud de cada línea se mide con ping marcado (sale por esa línea aunque no sea la ruta principal).
* Al caer una línea se borran sus conexiones de conntrack para que se rehagan al instante por otra, y si era la
  ruta del propio Bipon (DNS, Unbound, actualizaciones) también se cambia la ruta principal.
Requisito: Bipon debe ser la puerta de enlace (Router y firewall → modo router / asistente de gateway).
Las líneas pueden ser otro router en la misma red (un solo cable) o una segunda tarjeta/USB (4G, móvil).
"""
from __future__ import annotations

import asyncio
import ipaddress
import json
import logging
import re
import secrets
import shutil
import statistics
import time
from collections import deque

from . import netinfo
from .pinger import ping

log = logging.getLogger("bipon.multiwan")

MARK_BASE = 200
MAX_WANS = 8
IFACE_RE = re.compile(r"^[a-zA-Z0-9_.@-]{1,15}$")
MAC_RE = re.compile(r"^[0-9a-f]{2}(:[0-9a-f]{2}){5}$")
PRIVATE = "{ 10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16, 100.64.0.0/10, 169.254.0.0/16, 224.0.0.0/4 }"

# Puertos UDP de juegos y llamadas (conexiones nuevas → la línea con menos ping y prioridad DSCP)
GAME_UDP = ["88", "500", "3074-3079", "3544", "4379-4380", "4500", "5000-5500", "6672", "7000-8000",
            "9000-9100", "9295-9304", "12000-12100", "19132-19133", "25565", "27000-27100", "30000-30009", "37000-40000"]
VOICE_UDP = ["3478-3481", "8801-8810", "19302-19309", "50000-50059"]


async def _run(*args, input: str | None = None, timeout: float = 10) -> tuple[int, str]:
    p = await asyncio.create_subprocess_exec(*args, stdin=asyncio.subprocess.PIPE if input else None,
                                             stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
    try:
        out, _ = await asyncio.wait_for(p.communicate(input.encode() if input else None), timeout)
    except asyncio.TimeoutError:
        p.kill()
        return 124, "tiempo agotado"
    return p.returncode or 0, out.decode(errors="ignore")


def _write(path: str, val: str) -> None:
    try:
        with open(path, "w") as fh:
            fh.write(val)
    except OSError:
        pass


def weight_map(wans: list[dict], states: dict) -> list[tuple[int, int, int]]:
    """Reparte 0-99 entre las líneas activas según su peso → [(desde, hasta, marca)]."""
    act = [w for w in wans if states.get(w["id"], {}).get("up", True)]
    if not act:
        return []
    total = sum(max(1, int(w.get("weight") or 1)) for w in act)
    out, start = [], 0
    for i, w in enumerate(act):
        size = 100 - start if i == len(act) - 1 else max(1, round(100 * max(1, int(w.get("weight") or 1)) / total))
        end = min(99, start + size - 1)
        if start <= end:
            out.append((start, end, int(w["mark"])))
        start = end + 1
        if start > 99:
            break
    return out


class MultiWAN:
    def __init__(self, ctx):
        self.ctx = ctx
        self.state: dict[str, dict] = {}
        self.best: str | None = None
        self._best_votes: dict[str, int] = {}
        self.main_wan: str | None = None
        self.orig_default: dict | None = None
        self.forced_down: dict[str, float] = {}  # «simular caída» para probar el respaldo
        self.history: deque = deque(maxlen=200)
        self._resync = asyncio.Event()
        self._last_change = 0.0

    # ------------------------------------------------------------------ configuración
    def cfg(self) -> dict:
        c = self.ctx.settings.get("multiwan") or {}
        c.setdefault("enabled", False)
        c.setdefault("mode", "failover")          # failover | balance
        c.setdefault("balance_by", "device")      # device | connection
        c.setdefault("smart_gaming", True)
        c.setdefault("wans", [])
        c.setdefault("device_rules", {})          # mac -> id de línea
        c.setdefault("targets", ["1.1.1.1", "8.8.8.8", "9.9.9.9"])
        c.setdefault("interval", 2)
        c.setdefault("fail_after", 3)
        c.setdefault("recover_after", 6)
        c.setdefault("max_loss", 40)
        c.setdefault("max_latency", 0)
        return c

    def validate(self, c: dict) -> dict:
        wans = c.get("wans") or []
        if len(wans) > MAX_WANS:
            raise ValueError(f"Máximo {MAX_WANS} líneas")
        used_marks, ids = set(), set()
        for w in wans:
            w.setdefault("id", "w" + secrets.token_hex(3))
            if w["id"] in ids:
                raise ValueError("Líneas duplicadas")
            ids.add(w["id"])
            w["name"] = str(w.get("name") or w["id"])[:40]
            if not IFACE_RE.match(w.get("iface") or ""):
                raise ValueError(f"{w['name']}: interfaz inválida")
            gw = (w.get("gateway") or "auto").strip()
            if gw != "auto":
                ipaddress.IPv4Address(gw)
            w["gateway"] = gw
            w["weight"] = max(1, min(100, int(w.get("weight") or 1)))
            w["priority"] = max(1, min(99, int(w.get("priority") or 1)))
            w["enabled"] = bool(w.get("enabled", True))
            for k in ("down_mbit", "up_mbit"):
                w[k] = max(0.0, float(w.get(k) or 0))
            m = int(w.get("mark") or 0)
            if not (MARK_BASE < m <= MARK_BASE + 50) or m in used_marks:
                m = next(x for x in range(MARK_BASE + 1, MARK_BASE + 51) if x not in used_marks and
                         x not in {int(o.get("mark") or 0) for o in wans if o is not w})
            w["mark"] = m
            used_marks.add(m)
        c.setdefault("mode", "failover")
        c.setdefault("balance_by", "device")
        c.setdefault("smart_gaming", True)
        if c.get("mode") not in ("failover", "balance"):
            raise ValueError("Modo inválido")
        if c.get("balance_by") not in ("device", "connection"):
            raise ValueError("Reparto inválido")
        c["device_rules"] = {str(k).lower(): v for k, v in (c.get("device_rules") or {}).items()
                             if v in ids and MAC_RE.match(str(k).lower())}  # la MAC va al firewall: sólo formato válido
        c["targets"] = [t for t in (c.get("targets") or []) if re.match(r"^[0-9.]{7,15}$", str(t))][:5] or ["1.1.1.1", "8.8.8.8"]
        c["interval"] = max(1, min(30, int(c.get("interval") or 2)))
        c["fail_after"] = max(2, min(20, int(c.get("fail_after") or 3)))
        c["recover_after"] = max(2, min(60, int(c.get("recover_after") or 6)))
        c["max_loss"] = max(5, min(100, int(c.get("max_loss") or 40)))
        c["max_latency"] = max(0, min(5000, int(c.get("max_latency") or 0)))
        return c

    def wans(self) -> list[dict]:
        return [w for w in self.cfg()["wans"] if w.get("enabled", True)]

    @property
    def active(self) -> bool:
        c = self.cfg()
        rc = self.ctx.router.cfg()
        return bool(c.get("enabled") and rc.get("enabled") and len([w for w in c["wans"] if w.get("enabled", True)]) >= 1)

    def is_up(self, wid: str) -> bool:
        if self.forced_down.get(wid, 0) > time.time():
            return False
        return self.state.get(wid, {}).get("up", True) is not False

    def up_wans(self) -> list[dict]:
        return [w for w in self.wans() if self.is_up(w["id"])]

    def primary(self) -> dict | None:
        ups = sorted(self.up_wans(), key=lambda w: w.get("priority", 1))
        return ups[0] if ups else None

    def gateway_of(self, w: dict) -> str | None:
        gw = w.get("gateway") or "auto"
        if gw != "auto":
            return gw
        st = self.state.get(w["id"], {})
        if st.get("gw"):
            return st["gw"]
        return self._detect_gw(w["iface"])

    def _detect_gw(self, iface: str) -> str | None:
        """Puerta de enlace de una interfaz (ruta por defecto de DHCP o del módem USB)."""
        data = netinfo.run_json(["ip", "-j", "-4", "route", "show", "default", "dev", iface]) if shutil.which("ip") else None
        for r in data or []:
            if r.get("gateway"):
                return r["gateway"]
        if self.orig_default and self.orig_default.get("dev") == iface:
            return self.orig_default.get("gw")
        gw = netinfo.default_gateway()
        main = self._main_default()
        if main and main.get("dev") == iface:
            return main.get("gw") or gw
        return None

    @staticmethod
    def _main_default() -> dict | None:
        data = netinfo.run_json(["ip", "-j", "-4", "route", "show", "default", "table", "main"]) if shutil.which("ip") else None
        for r in data or []:
            if r.get("gateway"):
                return {"gw": r["gateway"], "dev": r.get("dev")}
        return None

    # ------------------------------------------------------------------ reglas nftables
    def nft_lines(self, lan_ifs: list[str], lan_net: str | None, consoles: list[str]) -> list[str]:
        """Cadena de selección de línea (se inserta en la tabla «inet bipon» del router)."""
        if not self.active or not lan_ifs:
            return []
        c = self.cfg()
        wans = self.wans()
        ups = self.up_wans()
        if not ups:
            return []
        marks = {w["id"]: int(w["mark"]) for w in wans}
        ifs = "{ " + ", ".join(f'"{i}"' for i in dict.fromkeys(lan_ifs)) + " }"
        src = f"ip saddr {lan_net} " if lan_net else ""
        L = []
        L.append(f"  set mwan_game_udp {{ type inet_service; flags interval; auto-merge; elements = {{ {', '.join(dict.fromkeys(GAME_UDP + VOICE_UDP))} }} }}")
        if consoles:
            L.append(f"  set mwan_consoles {{ type ether_addr; elements = {{ {', '.join(consoles)} }} }}")
        L.append("  chain mwan_pre {")
        L.append("    type filter hook prerouting priority mangle; policy accept;")
        L.append(f"    iifname {ifs} {src}ip daddr != {PRIVATE} jump mwan_sel")
        L.append("  }")
        L.append("  chain mwan_sel {")
        up_marks = {marks[w["id"]] for w in ups}
        # conexión ya asignada a una línea que sigue activa: se respeta (no cambia de IP a mitad de sesión)
        L.append(f"    ct mark {{ {', '.join(str(m) for m in sorted(up_marks))} }} meta mark set ct mark accept")
        for mac, wid in sorted((c.get("device_rules") or {}).items()):
            if wid in marks and self.is_up(wid):
                L.append(f"    ether saddr {mac} meta mark set {marks[wid]} ct mark set meta mark accept comment \"regla de dispositivo\"")
        if c.get("smart_gaming") and len(ups) > 1:
            best = next((w for w in ups if w["id"] == self.best), None) or self._lowest_latency(ups)
            if best:
                m = marks[best["id"]]
                L.append(f"    udp dport @mwan_game_udp meta mark set {m} ct mark set meta mark accept comment \"juegos y llamadas: línea con menos ping\"")
                if consoles:
                    L.append(f"    ether saddr @mwan_consoles meta mark set {m} ct mark set meta mark accept comment \"consolas\"")
        if c.get("mode") == "balance" and len(ups) > 1:
            if c.get("balance_by") == "device":
                assign = self.device_assignment(ups)
                if assign:
                    elems = ", ".join(f"{ip} : {m}" for ip, m in sorted(assign.items()))
                    L.append(f"    meta mark set ip saddr map {{ {elems} }} ct mark set meta mark accept comment \"reparto por consumo\"")
            ranges = ", ".join(f"{a}-{b} : {m}" if a != b else f"{a} : {m}" for a, b, m in weight_map(wans, {w["id"]: {"up": self.is_up(w["id"])} for w in wans}))
            sel = "jhash ip saddr mod 100" if c.get("balance_by") == "device" else "numgen random mod 100"
            L.append(f"    meta mark set {sel} map {{ {ranges} }}")
        else:
            L.append(f"    meta mark set {marks[self.primary()['id']]}")
        L.append("    ct mark set meta mark")
        L.append("  }")
        return L

    def device_assignment(self, ups: list[dict]) -> dict[str, int]:
        """Reparte los equipos conocidos entre las líneas activas según su consumo (24 h) y el peso de cada línea:
        el que más gasta va a la línea con más hueco. Estable: el orden sólo depende del consumo y de la MAC."""
        devs = self.ctx.devices
        known = {m: d["ip"] for m, d in devs.devices.items() if d.get("ip") and ":" not in d["ip"] and not m.startswith("ip:")
                 and d["ip"] not in (devs.self_ip, devs.gateway)}
        if not known:
            return {}
        try:
            usage = {r["mac"]: (r.get("rx") or 0) + (r.get("tx") or 0) for r in self.ctx.router.usage(24)}
        except Exception:
            usage = {}
        rules = self.cfg().get("device_rules") or {}
        load = {w["id"]: 0.0 for w in ups}
        weight = {w["id"]: float(max(1, int(w.get("weight") or 1))) for w in ups}
        out: dict[str, int] = {}
        for mac in sorted(known, key=lambda m: (-usage.get(m, 0), m)):
            if mac in rules:
                continue
            wid = min(load, key=lambda k: ((load[k] + max(1, usage.get(mac, 0))) / weight[k], k))
            load[wid] += max(1, usage.get(mac, 0))
            out[known[mac]] = int(next(w["mark"] for w in ups if w["id"] == wid))
        return out

    def wan_ifaces(self) -> list[str]:
        return [w["iface"] for w in self.wans()] if self.active else []

    # ------------------------------------------------------------------ rutas
    async def sync_routes(self) -> list[str]:
        """Tablas y reglas de enrutamiento por línea + ruta principal del propio Bipon."""
        notes: list[str] = []
        if not shutil.which("ip"):
            return ["Falta iproute2"]
        if not self.active:
            await self.teardown()
            return notes
        if self.orig_default is None:
            self.orig_default = self._main_default()
        _write("/proc/sys/net/ipv4/conf/all/rp_filter", "2")
        _write("/proc/sys/net/netfilter/nf_conntrack_acct", "1")
        for w in self.wans():
            m = int(w["mark"])
            _write(f"/proc/sys/net/ipv4/conf/{w['iface']}/rp_filter", "2")
            gw = self.gateway_of(w)
            if not gw:
                notes.append(f"{w['name']}: no se encontró su puerta de enlace (indícala a mano)")
                continue
            self._st(w["id"])["gw"] = gw
            code, out = await _run("ip", "route", "replace", "default", "via", gw, "dev", w["iface"], "table", str(m))
            if code != 0:
                code, out = await _run("ip", "route", "replace", "default", "via", gw, "dev", w["iface"], "table", str(m), "onlink")
            if code != 0:
                notes.append(f"{w['name']}: {out.strip()[:120]}")
            rules = await _run("ip", "rule", "show", "fwmark", f"{m}/0xff")
            if f"lookup {m}" not in rules[1]:
                await _run("ip", "rule", "add", "fwmark", f"{m}/0xff", "lookup", str(m), "priority", str(1000 + m))
        await self._sync_main_route(notes)
        return notes

    async def _sync_main_route(self, notes: list[str]) -> None:
        """La ruta del propio Bipon sigue a la línea principal activa."""
        p = self.primary()
        if not p:
            return
        gw = self.gateway_of(p)
        cur = self._main_default()
        if gw and (not cur or cur.get("gw") != gw or cur.get("dev") != p["iface"]):
            code, out = await _run("ip", "route", "replace", "default", "via", gw, "dev", p["iface"])
            if code != 0:
                notes.append(f"ruta principal: {out.strip()[:120]}")
        self.main_wan = p["id"]

    async def teardown(self) -> None:
        for m in range(MARK_BASE + 1, MARK_BASE + 51):
            code, out = await _run("ip", "rule", "show", "fwmark", f"{m}/0xff")
            if code == 0 and out.strip():
                await _run("ip", "rule", "del", "fwmark", f"{m}/0xff", "lookup", str(m))
                await _run("ip", "route", "flush", "table", str(m))
        if self.orig_default and self.orig_default.get("gw"):
            cur = self._main_default()
            if cur != self.orig_default:
                await _run("ip", "route", "replace", "default", "via", self.orig_default["gw"], "dev", self.orig_default["dev"])
        self.orig_default = None
        self.main_wan = None

    # ------------------------------------------------------------------ salud
    def _lowest_latency(self, ups: list[dict]) -> dict | None:
        scored = [(self.state.get(w["id"], {}).get("latency"), w.get("priority", 1), i, w) for i, w in enumerate(ups)]
        known = [x for x in scored if x[0] is not None]
        return min(known)[3] if known else (min(scored)[3] if scored else None)

    def _st(self, wid: str) -> dict:
        st = self.state.setdefault(wid, {})
        for k, v in (("up", None), ("fails", 0), ("oks", 0), ("since", time.time()), ("changes", 0)):
            st.setdefault(k, v)
        if "samples" not in st:
            st["samples"] = deque(maxlen=30)
        return st

    def _stats(self, wid: str) -> dict:
        st = self._st(wid)
        samples = [s for _, s in list(st["samples"])[-10:]]  # ventana corta: refleja el estado actual
        got = [s for s in samples if s is not None]
        st["loss"] = round(100 * (len(samples) - len(got)) / len(samples), 1) if samples else None
        st["latency"] = round(statistics.median(got[-10:]), 1) if got else None
        st["jitter"] = round(statistics.fmean(abs(a - b) for a, b in zip(got[-10:], got[-9:])), 1) if len(got) > 2 else None
        return st

    async def probe(self, w: dict, target: str) -> float | None:
        if not w.get("iface") or open_state(w["iface"]) == "down":
            return None
        r = await ping(target, 1, timeout=1.0, mark=int(w["mark"]))
        if r.get("received"):
            return r.get("avg")
        # un destino que no responde no tumba la línea: se confirma con otro antes de contar el fallo
        others = [t for t in self.cfg()["targets"] if t != target]
        if others:
            r = await ping(others[int(time.time()) % len(others)], 1, timeout=1.0, mark=int(w["mark"]))
            if r.get("received"):
                return r.get("avg")
        return None

    def _decide(self, w: dict, rtt: float | None, c: dict) -> str | None:
        """Actualiza la máquina de estados de una línea. Devuelve 'down'/'up' si cambió."""
        st = self._st(w["id"])
        st["samples"].append((time.time(), rtt))
        st["checked"] = time.time()
        bad = rtt is None or (c["max_latency"] and rtt > c["max_latency"])
        st["fails"] = st["fails"] + 1 if bad else 0
        st["oks"] = 0 if bad else st["oks"] + 1
        self._stats(w["id"])
        lossy = st["loss"] is not None and len(st["samples"]) >= 10 and st["loss"] >= c["max_loss"] and st["fails"] > 0
        if st["up"] is not False and (st["fails"] >= c["fail_after"] or lossy):
            st["up"], st["since"], st["changes"] = False, time.time(), st["changes"] + 1
            st["reason"] = "sin respuesta" if st["fails"] >= c["fail_after"] else f"pérdida {st['loss']}%"
            st.setdefault("flaps", []).append(time.time())
            return "down"
        if st["up"] is None and st["oks"] >= 2:
            st["up"] = True
        # línea inestable (cae y vuelve a menudo): debe demostrar estabilidad el triple de tiempo antes de recibir tráfico
        flaps = [t for t in st.setdefault("flaps", []) if time.time() - t < 600]
        st["flaps"] = flaps
        need = c["recover_after"] * (3 if len(flaps) >= 4 else 1)
        if st["up"] is False and st["oks"] >= need and not lossy:
            st["flaps"].append(time.time())
            st["up"], st["since"], st["changes"] = True, time.time(), st["changes"] + 1
            st["reason"] = ""
            return "up"
        return None

    def _pick_best(self) -> bool:
        """Línea con menos ping para juegos: sólo cambia si otra es claramente mejor varias veces seguidas."""
        ups = self.up_wans()
        cands = [(self._stats(w["id"]).get("latency"), w["id"]) for w in ups]
        cands = [x for x in cands if x[0] is not None]
        if not cands:
            return False
        lat, wid = min(cands)
        if self.best not in {w["id"] for w in ups}:
            self.best, self._best_votes = wid, {}
            return True
        cur = self._stats(self.best).get("latency")
        if wid != self.best and cur is not None and lat < cur * 0.85 and cur - lat >= 3:
            self._best_votes[wid] = self._best_votes.get(wid, 0) + 1
            if self._best_votes[wid] >= 5:
                self.best, self._best_votes = wid, {}
                return True
        else:
            self._best_votes = {}
        return False

    async def apply(self) -> dict:
        """Recarga reglas + rutas (llamar tras cambiar la configuración o al caer/volver una línea)."""
        notes = await self.sync_routes()
        r = self.ctx.router
        if r.cfg().get("enabled") and r.available and not r.pending:
            res = await r.apply(r.cfg(), confirm_required=False)
            if not res.get("ok"):
                notes.append(res.get("message", ""))
        return {"ok": not notes, "notes": notes}

    async def _flush(self, mark: int) -> None:
        if shutil.which("conntrack"):
            await _run("conntrack", "-D", "-m", str(mark))

    def _event(self, w: dict, kind: str) -> None:
        st = self.state.get(w["id"], {})
        now_main = self.primary()
        msg = (f"{w['name']} caída ({st.get('reason', '')}). Tráfico movido a {now_main['name']}." if kind == "down" and now_main else
               f"{w['name']} caída y no queda ninguna línea activa." if kind == "down" else
               f"{w['name']} recuperada (ping {st.get('latency')} ms).")
        self.history.appendleft({"ts": time.time(), "wan": w["id"], "name": w["name"], "kind": kind, "message": msg})
        self.ctx.db.event("warning" if kind == "down" else "info", "multi-wan", msg)
        self.ctx.db.execute("INSERT INTO wan_events(ts,wan,kind,message) VALUES (?,?,?,?)", (time.time(), w["id"], kind, msg))
        try:
            self.ctx.notify.emit("wan_down" if kind == "down" else "wan_up", "Línea de Internet " + ("caída" if kind == "down" else "recuperada"), msg, key=w["id"])
        except Exception:  # pragma: no cover
            pass

    async def loop(self) -> None:
        await asyncio.sleep(20)
        tick = 0
        while True:
            c = self.cfg()
            try:
                if self.active:
                    if tick == 0 or tick % 60 == 0:
                        await self.sync_routes()
                    wans = self.wans()
                    target = c["targets"][tick % len(c["targets"])]
                    rtts = await asyncio.gather(*(self.probe(w, target) for w in wans), return_exceptions=True)
                    changed = False
                    for w, rtt in zip(wans, rtts):
                        if isinstance(rtt, Exception):
                            rtt = None
                        ev = self._decide(w, rtt, c)
                        if ev:
                            changed = True
                            if ev == "down":
                                await self._flush(int(w["mark"]))
                            self._event(w, ev)
                    for wid, until in list(self.forced_down.items()):
                        if until <= time.time():
                            self.forced_down.pop(wid)
                            changed = True
                    if c.get("smart_gaming") and self._pick_best() and time.time() - self._last_change > 30:
                        changed = True
                    if changed or self._resync.is_set():
                        self._resync.clear()
                        self._last_change = time.time()
                        await self.apply()
                elif self.main_wan or self.orig_default:
                    await self.teardown()
            except Exception as exc:  # pragma: no cover
                log.warning("multi-wan: %s", exc)
            tick += 1
            await asyncio.sleep(c.get("interval", 2))

    async def simulate_down(self, wid: str, seconds: int = 60) -> dict:
        w = next((x for x in self.wans() if x["id"] == wid), None)
        if not w:
            raise ValueError("Línea desconocida")
        self.forced_down[wid] = time.time() + max(10, min(600, seconds))
        await self._flush(int(w["mark"]))
        await self.apply()
        self.history.appendleft({"ts": time.time(), "wan": wid, "name": w["name"], "kind": "test",
                                 "message": f"Prueba: {w['name']} desactivada {seconds} s"})
        return {"ok": True, "until": self.forced_down[wid]}

    # ------------------------------------------------------------------ estado
    def status(self) -> dict:
        c = self.cfg()
        out = []
        for w in c["wans"]:
            st = self._stats(w["id"]) if w["id"] in self.state else {}
            out.append({**w, "up": self.is_up(w["id"]) if w.get("enabled", True) else None, "checked": st.get("checked"),
                        "latency": st.get("latency"), "loss": st.get("loss"), "jitter": st.get("jitter"),
                        "since": st.get("since"), "reason": st.get("reason", ""), "changes": st.get("changes", 0),
                        "gw_detected": st.get("gw"), "forced": self.forced_down.get(w["id"], 0) > time.time(),
                        "spark": [s for _, s in list(st.get("samples", []))[-30:]],
                        "is_primary": bool(self.primary() and self.primary()["id"] == w["id"]),
                        "is_best": w["id"] == self.best})
        rc = self.ctx.router.cfg()
        return {"config": c, "wans": out, "active": self.active, "router_enabled": bool(rc.get("enabled")),
                "lan": rc.get("lan"), "interfaces": netinfo.interfaces(), "all_ifaces": all_ifaces(),
                "history": list(self.history)[:50], "game_ports": GAME_UDP, "voice_ports": VOICE_UDP}

    def uptime(self, days: int = 7) -> list[dict]:
        """Caídas por línea en los últimos días (para el informe semanal)."""
        since = time.time() - days * 86400
        rows = self.ctx.db.query("SELECT wan, kind, ts FROM wan_events WHERE ts>=? ORDER BY ts", (since,))
        names = {w["id"]: w["name"] for w in self.cfg()["wans"]}
        out = {}
        for r in rows:
            o = out.setdefault(r["wan"], {"wan": r["wan"], "name": names.get(r["wan"], r["wan"]), "outages": 0, "down_s": 0.0, "_t": None})
            if r["kind"] == "down":
                o["outages"] += 1
                o["_t"] = r["ts"]
            elif r["kind"] == "up" and o["_t"]:
                o["down_s"] += r["ts"] - o["_t"]
                o["_t"] = None
        for o in out.values():
            if o["_t"]:
                o["down_s"] += time.time() - o["_t"]
            o.pop("_t")
            o["down_s"] = round(o["down_s"])
        return list(out.values())


def open_state(iface: str) -> str:
    try:
        return open(f"/sys/class/net/{iface}/operstate").read().strip()
    except OSError:
        return "unknown"


def all_ifaces() -> list[dict]:
    """Todas las interfaces (también las que aún no tienen IP, p. ej. un módem USB recién conectado)."""
    data = netinfo.run_json(["ip", "-j", "addr", "show"]) if shutil.which("ip") else None
    out = []
    for itf in data or []:
        name = itf.get("ifname", "")
        if name == "lo" or name.startswith(("veth", "docker", "br-", "ifb", "wg", "tun", "tailscale")):
            continue
        ip4 = next((f"{a['local']}/{a['prefixlen']}" for a in itf.get("addr_info", []) if a.get("family") == "inet"), "")
        out.append({"name": name, "ip": ip4, "state": (itf.get("operstate") or "").lower(), "mac": itf.get("address", "")})
    return out


def dumps(o) -> str:  # pragma: no cover - útil para depurar
    return json.dumps(o, default=list)
