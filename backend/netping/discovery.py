"""Identificación de dispositivos: marca, modelo, sistema y virtualización.

Fuentes (todas pasivas o de consulta estándar en la LAN):
  * OUI del fabricante (MAC)                      → marca
  * mDNS / DNS-SD (Bonjour)                       → nombre, modelo (Apple, Chromecast, impresoras…)
  * SSDP / UPnP (descripción XML)                 → nombre comercial, fabricante y modelo (TV, consolas, routers)
  * NetBIOS (nbstat)                              → nombre de equipos Windows
  * Huella DHCP (opción 60/55 cuando Bipon es el DHCP) → sistema operativo
  * Puertos de servicio (8006 Proxmox, 8123 Home Assistant…) → rol del equipo
"""
from __future__ import annotations

import asyncio
import ipaddress
import logging
import re
import socket
import struct
import time
import xml.etree.ElementTree as ET

import httpx
from dnslib import DNSRecord, QTYPE, DNSQuestion

log = logging.getLogger("netping.discovery")

# ------------------------------------------------------------------ marcas
# (palabras clave en fabricante/modelo/hostname, clave de marca, nombre visible, tipo por defecto)
BRANDS: list[tuple[tuple[str, ...], str, str, str]] = [
    (("proxmox",), "proxmox", "Proxmox", "server"),
    (("apple", "iphone", "ipad", "macbook", "imac", "airpods", "homepod", "appletv", "apple tv"), "apple", "Apple", "phone"),
    (("samsung", "galaxy"), "samsung", "Samsung", "phone"),
    (("xiaomi", "redmi", "poco", "beijing xiaomi"), "xiaomi", "Xiaomi", "phone"),
    (("huawei",), "huawei", "Huawei", "phone"),
    (("honor",), "honor", "Honor", "phone"),
    (("oneplus",), "oneplus", "OnePlus", "phone"),
    (("oppo",), "oppo", "OPPO", "phone"),
    (("vivo mobile", "vivo"), "vivo", "vivo", "phone"),
    (("motorola", "lenovo mobility"), "motorola", "Motorola", "phone"),
    (("google", "pixel", "chromecast", "nest"), "google", "Google", "tv"),
    (("amazon", "echo", "kindle", "fire tv", "firetv", "ring "), "amazon", "Amazon", "speaker"),
    (("sony interactive", "playstation", "ps5", "ps4"), "playstation", "PlayStation", "console"),
    (("xbox",), "xbox", "Xbox", "console"),
    (("nintendo",), "nintendo", "Nintendo", "console"),
    (("valve", "steam deck", "steamdeck"), "steam", "Valve / Steam Deck", "console"),
    (("meta platforms", "oculus", "quest"), "meta", "Meta Quest", "console"),
    (("sony",), "sony", "Sony", "tv"),
    (("lg electronics", "lg innotek", "webos"), "lg", "LG", "tv"),
    (("hisense",), "hisense", "Hisense", "tv"),
    (("tcl",), "tcl", "TCL", "tv"),
    (("roku",), "roku", "Roku", "tv"),
    (("vizio",), "vizio", "VIZIO", "tv"),
    (("philips", "signify"), "philips", "Philips", "iot"),
    (("sonos",), "sonos", "Sonos", "speaker"),
    (("bose",), "bose", "Bose", "speaker"),
    (("tp-link", "tplink", "tp link", "mercusys"), "tplink", "TP-Link", "router"),
    (("asustek", "asus"), "asus", "ASUS", "computer"),
    (("netgear",), "netgear", "NETGEAR", "router"),
    (("ubiquiti", "unifi"), "ubiquiti", "Ubiquiti", "router"),
    (("mikrotik", "routerboard"), "mikrotik", "MikroTik", "router"),
    (("cisco", "linksys", "meraki"), "cisco", "Cisco", "router"),
    (("d-link", "dlink"), "dlink", "D-Link", "router"),
    (("tenda",), "tenda", "Tenda", "router"),
    (("zte",), "zte", "ZTE", "router"),
    (("avm", "fritz"), "avm", "AVM FRITZ!", "router"),
    (("openwrt",), "openwrt", "OpenWrt", "router"),
    (("synology",), "synology", "Synology", "server"),
    (("qnap",), "qnap", "QNAP", "server"),
    (("raspberry",), "raspberrypi", "Raspberry Pi", "server"),
    (("espressif", "esp32", "esp8266"), "espressif", "Espressif (IoT)", "iot"),
    (("tuya", "smart life"), "tuya", "Tuya", "iot"),
    (("shelly", "allterco"), "shelly", "Shelly", "iot"),
    (("hikvision",), "hikvision", "Hikvision", "camera"),
    (("dahua",), "dahua", "Dahua", "camera"),
    (("reolink",), "reolink", "Reolink", "camera"),
    (("hewlett", "hp inc", " hp "), "hp", "HP", "printer"),
    (("epson", "seiko epson"), "epson", "Epson", "printer"),
    (("canon",), "canon", "Canon", "printer"),
    (("brother",), "brother", "Brother", "printer"),
    (("dell",), "dell", "Dell", "computer"),
    (("lenovo",), "lenovo", "Lenovo", "computer"),
    (("acer",), "acer", "Acer", "computer"),
    (("micro-star", "msi"), "msi", "MSI", "computer"),
    (("gigabyte", "giga-byte"), "gigabyte", "GIGABYTE", "computer"),
    (("intel corporate", "intel"), "intel", "Intel", "computer"),
    (("realtek",), "realtek", "Realtek", "computer"),
    (("nvidia", "shield"), "nvidia", "NVIDIA", "tv"),
    (("microsoft", "surface", "windows"), "microsoft", "Microsoft", "computer"),
    (("liteon", "lite-on"), "liteon", "Lite-On", "computer"),
    (("vmware",), "vmware", "VMware", "server"),
]

# OUI de máquinas virtuales → hipervisor
VIRTUAL_OUIS = {
    "bc:24:11": "proxmox", "52:54:00": "qemu", "00:50:56": "vmware", "00:0c:29": "vmware", "00:05:69": "vmware",
    "08:00:27": "virtualbox", "00:15:5d": "hyperv", "00:16:3e": "xen", "02:42:ac": "docker",
}
HYPERVISOR_PORTS = {8006: "proxmox", 9440: "nutanix", 902: "vmware"}
SERVICE_PORTS = {8006: "Proxmox VE", 8123: "Home Assistant", 32400: "Plex", 5000: "Synology DSM", 9000: "Portainer",
                 8096: "Jellyfin", 80: "Web", 443: "Web segura", 22: "SSH", 445: "Compartir archivos", 9100: "Impresora",
                 631: "Impresora (IPP)", 3389: "Escritorio remoto", 5900: "VNC", 1883: "MQTT", 8443: "HTTPS"}


def brand_of(*texts: str | None) -> tuple[str | None, str | None, str | None]:
    s = " " + " ".join(t for t in texts if t).lower() + " "
    for keys, key, name, kind in BRANDS:
        if any(k in s for k in keys):
            return key, name, kind
    return None, None, None


def hypervisor_of_mac(mac: str) -> str | None:
    return VIRTUAL_OUIS.get(mac[:8].lower())


def os_from_dhcp(vendor_class: str, params: bytes | None = None) -> str | None:
    v = (vendor_class or "").lower()
    if v.startswith("android") or "android" in v:
        return "Android"
    if v.startswith("msft"):
        return "Windows"
    if "dhcpcd" in v:
        return "Linux"
    if "udhcp" in v:
        return "Linux embebido"
    if params and params[:4] == bytes([1, 121, 3, 6]) and 119 in params:
        return "Apple (iOS/macOS)"
    return None


# ------------------------------------------------------------------ mDNS
MDNS_SERVICES = ["_services._dns-sd._udp.local", "_device-info._tcp.local", "_airplay._tcp.local", "_googlecast._tcp.local",
                 "_raop._tcp.local", "_companion-link._tcp.local", "_hap._tcp.local", "_ipp._tcp.local", "_printer._tcp.local",
                 "_smb._tcp.local", "_workstation._tcp.local", "_spotify-connect._tcp.local", "_sonos._tcp.local",
                 "_androidtvremote2._tcp.local", "_homekit._tcp.local", "_ssh._tcp.local", "_http._tcp.local", "_amzn-wplay._tcp.local"]


async def mdns_scan(timeout: float = 3.0) -> dict[str, dict]:
    """Consulta DNS-SD (respuestas unicast) y devuelve {ip: {name, model, services}}."""
    loop = asyncio.get_running_loop()
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setblocking(False)
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 255)
    sock.bind(("0.0.0.0", 0))
    out: dict[str, dict] = {}
    host_ip: dict[str, str] = {}
    try:
        for i in range(0, len(MDNS_SERVICES), 6):
            q = DNSRecord()
            for name in MDNS_SERVICES[i:i + 6]:
                q.add_question(DNSQuestion(name, QTYPE.PTR, 0x8001))  # bit QU: respuesta unicast
            sock.sendto(q.pack(), ("224.0.0.251", 5353))
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            try:
                data, addr = await asyncio.wait_for(loop.sock_recvfrom(sock, 9000), end - time.monotonic())
            except (asyncio.TimeoutError, OSError):
                break
            try:
                rec = DNSRecord.parse(data)
            except Exception:
                continue
            ip = addr[0]
            info = out.setdefault(ip, {"services": set(), "name": None, "model": None})
            for rr in rec.rr + rec.ar + rec.auth:
                rname = str(rr.rname).rstrip(".")
                if rr.rtype == QTYPE.PTR:
                    target = str(rr.rdata).rstrip(".")
                    m = re.search(r"\.(_[\w-]+\._(tcp|udp))\.local$", target)
                    if m:
                        info["services"].add(m.group(1))
                        inst = target[: m.start()]
                        if inst and not info["name"]:
                            info["name"] = inst.replace("\\032", " ")[:60]
                elif rr.rtype == QTYPE.TXT:
                    items = [x.decode(errors="ignore") for x in getattr(rr.rdata, "data", [])]
                    kv = dict(i.split("=", 1) for i in items if "=" in i)
                    model = kv.get("model") or kv.get("md") or kv.get("am") or kv.get("ty")
                    if model and not info["model"]:
                        info["model"] = model[:60]
                    if kv.get("fn") and not info["name"]:
                        info["name"] = kv["fn"][:60]
                elif rr.rtype == QTYPE.A:
                    host_ip[rname] = str(rr.rdata)
                elif rr.rtype == QTYPE.SRV:
                    hn = str(rr.rdata.target).rstrip(".")
                    if hn.endswith(".local") and not info.get("hostname"):
                        info["hostname"] = hn[:-6]
    finally:
        sock.close()
    for v in out.values():
        v["services"] = sorted(v["services"])
    return {ip: v for ip, v in out.items() if v["services"] or v["name"] or v["model"]}


# ------------------------------------------------------------------ SSDP / UPnP
async def ssdp_scan(timeout: float = 3.0) -> dict[str, dict]:
    loop = asyncio.get_running_loop()
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setblocking(False)
    sock.bind(("0.0.0.0", 0))
    locations: dict[str, str] = {}
    try:
        msg = b'M-SEARCH * HTTP/1.1\r\nHOST: 239.255.255.250:1900\r\nMAN: "ssdp:discover"\r\nMX: 2\r\nST: ssdp:all\r\n\r\n'
        for _ in range(2):
            sock.sendto(msg, ("239.255.255.250", 1900))
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            try:
                data, addr = await asyncio.wait_for(loop.sock_recvfrom(sock, 4096), end - time.monotonic())
            except (asyncio.TimeoutError, OSError):
                break
            m = re.search(rb"(?im)^location:\s*(\S+)", data)
            if m and addr[0] not in locations:
                locations[addr[0]] = m.group(1).decode(errors="ignore")
    finally:
        sock.close()
    out: dict[str, dict] = {}

    async def describe(ip, url):
        try:
            host = re.match(r"https?://([^/:]+)", url)
            if not host or host.group(1) != ip:  # sólo descripciones servidas por el propio equipo
                return
            async with httpx.AsyncClient(timeout=3) as c:
                r = await c.get(url)
            root = ET.fromstring(r.content)
            for el in root.iter():
                if "}" in el.tag:
                    el.tag = el.tag.split("}", 1)[1]
            dev = root.find(".//device")
            if dev is None:
                return
            out[ip] = {k: (dev.findtext(k) or "").strip()[:80] for k in ("friendlyName", "manufacturer", "modelName", "modelNumber", "deviceType")}
        except Exception:
            pass
    await asyncio.gather(*(describe(ip, u) for ip, u in list(locations.items())[:64]))
    return out


# ------------------------------------------------------------------ NetBIOS
def _nbstat_packet() -> bytes:
    tid = int(time.time() * 1000) & 0xFFFF
    name = b"*" + b"\x00" * 15
    enc = b"".join(bytes([(c >> 4) + 0x41, (c & 0x0F) + 0x41]) for c in name)  # codificación de nombre NetBIOS
    return struct.pack("!HHHHHH", tid, 0, 1, 0, 0, 0) + bytes([32]) + enc + b"\x00" + struct.pack("!HH", 0x21, 1)


async def netbios_names(ips: list[str], timeout: float = 1.5) -> dict[str, str]:
    loop = asyncio.get_running_loop()
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setblocking(False)
    sock.bind(("0.0.0.0", 0))
    out: dict[str, str] = {}
    try:
        pkt = _nbstat_packet()
        for ip in ips:
            try:
                sock.sendto(pkt, (ip, 137))
            except OSError:
                pass
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            try:
                data, addr = await asyncio.wait_for(loop.sock_recvfrom(sock, 2048), end - time.monotonic())
            except (asyncio.TimeoutError, OSError):
                break
            try:
                off = 12 + 34 + 2 + 2 + 4 + 2  # cabecera + nombre + tipo/clase/ttl/longitud
                n = data[off]
                for i in range(n):
                    entry = data[off + 1 + i * 18: off + 1 + (i + 1) * 18]
                    nm = entry[:15].decode(errors="ignore").strip()
                    suffix, flags = entry[15], struct.unpack("!H", entry[16:18])[0]
                    if suffix == 0x00 and not flags & 0x8000 and nm:  # nombre único de estación
                        out[addr[0]] = nm
                        break
            except Exception:
                continue
    finally:
        sock.close()
    return out


async def service_ports(ip: str, ports=(8006, 8123, 32400, 5000, 9000, 8096, 22, 80, 443, 445, 3389), timeout: float = 0.6) -> list[int]:
    async def probe(p):
        try:
            _, w = await asyncio.wait_for(asyncio.open_connection(ip, p), timeout)
            w.close()
            return p
        except Exception:
            return None
    return [p for p in await asyncio.gather(*(probe(p) for p in ports)) if p]


def is_private(ip: str) -> bool:
    try:
        return ipaddress.ip_address(ip).is_private
    except ValueError:
        return False
