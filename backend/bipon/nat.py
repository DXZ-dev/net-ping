"""Análisis de conectividad: CGNAT, doble NAT, tipo de NAT (STUN), UPnP/IGD, MTU
y recomendaciones para mantener ping, velocidad y acceso remoto."""
from __future__ import annotations

import asyncio
import ipaddress
import logging
import os
import re
import shutil
import socket
import struct
import time
import xml.etree.ElementTree as ET
from urllib.parse import urljoin, urlparse

import httpx

from . import netinfo
from .tools import public_info

log = logging.getLogger("bipon.nat")

CGNAT_NET = ipaddress.ip_network("100.64.0.0/10")
STUN_SERVERS = [("stun.l.google.com", 19302), ("stun.cloudflare.com", 3478), ("stun1.l.google.com", 19302)]
IGD_SERVICES = (
    "urn:schemas-upnp-org:service:WANIPConnection:2",
    "urn:schemas-upnp-org:service:WANIPConnection:1",
    "urn:schemas-upnp-org:service:WANPPPConnection:1",
)


def classify_ip(ip: str | None) -> str:
    if not ip:
        return "unknown"
    try:
        a = ipaddress.ip_address(ip)
    except ValueError:
        return "unknown"
    if a in CGNAT_NET:
        return "cgnat"
    if a.is_private:
        return "private"
    return "public"


# ------------------------------------------------------------------ STUN
def _stun_request() -> tuple[bytes, bytes]:
    tid = os.urandom(12)
    return struct.pack("!HHI", 0x0001, 0, 0x2112A442) + tid, tid


def _stun_parse(data: bytes, tid: bytes) -> tuple[str, int] | None:
    if len(data) < 20 or data[8:20] != tid:
        return None
    pos, end = 20, 20 + struct.unpack("!H", data[2:4])[0]
    mapped = None
    while pos + 4 <= min(end, len(data)):
        atype, alen = struct.unpack("!HH", data[pos:pos + 4])
        val = data[pos + 4:pos + 4 + alen]
        if atype in (0x0020, 0x8020) and len(val) >= 8 and val[1] == 1:  # XOR-MAPPED-ADDRESS (IPv4)
            port = struct.unpack("!H", val[2:4])[0] ^ 0x2112
            ip = socket.inet_ntoa(bytes(b ^ m for b, m in zip(val[4:8], struct.pack("!I", 0x2112A442))))
            return ip, port
        if atype == 0x0001 and len(val) >= 8 and val[1] == 1:  # MAPPED-ADDRESS
            mapped = (socket.inet_ntoa(val[4:8]), struct.unpack("!H", val[2:4])[0])
        pos += 4 + alen + ((4 - alen % 4) % 4)
    return mapped


async def nat_type() -> dict:
    """Comprueba si el NAT conserva el puerto entre destinos (cono) o no (simétrico)."""
    loop = asyncio.get_running_loop()
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setblocking(False)
    sock.bind(("0.0.0.0", 0))
    local_port = sock.getsockname()[1]
    results = []
    try:
        for host, port in STUN_SERVERS:
            try:
                addr = (await loop.getaddrinfo(host, port, family=socket.AF_INET, type=socket.SOCK_DGRAM))[0][4]
            except Exception:
                continue
            req, tid = _stun_request()
            got = None
            for _ in range(2):
                await loop.sock_sendto(sock, req, addr)
                try:
                    t0 = time.perf_counter()
                    while time.perf_counter() - t0 < 1.5:
                        data = await asyncio.wait_for(loop.sock_recv(sock, 2048), 1.5)
                        got = _stun_parse(data, tid)
                        if got:
                            break
                except asyncio.TimeoutError:
                    pass
                if got:
                    break
            if got:
                results.append({"server": f"{host}:{port}", "ip": got[0], "port": got[1]})
    finally:
        sock.close()
    local_ip = netinfo.primary_ip()
    if not results:
        return {"type": "blocked", "label": "UDP bloqueado", "detail": "No hubo respuesta STUN: el tráfico UDP saliente parece filtrado.",
                "results": [], "local_port": local_port}
    ips = {r["ip"] for r in results}
    ports = {r["port"] for r in results}
    if local_ip in ips and local_port in ports:
        t, label, detail = "open", "Abierto (sin NAT)", "El equipo tiene IP pública directa."
    elif len(ports) == 1 and len(ips) == 1:
        if local_port in ports:
            t, label, detail = "cone_preserving", "Moderado / Abierto (cono)", "El NAT conserva el puerto: juegos online y llamadas P2P funcionan bien."
        else:
            t, label, detail = "cone", "Moderado (cono)", "El NAT asigna un puerto estable: P2P funciona en la mayoría de casos."
    else:
        t, label, detail = "symmetric", "Estricto (simétrico)", "El NAT cambia de puerto por destino: peor para juegos P2P, consolas y llamadas. Típico de CGNAT."
    return {"type": t, "label": label, "detail": detail, "results": results, "local_port": local_port,
            "multiple_ips": len(ips) > 1}


# ------------------------------------------------------------------ RFC 5780: comportamiento del NAT
RFC5780_SERVERS = [("stun.stunprotocol.org", 3478), ("stun.voipgate.com", 3478)]


def _stun_msg(change: int = 0) -> tuple[bytes, bytes]:
    tid = os.urandom(12)
    attrs = struct.pack("!HHI", 0x0003, 4, change) if change else b""  # CHANGE-REQUEST
    return struct.pack("!HHI", 0x0001, len(attrs), 0x2112A442) + tid + attrs, tid


def _stun_attrs(data: bytes, tid: bytes) -> dict | None:
    if len(data) < 20 or data[8:20] != tid:
        return None
    out = {}
    pos, end = 20, 20 + struct.unpack("!H", data[2:4])[0]
    while pos + 4 <= min(end, len(data)):
        at, ln = struct.unpack("!HH", data[pos:pos + 4])
        v = data[pos + 4:pos + 4 + ln]
        if len(v) >= 8 and v[1] == 1:
            if at in (0x0020, 0x8020):
                out["mapped"] = (socket.inet_ntoa(bytes(b ^ m for b, m in zip(v[4:8], struct.pack("!I", 0x2112A442)))),
                                 struct.unpack("!H", v[2:4])[0] ^ 0x2112)
            elif at == 0x0001 and "mapped" not in out:
                out["mapped"] = (socket.inet_ntoa(v[4:8]), struct.unpack("!H", v[2:4])[0])
            elif at in (0x802C, 0x0005):  # OTHER-ADDRESS / CHANGED-ADDRESS
                out["other"] = (socket.inet_ntoa(v[4:8]), struct.unpack("!H", v[2:4])[0])
        pos += 4 + ln + ((4 - ln % 4) % 4)
    return out


async def nat_behavior(timeout: float = 1.2) -> dict:
    """Pruebas RFC 5780: comportamiento de mapeo y de filtrado del NAT."""
    loop = asyncio.get_running_loop()
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setblocking(False)
    sock.bind(("0.0.0.0", 0))
    local_port = sock.getsockname()[1]

    async def ask(addr, change=0):
        msg, tid = _stun_msg(change)
        for _ in range(2):
            try:
                await loop.sock_sendto(sock, msg, addr)
                end = time.monotonic() + timeout
                while time.monotonic() < end:
                    data = await asyncio.wait_for(loop.sock_recv(sock, 2048), end - time.monotonic())
                    r = _stun_attrs(data, tid)
                    if r:
                        return r
            except (asyncio.TimeoutError, OSError):
                continue
        return None
    try:
        for host, port in RFC5780_SERVERS:
            try:
                addr = (await loop.getaddrinfo(host, port, family=socket.AF_INET, type=socket.SOCK_DGRAM))[0][4]
            except Exception:
                continue
            t1 = await ask(addr)
            if not t1 or "mapped" not in t1:
                continue
            other = t1.get("other")
            res = {"server": f"{host}:{port}", "mapped": t1["mapped"], "local_port": local_port,
                   "port_preserved": t1["mapped"][1] == local_port, "mapping": None, "filtering": None}
            if not other or other[0] == addr[0]:
                res["note"] = "El servidor no admite pruebas RFC 5780 completas"
                return res
            # mapeo: misma petición a la IP alternativa
            t2 = await ask((other[0], addr[1]))
            if t2 and t2.get("mapped") == t1["mapped"]:
                res["mapping"] = "endpoint-independent"
            else:
                t3 = await ask((other[0], other[1]))
                res["mapping"] = "address-dependent" if t2 and t3 and t3.get("mapped") == t2.get("mapped") else "address-and-port-dependent"
            # filtrado: pedir respuesta desde otra IP y puerto / sólo otro puerto
            if await ask(addr, change=0x06):
                res["filtering"] = "endpoint-independent"
            elif await ask(addr, change=0x02):
                res["filtering"] = "address-dependent"
            else:
                res["filtering"] = "address-and-port-dependent"
            return res
    finally:
        sock.close()
    return {"error": "Sin respuesta de los servidores STUN RFC 5780 (UDP saliente bloqueado o servidores caídos)"}


def classify_nat(beh: dict, nat_basic: dict) -> dict:
    """Clasificación clásica (RFC 3489) y equivalencia en consolas."""
    m, f = beh.get("mapping"), beh.get("filtering")
    if nat_basic.get("type") == "open":
        return {"classic": "Sin NAT (IP pública directa)", "playstation": "Tipo 1", "xbox": "Abierto", "nintendo": "A", "level": "ok"}
    if nat_basic.get("type") == "blocked":
        return {"classic": "UDP bloqueado", "playstation": "Tipo 3 / falla", "xbox": "No disponible (Teredo)", "nintendo": "F", "level": "err"}
    if m == "endpoint-independent" and f == "endpoint-independent":
        return {"classic": "Cono completo (Full Cone)", "playstation": "Tipo 2 (casi tipo 1)", "xbox": "Abierto", "nintendo": "A", "level": "ok"}
    if m == "endpoint-independent" and f == "address-dependent":
        return {"classic": "Cono restringido (Restricted Cone)", "playstation": "Tipo 2", "xbox": "Moderado", "nintendo": "B", "level": "warn"}
    if m == "endpoint-independent":
        return {"classic": "Cono restringido por puerto (Port-Restricted Cone)", "playstation": "Tipo 2", "xbox": "Moderado", "nintendo": "B", "level": "warn"}
    if m in ("address-dependent", "address-and-port-dependent"):
        return {"classic": "Simétrico (Symmetric)", "playstation": "Tipo 3", "xbox": "Estricto", "nintendo": "C/D", "level": "err"}
    # sin pruebas RFC 5780: estimación por la prueba básica
    t = nat_basic.get("type")
    if t == "symmetric":
        return {"classic": "Simétrico (estimado)", "playstation": "Tipo 3", "xbox": "Estricto", "nintendo": "C/D", "level": "err"}
    if t in ("cone", "cone_preserving"):
        return {"classic": "Cono (estimado)", "playstation": "Tipo 2", "xbox": "Moderado", "nintendo": "B", "level": "warn"}
    return {"classic": "Desconocido", "playstation": "—", "xbox": "—", "nintendo": "—", "level": "info"}


async def nat_sessions() -> dict:
    """Tabla de sesiones NAT/conntrack de este equipo (relevante en modo router)."""
    out: dict = {"available": False}
    try:
        with open("/proc/sys/net/netfilter/nf_conntrack_count") as fh:
            out["count"] = int(fh.read())
        with open("/proc/sys/net/netfilter/nf_conntrack_max") as fh:
            out["max"] = int(fh.read())
        out["usage_pct"] = round(out["count"] / out["max"] * 100, 1) if out["max"] else 0
        out["available"] = True
    except OSError:
        pass
    for name, path in (("tcp_established_timeout", "nf_conntrack_tcp_timeout_established"), ("udp_timeout", "nf_conntrack_udp_timeout"),
                       ("udp_stream_timeout", "nf_conntrack_udp_timeout_stream")):
        try:
            with open(f"/proc/sys/net/netfilter/{path}") as fh:
                out[name] = int(fh.read())
        except OSError:
            pass
    if shutil.which("conntrack"):
        proc = await asyncio.create_subprocess_exec("conntrack", "-L", stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
        try:
            data, _ = await asyncio.wait_for(proc.communicate(), 10)
        except asyncio.TimeoutError:
            proc.kill()
            data = b""
        states: dict[str, int] = {}
        protos: dict[str, int] = {}
        nat = 0
        for line in data.decode(errors="ignore").splitlines():
            f = line.split()
            if not f:
                continue
            protos[f[0]] = protos.get(f[0], 0) + 1
            if f[0] == "tcp" and len(f) > 3:
                states[f[3]] = states.get(f[3], 0) + 1
            elif f[0] == "udp":
                key = "UDP con respuesta" if "[ASSURED]" in line else "UDP sin respuesta"
                states[key] = states.get(key, 0) + 1
            srcs = re.findall(r"src=(\S+)", line)
            dsts = re.findall(r"dst=(\S+)", line)
            if len(srcs) == 2 and len(dsts) == 2 and (srcs[0] != dsts[1] or dsts[0] != srcs[1]):
                nat += 1  # la respuesta vuelve a otra dirección: sesión traducida (NAT)
        out.update(states=dict(sorted(states.items(), key=lambda x: -x[1])), protocols=protos, translated=nat, available=True)
    return out


# ------------------------------------------------------------------ UPnP / IGD
async def _ssdp_discover(timeout: float = 2.5) -> list[str]:
    loop = asyncio.get_running_loop()
    locs: set[str] = set()

    class P(asyncio.DatagramProtocol):
        def datagram_received(self, data, addr):
            m = re.search(rb"(?im)^location:\s*(\S+)", data)
            if m:
                locs.add(m.group(1).decode(errors="ignore"))

    transport, _ = await loop.create_datagram_endpoint(P, local_addr=("0.0.0.0", 0))
    try:
        for st in ("urn:schemas-upnp-org:device:InternetGatewayDevice:1", "urn:schemas-upnp-org:device:InternetGatewayDevice:2",
                   "urn:schemas-upnp-org:service:WANIPConnection:1"):
            msg = ("M-SEARCH * HTTP/1.1\r\nHOST: 239.255.255.250:1900\r\nMAN: \"ssdp:discover\"\r\n"
                   f"MX: 2\r\nST: {st}\r\n\r\n").encode()
            transport.sendto(msg, ("239.255.255.250", 1900))
        await asyncio.sleep(timeout)
    finally:
        transport.close()
    return list(locs)


def _strip_ns(tree: ET.Element) -> ET.Element:
    for el in tree.iter():
        if "}" in el.tag:
            el.tag = el.tag.split("}", 1)[1]
    return tree


class IGD:
    def __init__(self, control_url: str, service: str, name: str):
        self.control_url, self.service, self.name = control_url, service, name

    async def soap(self, action: str, args: dict | None = None) -> dict:
        body = "".join(f"<{k}>{v}</{k}>" for k, v in (args or {}).items())
        env = ('<?xml version="1.0"?><s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" '
               's:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/"><s:Body>'
               f'<u:{action} xmlns:u="{self.service}">{body}</u:{action}></s:Body></s:Envelope>')
        async with httpx.AsyncClient(timeout=5) as c:
            r = await c.post(self.control_url, content=env, headers={
                "Content-Type": 'text/xml; charset="utf-8"', "SOAPAction": f'"{self.service}#{action}"'})
        root = _strip_ns(ET.fromstring(r.content))
        if r.status_code != 200:
            err = root.find(".//errorDescription")
            raise RuntimeError(err.text if err is not None else f"HTTP {r.status_code}")
        resp = root.find(f".//{action}Response")
        return {child.tag: (child.text or "") for child in (resp if resp is not None else [])}

    async def external_ip(self) -> str | None:
        try:
            return (await self.soap("GetExternalIPAddress")).get("NewExternalIPAddress")
        except Exception:
            return None

    async def mappings(self) -> list[dict]:
        out = []
        for i in range(128):
            try:
                m = await self.soap("GetGenericPortMappingEntry", {"NewPortMappingIndex": i})
            except Exception:
                break
            out.append({"external_port": int(m.get("NewExternalPort") or 0), "protocol": m.get("NewProtocol"),
                        "internal_ip": m.get("NewInternalClient"), "internal_port": int(m.get("NewInternalPort") or 0),
                        "description": m.get("NewPortMappingDescription"), "enabled": m.get("NewEnabled") == "1",
                        "remote_host": m.get("NewRemoteHost") or "*", "lease": int(m.get("NewLeaseDuration") or 0)})
        return out

    async def add_mapping(self, ext_port: int, proto: str, int_ip: str, int_port: int, desc: str, lease: int = 0):
        await self.soap("AddPortMapping", {
            "NewRemoteHost": "", "NewExternalPort": ext_port, "NewProtocol": proto.upper(), "NewInternalPort": int_port,
            "NewInternalClient": int_ip, "NewEnabled": 1, "NewPortMappingDescription": desc[:60] or "Bipon Net",
            "NewLeaseDuration": lease})

    async def delete_mapping(self, ext_port: int, proto: str):
        await self.soap("DeletePortMapping", {"NewRemoteHost": "", "NewExternalPort": ext_port, "NewProtocol": proto.upper()})


async def find_igd() -> IGD | None:
    for loc in await _ssdp_discover():
        try:
            async with httpx.AsyncClient(timeout=4) as c:
                r = await c.get(loc)
            root = _strip_ns(ET.fromstring(r.content))
            name = (root.findtext(".//friendlyName") or root.findtext(".//modelName") or "Router").strip()
            base = root.findtext("URLBase") or loc
            for svc in root.iter("service"):
                st = svc.findtext("serviceType") or ""
                if st in IGD_SERVICES:
                    ctrl = urljoin(base, svc.findtext("controlURL") or "")
                    if urlparse(ctrl).hostname and netinfo.is_private(urlparse(ctrl).hostname):
                        return IGD(ctrl, st, name)
        except Exception as exc:
            log.debug("IGD %s: %s", loc, exc)
    return None


# ------------------------------------------------------------------ Traceroute / MTU
async def first_hops(target: str = "1.1.1.1", max_hops: int = 6) -> list[str]:
    if not shutil.which("traceroute"):
        return []
    proc = await asyncio.create_subprocess_exec("traceroute", "-n", "-q", "1", "-w", "1", "-m", str(max_hops), target,
                                                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), 20)
    except asyncio.TimeoutError:
        proc.kill()
        return []
    hops = []
    for line in out.decode(errors="ignore").splitlines()[1:]:
        m = re.match(r"\s*\d+\s+(\S+)", line)
        hops.append(m.group(1) if m and m.group(1) != "*" else "*")
    return hops


async def _ping_df(size: int, host: str) -> bool:
    proc = await asyncio.create_subprocess_exec("ping", "-n", "-c", "1", "-W", "1", "-M", "do", "-s", str(size), host,
                                                stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
    return await proc.wait() == 0


async def path_mtu(host: str = "1.1.1.1") -> dict:
    if not shutil.which("ping") or not await _ping_df(1200, host):
        return {"mtu": None, "detail": "No se pudo medir (ICMP bloqueado)"}
    lo, hi = 1200, 1472
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if await _ping_df(mid, host):
            lo = mid
        else:
            hi = mid - 1
    mtu = lo + 28
    detail = "MTU estándar Ethernet: óptimo." if mtu >= 1500 else (
        "MTU típico de PPPoE (1492): correcto si tu conexión usa PPPoE." if mtu >= 1492 else
        "MTU reducido: probablemente hay un túnel/VPN/CGNAT en la ruta. Ajusta el MTU del router a este valor para evitar fragmentación.")
    return {"mtu": mtu, "mss": mtu - 40, "detail": detail}


# ------------------------------------------------------------------ análisis completo
async def analyze() -> dict:
    t0 = time.time()
    public_t = asyncio.create_task(public_info())
    nat_t = asyncio.create_task(nat_type())
    igd_t = asyncio.create_task(find_igd())
    hops_t = asyncio.create_task(first_hops())
    mtu_t = asyncio.create_task(path_mtu())
    ipv6_t = asyncio.create_task(_ipv6())
    beh_t = asyncio.create_task(nat_behavior())
    sess_t = asyncio.create_task(nat_sessions())
    public, nat, igd, hops, mtu, ipv6, beh, sess = await asyncio.gather(public_t, nat_t, igd_t, hops_t, mtu_t, ipv6_t, beh_t, sess_t,
                                                                         return_exceptions=True)
    beh = beh if isinstance(beh, dict) else {"error": str(beh)}
    sess = sess if isinstance(sess, dict) else {"available": False}
    public = public if isinstance(public, dict) else {}
    nat = nat if isinstance(nat, dict) else {"type": "unknown", "label": "Desconocido"}
    igd = igd if isinstance(igd, IGD) else None
    hops = hops if isinstance(hops, list) else []
    mtu = mtu if isinstance(mtu, dict) else {"mtu": None}
    ipv6 = ipv6 if isinstance(ipv6, dict) else {"available": False}

    wan_ip = await igd.external_ip() if igd else None
    mappings = await igd.mappings() if igd else []
    gw = netinfo.default_gateway()
    hop_info = [{"hop": i + 1, "ip": h, "class": classify_ip(h) if h != "*" else "timeout"} for i, h in enumerate(hops)]

    evidence = []
    verdict = "unknown"
    wan_class = classify_ip(wan_ip)
    if wan_ip:
        evidence.append(f"El router informa IP WAN {wan_ip} ({ {'cgnat': 'rango CGNAT 100.64/10', 'private': 'IP privada', 'public': 'IP pública'}.get(wan_class, '?') }).")
    if wan_class == "cgnat":
        verdict = "cgnat"
    elif wan_class == "private":
        verdict = "double_nat"
    elif wan_class == "public":
        verdict = "public" if wan_ip == public.get("ip") else "cgnat"
        if wan_ip != public.get("ip") and public.get("ip"):
            evidence.append(f"La IP WAN del router ({wan_ip}) no coincide con la IP pública vista desde Internet ({public.get('ip')}).")
    after_gw = [h for h in hop_info[1:] if h["class"] in ("cgnat", "private")]
    if any(h["class"] == "cgnat" for h in after_gw):
        evidence.append("El traceroute atraviesa saltos en 100.64.0.0/10 (red interna CGNAT del proveedor).")
        verdict = "cgnat" if verdict in ("unknown", "double_nat") else verdict
    elif after_gw and verdict == "unknown":
        evidence.append(f"Hay saltos con IP privada después del router ({after_gw[0]['ip']}): posible doble NAT o red interna del ISP.")
        verdict = "double_nat_possible"
    if verdict == "unknown" and nat.get("type") == "symmetric":
        verdict = "cgnat_possible"
        evidence.append("NAT simétrico detectado por STUN: frecuente en CGNAT.")
    if verdict == "unknown" and public.get("ip") and hop_info and len(hop_info) > 1 and hop_info[1]["class"] == "public":
        verdict = "public_likely"
        evidence.append("El segundo salto ya es una IP pública: probablemente tienes IP pública.")

    labels = {
        "public": ("IP pública", "ok", "Tu router tiene IP pública: puedes abrir puertos y alojar servicios."),
        "public_likely": ("IP pública (probable)", "ok", "Todo indica que tienes IP pública. Activa UPnP en el router para confirmarlo."),
        "cgnat": ("CGNAT detectado", "warn", "Tu proveedor comparte tu IP pública con otros clientes: no puedes abrir puertos hacia dentro."),
        "cgnat_possible": ("Posible CGNAT", "warn", "Hay indicios de CGNAT. Revisa la IP WAN en el panel del router."),
        "double_nat": ("Doble NAT", "warn", "Hay dos routers haciendo NAT (p. ej. módem del ISP + tu router)."),
        "double_nat_possible": ("Posible doble NAT", "warn", "Puede haber dos routers haciendo NAT."),
        "unknown": ("Indeterminado", "info", "No se pudo determinar. Activa UPnP en el router para un análisis más preciso."),
    }
    label, status, summary = labels[verdict]
    cls = classify_nat(beh, nat)
    states = [
        {"name": "IP pública", "value": public.get("ip") or "—", "status": "ok" if public.get("ip") else "warn"},
        {"name": "CGNAT del proveedor", "value": "Sí" if verdict.startswith("cgnat") else "No" if verdict.startswith("public") else "Indeterminado",
         "status": "warn" if verdict.startswith("cgnat") else "ok" if verdict.startswith("public") else "info"},
        {"name": "Doble NAT", "value": "Sí" if verdict.startswith("double_nat") else "No", "status": "warn" if verdict.startswith("double_nat") else "ok"},
        {"name": "Tipo de NAT", "value": cls["classic"], "status": cls["level"]},
        {"name": "Mapeo (RFC 4787)", "value": {"endpoint-independent": "Independiente del destino", "address-dependent": "Depende de la IP destino",
                                               "address-and-port-dependent": "Depende de IP y puerto destino"}.get(beh.get("mapping"), "—"),
         "status": "ok" if beh.get("mapping") == "endpoint-independent" else "warn" if beh.get("mapping") else "info"},
        {"name": "Filtrado (RFC 4787)", "value": {"endpoint-independent": "Abierto a cualquier origen", "address-dependent": "Sólo IP contactadas",
                                                  "address-and-port-dependent": "Sólo IP y puerto contactados"}.get(beh.get("filtering"), "—"),
         "status": "ok" if beh.get("filtering") == "endpoint-independent" else "warn" if beh.get("filtering") else "info"},
        {"name": "Preservación de puertos", "value": "Sí" if beh.get("port_preserved") or nat.get("type") == "cone_preserving" else "No",
         "status": "ok" if beh.get("port_preserved") else "info"},
        {"name": "UPnP-IGD en el router", "value": f"Activo ({igd.name})" if igd else "No disponible", "status": "ok" if igd else "warn"},
        {"name": "Redirecciones UPnP activas", "value": str(len(mappings)), "status": "info"},
        {"name": "IPv6", "value": "Disponible" if ipv6.get("available") else "No", "status": "ok" if ipv6.get("available") else "info"},
    ]
    if sess.get("available") and sess.get("count") is not None:
        states.append({"name": "Sesiones NAT en este equipo", "value": f"{sess['count']:,} / {sess.get('max', 0):,} ({sess.get('usage_pct', 0)} %)",
                       "status": "warn" if sess.get("usage_pct", 0) > 80 else "ok"})
    return {
        "nat_class": cls, "behavior": beh, "states": states, "sessions": sess,
        "verdict": verdict, "label": label, "status": status, "summary": summary, "evidence": evidence,
        "public": public, "wan_ip": wan_ip, "gateway": gw, "local_ip": netinfo.primary_ip(),
        "nat": nat, "upnp": {"available": igd is not None, "name": igd.name if igd else None, "mappings": mappings},
        "hops": hop_info, "mtu": mtu, "ipv6": ipv6,
        "recommendations": recommendations(verdict, nat, igd is not None, ipv6, mtu),
        "seconds": round(time.time() - t0, 1),
    }


async def _ipv6() -> dict:
    try:
        t0 = time.perf_counter()
        _, w = await asyncio.wait_for(asyncio.open_connection("2606:4700:4700::1111", 443), 3)
        ms = (time.perf_counter() - t0) * 1000
        w.close()
        return {"available": True, "ms": round(ms, 1)}
    except Exception:
        return {"available": False}


def recommendations(verdict: str, nat: dict, upnp: bool, ipv6: dict, mtu: dict) -> list[dict]:
    """Soluciones ordenadas según la situación detectada."""
    behind = verdict in ("cgnat", "cgnat_possible")
    rec: list[dict] = []
    if behind or verdict.startswith("double_nat"):
        rec.append({"id": "tailscale", "title": "Tailscale / WireGuard mesh (recomendado)", "fit": "excelente",
                    "why": "Funciona detrás de CGNAT y sin abrir puertos: conecta tus dispositivos de forma cifrada y directa (P2P) y, si no puede, usa relés cercanos. Puedes usar Bipon Net como DNS (bloqueo de anuncios fuera de casa) y como subnet router para llegar a toda tu LAN.",
                    "how": ["Abre VPN → Tailscale en Bipon Net y pulsa Conectar", "Inicia sesión con el enlace que aparece (o usa una auth key)", "Activa 'Anunciar mi LAN' / 'Exit node' y apruébalos en login.tailscale.com", "En el panel de Tailscale → DNS, usa la IP de Tailscale de Bipon Net como servidor"], "action": "vpn/tailscale"})
        rec.append({"id": "cloudflared", "title": "Cloudflare Tunnel (publicar servicios web)", "fit": "excelente",
                    "why": "Expone paneles web (Bipon Net, Home Assistant, Jellyfin…) con HTTPS sin abrir puertos ni tener IP pública. Añade autenticación (Zero Trust).",
                    "how": ["Crea un túnel en one.dash.cloudflare.com → Networks → Tunnels", "Copia el token a CLOUDFLARE_TUNNEL_TOKEN en .env", "docker compose --profile cloudflared up -d"]})
        rec.append({"id": "vps", "title": "VPS de relevo con WireGuard", "fit": "buena",
                    "why": "Un VPS barato con IP pública cerca de ti reenvía puertos (juegos, servidores) a casa por un túnel WireGuard. Latencia mínima si eliges un VPS en tu misma ciudad/país.",
                    "action": "vpn/relay", "how": ["Genera la configuración en VPN → WireGuard → VPS de relevo", "Instala WireGuard en el VPS y en casa", "Reenvía puertos en el VPS con iptables (se genera el script)"]})
        if ipv6.get("available"):
            rec.append({"id": "ipv6", "title": "Usar IPv6", "fit": "buena",
                        "why": "Tienes IPv6: cada dispositivo tiene dirección pública propia y el CGNAT no aplica. Abre el puerto en el firewall IPv6 del router.",
                        "how": ["Habilita IPv6 en el router", "Crea una regla de firewall IPv6 para el servicio", "Usa DNS dinámico con registros AAAA"]})
        rec.append({"id": "isp", "title": "Pedir IP pública al proveedor", "fit": "definitiva",
                    "why": "Muchos ISP sacan la línea de CGNAT gratis o por un pequeño coste si lo pides (\"IP pública\", \"IP fija\", \"sacar de CGNAT\").",
                    "how": ["Llama a soporte y pide salir de CGNAT", "Vuelve a ejecutar este análisis para confirmarlo"]})
        if verdict.startswith("double_nat"):
            rec.insert(0, {"id": "bridge", "title": "Eliminar el doble NAT", "fit": "excelente",
                           "why": "Pon el módem del ISP en modo bridge o configura DMZ hacia tu router: menos latencia, NAT abierto y puertos funcionales.",
                           "how": ["Entra al módem del ISP", "Activa 'Modo bridge' o 'DMZ' hacia la IP WAN de tu router", "Reinicia ambos equipos"]})
    else:
        rec.append({"id": "wireguard", "title": "Servidor WireGuard en casa", "fit": "excelente",
                    "why": "Con IP pública, WireGuard es la VPN más rápida y con menor latencia: accede a tu red y usa el bloqueo de anuncios desde cualquier lugar.",
                    "action": "vpn/wireguard", "how": ["Crea un servidor en VPN → WireGuard", "Abre el puerto UDP 51820 en el router (o vía UPnP desde aquí)", "Escanea el QR desde la app WireGuard"]})
        rec.append({"id": "ddns", "title": "DNS dinámico", "fit": "buena",
                    "why": "Si tu IP pública cambia, un DNS dinámico (DuckDNS, Cloudflare) mantiene un nombre fijo para tu casa.",
                    "how": ["Crea un dominio gratuito en duckdns.org", "Configúralo en el router o con el perfil 'ddns' de docker compose"]})
    if nat.get("type") == "symmetric":
        rec.append({"id": "nat_strict", "title": "NAT estricto para juegos/consolas", "fit": "info",
                    "why": "Con NAT simétrico las partidas P2P y chats de voz pueden fallar o tener más latencia. Soluciones: UPnP activado, IP pública, o VPN de juego con NAT abierto.",
                    "how": ["Activa UPnP en el router", "Asigna IP fija (reserva DHCP) a la consola", "Si hay CGNAT, pide IP pública al ISP"]})
    if not upnp and not behind:
        rec.append({"id": "upnp", "title": "Activa UPnP-IGD en el router", "fit": "info",
                    "why": "Permite a consolas y apps abrir puertos automáticamente y a Bipon Net analizar con precisión tu conexión.",
                    "how": ["Router → Ajustes avanzados → UPnP → Activar"]})
    if mtu.get("mtu") and mtu["mtu"] < 1492:
        rec.append({"id": "mtu", "title": f"Ajusta el MTU a {mtu['mtu']}", "fit": "buena",
                    "why": "Un MTU mayor que el de la ruta provoca fragmentación: más latencia y conexiones que se cuelgan.",
                    "how": [f"Router → WAN → MTU = {mtu['mtu']} (MSS {mtu['mss']})"]})
    rec.append({"id": "sqm", "title": "Controla el bufferbloat (SQM / QoS)", "fit": "info",
                "why": "Si el ping sube mucho al descargar (ver test de velocidad), activa SQM (fq_codel/CAKE) en el router limitando al ~90 % de tu velocidad: el ping se mantiene bajo aunque la red esté saturada.",
                "how": ["Ejecuta el test de velocidad y mira la nota de bufferbloat", "Router: QoS/SQM → CAKE → ancho de banda al 90-95 %", "OpenWrt: paquete luci-app-sqm"]})
    return rec
