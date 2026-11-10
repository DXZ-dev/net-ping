"""Información de la red local: interfaces, puerta de enlace, fabricantes (OUI)."""
from __future__ import annotations

import asyncio
import ipaddress
import json
import logging
import os
import re
import shutil
import socket
import struct
from pathlib import Path

import httpx

from .config import DATA_DIR

log = logging.getLogger("netping.netinfo")

_SKIP_IF = re.compile(r"^(lo|docker\d*|br-|veth|virbr|cni|flannel|cali|tun|tap|wg|zt|tailscale|kube|vmnet|vboxnet)")


def run_json(cmd: list[str]):
    import subprocess
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
        return json.loads(out.stdout or "null")
    except Exception:
        return None


def interfaces() -> list[dict]:
    """Interfaces IPv4 de la LAN (excluye loopback, docker, VPN...)."""
    res = []
    data = run_json(["ip", "-j", "-4", "addr", "show"]) if shutil.which("ip") else None
    if data:
        for itf in data:
            name = itf.get("ifname", "")
            if _SKIP_IF.match(name) or "UP" not in itf.get("flags", []):
                continue
            for a in itf.get("addr_info", []):
                if a.get("family") != "inet":
                    continue
                net = ipaddress.ip_interface(f"{a['local']}/{a['prefixlen']}")
                if net.ip.is_loopback or net.ip.is_link_local:
                    continue
                res.append({"name": name, "ip": str(net.ip), "prefix": a["prefixlen"],
                            "network": str(net.network), "mac": itf.get("address", "")})
    if not res:
        ip = primary_ip()
        if ip:
            net = ipaddress.ip_interface(f"{ip}/24")
            res.append({"name": "?", "ip": ip, "prefix": 24, "network": str(net.network), "mac": ""})
    return res


def primary_ip() -> str | None:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("1.1.1.1", 53))
        return s.getsockname()[0]
    except OSError:
        return None
    finally:
        s.close()


def default_gateway() -> str | None:
    try:
        with open("/proc/net/route") as fh:
            next(fh)
            for line in fh:
                f = line.split()
                if f[1] == "00000000" and int(f[3], 16) & 2:
                    return socket.inet_ntoa(struct.pack("<L", int(f[2], 16)))
    except Exception:
        pass
    return None


def dns_servers_of_host() -> list[str]:
    out = []
    try:
        for line in Path("/etc/resolv.conf").read_text().splitlines():
            if line.startswith("nameserver"):
                out.append(line.split()[1])
    except Exception:
        pass
    return out


def neighbors() -> list[dict]:
    """Tabla de vecinos (ARP) con estado."""
    res = []
    data = run_json(["ip", "-j", "-4", "neigh", "show"]) if shutil.which("ip") else None
    if data is not None:
        for n in data:
            mac = n.get("lladdr")
            if not mac or _SKIP_IF.match(n.get("dev", "")):
                continue
            state = (n.get("state") or ["?"])
            res.append({"ip": n["dst"], "mac": mac.lower(), "dev": n.get("dev"), "state": state[0] if isinstance(state, list) else state})
        return res
    try:
        with open("/proc/net/arp") as fh:
            next(fh)
            for line in fh:
                f = line.split()
                if len(f) >= 6 and f[3] != "00:00:00:00:00:00" and int(f[2], 16) & 2:
                    res.append({"ip": f[0], "mac": f[3].lower(), "dev": f[5], "state": "REACHABLE"})
    except Exception:
        pass
    return res


# --------------------------------------------------------------------- OUI
class OUIDatabase:
    URLS = [
        "https://www.wireshark.org/download/automated/data/manuf",
        "https://standards-oui.ieee.org/oui/oui.csv",
    ]

    def __init__(self):
        self.path = DATA_DIR / "manuf.txt"
        self.prefixes: dict[int, dict[str, str]] = {}

    def load(self) -> None:
        if not self.path.exists():
            return
        table: dict[int, dict[str, str]] = {}
        with open(self.path, encoding="utf-8", errors="ignore") as fh:
            for line in fh:
                if not line.strip() or line.startswith("#"):
                    continue
                if line.startswith(("MA-L,", "MA-M,", "MA-S,")):  # formato CSV del IEEE
                    reg, rest = line.split(",", 1)
                    assign, _, org = rest.partition(",")
                    org = org.split('",')[0].strip('"') if org.startswith('"') else org.split(",")[0]
                    bits = {"MA-L": 24, "MA-M": 28, "MA-S": 36}[reg]
                    table.setdefault(bits, {})[assign.lower()[: bits // 4]] = org.strip()
                    continue
                parts = line.rstrip("\n").split("\t")
                if len(parts) < 2:
                    continue
                prefix, short = parts[0], parts[1]
                long = parts[2] if len(parts) > 2 else short
                bits = 24
                if "/" in prefix:
                    prefix, b = prefix.split("/")
                    bits = int(b)
                hexs = prefix.replace(":", "").replace("-", "").lower()
                hexs = hexs[: bits // 4]
                table.setdefault(bits, {})[hexs] = long.strip() or short
        self.prefixes = dict(sorted(table.items(), reverse=True))
        log.info("base OUI cargada (%d prefijos)", sum(len(v) for v in table.values()))

    async def ensure(self) -> None:
        if self.path.exists() and self.path.stat().st_size > 100_000:
            await asyncio.to_thread(self.load)
            return
        async with httpx.AsyncClient(follow_redirects=True, timeout=60) as c:
            for url in self.URLS:
                try:
                    r = await c.get(url)
                    if r.status_code == 200 and len(r.content) > 100_000:
                        self.path.write_bytes(r.content)
                        break
                except Exception as exc:
                    log.warning("no se pudo descargar OUI de %s: %s", url, exc)
        await asyncio.to_thread(self.load)

    def lookup(self, mac: str) -> str:
        if not mac or mac.startswith("ip:"):
            return ""
        h = mac.replace(":", "").lower()
        if len(h) >= 2 and int(h[1], 16) & 0x2:
            return "MAC privada (aleatoria)"
        for bits, table in self.prefixes.items():
            v = table.get(h[: bits // 4])
            if v:
                return v
        return ""


def guess_kind(vendor: str, hostname: str, name: str = "") -> str:
    s = f"{vendor} {hostname} {name}".lower()
    rules = [
        ("router", ("router", "gateway", "tp-link", "netgear", "ubiquiti", "mikrotik", "zte", "huawei tech", "sagemcom", "technicolor", "arris", "fritz", "asus", "linksys", "openwrt", "pfsense", "opnsense", "unifi", "eero")),
        ("tv", ("tv", "roku", "chromecast", "firetv", "fire-tv", "bravia", "lg electronics", "samsung electronics", "tcl", "hisense", "vizio", "apple-tv", "appletv", "shield")),
        ("console", ("playstation", "xbox", "nintendo", "ps4", "ps5", "switch", "sony interactive")),
        ("phone", ("iphone", "android", "galaxy", "pixel", "xiaomi", "redmi", "oneplus", "oppo", "vivo", "motorola", "realme", "honor", "phone", "mac privada")),
        ("tablet", ("ipad", "tablet", "tab-")),
        ("printer", ("printer", "hp inc", "epson", "canon", "brother", "lexmark", "kyocera", "impresora")),
        ("speaker", ("echo", "alexa", "sonos", "google home", "nest", "homepod", "bose")),
        ("camera", ("camera", "cam", "hikvision", "dahua", "reolink", "wyze", "ezviz", "ring")),
        ("iot", ("espressif", "tuya", "shelly", "sonoff", "broadlink", "philips lighting", "signify", "smart", "iot", "raspberry")),
        ("server", ("proxmox", "server", "nas", "synology", "qnap", "truenas", "unraid", "docker", "vmware", "supermicro")),
        ("computer", ("desktop", "laptop", "pc", "macbook", "imac", "dell", "lenovo", "intel corporate", "micro-star", "gigabyte", "asustek", "acer", "hewlett", "windows", "linux", "apple")),
    ]
    for kind, keys in rules:
        if any(k in s for k in keys):
            return kind
    return "unknown"


def is_private(ip: str) -> bool:
    try:
        return ipaddress.ip_address(ip).is_private
    except ValueError:
        return False


def env_flag(name: str, default: bool = False) -> bool:
    v = os.environ.get(name)
    return default if v is None else v.lower() in ("1", "true", "yes", "on")
