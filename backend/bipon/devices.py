"""Descubrimiento de dispositivos, perfiles y controles parentales por dispositivo."""
from __future__ import annotations

import asyncio
import ipaddress
import logging
import socket
import time
from datetime import datetime

from dnslib import DNSRecord, QTYPE

import json

from . import discovery, netinfo
from .config import DEFAULT_PROFILE_ID, Settings
from .db import Database
from .dns.filter import Policy
from .dns.upstream import query_server
from .pinger import ping

log = logging.getLogger("bipon.devices")

ONLINE_WINDOW = 600  # s sin verse -> desconectado
MAX_HOSTS_PER_SUBNET = 1024


def _hhmm(s: str) -> int:
    h, m = s.split(":")
    return int(h) * 60 + int(m)


def schedule_active(entry: dict, now: datetime | None = None) -> bool:
    """Ventana horaria activa; soporta ventanas que cruzan la medianoche (22:00-07:00)."""
    now = now or datetime.now()
    try:
        start, end = _hhmm(entry["start"]), _hhmm(entry["end"])
    except Exception:
        return False
    days = entry.get("days") or list(range(7))
    minute = now.hour * 60 + now.minute
    wd = now.weekday()
    if start <= end:
        return wd in days and start <= minute < end
    # cruza medianoche: la parte de después de medianoche pertenece al día anterior
    if minute >= start:
        return wd in days
    if minute < end:
        return (wd - 1) % 7 in days
    return False


class DeviceManager:
    def __init__(self, db: Database, settings: Settings):
        self.db = db
        self.settings = settings
        self.oui = netinfo.OUIDatabase()
        self.ip_to_mac: dict[str, str] = {}
        self.devices: dict[str, dict] = {}
        self.activity: dict[str, float] = {}
        self._policy_cache: dict[str, tuple[float, Policy]] = {}
        self.scanning = False
        self.last_scan = 0.0
        self.gateway: str | None = None
        self.self_ip: str | None = None
        self.ip6_to_mac: dict[str, str] = {}
        self._ip6_refreshed = 0.0
        self.family = None  # FamilyManager (tiempo de pantalla), lo asigna el contexto
        self.listeners: set[asyncio.Queue] = set()
        self._last_seen_arp: dict[str, float] = {}
        self.last_sweep = 0.0
        self.on_new_device = None
        self.on_policy_change = None  # aplica al instante los cambios de control de Internet
        self.load()

    # ---- estado ---------------------------------------------------------------
    def load(self) -> None:
        rows = self.db.query("SELECT * FROM devices")
        self.devices = {r["mac"]: r for r in rows}
        self.ip_to_mac = {r["ip"]: r["mac"] for r in rows if r["ip"]}
        self._policy_cache.clear()

    def invalidate(self) -> None:
        self._policy_cache.clear()

    def track_client(self, ip: str) -> None:
        self.activity[ip] = time.time()

    def mac_for(self, ip: str) -> str | None:
        """MAC de un cliente (IPv4 por ARP/escaneo; IPv6 por la tabla de vecinos)."""
        if ip.startswith("::ffff:"):
            ip = ip[7:]
        mac = self.ip_to_mac.get(ip)
        if mac or ":" not in ip:
            return mac
        mac = self.ip6_to_mac.get(ip)
        if mac is None and time.time() - self._ip6_refreshed > 30:
            self._ip6_refreshed = time.time()
            for n in netinfo.neighbors6():
                self.ip6_to_mac[n["ip"]] = n["mac"]
            mac = self.ip6_to_mac.get(ip)
        return mac

    def track_dhcp(self, mac: str, ip: str, hostname: str, vendor_class: str = "", params: bytes | None = None) -> None:
        """Alta/actualización inmediata desde el servidor DHCP (incluye huella del sistema operativo)."""
        now = time.time()
        os_name = discovery.os_from_dhcp(vendor_class, params)
        if os_name:
            self.db.execute("UPDATE devices SET os=?, dhcp_vendor=? WHERE mac=?", (os_name, vendor_class, mac))
        if mac not in self.devices:
            vendor = self.oui.lookup(mac)
            self.db.execute("INSERT OR IGNORE INTO devices(mac,ip,hostname,vendor,kind,profile,first_seen,last_seen,online) VALUES (?,?,?,?,?,?,?,?,1)",
                            (mac, ip, hostname, vendor, netinfo.guess_kind(vendor, hostname), DEFAULT_PROFILE_ID, now, now))
            if self.on_new_device:
                self.on_new_device({"mac": mac, "ip": ip, "hostname": hostname, "vendor": vendor})
            if os_name:
                self.db.execute("UPDATE devices SET os=?, dhcp_vendor=? WHERE mac=?", (os_name, vendor_class, mac))
        else:
            self.db.execute("UPDATE devices SET ip=?, last_seen=?, online=1 WHERE mac=?", (ip, now, mac))
        self.db.execute("UPDATE devices SET ip=NULL WHERE ip=? AND mac<>?", (ip, mac))
        self.load()
        self.publish("updated", mac)

    def ptr_name(self, ip: str) -> str | None:
        mac = self.ip_to_mac.get(ip)
        d = self.devices.get(mac) if mac else None
        if not d:
            return None
        name = d.get("name") or d.get("hostname")
        if not name:
            return None
        slug = "".join(c if c.isalnum() or c == "-" else "-" for c in name.lower()).strip("-")
        return f"{slug}.lan" if slug and "." not in slug else name

    # ---- políticas ------------------------------------------------------------
    def profiles(self) -> dict[str, dict]:
        return {p["id"]: p for p in self.settings.get("profiles", [])}

    def policy_for(self, ip: str) -> Policy:
        now = time.time()
        hit = self._policy_cache.get(ip)
        if hit and now - hit[0] < 15:
            return hit[1]
        mac = self.mac_for(ip)
        dev = self.devices.get(mac) if mac else None
        profiles = self.profiles()
        prof = profiles.get((dev or {}).get("profile") or DEFAULT_PROFILE_ID) or profiles.get(DEFAULT_PROFILE_ID) or {}
        services = set(prof.get("blocked_services", []))
        block_all, reason = bool(prof.get("block_all")), "Perfil sin Internet" if prof.get("block_all") else ""
        for sch in prof.get("schedules", []):
            if not sch.get("enabled", True) or not schedule_active(sch):
                continue
            if sch.get("action", "block_all") == "block_all":
                block_all, reason = True, f"Horario: {sch.get('label') or sch['start'] + '–' + sch['end']}"
            else:
                services |= set(sch.get("services", []))
        if dev and self.family is not None and prof.get("limits"):
            spent, all_out, why = self.family.exceeded(mac, prof)
            services |= spent
            if all_out and not block_all:
                block_all, reason = True, why
            elif spent and not reason:
                reason = "Tiempo de pantalla agotado"
        if dev:
            if dev.get("paused") or (dev.get("paused_until") or 0) > now:
                block_all, reason = True, "Internet pausado para este dispositivo"
        parental = bool(prof.get("schedules") or prof.get("limits") or prof.get("block_all") or prof.get("blocked_services")
                        or (dev or {}).get("paused") or ((dev or {}).get("paused_until") or 0) > 0)
        pol = Policy(
            profile_id=prof.get("id", DEFAULT_PROFILE_ID), profile_name=prof.get("name", "Predeterminado"),
            filtering=prof.get("filtering", True), threat=prof.get("threat_protection", True),
            adult=prof.get("adult_filter", False), safesearch=prof.get("safesearch", False),
            youtube=prof.get("youtube_restricted", False), services=frozenset(services),
            block_all=block_all, block_reason=reason, device=(dev or {}).get("name") or "", short_ttl=parental,
        )
        self._policy_cache[ip] = (now, pol)
        return pol

    # ---- descubrimiento -------------------------------------------------------
    async def _probe(self, hosts: list[str]) -> None:
        """Envía un datagrama UDP a cada IP: obliga al kernel a resolver ARP (barrido sin privilegios)."""
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setblocking(False)
        try:
            for i, h in enumerate(hosts):
                try:
                    sock.sendto(b"\x00", (h, 9))
                except OSError:
                    pass
                if i % 64 == 63:
                    await asyncio.sleep(0.02)
        finally:
            sock.close()

    # ---- presencia fiable en Wi-Fi --------------------------------------------
    @staticmethod
    def _wifi_like(d: dict) -> bool:
        """Móviles, tablets, portátiles y MAC aleatorias (privadas): casi siempre Wi-Fi con ahorro de energía."""
        mac = d.get("mac") or ""
        try:
            random_mac = bool(int(mac.split(":")[0], 16) & 0x02)
        except ValueError:
            random_mac = False
        return random_mac or d.get("kind") in ("phone", "tablet", "laptop", "watch", "speaker", "tv", "iot", "camera")

    async def _wake_probe(self, targets: list[dict], ifaces: list[dict]) -> set[str]:
        """Pregunta directamente a cada dispositivo por su MAC (ARP unicast, 3 intentos) y toca puertos
        que responden aunque el móvil esté en reposo. El punto de acceso guarda las tramas unicast hasta
        que el equipo despierta, así que contestan aunque ignoren los sondeos de difusión.
        Devuelve las MAC que respondieron."""
        seen: set[str] = set()
        if not targets:
            return seen
        itf = ifaces[0] if ifaces else None
        raw = None
        if itf and itf.get("name") and itf.get("name") != "?" and hasattr(socket, "AF_PACKET"):
            try:
                raw = socket.socket(socket.AF_PACKET, socket.SOCK_RAW, socket.htons(0x0806))
                raw.bind((itf["name"], 0))
                raw.setblocking(False)
            except (OSError, PermissionError):
                raw = None
        mac_s = (itf or {}).get("mac") or ""
        if not mac_s and itf and itf.get("name") not in (None, "?"):
            try:
                mac_s = open(f"/sys/class/net/{itf['name']}/address").read().strip()
            except OSError:
                mac_s = ""
        src_mac = bytes.fromhex(mac_s.replace(":", "")) if mac_s else b""
        src_ip = socket.inet_aton(itf["ip"]) if itf else b""
        by_ip = {t["ip"]: t["mac"] for t in targets}

        def arp_req(dst_mac: str, dst_ip: str) -> bytes:
            dm = bytes.fromhex(dst_mac.replace(":", ""))
            return dm + src_mac + b"\x08\x06" + b"\x00\x01\x08\x00\x06\x04\x00\x01" + src_mac + src_ip + b"\x00" * 6 + socket.inet_aton(dst_ip)

        async def tcp_touch(ip: str):
            # iPhone/iPad (62078), Android/Chromecast (8008/8009), Windows (445/139), genéricos (80/443)
            for port in (62078, 8009, 445, 80, 443):
                try:
                    r, w = await asyncio.wait_for(asyncio.open_connection(ip, port), 0.8)
                    w.close()
                    return ip
                except ConnectionRefusedError:
                    return ip  # un RST también demuestra que está despierto
                except Exception:
                    continue
            return None

        udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        udp.setblocking(False)
        try:
            for attempt in range(3):
                if all(t["mac"] in seen for t in targets):
                    break
                for t in targets:
                    if t["mac"] in seen:
                        continue
                    if raw is not None and len(src_mac) == 6:
                        try:
                            raw.send(arp_req(t["mac"], t["ip"]))
                        except OSError:
                            pass
                    for port in (5353, 9):  # mDNS y «discard»: fuerzan ARP y despiertan la pila de red
                        try:
                            udp.sendto(b"\x00", (t["ip"], port))
                        except OSError:
                            pass
                if raw is not None:
                    end = time.time() + 1.2
                    while time.time() < end and not all(t["mac"] in seen for t in targets):
                        try:
                            pkt = raw.recv(64)
                            if len(pkt) >= 42 and pkt[12:14] == b"\x08\x06" and pkt[20:22] == b"\x00\x02":
                                ip = socket.inet_ntoa(pkt[28:32])
                                mac = pkt[22:28].hex(":")
                                if by_ip.get(ip) == mac:
                                    seen.add(mac)
                        except BlockingIOError:
                            await asyncio.sleep(0.05)
                        except OSError:
                            break
                else:
                    await asyncio.sleep(1.2)
            missing = [t for t in targets if t["mac"] not in seen]
            for ip in await asyncio.gather(*(tcp_touch(t["ip"]) for t in missing[:40])):
                if ip and by_ip.get(ip):
                    seen.add(by_ip[ip])
        finally:
            udp.close()
            if raw is not None:
                raw.close()
        return seen

    async def _ptr_lookup(self, ip: str, servers: list[str]) -> str:
        name = ipaddress.ip_address(ip).reverse_pointer
        q = DNSRecord.question(name, "PTR").pack()
        for srv in servers:
            try:
                resp, _ = await query_server(srv, q, timeout=1.0)
                rec = DNSRecord.parse(resp)
                for rr in rec.rr:
                    if rr.rtype == QTYPE.PTR:
                        return str(rr.rdata).rstrip(".")
            except Exception:
                continue
        return ""

    # ---- eventos en tiempo real ---------------------------------------------
    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=200)
        self.listeners.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self.listeners.discard(q)

    def publish(self, kind: str, mac: str, extra: dict | None = None) -> None:
        d = self.devices.get(mac) or {}
        ev = {"type": kind, "mac": mac, "ts": time.time(), "name": d.get("name") or d.get("hostname") or d.get("vendor") or d.get("ip") or mac,
              "ip": d.get("ip"), **(extra or {})}
        for q in list(self.listeners):
            try:
                q.put_nowait(ev)
            except asyncio.QueueFull:
                pass

    # ---- descubrimiento -------------------------------------------------------
    def _hosts(self, ifaces: list[dict]) -> list[str]:
        subnets = self.settings.get("devices.subnets") or [i["network"] for i in ifaces]
        hosts: list[str] = []
        for sn in subnets:
            try:
                net = ipaddress.ip_network(sn, strict=False)
            except ValueError:
                continue
            if net.num_addresses > MAX_HOSTS_PER_SUBNET + 2:
                own = next((ipaddress.ip_address(i["ip"]) for i in ifaces if ipaddress.ip_address(i["ip"]) in net), None)
                net = ipaddress.ip_network(f"{own}/24", strict=False) if own else None
            if net:
                hosts += [str(h) for h in net.hosts()]
        return hosts

    def _mark_seen(self, mac: str, ip: str, now: float) -> bool:
        """Registra presencia. Devuelve True si el dispositivo es nuevo."""
        self._last_seen_arp[mac] = now
        d = self.devices.get(mac)
        if d is None:
            vendor = self.oui.lookup(mac)
            kind = netinfo.guess_kind(vendor, "")
            if ip == self.gateway:
                kind = "router"
            elif ip == self.self_ip:
                kind = "server"
            self.db.execute("INSERT OR IGNORE INTO devices(mac,ip,hostname,vendor,kind,profile,first_seen,last_seen,online) VALUES (?,?,?,?,?,?,?,?,1)",
                            (mac, ip, "", vendor, kind, DEFAULT_PROFILE_ID, now, now))
            self.db.execute("UPDATE devices SET ip=NULL WHERE ip=? AND mac<>?", (ip, mac))
            self.load()
            return True
        changed_ip = d.get("ip") != ip
        if changed_ip or not d.get("online") or now - (d.get("last_seen") or 0) > 60:
            self.db.execute("UPDATE devices SET ip=?, last_seen=?, online=1 WHERE mac=?", (ip, now, mac))
            if changed_ip:
                self.db.execute("UPDATE devices SET ip=NULL WHERE ip=? AND mac<>?", (ip, mac))
            was_online = d.get("online")
            d.update(ip=ip, last_seen=now, online=1)
            if changed_ip:
                self.load()
            if not was_online:
                self.publish("online", mac)
        return False

    async def _on_new(self, mac: str) -> None:
        self.publish("new", mac)
        d = self.devices.get(mac) or {}
        self.db.event("info", "dispositivos", f"Nuevo dispositivo detectado: {d.get('vendor') or d.get('ip') or mac}")
        try:
            await self.enrich([mac])
        except Exception:  # pragma: no cover
            log.exception("enriquecimiento")
        d = self.devices.get(mac) or {}
        if self.on_new_device:
            self.on_new_device({"mac": mac, "ip": d.get("ip"), "name": d.get("name") or d.get("hostname"),
                                "vendor": d.get("brand_name") or d.get("vendor"), "hostname": d.get("hostname")})
        self.publish("updated", mac)

    async def sweep(self) -> dict:
        """Barrido ligero: sondeo ARP de toda la subred y actualización de presencia."""
        ifaces = await asyncio.to_thread(netinfo.interfaces)
        self.gateway = netinfo.default_gateway()
        self.self_ip = ifaces[0]["ip"] if ifaces else netinfo.primary_ip()
        await self._probe(self._hosts(ifaces))
        now0 = time.time()
        quiet = [{"mac": m, "ip": d["ip"]} for m, d in self.devices.items()
                 if d.get("ip") and not m.startswith("ip:") and d.get("ip") not in (self.self_ip, self.gateway)
                 and 40 < now0 - max(self._last_seen_arp.get(m, d.get("last_seen") or 0), self.activity.get(d["ip"], 0)) < 1800]
        woke = await self._wake_probe(quiet, ifaces) if quiet else set()
        if not quiet:
            await asyncio.sleep(2.5)
        neigh = await asyncio.to_thread(netinfo.neighbors)
        now = time.time()
        for mac in woke:
            d = self.devices.get(mac)
            if d and d.get("ip"):
                self._mark_seen(mac, d["ip"], now)
        new = []
        for n in neigh:
            if n["state"] in ("FAILED", "INCOMPLETE"):
                continue
            if n["state"] == "STALE" and now - self.activity.get(n["ip"], 0) > ONLINE_WINDOW:
                continue
            if self._mark_seen(n["mac"], n["ip"], now):
                new.append(n["mac"])
        for itf in ifaces:
            if itf.get("mac") and self._mark_seen(itf["mac"].lower(), itf["ip"], now):
                new.append(itf["mac"].lower())
        # desconexiones: ni ARP ni tráfico DNS durante ~3 barridos
        grace = max(90, int(self.settings.get("devices.offline_after", 90)))
        for mac, d in list(self.devices.items()):
            if not d.get("online"):
                continue
            arp = self._last_seen_arp.get(mac, d.get("last_seen") or 0)
            dns = self.activity.get(d.get("ip") or "", 0)
            age = now - max(arp, dns)
            limit = max(grace, 420) if self._wifi_like({**d, "mac": mac}) else grace  # un móvil en reposo puede tardar en contestar
            d["sleeping"] = bool(self._wifi_like({**d, "mac": mac}) and age > 90 and age <= limit)
            if age > limit:
                self.db.execute("UPDATE devices SET online=0 WHERE mac=?", (mac,))
                d["online"] = 0
                self.publish("offline", mac)
        for ip, ts in self.activity.items():
            mac = self.ip_to_mac.get(ip)
            if mac and now - ts < ONLINE_WINDOW and not (self.devices.get(mac) or {}).get("online"):
                self._mark_seen(mac, ip, now)
        if self.last_scan:  # en el primer escaneo no se tratan como «nuevos»
            for mac in new:
                asyncio.create_task(self._on_new(mac))
        self.last_sweep = now
        return {"found": sum(1 for d in self.devices.values() if d.get("online")), "new": len(new)}

    async def watch_neighbors(self) -> None:
        """Escucha los cambios de la tabla ARP del kernel: conexiones al instante."""
        import re as _re
        while True:
            try:
                proc = await asyncio.create_subprocess_exec("ip", "-4", "monitor", "neigh", stdout=asyncio.subprocess.PIPE,
                                                            stderr=asyncio.subprocess.DEVNULL)
            except FileNotFoundError:
                return
            assert proc.stdout
            async for raw in proc.stdout:
                line = raw.decode(errors="ignore")
                m = _re.match(r"^(?:Deleted )?(\d+\.\d+\.\d+\.\d+) dev (\S+)(?: lladdr ([0-9a-f:]{17}))?.*?(REACHABLE|STALE|DELAY|PROBE|FAILED|PERMANENT)?\s*$", line.strip())
                if not m:
                    continue
                ip, dev, mac, state = m.groups()
                if not mac or state in ("FAILED", None) or line.startswith("Deleted"):
                    continue
                if state in ("REACHABLE", "DELAY", "PROBE", "PERMANENT"):
                    if self._mark_seen(mac.lower(), ip, time.time()) and self.last_scan:
                        asyncio.create_task(self._on_new(mac.lower()))
            await proc.wait()
            await asyncio.sleep(5)

    async def enrich(self, macs: list[str] | None = None) -> None:
        """Nombre, marca, modelo, sistema, servicios, latencia y jerarquía."""
        targets = {m: d for m, d in self.devices.items() if d.get("ip") and (macs is None or m in macs)}
        if not targets:
            return
        ips = [d["ip"] for d in targets.values()]
        dns_servers = []
        if self.settings.get("dns.conditional_forwarding.enabled") and self.settings.get("dns.conditional_forwarding.server"):
            dns_servers.append(self.settings.get("dns.conditional_forwarding.server"))
        if self.gateway:
            dns_servers.append(self.gateway)
        resolve = self.settings.get("devices.resolve_names", True)
        mdns_t = asyncio.create_task(discovery.mdns_scan())
        ssdp_t = asyncio.create_task(discovery.ssdp_scan())
        nb_t = asyncio.create_task(discovery.netbios_names(ips))

        async def per(mac, d):
            ip = d["ip"]
            host = d.get("hostname") or ""
            if resolve and not host:
                host = await self._ptr_lookup(ip, dns_servers) or ""
            pr = await ping(ip, count=1, timeout=1)
            ports = None
            if macs is not None or not d.get("services") or time.time() - (d.get("enriched") or 0) > 6 * 3600:
                ports = await discovery.service_ports(ip)
            return mac, host, pr["avg"], ports
        per_results = await asyncio.gather(*(per(m, d) for m, d in targets.items()))
        mdns, ssdp, nb = await asyncio.gather(mdns_t, ssdp_t, nb_t, return_exceptions=True)
        mdns = mdns if isinstance(mdns, dict) else {}
        ssdp = ssdp if isinstance(ssdp, dict) else {}
        nb = nb if isinstance(nb, dict) else {}
        now = time.time()
        for mac, host, lat, ports in per_results:
            d = targets[mac]
            ip = d["ip"]
            md, sd = mdns.get(ip, {}), ssdp.get(ip, {})
            host = host or md.get("hostname") or nb.get(ip, "") or ""
            model = sd.get("modelName") or md.get("model") or d.get("model") or ""
            if sd.get("modelNumber") and sd.get("modelNumber") not in model:
                model = f"{model} {sd['modelNumber']}".strip()
            friendly = sd.get("friendlyName") or md.get("name") or ""
            hv = discovery.hypervisor_of_mac(mac)
            brand, brand_name, brand_kind = discovery.brand_of(d.get("vendor"), sd.get("manufacturer"), model, friendly, host)
            if hv == "proxmox":
                brand, brand_name = "proxmox", "Proxmox (VM/LXC)"
            os_name = d.get("os") or ""
            svc = set(json.loads(d.get("services") or "[]"))
            if ports is not None:
                svc = {discovery.SERVICE_PORTS.get(p, str(p)) for p in ports}
                svc_ports = set(ports)
            else:
                svc_ports = set(json.loads(d.get("ports") or "[]"))
            svc |= {s.strip("_").split(".")[0] for s in md.get("services", [])}
            kind = d.get("kind") or "unknown"
            if not d.get("kind_manual"):
                guess = netinfo.guess_kind(" ".join(filter(None, [d.get("vendor"), sd.get("manufacturer"), brand_name])), host, f"{model} {friendly}")
                if ip == self.gateway:
                    kind = "router"
                elif 8006 in svc_ports or hv:
                    kind = "server"
                elif guess != "unknown":
                    kind = guess
                elif brand_kind:
                    kind = brand_kind
                if "_googlecast" in " ".join(md.get("services", [])) or "_airplay" in " ".join(md.get("services", [])) and "tv" in model.lower():
                    kind = "tv"
            self.db.execute(
                "UPDATE devices SET hostname=COALESCE(NULLIF(?,''),hostname), latency=?, brand=?, brand_name=?, model=COALESCE(NULLIF(?,''),model), "
                "friendly=COALESCE(NULLIF(?,''),friendly), os=COALESCE(NULLIF(?,''),os), services=?, ports=?, hypervisor=?, kind=?, enriched=? WHERE mac=?",
                (host, lat, brand, brand_name, model, friendly, os_name, json.dumps(sorted(svc)), json.dumps(sorted(svc_ports)), hv, kind, now, mac))
        self.load()
        self.compute_parents()

    def compute_parents(self) -> None:
        """Asocia máquinas virtuales/contenedores a su servidor anfitrión."""
        devs = self.devices
        proxmox_map = (self.settings.get("integrations") or {}).get("proxmox_map") or {}  # mac → ip del nodo (API Proxmox)
        hosts_by_hv: dict[str, list[str]] = {}
        for mac, d in devs.items():
            ports = set(json.loads(d.get("ports") or "[]"))
            if d.get("hypervisor"):
                continue
            if 8006 in ports:
                hosts_by_hv.setdefault("proxmox", []).append(mac)
                hosts_by_hv.setdefault("qemu", []).append(mac)
            if 902 in ports:
                hosts_by_hv.setdefault("vmware", []).append(mac)
        ip_to_mac = {d.get("ip"): m for m, d in devs.items() if d.get("ip")}
        changed = False
        for mac, d in devs.items():
            parent = None
            if mac in proxmox_map and proxmox_map[mac] in ip_to_mac:
                parent = ip_to_mac[proxmox_map[mac]]
            elif d.get("hypervisor"):
                cands = hosts_by_hv.get(d["hypervisor"], [])
                if len(cands) == 1:
                    parent = cands[0]
                elif cands and d.get("ip"):
                    # varios anfitriones: el de IP más cercana suele ser el correcto
                    def dist(m):
                        try:
                            return abs(int(ipaddress.ip_address(devs[m]["ip"])) - int(ipaddress.ip_address(d["ip"])))
                        except Exception:
                            return 1 << 32
                    parent = min(cands, key=dist)
            if parent == mac:
                parent = None
            if parent != d.get("auto_parent"):
                self.db.execute("UPDATE devices SET auto_parent=? WHERE mac=?", (parent, mac))
                changed = True
        if changed:
            self.load()

    async def scan(self) -> dict:
        if self.scanning:
            return {"status": "en curso"}
        self.scanning = True
        t0 = time.time()
        try:
            res = await self.sweep()
            await self.enrich()
            self.last_scan = time.time()
            return {**res, "seconds": round(time.time() - t0, 1)}
        finally:
            self.scanning = False

    async def scheduler(self) -> None:
        await self.oui.ensure()
        asyncio.create_task(self.watch_neighbors())
        last_enrich = 0.0
        while True:
            try:
                if time.time() - last_enrich > max(120, int(self.settings.get("devices.scan_interval", 300))):
                    last_enrich = time.time()
                    await self.scan()
                else:
                    await self.sweep()
            except Exception:  # pragma: no cover
                log.exception("escaneo de red falló")
            await asyncio.sleep(max(15, int(self.settings.get("devices.sweep_interval", 30))))

    # ---- acciones -------------------------------------------------------------
    def update(self, mac: str, patch: dict) -> dict | None:
        allowed = {"name", "kind", "profile", "paused", "paused_until", "notes", "parent", "limit_down", "limit_up", "kind_manual"}
        if "kind" in patch:
            patch["kind_manual"] = 1
        fields = {k: v for k, v in patch.items() if k in allowed}
        if fields:
            sets = ", ".join(f"{k}=?" for k in fields)
            self.db.execute(f"UPDATE devices SET {sets} WHERE mac=?", (*fields.values(), mac))
            self.load()
            if self.on_policy_change and fields.keys() & {"paused", "paused_until", "profile"}:
                self.on_policy_change([mac])
        return self.devices.get(mac)

    def delete(self, mac: str) -> None:
        self.db.execute("DELETE FROM devices WHERE mac=?", (mac,))
        self.load()

    @staticmethod
    def wake_on_lan(mac: str, broadcast: str = "255.255.255.255") -> None:
        h = mac.replace(":", "").replace("-", "")
        if len(h) != 12:
            raise ValueError("MAC inválida")
        packet = b"\xff" * 6 + bytes.fromhex(h) * 16
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        try:
            for port in (9, 7):
                s.sendto(packet, (broadcast, port))
        finally:
            s.close()
