"""Anti-lag con la red cargada: SQM (CAKE / fq_codel), límites por dispositivo y prioridad DSCP/WMM.

Por qué baja el ping: el router del proveedor tiene colas enormes; cuando alguien descarga, los paquetes del
juego esperan detrás (bufferbloat: +50…500 ms). Bipon limita el tráfico a ~90 % de la velocidad real, así la cola
se forma en Bipon, donde CAKE la gestiona con colas por conexión y por equipo y prioriza lo interactivo.

Topologías
* Un solo cable (gateway de un brazo, WAN = LAN): por la misma interfaz salen la bajada (hacia los equipos) y la
  subida (hacia el router). Se separan con HTB: clase de bajada (destino = red local) y clase de subida (resto),
  cada una con CAKE. Con multi-WAN la subida se divide por la marca de cada línea.
* Dos interfaces: bajada en la salida LAN, subida en la salida de cada WAN.
Los límites por dispositivo son clases hijas de la bajada (y de ifb0 para la subida, sólo si hay alguno).

Prioridad (DSCP), en este orden: 1) juegos y consolas (CS4) y llamadas (EF) → bandeja «Voice» de CAKE y WMM vídeo/voz;
2) streaming (AF41, reconocido por su DNS) → bandeja «Video»; 3) navegación → normal; 4) descargas grandes,
actualizaciones, copias en la nube y torrents (CS1) → bandeja «Bulk», que sólo usa lo que sobra.
El punto de acceso Wi-Fi (WMM) y CAKE (diffserv4) usan esas marcas para dar prioridad en el aire y en la cola.
"""
from __future__ import annotations

import asyncio
import logging
import shutil
import time
from pathlib import Path

from dnslib import QTYPE, DNSRecord

log = logging.getLogger("bipon.qos")

_cake: bool | None = None


async def _run(*args, timeout: float = 15) -> tuple[int, str]:
    p = await asyncio.create_subprocess_exec(*args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
    try:
        out, _ = await asyncio.wait_for(p.communicate(), timeout)
    except asyncio.TimeoutError:
        p.kill()
        return 124, "tiempo agotado"
    return p.returncode or 0, out.decode(errors="ignore")


async def has_cake() -> bool:
    """¿El núcleo tiene sch_cake? (en un LXC depende del anfitrión: create-lxc.sh lo carga)."""
    global _cake
    if _cake is None:
        if not shutil.which("tc"):
            _cake = False
        elif Path("/sys/module/sch_cake").exists():
            _cake = True
        else:
            code, _ = await _run("ip", "link", "add", "bpcake0", "type", "dummy")
            if code == 0:
                code, _ = await _run("tc", "qdisc", "add", "dev", "bpcake0", "root", "cake")
                await _run("ip", "link", "del", "bpcake0")
            _cake = code == 0
    return _cake


LEAVES = ("cake", "fq_codel", "sfq", "pfifo")


def _leaf(kind: str, direction: str) -> list[str]:
    if kind == "cake":
        return ["cake", "unlimited", "diffserv4", "dual-dsthost" if direction == "down" else "dual-srchost", "nat"]
    if kind == "sfq":
        return ["sfq", "perturb", "10"]
    return [kind]


def cfg(ctx) -> dict:
    c = dict(ctx.settings.get("sqm") or {})
    c.setdefault("enabled", False)
    c.setdefault("auto", True)          # velocidades tomadas del último test (× factor)
    c.setdefault("down_mbit", 0.0)
    c.setdefault("up_mbit", 0.0)
    c.setdefault("factor", 0.9)
    c.setdefault("dscp", True)          # prioridad para juegos y llamadas
    c.setdefault("interactive_heuristic", True)
    c.setdefault("bulk_mb", 20)         # conexiones de más de N MB → baja prioridad
    c.setdefault("last_tune", None)
    return c


def rates(ctx, c: dict | None = None) -> tuple[float, float, str]:
    """(bajada, subida, origen) en Mbit/s para SQM."""
    c = c or cfg(ctx)
    if not c.get("auto") and c.get("down_mbit") and c.get("up_mbit"):
        return float(c["down_mbit"]), float(c["up_mbit"]), "manual"
    row = ctx.db.one("SELECT download, upload FROM speedtests WHERE download>0 AND upload>0 ORDER BY ts DESC LIMIT 1")
    f = max(0.5, min(1.0, float(c.get("factor") or 0.9)))
    if row:
        return round(row["download"] * f, 1), round(row["upload"] * f, 1), "test de velocidad"
    if c.get("down_mbit") and c.get("up_mbit"):
        return float(c["down_mbit"]), float(c["up_mbit"]), "manual"
    return 0.0, 0.0, ""


def _quantum(mbit: float) -> str:
    return str(int(max(1514, min(60000, mbit * 125000 / 1000))))


def plan(*, lan: str, lan_net: str | None, wan_ifs: list[str], down: float, up: float, sqm: bool, cake: bool | str,
         limits: list[tuple[str, float, float]], wan_ups: list[tuple[int, float]] | None = None,
         wan_rates: dict[str, float] | None = None) -> dict:
    """Comandos tc (función pura, probada en los tests).
    limits: [(ip, bajada, subida)] por dispositivo; wan_ups: [(marca, subida)] por línea del multi-WAN."""
    one_arm = lan in wan_ifs or not wan_ifs
    line_down = down if sqm and down else 1000.0 * 10
    line_up = up if sqm and up else 1000.0 * 10
    kind = cake if isinstance(cake, str) else ("cake" if cake else "fq_codel")
    if kind == "cake" and not sqm:
        kind = "fq_codel"
    leaf_down, leaf_up = _leaf(kind, "down"), _leaf(kind, "up")
    small = _leaf("fq_codel" if kind == "cake" else kind, "down")  # hojas de los límites por dispositivo
    eg: list[list[str]] = []
    default = "29" if one_arm else "19"
    eg.append(["qdisc", "replace", "dev", lan, "root", "handle", "1:", "htb", "default", default])
    # bajada (hacia los equipos de casa)
    eg.append(["class", "replace", "dev", lan, "parent", "1:", "classid", "1:10", "htb", "rate", f"{line_down}mbit", "ceil", f"{line_down}mbit",
               "quantum", _quantum(line_down)])
    eg.append(["class", "replace", "dev", lan, "parent", "1:10", "classid", "1:19", "htb", "rate", f"{max(1.0, line_down * 0.5)}mbit",
               "ceil", f"{line_down}mbit", "prio", "1", "quantum", _quantum(line_down)])
    eg.append(["qdisc", "replace", "dev", lan, "parent", "1:19", "handle", "19:", *leaf_down])
    up_limited = []
    for i, (ip, d, u) in enumerate(limits):
        if u:
            up_limited.append((ip, u))
        if not d:
            continue
        cid = f"1:{100 + i}"
        eg.append(["class", "replace", "dev", lan, "parent", "1:10", "classid", cid, "htb", "rate", f"{min(d, line_down)}mbit",
                   "ceil", f"{min(d, line_down)}mbit", "prio", "2", "quantum", _quantum(d)])
        eg.append(["qdisc", "replace", "dev", lan, "parent", cid, *small])
        eg.append(["filter", "add", "dev", lan, "parent", "1:", "protocol", "ip", "prio", "1", "u32", "match", "ip", "dst", f"{ip}/32", "flowid", cid])
    wan_root: list[list[str]] = []
    if one_arm:
        # subida (hacia el router del proveedor) por la misma interfaz
        eg.append(["filter", "add", "dev", lan, "parent", "1:", "protocol", "ip", "prio", "5", "u32", "match", "ip", "dst", lan_net or "0.0.0.0/0",
                   "flowid", "1:19"])
        eg.append(["class", "replace", "dev", lan, "parent", "1:", "classid", "1:20", "htb", "rate", f"{line_up}mbit", "ceil", f"{line_up}mbit",
                   "quantum", _quantum(line_up)])
        eg.append(["class", "replace", "dev", lan, "parent", "1:20", "classid", "1:29", "htb", "rate", f"{max(1.0, line_up * 0.5)}mbit",
                   "ceil", f"{line_up}mbit", "quantum", _quantum(line_up)])
        eg.append(["qdisc", "replace", "dev", lan, "parent", "1:29", "handle", "29:", *leaf_up])
        for k, (mark, u) in enumerate(wan_ups or []):
            if not (sqm and u):
                continue
            cid = f"1:{21 + k}"
            eg.append(["class", "replace", "dev", lan, "parent", "1:20", "classid", cid, "htb", "rate", f"{min(u, line_up)}mbit",
                       "ceil", f"{min(u, line_up)}mbit", "quantum", _quantum(u)])
            eg.append(["qdisc", "replace", "dev", lan, "parent", cid, "handle", f"{21 + k}:", *leaf_up])
            eg.append(["filter", "add", "dev", lan, "parent", "1:", "protocol", "ip", "prio", "3", "u32", "match", "mark", str(mark), "0xff",
                       "flowid", cid])
    elif sqm:
        for w in dict.fromkeys(wan_ifs):
            rate = (wan_rates or {}).get(w) or up
            if kind == "cake":
                wan_root.append(["qdisc", "replace", "dev", w, "root", "cake", "bandwidth", f"{rate}mbit", "diffserv4", "dual-srchost", "nat"])
            else:
                wan_root.append(["qdisc", "replace", "dev", w, "root", "handle", "1:", "htb", "default", "1"])
                wan_root.append(["class", "replace", "dev", w, "parent", "1:", "classid", "1:1", "htb", "rate", f"{rate}mbit", "ceil", f"{rate}mbit"])
                wan_root.append(["qdisc", "replace", "dev", w, "parent", "1:1", *leaf_up])
    ifb: list[list[str]] = []
    if up_limited:
        ifb.append(["qdisc", "replace", "dev", "ifb0", "root", "handle", "1:", "htb", "default", "9999"])
        ifb.append(["class", "replace", "dev", "ifb0", "parent", "1:", "classid", "1:9999", "htb", "rate", "10000mbit"])
        ifb.append(["qdisc", "replace", "dev", "ifb0", "parent", "1:9999", *small])
        for i, (ip, u) in enumerate(up_limited):
            cid = f"1:{100 + i}"
            ifb.append(["class", "replace", "dev", "ifb0", "parent", "1:", "classid", cid, "htb", "rate", f"{u}mbit", "ceil", f"{u}mbit"])
            ifb.append(["qdisc", "replace", "dev", "ifb0", "parent", cid, *small])
            ifb.append(["filter", "add", "dev", "ifb0", "parent", "1:", "protocol", "ip", "prio", "1", "u32", "match", "ip", "src", f"{ip}/32",
                        "flowid", cid])
    return {"egress": eg, "wan": wan_root, "ifb": ifb, "one_arm": one_arm, "leaf": kind}


async def clear(lan: str | None, wan_ifs: list[str]) -> None:
    for dev in dict.fromkeys([d for d in [lan, *wan_ifs] if d]):
        await _run("tc", "qdisc", "del", "dev", dev, "root")
        await _run("tc", "qdisc", "del", "dev", dev, "ingress")
    await _run("ip", "link", "del", "ifb0")


async def apply(ctx) -> dict:
    """Aplica SQM + límites por dispositivo según la configuración. Única dueña de tc en Bipon."""
    r = ctx.router
    rc = r.cfg()
    sh = rc.get("shaping") or {}
    c = cfg(ctx)
    lan = sh.get("iface") or rc.get("lan")
    mw = getattr(ctx, "multiwan", None)
    wan_ifs = list(dict.fromkeys(([rc["wan"]] if rc.get("wan") else []) + (mw.wan_ifaces() if mw else [])))
    if not shutil.which("tc"):
        return {"ok": False, "message": "tc (iproute2) no está instalado"}
    if not lan:
        return {"ok": False, "message": "Indica la interfaz LAN en Router y firewall"}
    sqm = bool(c.get("enabled") and rc.get("enabled"))
    limits = [(d["ip"], float(d.get("limit_down") or 0), float(d.get("limit_up") or 0))
              for m, d in ctx.devices.devices.items() if sh.get("enabled") and d.get("ip") and (d.get("limit_down") or d.get("limit_up"))]
    await clear(lan, wan_ifs)
    r.shaped = {}
    if not sqm and not limits:
        return {"ok": True, "message": "Sin colas: tráfico sin limitar", "active": False}
    down, up, src = rates(ctx, c)
    if sqm and not (down and up):
        return {"ok": False, "message": "Haz un test de velocidad (o indica las velocidades) para activar el anti-lag"}
    cake = await has_cake()
    f = float(c.get("factor") or 0.9)
    mws = mw.wans() if mw and mw.active else []
    wan_rates = {w["iface"]: round(float(w["up_mbit"]) * f, 1) for w in mws if w.get("up_mbit")}
    net = r._iface_net(lan)
    wan_ups = [(int(w["mark"]), round(float(w.get("up_mbit") or 0) * f, 1)) for w in mws]
    errors: list[str] = []
    ifb_ok = True
    if any(u for _, _, u in limits):
        code, out = await _run("ip", "link", "add", "ifb0", "type", "ifb")
        if code != 0 and "exists" not in out:
            errors.append(f"ifb0: {out.strip()} (¿módulo ifb disponible?)")
            ifb_ok = False
        else:
            await _run("ip", "link", "set", "ifb0", "up")
            await _run("tc", "qdisc", "add", "dev", lan, "handle", "ffff:", "ingress")
            await _run("tc", "filter", "add", "dev", lan, "parent", "ffff:", "protocol", "ip", "u32", "match", "u32", "0", "0",
                       "action", "mirred", "egress", "redirect", "dev", "ifb0")
    # se prueba la mejor disciplina de cola disponible en este núcleo: CAKE → fq_codel → sfq → pfifo
    kinds = [k for k in LEAVES if k != "cake" or cake]
    used = None
    for kind in kinds:
        p = plan(lan=lan, lan_net=net, wan_ifs=wan_ifs, down=down, up=up, sqm=sqm, cake=kind, limits=limits, wan_ups=wan_ups, wan_rates=wan_rates)
        errs = []
        for cmd in p["egress"] + p["wan"] + (p["ifb"] if ifb_ok else []):
            code, out = await _run("tc", *cmd)
            if code != 0:
                errs.append(f"tc {' '.join(cmd[:5])}: {out.strip()[:140]}")
        if any("kind is unknown" in e or "Unknown qdisc" in e for e in errs) and kind != kinds[-1]:
            for dev in dict.fromkeys([lan, *wan_ifs, "ifb0"]):
                await _run("tc", "qdisc", "del", "dev", dev, "root")
            continue
        used = p
        errors += errs
        break
    for ip, d, u in limits:
        mac = next((m for m, x in ctx.devices.devices.items() if x.get("ip") == ip), ip)
        r.shaped[mac] = {"limit_down": d, "limit_up": u}
    leaf = (used or {}).get("leaf", "?")
    names = {"cake": "CAKE", "fq_codel": "fq_codel", "sfq": "SFQ", "pfifo": "FIFO (el núcleo no tiene CAKE ni fq_codel)"}
    msg = f"Anti-lag activo: {down} / {up} Mbit/s ({names.get(leaf, leaf)}, {src})" if sqm else "Límites por dispositivo aplicados"
    if errors:
        r.last_error = "; ".join(errors[:3])
        msg = r.last_error
    return {"ok": not errors, "message": msg, "active": True, "cake": leaf == "cake", "leaf": leaf, "down": down, "up": up,
            "source": src, "one_arm": (used or {}).get("one_arm")}


async def autotune(ctx) -> dict:
    """Mide la línea sin colas (test de velocidad), fija ~90 % y vuelve a aplicar."""
    rc = ctx.router.cfg()
    mw = getattr(ctx, "multiwan", None)
    await clear(rc.get("lan"), [rc["wan"]] if rc.get("wan") else (mw.wan_ifaces() if mw else []))
    res = await ctx.scheduler.run_speedtest()
    if not res:
        await apply(ctx)
        return {"ok": False, "message": "No se pudo medir la velocidad (¿hay otro test en marcha?)"}
    c = cfg(ctx)
    f = float(c.get("factor") or 0.9)
    c.update({"auto": True, "down_mbit": round(res["download"] * f, 1), "up_mbit": round(res["upload"] * f, 1),
              "last_tune": {"ts": time.time(), "download": res["download"], "upload": res["upload"], "ping": res.get("ping"),
                            "loaded_ping": res.get("loaded_ping"), "bufferbloat": res.get("bufferbloat"), "grade": res.get("grade")}})
    ctx.settings.set("sqm", c, notify=False)
    out = await apply(ctx)
    return {**out, "measured": res}


def dscp_lines(ctx, consoles_mac: list[str], consoles_ip: list[str]) -> list[str]:
    """Cadena nftables de prioridad (forward)."""
    c = cfg(ctx)
    if not c.get("dscp"):
        return []
    from .multiwan import GAME_UDP, VOICE_UDP
    L = [f"  set qos_game {{ type inet_service; flags interval; auto-merge; elements = {{ {', '.join(GAME_UDP)} }} }}",
         f"  set qos_voice {{ type inet_service; flags interval; auto-merge; elements = {{ {', '.join(VOICE_UDP)} }} }}"]
    if consoles_mac:
        L.append(f"  set qos_console_mac {{ type ether_addr; elements = {{ {', '.join(consoles_mac)} }} }}")
    if consoles_ip:
        L.append(f"  set qos_console_ip {{ type ipv4_addr; elements = {{ {', '.join(consoles_ip)} }} }}")
    L.append("  set qos_stream { type ipv4_addr; flags timeout; timeout 3h; }")
    L.append("  set qos_call { type ipv4_addr; flags timeout; timeout 3h; }")
    L.append("  set qos_bulk { type ipv4_addr; flags timeout; timeout 3h; }")
    L.append("  chain qos_dscp {")
    L.append("    type filter hook forward priority mangle; policy accept;")
    L.append("    ct direction reply ip dscp != cs0 ip dscp set cs0 comment \"marcas de Internet: no se confía en ellas\"")
    bulk = max(1, int(c.get("bulk_mb") or 20)) * 1000000
    L.append(f"    ct bytes > {bulk} ip dscp set cs1 comment \"descargas grandes: no estorban\"")
    if c.get("interactive_heuristic"):
        L.append("    meta l4proto udp udp dport != { 53, 80, 443, 853 } udp sport != { 80, 443 } meta length < 300 ct bytes < 5000000 "
                 "ip dscp set cs4 comment \"paquetes UDP pequeños (juego)\"")
    L.append("    ip daddr @qos_bulk ip dscp set cs1")
    L.append("    ip saddr @qos_bulk ip dscp set cs1 comment \"descargas, actualizaciones, nube, torrents\"")
    L.append("    ip daddr @qos_stream ip dscp set af41")
    L.append("    ip saddr @qos_stream ip dscp set af41 comment \"streaming (por su DNS)\"")
    L.append("    ip daddr @qos_call ip dscp set ef")
    L.append("    ip saddr @qos_call ip dscp set ef comment \"llamadas (por su DNS)\"")
    L.append("    udp dport @qos_game ip dscp set cs4")
    L.append("    udp sport @qos_game ip dscp set cs4")
    if consoles_mac:
        L.append("    ether saddr @qos_console_mac meta l4proto udp ip dscp set cs4")
    if consoles_ip:
        L.append("    ip daddr @qos_console_ip meta l4proto udp ip dscp set cs4")
    L.append("    udp dport @qos_voice ip dscp set ef")
    L.append("    udp sport @qos_voice ip dscp set ef")
    L.append("  }")
    return L


# ---------------------------------------------------------------------------- clasificación por DNS
STREAM = ("googlevideo.com", "nflxvideo.net", "nflxso.net", "aiv-cdn.net", "pv-cdn.net", "dssott.com", "bamgrid.com", "hbomaxcdn.com",
          "max.com", "ttvnw.net", "twitchcdn.net", "jtvnw.net", "tiktokcdn.com", "tiktokv.com", "byteoversea.com", "cdninstagram.com",
          "fbcdn.net", "scdn.co", "spotifycdn.com", "vimeocdn.com", "hls.ttvnw.net", "hulustream.com", "paramountplus.com",
          "cbsivideo.com", "crunchyroll.com", "vrv.co", "dazn.com", "dazndn.com", "pluto.tv", "plex.direct", "music.apple.com",
          "aod.itunes.apple.com", "hls.apple.com", "deezer.com", "dzcdn.net", "kick.com", "live-video.net")
CALLS = ("discord.media", "zoom.us", "zoom.com", "zoomgov.com", "relay.teams.microsoft.com", "teams.microsoft.com", "skype.com",
         "whatsapp.net", "webex.com", "meet.google.com", "facetime.apple.com", "telegram.org", "signal.org")
BULK = ("steamcontent.com", "steamserver.net", "epicgames-download1.akamaized.net", "download.epicgames.com", "dl.playstation.net",
        "gst.prod.dl.playstation.net", "assets1.xboxlive.com", "xvcf1.xboxlive.com", "dlassets.xboxlive.com", "blzddist1-a.akamaihd.net",
        "riotcdn.net", "origin-a.akamaihd.net", "windowsupdate.com", "delivery.mp.microsoft.com", "update.microsoft.com",
        "swcdn.apple.com", "updates.cdn-apple.com", "mesu.apple.com", "android.clients.google.com", "dl.google.com", "gvt1.com",
        "icloud-content.com", "backup.icloud.com", "googleusercontent.com", "drive.google.com", "onedrive.live.com",
        "sharepoint.com", "dropboxusercontent.com", "mega.nz", "mega.co.nz", "backblazeb2.com", "nintendo.net")
_CAT: dict[str, str] = {**{d: "bulk" for d in BULK}, **{d: "stream" for d in STREAM}, **{d: "call" for d in CALLS}}


def category(name: str) -> str | None:
    parts = name.split(".")
    for i in range(len(parts) - 1):
        hit = _CAT.get(".".join(parts[i:]))
        if hit:
            return hit
    return None


class Classifier:
    """Mete en los conjuntos del firewall las IP que el DNS devuelve para streaming, llamadas y descargas."""

    def __init__(self, ctx):
        self.ctx = ctx
        self.pending: dict[str, set[str]] = {"stream": set(), "call": set(), "bulk": set()}
        self.counts = {"stream": 0, "call": 0, "bulk": 0}
        self._task: asyncio.Task | None = None

    def enabled(self) -> bool:
        rc = self.ctx.router.cfg()
        return bool(rc.get("enabled") and cfg(self.ctx).get("dscp") and self.ctx.router.available)

    def on_answer(self, name: str, wire: bytes) -> None:
        cat = category(name)
        if not cat or not self.enabled():
            return
        try:
            ips = [str(r.rdata) for r in DNSRecord.parse(wire).rr if r.rtype == QTYPE.A]
        except Exception:
            return
        if ips:
            self.pending[cat].update(ips)
            if self._task is None or self._task.done():
                self._task = asyncio.get_running_loop().create_task(self._flush())

    async def _flush(self) -> None:
        await asyncio.sleep(1.0)  # se agrupan las respuestas de 1 s en una sola llamada a nft
        cmds = []
        for cat, ips in self.pending.items():
            if ips:
                cmds.append(f"add element inet bipon qos_{cat} {{ {', '.join(sorted(ips))} }}")
                self.counts[cat] += len(ips)
        self.pending = {k: set() for k in self.pending}
        if cmds:
            p = await asyncio.create_subprocess_exec("nft", "-f", "-", stdin=asyncio.subprocess.PIPE,
                                                     stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
            await p.communicate("\n".join(cmds).encode())
