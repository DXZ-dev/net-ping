"""Servidor DHCPv4 (RFC 2131/2132).

Pensado para routers del ISP que no permiten cambiar el DNS: se desactiva el
DHCP del router y Bipon Net reparte las IP con su propio DNS. Incluye reservas
por MAC, nombres de equipo publicados en el DNS local (equipo.lan) y detección
de otros servidores DHCP en la red."""
from __future__ import annotations

import asyncio
import ipaddress
import logging
import os
import socket
import struct
import time

from . import netinfo

log = logging.getLogger("bipon.dhcp")

MAGIC = b"\x63\x82\x53\x63"
DISCOVER, OFFER, REQUEST, DECLINE, ACK, NAK, RELEASE, INFORM = 1, 2, 3, 4, 5, 6, 7, 8
MSG_NAMES = {1: "DISCOVER", 2: "OFFER", 3: "REQUEST", 4: "DECLINE", 5: "ACK", 6: "NAK", 7: "RELEASE", 8: "INFORM"}
OFFER_HOLD = 60


def parse_packet(data: bytes) -> dict | None:
    if len(data) < 240 or data[236:240] != MAGIC:
        return None
    op, htype, hlen, hops, xid, secs, flags = struct.unpack("!BBBBIHH", data[:12])
    ciaddr, yiaddr, siaddr, giaddr = (socket.inet_ntoa(data[i:i + 4]) for i in (12, 16, 20, 24))
    mac = ":".join(f"{b:02x}" for b in data[28:28 + min(hlen, 16)])
    opts: dict[int, bytes] = {}
    i = 240
    while i < len(data):
        code = data[i]
        if code == 255:
            break
        if code == 0:
            i += 1
            continue
        if i + 1 >= len(data):
            break
        ln = data[i + 1]
        opts[code] = data[i + 2:i + 2 + ln]
        i += 2 + ln
    return {"op": op, "xid": xid, "flags": flags, "ciaddr": ciaddr, "giaddr": giaddr, "mac": mac, "chaddr": data[28:44],
            "type": opts.get(53, b"\x00")[0], "requested": socket.inet_ntoa(opts[50]) if len(opts.get(50, b"")) == 4 else None,
            "server_id": socket.inet_ntoa(opts[54]) if len(opts.get(54, b"")) == 4 else None,
            "hostname": opts.get(12, b"").decode(errors="ignore").strip()[:63], "opts": opts}


def build_packet(req: dict, msg_type: int, yiaddr: str, server_ip: str, options: list[tuple[int, bytes]]) -> bytes:
    head = struct.pack("!BBBBIHH", 2, 1, 6, 0, req["xid"], 0, req["flags"])
    head += socket.inet_aton(req["ciaddr"] if msg_type == ACK and req["type"] == INFORM else "0.0.0.0")
    head += socket.inet_aton(yiaddr if msg_type != NAK else "0.0.0.0")
    head += socket.inet_aton(server_ip) + socket.inet_aton(req["giaddr"])
    head += req["chaddr"].ljust(16, b"\x00") + b"\x00" * 192 + MAGIC
    body = bytes([53, 1, msg_type]) + bytes([54, 4]) + socket.inet_aton(server_ip)
    for code, val in options:
        body += bytes([code, len(val)]) + val
    pkt = head + body + b"\xff"
    return pkt.ljust(300, b"\x00")


def _ip(s: str) -> bytes:
    return socket.inet_aton(s)


class DHCPServer:
    def __init__(self, ctx):
        self.ctx = ctx
        self.transport = None
        self.offers: dict[str, tuple[str, float]] = {}  # mac -> (ip, caduca)
        self.running = False
        self.error = ""
        self.stats = {"discover": 0, "request": 0, "ack": 0, "nak": 0, "release": 0, "decline": 0, "inform": 0}
        self.other_servers: list[dict] = []

    # ---------------------------------------------------------------- config
    def cfg(self) -> dict:
        return self.ctx.settings.get("dhcp") or {}

    def suggest(self) -> dict:
        ifs = netinfo.interfaces()
        if not ifs:
            return {}
        itf = ifs[0]
        net = ipaddress.ip_network(itf["network"])
        hosts = list(net.hosts())
        gw = netinfo.default_gateway() or str(hosts[0])
        start = hosts[min(99, len(hosts) - 1)] if len(hosts) > 200 else hosts[len(hosts) // 2]
        end = hosts[min(198, len(hosts) - 1)] if len(hosts) > 200 else hosts[-2]
        return {"interface": itf["name"], "server_ip": itf["ip"], "netmask": str(net.netmask), "router": gw,
                "range_start": str(start), "range_end": str(end), "dns": [itf["ip"]], "domain": "lan", "lease_time": 86400}

    def validate(self, c: dict) -> dict:
        net = ipaddress.ip_network(f"{c['server_ip']}/{c['netmask']}", strict=False)
        a, b = ipaddress.ip_address(c["range_start"]), ipaddress.ip_address(c["range_end"])
        if a not in net or b not in net or a > b:
            raise ValueError("El rango debe estar dentro de la red del servidor y ser ascendente")
        if ipaddress.ip_address(c["router"]) not in net:
            raise ValueError("La puerta de enlace debe estar en la misma red")
        if int(b) - int(a) > 4096:
            raise ValueError("Rango demasiado grande (máx. 4096 direcciones)")
        for s in c.get("static", []):
            if ipaddress.ip_address(s["ip"]) not in net:
                raise ValueError(f"La reserva {s['ip']} no está en la red")
        int(c.get("lease_time", 86400))
        return c

    # ---------------------------------------------------------------- ciclo
    async def start(self) -> None:
        c = self.cfg()
        if not c.get("enabled"):
            return
        try:
            self.validate(c)
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            if c.get("interface"):
                try:
                    sock.setsockopt(socket.SOL_SOCKET, 25, c["interface"].encode())  # SO_BINDTODEVICE
                except OSError as exc:
                    log.warning("SO_BINDTODEVICE falló (%s); escuchando en todas las interfaces", exc)
            sock.bind(("0.0.0.0", 67))
            sock.setblocking(False)
            loop = asyncio.get_running_loop()
            self.transport, _ = await loop.create_datagram_endpoint(lambda: _Proto(self), sock=sock)
            self.running = True
            self.error = ""
            log.info("DHCP activo: %s – %s", c["range_start"], c["range_end"])
            self.ctx.db.event("info", "dhcp", f"Servidor DHCP iniciado ({c['range_start']} – {c['range_end']})")
        except Exception as exc:
            self.error = f"No se pudo iniciar el DHCP: {exc}"
            self.running = False
            log.error(self.error)
            self.ctx.db.event("error", "dhcp", self.error)

    async def stop(self) -> None:
        if self.transport:
            self.transport.close()
            self.transport = None
        self.running = False

    async def restart(self) -> None:
        await self.stop()
        await self.start()

    # ---------------------------------------------------------------- asignación
    def leases(self) -> dict[str, dict]:
        return {r["mac"]: r for r in self.ctx.db.query("SELECT * FROM dhcp_leases")}

    def _static(self, mac: str) -> dict | None:
        return next((s for s in self.cfg().get("static", []) if s["mac"].lower() == mac), None)

    def _in_use(self, ip: str, mac: str, leases: dict) -> bool:
        now = time.time()
        for m, l in leases.items():
            if m != mac and l["ip"] == ip and l["expires"] > now:
                return True
        for m, (oip, exp) in self.offers.items():
            if m != mac and oip == ip and exp > now:
                return True
        if any(s["ip"] == ip and s["mac"].lower() != mac for s in self.cfg().get("static", [])):
            return True
        c = self.cfg()
        return ip in (c["server_ip"], c["router"])

    def allocate(self, mac: str, requested: str | None) -> str | None:
        c = self.cfg()
        st = self._static(mac)
        if st:
            return st["ip"]
        leases = self.leases()
        lo, hi = int(ipaddress.ip_address(c["range_start"])), int(ipaddress.ip_address(c["range_end"]))
        def ok(ip):
            return ip and lo <= int(ipaddress.ip_address(ip)) <= hi and not self._in_use(ip, mac, leases)
        cur = leases.get(mac)
        if cur and ok(cur["ip"]):
            return cur["ip"]
        if requested and ok(requested):
            return requested
        # IP ocupadas según ARP (equipos con IP fija configurada a mano)
        arp = {n["ip"] for n in netinfo.neighbors() if n["mac"] != mac}
        now = time.time()
        expired = None
        for n in range(lo, hi + 1):
            ip = str(ipaddress.ip_address(n))
            if ip in arp:
                continue
            l = next((x for x in leases.values() if x["ip"] == ip), None)
            if l is None and not self._in_use(ip, mac, leases):
                return ip
            if l and l["expires"] < now and expired is None:
                expired = ip
        return expired

    def _options(self, lease_time: int) -> list[tuple[int, bytes]]:
        c = self.cfg()
        net = ipaddress.ip_network(f"{c['server_ip']}/{c['netmask']}", strict=False)
        dns = [d for d in (c.get("dns") or [c["server_ip"]]) if d][:3]
        backup = c.get("isp_router") or c.get("router")  # en modo puerta de enlace el «router» es Bipon
        if c.get("backup_dns_router") and backup and backup not in dns:
            dns = (dns + [backup])[:3]  # si Bipon se apaga, los equipos siguen resolviendo con el router
        opts = [(51, struct.pack("!I", lease_time)), (58, struct.pack("!I", lease_time // 2)), (59, struct.pack("!I", lease_time * 7 // 8)),
                (1, _ip(c["netmask"])), (3, _ip(c["router"])), (6, b"".join(_ip(d) for d in dns)), (28, _ip(str(net.broadcast_address)))]
        if c.get("domain"):
            opts.append((15, c["domain"].encode()[:63]))
        if c.get("ntp"):
            opts.append((42, _ip(c["ntp"])))
        cap = self.ctx.captive.cfg() if hasattr(self.ctx, "captive") else {}
        if cap.get("enabled"):
            opts.append((114, self.ctx.captive.portal_url().encode()[:250]))  # RFC 8910: URL del portal cautivo
        return opts

    def handle(self, data: bytes, addr) -> tuple[bytes, tuple] | None:
        req = parse_packet(data)
        if not req or req["op"] != 1:
            return None
        c = self.cfg()
        server_ip = c["server_ip"]
        mac, t = req["mac"], req["type"]
        lease_time = int(c.get("lease_time", 86400))
        st = self._static(mac)
        if st and st.get("lease_time"):
            lease_time = int(st["lease_time"])
        self.stats[MSG_NAMES.get(t, "?").lower()] = self.stats.get(MSG_NAMES.get(t, "?").lower(), 0) + 1

        if t == DISCOVER:
            ip = self.allocate(mac, req["requested"])
            if not ip:
                log.warning("DHCP: sin direcciones libres para %s", mac)
                return None
            self.offers[mac] = (ip, time.time() + OFFER_HOLD)
            return self._reply(req, OFFER, ip, server_ip, self._options(lease_time))

        if t == REQUEST:
            if req["server_id"] and req["server_id"] != server_ip:
                self.offers.pop(mac, None)  # el cliente eligió otro servidor
                return None
            want = req["requested"] or (req["ciaddr"] if req["ciaddr"] != "0.0.0.0" else None)
            ip = self.allocate(mac, want)
            if not want or ip != want:
                if c.get("authoritative", True) and want:
                    self.stats["nak"] += 1
                    return self._reply(req, NAK, "0.0.0.0", server_ip, [])
                return None
            hostname = req["hostname"] or (st or {}).get("hostname", "")
            self.ctx.db.execute("INSERT INTO dhcp_leases(mac,ip,hostname,expires) VALUES (?,?,?,?) "
                                "ON CONFLICT(mac) DO UPDATE SET ip=excluded.ip, hostname=CASE WHEN excluded.hostname<>'' THEN excluded.hostname ELSE dhcp_leases.hostname END, expires=excluded.expires",
                                (mac, ip, hostname, time.time() + lease_time))
            self.offers.pop(mac, None)
            self.stats["ack"] += 1
            if hostname:
                self.ctx.db.execute("UPDATE devices SET hostname=? WHERE mac=? AND (hostname IS NULL OR hostname='')", (hostname, mac))
            vendor_class = req["opts"].get(60, b"").decode(errors="ignore")[:60]
            self.ctx.devices.track_dhcp(mac, ip, hostname, vendor_class, req["opts"].get(55))
            return self._reply(req, ACK, ip, server_ip, self._options(lease_time))

        if t == RELEASE:
            self.ctx.db.execute("UPDATE dhcp_leases SET expires=? WHERE mac=?", (time.time(), mac))
            return None
        if t == DECLINE:
            # la IP ya la usa otro equipo: se bloquea temporalmente con una MAC ficticia
            if req["requested"]:
                self.ctx.db.execute("INSERT OR REPLACE INTO dhcp_leases(mac,ip,hostname,expires) VALUES (?,?,?,?)",
                                    (f"declined:{req['requested']}", req["requested"], "conflicto", time.time() + 3600))
            return None
        if t == INFORM:
            return self._reply(req, ACK, "0.0.0.0", server_ip, [o for o in self._options(lease_time) if o[0] not in (51, 58, 59)])
        return None

    def _reply(self, req, mtype, ip, server_ip, opts) -> tuple[bytes, tuple]:
        pkt = build_packet(req, mtype, ip, server_ip, opts)
        if req["giaddr"] != "0.0.0.0":
            return pkt, (req["giaddr"], 67)
        if req["ciaddr"] != "0.0.0.0" and mtype != NAK:
            return pkt, (req["ciaddr"], 68)
        return pkt, ("255.255.255.255", 68)

    def lookup_name(self, name: str) -> str | None:
        """Resuelve equipo.dominio desde las concesiones (para el DNS local)."""
        dom = (self.cfg().get("domain") or "lan").lower()
        host = name[: -len(dom) - 1] if name.endswith("." + dom) else (name if "." not in name else None)
        if not host:
            return None
        row = self.ctx.db.one("SELECT ip FROM dhcp_leases WHERE lower(hostname)=? AND expires>? ORDER BY expires DESC LIMIT 1",
                              (host.lower(), time.time()))
        return row["ip"] if row else None

    def status(self) -> dict:
        now = time.time()
        leases = self.ctx.db.query("SELECT * FROM dhcp_leases ORDER BY expires DESC")
        names = {d["mac"]: d.get("name") for d in self.ctx.devices.devices.values()}
        for l in leases:
            l["active"] = l["expires"] > now
            l["name"] = names.get(l["mac"]) or ""
        return {"running": self.running, "error": self.error, "config": self.cfg(), "suggested": self.suggest(),
                "leases": leases, "stats": self.stats, "other_servers": self.other_servers, "takeover": self.takeover()}

    def takeover(self) -> dict:
        """Cuántos equipos en línea ya reciben su IP de Bipon (modo principal junto al DHCP del router)."""
        c = self.cfg()
        if not c.get("range_start"):
            return {}
        now = time.time()
        active = {r["mac"] for r in self.ctx.db.query("SELECT mac FROM dhcp_leases WHERE expires>?", (now,))}
        static = {s["mac"].lower() for s in c.get("static", [])}
        skip = {c.get("server_ip"), c.get("router")}
        mine, pending = [], []
        for mac, d in self.ctx.devices.devices.items():
            ip = d.get("ip")
            if not ip or mac.startswith("ip:") or ":" in ip or ip in skip or not d.get("online"):
                continue
            item = {"mac": mac, "ip": ip, "name": d.get("name") or d.get("friendly") or d.get("hostname") or ip}
            (mine if mac in active or mac in static else pending).append(item)
        total = len(mine) + len(pending)
        return {"bipon": len(mine), "total": total, "pct": round(len(mine) / total * 100) if total else 0, "pending": pending[:50]}

    # ---------------------------------------------------------------- detección de otros servidores
    async def probe(self, timeout: float = 4.0) -> list[dict]:
        """Envía un DISCOVER con una MAC ficticia y recoge las ofertas de otros servidores DHCP."""
        loop = asyncio.get_running_loop()
        found: dict[str, dict] = {}
        fake = bytes([0x02]) + os.urandom(5)
        xid = int.from_bytes(os.urandom(4), "big")
        pkt = struct.pack("!BBBBIHH", 1, 1, 6, 0, xid, 0, 0x8000) + b"\x00" * 16 + fake.ljust(16, b"\x00") + b"\x00" * 192 + MAGIC
        pkt += bytes([53, 1, DISCOVER, 55, 3, 1, 3, 6, 255])
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            sock.bind(("0.0.0.0", 68))
            sock.setblocking(False)
        except OSError as exc:
            sock.close()
            return [{"error": f"No se puede escuchar en el puerto 68: {exc}"}]
        try:
            await loop.sock_sendto(sock, pkt, ("255.255.255.255", 67))
            end = time.monotonic() + timeout
            while time.monotonic() < end:
                try:
                    data, addr = await asyncio.wait_for(loop.sock_recvfrom(sock, 2048), end - time.monotonic())
                except asyncio.TimeoutError:
                    break
                r = parse_packet(data)
                if r and r["op"] == 2 and r["xid"] == xid:
                    sid = r["server_id"] or addr[0]
                    own = self.running and sid == self.cfg().get("server_ip")
                    dns = r["opts"].get(6, b"")
                    found[sid] = {"server": sid, "own": own, "offered_ip": socket.inet_ntoa(data[16:20]),
                                  "router": socket.inet_ntoa(r["opts"][3][:4]) if len(r["opts"].get(3, b"")) >= 4 else None,
                                  "dns": [socket.inet_ntoa(dns[i:i + 4]) for i in range(0, len(dns) - 3, 4)]}
        finally:
            sock.close()
        self.other_servers = list(found.values())
        foreign = [s for s in self.other_servers if not s.get("own")]
        if self.running and foreign:
            self.ctx.notify.emit("dhcp_conflict", "Otro servidor DHCP en la red",
                                 f"Además de Bipon Net responde {foreign[0]['server']}. Desactiva el DHCP del router para evitar conflictos.",
                                 key=foreign[0]["server"])
        return self.other_servers

    async def loop(self) -> None:
        await self.start()
        while True:
            await asyncio.sleep(3600)
            now = time.time()
            self.offers = {m: o for m, o in self.offers.items() if o[1] > now}
            if self.running:
                try:
                    await self.probe()
                except Exception:  # pragma: no cover
                    log.exception("sondeo DHCP")


class _Proto(asyncio.DatagramProtocol):
    def __init__(self, server: DHCPServer):
        self.server = server
        self.transport = None

    def connection_made(self, transport):
        self.transport = transport

    def datagram_received(self, data, addr):
        try:
            out = self.server.handle(data, addr)
        except Exception:  # pragma: no cover
            log.exception("paquete DHCP inválido")
            return
        if out and self.transport:
            self.transport.sendto(*out)
