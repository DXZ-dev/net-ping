"""Módulo VPN: WireGuard, OpenVPN y Tailscale.

Gestiona servidores y clientes VPN directamente desde Bipon Net. Requiere
CAP_NET_ADMIN y /dev/net/tun (Docker: ver docker-compose.yml)."""
from __future__ import annotations

import asyncio
import datetime as dt
import ipaddress
import json
import logging
import os
import re
import shutil
import time
from collections import deque
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

from . import netinfo
from .config import DATA_DIR
from .wireguard import keypair, psk, qr_svg

log = logging.getLogger("bipon.vpn")

NAME_RE = re.compile(r"^[a-zA-Z0-9_=+.-]{1,15}$")
PEER_RE = re.compile(r"^[\w .@-]{1,40}$")
VPN_DIR = DATA_DIR / "vpn"


def _check_name(name: str, rx=NAME_RE) -> str:
    if not rx.match(name or ""):
        raise ValueError("nombre inválido (letras, números, _ . - ; máx. 15)")
    return name


def _write_secret(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write(text)
    os.replace(tmp, path)


async def run(*args: str, timeout: float = 30, input: bytes | None = None) -> tuple[int, str]:
    try:
        proc = await asyncio.create_subprocess_exec(*args, stdin=asyncio.subprocess.PIPE if input else None,
                                                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
    except FileNotFoundError:
        return 127, f"{args[0]}: no instalado"
    try:
        out, _ = await asyncio.wait_for(proc.communicate(input), timeout)
    except asyncio.TimeoutError:
        proc.kill()
        return 124, "tiempo agotado"
    return proc.returncode or 0, out.decode(errors="ignore")


def tun_available() -> bool:
    return os.path.exists("/dev/net/tun")


def lan_iface() -> str:
    ifs = netinfo.interfaces()
    return ifs[0]["name"] if ifs and ifs[0]["name"] != "?" else "eth0"


def lan_net() -> str:
    ifs = netinfo.interfaces()
    return ifs[0]["network"] if ifs else "192.168.1.0/24"


async def ensure_forwarding(tunnel_net: str, out_iface: str) -> list[str]:
    """Activa reenvío IP y NAT para que los clientes VPN lleguen a la LAN/Internet."""
    notes = []
    try:
        with open("/proc/sys/net/ipv4/ip_forward", "r+") as fh:
            if fh.read().strip() != "1":
                fh.seek(0)
                fh.write("1")
    except OSError:
        notes.append("No se pudo activar net.ipv4.ip_forward (ejecuta en el host: sysctl -w net.ipv4.ip_forward=1)")
    if shutil.which("iptables"):
        rule = ["-t", "nat", "POSTROUTING", "-s", tunnel_net, "-o", out_iface, "-j", "MASQUERADE"]
        check = ["iptables", rule[0], rule[1], "-C", *rule[2:]]
        code, _ = await run(*check)
        if code != 0:
            code, out = await run("iptables", rule[0], rule[1], "-A", *rule[2:])
            if code != 0:
                notes.append(f"iptables: {out.strip()[:200]}")
        for chain_rule in (["-s", tunnel_net, "-j", "ACCEPT"], ["-d", tunnel_net, "-j", "ACCEPT"]):
            if (await run("iptables", "-C", "FORWARD", *chain_rule))[0] != 0:
                await run("iptables", "-A", "FORWARD", *chain_rule)
    else:
        notes.append("iptables no está instalado: sin NAT para clientes VPN")
    return notes


class ManagedProcess:
    """Proceso supervisado con reinicio automático y registro."""

    def __init__(self, name: str, args: list[str], cwd: Path | None = None):
        self.name, self.args, self.cwd = name, args, cwd
        self.proc: asyncio.subprocess.Process | None = None
        self.logs: deque[str] = deque(maxlen=400)
        self.task: asyncio.Task | None = None
        self.started = 0.0
        self.stopping = False
        self.on_line = None

    @property
    def running(self) -> bool:
        return bool(self.proc and self.proc.returncode is None)

    def start(self) -> None:
        if self.task and not self.task.done():
            return
        self.stopping = False
        self.task = asyncio.create_task(self._loop())

    async def _loop(self):
        backoff = 2
        while not self.stopping:
            try:
                self.proc = await asyncio.create_subprocess_exec(*self.args, cwd=self.cwd, stdout=asyncio.subprocess.PIPE,
                                                                 stderr=asyncio.subprocess.STDOUT)
            except FileNotFoundError:
                self.logs.append(f"{self.args[0]} no está instalado")
                return
            self.started = time.time()
            assert self.proc.stdout
            async for raw in self.proc.stdout:
                line = raw.decode(errors="ignore").rstrip()
                self.logs.append(line)
                if self.on_line:
                    self.on_line(line)
            code = await self.proc.wait()
            if self.stopping:
                break
            self.logs.append(f"[bipon] proceso terminó (código {code}); reinicio en {backoff}s")
            await asyncio.sleep(backoff)
            backoff = 2 if time.time() - self.started > 60 else min(backoff * 2, 60)

    async def stop(self) -> None:
        self.stopping = True
        if self.running:
            self.proc.terminate()
            try:
                await asyncio.wait_for(self.proc.wait(), 8)
            except asyncio.TimeoutError:
                self.proc.kill()
        if self.task:
            self.task.cancel()
            self.task = None


# =====================================================================
# WireGuard
# =====================================================================
class WireGuard:
    def __init__(self):
        self.dir = VPN_DIR / "wireguard"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.state_path = self.dir / "state.json"
        self.state = {"servers": {}, "clients": {}}
        if self.state_path.exists():
            self.state = json.loads(self.state_path.read_text())

    def save(self):
        _write_secret(self.state_path, json.dumps(self.state, indent=2))

    @property
    def available(self) -> bool:
        return bool(shutil.which("wg") and shutil.which("wg-quick"))

    def conf_path(self, name: str) -> Path:
        return self.dir / "conf" / f"{name}.conf"

    # ---- servidores ----------------------------------------------------
    def create_server(self, opts: dict) -> dict:
        name = _check_name(opts.get("name", "wg0"))
        if name in self.state["servers"] or name in self.state["clients"]:
            raise ValueError("ya existe una interfaz con ese nombre")
        net = ipaddress.ip_network(opts.get("network", "10.8.0.0/24"), strict=False)
        priv, pub = keypair()
        srv = {
            "name": name, "network": str(net), "address": f"{next(net.hosts())}/{net.prefixlen}",
            "port": int(opts.get("port", 51820)), "private_key": priv, "public_key": pub,
            "endpoint": (opts.get("endpoint") or "").strip(), "dns": (opts.get("dns") or "").strip(),
            "lan_net": opts.get("lan_net") or lan_net(), "full_tunnel": bool(opts.get("full_tunnel", True)),
            "mtu": int(opts.get("mtu", 1420)), "keepalive": int(opts.get("keepalive", 25)),
            "out_iface": opts.get("out_iface") or lan_iface(), "autostart": bool(opts.get("autostart", True)),
            "peers": [], "created": time.time(),
        }
        self.state["servers"][name] = srv
        self.save()
        return self._public(srv)

    def update_server(self, name: str, patch: dict) -> dict:
        srv = self._srv(name)
        for k in ("endpoint", "dns", "lan_net", "full_tunnel", "mtu", "keepalive", "port", "autostart", "out_iface"):
            if k in patch:
                srv[k] = patch[k]
        self.save()
        return self._public(srv)

    def delete(self, name: str) -> None:
        self.state["servers"].pop(name, None)
        self.state["clients"].pop(name, None)
        self.save()
        try:
            self.conf_path(name).unlink()
        except FileNotFoundError:
            pass

    def _srv(self, name: str) -> dict:
        if name not in self.state["servers"]:
            raise KeyError("servidor no encontrado")
        return self.state["servers"][name]

    def _next_ip(self, srv: dict) -> str:
        net = ipaddress.ip_network(srv["network"])
        used = {srv["address"].split("/")[0]} | {p["address"].split("/")[0] for p in srv["peers"]}
        for h in net.hosts():
            if str(h) not in used:
                return f"{h}/32"
        raise ValueError("no quedan direcciones en la red del túnel")

    def add_peer(self, name: str, opts: dict) -> dict:
        srv = self._srv(name)
        pname = opts.get("name", "").strip()
        if not PEER_RE.match(pname):
            raise ValueError("nombre de peer inválido")
        if any(p["name"] == pname for p in srv["peers"]):
            raise ValueError("ya existe un peer con ese nombre")
        priv, pub = keypair()
        peer = {"id": os.urandom(4).hex(), "name": pname, "private_key": priv, "public_key": pub, "psk": psk(),
                "address": self._next_ip(srv), "enabled": True, "full_tunnel": opts.get("full_tunnel"),
                "extra_allowed": opts.get("extra_allowed", ""), "created": time.time()}
        srv["peers"].append(peer)
        self.save()
        return peer

    def update_peer(self, name: str, pid: str, patch: dict) -> dict:
        srv = self._srv(name)
        for p in srv["peers"]:
            if p["id"] == pid:
                for k in ("enabled", "full_tunnel", "extra_allowed", "name"):
                    if k in patch:
                        p[k] = patch[k]
                self.save()
                return p
        raise KeyError("peer no encontrado")

    def remove_peer(self, name: str, pid: str) -> None:
        srv = self._srv(name)
        srv["peers"] = [p for p in srv["peers"] if p["id"] != pid]
        self.save()

    def server_conf(self, srv: dict) -> str:
        lines = ["# Generado por Bipon Net", "[Interface]", f"Address = {srv['address']}", f"ListenPort = {srv['port']}",
                 f"PrivateKey = {srv['private_key']}", f"MTU = {srv['mtu']}"]
        for p in srv["peers"]:
            if not p["enabled"]:
                continue
            allowed = p["address"]
            if p.get("extra_allowed"):
                allowed += ", " + p["extra_allowed"]
            lines += ["", "[Peer]", f"# {p['name']}", f"PublicKey = {p['public_key']}", f"PresharedKey = {p['psk']}",
                      f"AllowedIPs = {allowed}"]
        return "\n".join(lines) + "\n"

    def peer_conf(self, name: str, pid: str) -> dict:
        srv = self._srv(name)
        p = next((x for x in srv["peers"] if x["id"] == pid), None)
        if not p:
            raise KeyError("peer no encontrado")
        if not srv.get("endpoint"):
            raise ValueError("define el endpoint público del servidor (IP o dominio) antes de exportar")
        full = srv["full_tunnel"] if p.get("full_tunnel") is None else p["full_tunnel"]
        allowed = "0.0.0.0/0, ::/0" if full else f"{srv['network']}, {srv['lan_net']}"
        ep = srv["endpoint"]
        if ":" in ep and not ep.startswith("["):  # IPv6 literal
            ep = f"[{ep}]"
        iface = ["[Interface]", f"# {p['name']}", f"PrivateKey = {p['private_key']}", f"Address = {p['address']}"]
        if srv.get("dns"):
            iface.append(f"DNS = {srv['dns']}")
        iface.append(f"MTU = {srv['mtu']}")
        peer = ["[Peer]", f"PublicKey = {srv['public_key']}", f"PresharedKey = {p['psk']}", f"Endpoint = {ep}:{srv['port']}",
                f"AllowedIPs = {allowed}", f"PersistentKeepalive = {srv['keepalive']}"]
        conf = "\n".join(iface) + "\n\n" + "\n".join(peer) + "\n"
        return {"name": f"{p['name']}.conf", "content": conf, "qr": qr_svg(conf)}

    # ---- clientes (túneles salientes, p. ej. proveedor VPN) -----------
    def import_client(self, name: str, conf: str, autostart: bool = False) -> dict:
        name = _check_name(name)
        if name in self.state["servers"]:
            raise ValueError("ya existe un servidor con ese nombre")
        if "[Interface]" not in conf or "[Peer]" not in conf:
            raise ValueError("no parece una configuración WireGuard válida")
        bad = re.search(r"(?im)^\s*(PreUp|PostUp|PreDown|PostDown)\s*=", conf)
        if bad:
            raise ValueError("por seguridad no se permiten scripts PreUp/PostUp en configuraciones importadas")
        self.state["clients"][name] = {"name": name, "conf": conf, "autostart": autostart, "created": time.time()}
        self.save()
        return {"name": name}

    # ---- ciclo de vida ----------------------------------------------------
    def _render(self, name: str) -> Path:
        if name in self.state["servers"]:
            text = self.server_conf(self.state["servers"][name])
        elif name in self.state["clients"]:
            # Bipon Net es el DNS de la red: se omite DNS= (wg-quick requeriría resolvconf)
            text = re.sub(r"(?im)^\s*DNS\s*=.*$", "", self.state["clients"][name]["conf"])
        else:
            raise KeyError("interfaz no encontrada")
        path = self.conf_path(name)
        _write_secret(path, text)
        return path

    async def interfaces_up(self) -> set[str]:
        code, out = await run("wg", "show", "interfaces")
        return set(out.split()) if code == 0 else set()

    async def up(self, name: str) -> tuple[bool, str]:
        path = self._render(name)
        notes = []
        if name in self.state["servers"]:
            srv = self.state["servers"][name]
            notes = await ensure_forwarding(srv["network"], srv.get("out_iface") or lan_iface())
        if name in await self.interfaces_up():
            return await self.reload(name)
        code, out = await run("wg-quick", "up", str(path), timeout=30)
        return code == 0, (out.strip() + ("\n" + "\n".join(notes) if notes else "")).strip()

    async def down(self, name: str) -> tuple[bool, str]:
        path = self._render(name)
        code, out = await run("wg-quick", "down", str(path), timeout=30)
        return code == 0, out.strip()

    async def reload(self, name: str) -> tuple[bool, str]:
        path = self._render(name)
        code, stripped = await run("wg-quick", "strip", str(path))
        if code != 0:
            return False, stripped
        tmp = self.dir / f".{name}.strip"
        _write_secret(tmp, stripped)
        code, out = await run("wg", "syncconf", name, str(tmp))
        tmp.unlink(missing_ok=True)
        return code == 0, out.strip() or "Configuración aplicada en caliente"

    async def autostart(self) -> None:
        if not self.available:
            return
        for name, s in {**self.state["servers"], **self.state["clients"]}.items():
            if s.get("autostart"):
                ok, msg = await self.up(name)
                log.info("WireGuard %s: %s", name, "activo" if ok else msg)

    async def dump(self, name: str) -> dict:
        code, out = await run("wg", "show", name, "dump")
        if code != 0:
            return {}
        lines = out.strip().splitlines()
        peers = {}
        for line in lines[1:]:
            f = line.split("\t")
            if len(f) >= 8:
                peers[f[0]] = {"endpoint": None if f[2] == "(none)" else f[2], "handshake": int(f[4] or 0),
                               "rx": int(f[5] or 0), "tx": int(f[6] or 0)}
        return peers

    def _public(self, s: dict) -> dict:
        out = {k: v for k, v in s.items() if k not in ("private_key", "peers", "conf")}
        if "peers" in s:
            out["peers"] = [{k: v for k, v in p.items() if k not in ("private_key", "psk")} for p in s["peers"]]
        return out

    async def status(self) -> dict:
        up = await self.interfaces_up() if self.available else set()
        servers = []
        now = time.time()
        for name, s in self.state["servers"].items():
            pub = self._public(s)
            pub["up"] = name in up
            dump = await self.dump(name) if pub["up"] else {}
            for p in pub["peers"]:
                d = dump.get(p["public_key"], {})
                p.update(d)
                p["online"] = bool(d.get("handshake")) and now - d.get("handshake", 0) < 180
            servers.append(pub)
        clients = []
        for name, c in self.state["clients"].items():
            dump = await self.dump(name) if name in up else {}
            peer = next(iter(dump.values()), {})
            ep = re.search(r"(?im)^\s*Endpoint\s*=\s*(\S+)", c["conf"])
            clients.append({"name": name, "up": name in up, "autostart": c.get("autostart", False),
                            "endpoint": ep.group(1) if ep else None, **peer,
                            "online": bool(peer.get("handshake")) and now - peer.get("handshake", 0) < 180})
        return {"available": self.available, "tun": tun_available(), "servers": servers, "clients": clients}


# =====================================================================
# OpenVPN (PKI propia con certificados EC P-256)
# =====================================================================
class OpenVPN:
    def __init__(self):
        self.dir = VPN_DIR / "openvpn"
        self.pki = self.dir / "pki"
        self.pki.mkdir(parents=True, exist_ok=True)
        self.state_path = self.dir / "state.json"
        self.state = {"server": None, "clients": [], "profiles": {}}
        if self.state_path.exists():
            self.state = json.loads(self.state_path.read_text())
        self.server_proc: ManagedProcess | None = None
        self.profile_procs: dict[str, ManagedProcess] = {}

    def save(self):
        _write_secret(self.state_path, json.dumps(self.state, indent=2))

    @property
    def available(self) -> bool:
        return shutil.which("openvpn") is not None

    # ---- PKI -------------------------------------------------------------
    def _load(self, name: str):
        return (self.pki / name).read_bytes()

    def _ca(self):
        cert = x509.load_pem_x509_certificate(self._load("ca.crt"))
        key = serialization.load_pem_private_key(self._load("ca.key"), None)
        return cert, key

    @staticmethod
    def _pem_key(key) -> bytes:
        return key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())

    def _issue(self, cn: str, server: bool) -> tuple[bytes, bytes, int]:
        ca_cert, ca_key = self._ca()
        key = ec.generate_private_key(ec.SECP256R1())
        now = dt.datetime.now(dt.timezone.utc)
        serial = x509.random_serial_number()
        b = (x509.CertificateBuilder().subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)]))
             .issuer_name(ca_cert.subject).public_key(key.public_key()).serial_number(serial)
             .not_valid_before(now - dt.timedelta(minutes=5)).not_valid_after(now + dt.timedelta(days=3650))
             .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
             .add_extension(x509.KeyUsage(digital_signature=True, key_encipherment=False, content_commitment=False, data_encipherment=False,
                                          key_agreement=True, key_cert_sign=False, crl_sign=False, encipher_only=False, decipher_only=False), critical=True)
             .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH if server else ExtendedKeyUsageOID.CLIENT_AUTH]), critical=False))
        cert = b.sign(ca_key, hashes.SHA256())
        return cert.public_bytes(serialization.Encoding.PEM), self._pem_key(key), serial

    def _write_crl(self):
        ca_cert, ca_key = self._ca()
        now = dt.datetime.now(dt.timezone.utc)
        b = x509.CertificateRevocationListBuilder().issuer_name(ca_cert.subject).last_update(now).next_update(now + dt.timedelta(days=3650))
        for c in self.state["clients"]:
            if c.get("revoked"):
                b = b.add_revoked_certificate(x509.RevokedCertificateBuilder().serial_number(int(c["serial"]))
                                              .revocation_date(dt.datetime.fromtimestamp(c["revoked"], dt.timezone.utc)).build())
        crl = b.sign(ca_key, hashes.SHA256())
        (self.pki / "crl.pem").write_bytes(crl.public_bytes(serialization.Encoding.PEM))
        os.chmod(self.pki / "crl.pem", 0o644)

    def init_server(self, opts: dict) -> dict:
        net = ipaddress.ip_network(opts.get("network", "10.9.0.0/24"), strict=False)
        proto = opts.get("proto", "udp")
        if proto not in ("udp", "tcp"):
            raise ValueError("protocolo inválido")
        if not (self.pki / "ca.key").exists() or opts.get("regenerate"):
            key = ec.generate_private_key(ec.SECP256R1())
            now = dt.datetime.now(dt.timezone.utc)
            name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Bipon Net VPN CA")])
            ca = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
                  .serial_number(x509.random_serial_number()).not_valid_before(now - dt.timedelta(minutes=5))
                  .not_valid_after(now + dt.timedelta(days=3650))
                  .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
                  .add_extension(x509.KeyUsage(digital_signature=True, key_cert_sign=True, crl_sign=True, content_commitment=False,
                                               key_encipherment=False, data_encipherment=False, key_agreement=False,
                                               encipher_only=False, decipher_only=False), critical=True)
                  .sign(key, hashes.SHA256()))
            _write_secret(self.pki / "ca.key", self._pem_key(key).decode())
            (self.pki / "ca.crt").write_bytes(ca.public_bytes(serialization.Encoding.PEM))
            crt, k, _ = self._issue("server", server=True)
            (self.pki / "server.crt").write_bytes(crt)
            _write_secret(self.pki / "server.key", k.decode())
            tc = os.urandom(256).hex()
            body = "\n".join(tc[i:i + 32] for i in range(0, len(tc), 32))
            _write_secret(self.pki / "tc.key", f"-----BEGIN OpenVPN Static key V1-----\n{body}\n-----END OpenVPN Static key V1-----\n")
            self.state["clients"] = []
        srv = {
            "port": int(opts.get("port", 1194)), "proto": proto, "network": str(net),
            "endpoint": (opts.get("endpoint") or "").strip(), "dns": (opts.get("dns") or "").strip(),
            "lan_net": opts.get("lan_net") or lan_net(), "full_tunnel": bool(opts.get("full_tunnel", True)),
            "client_to_client": bool(opts.get("client_to_client", False)), "max_clients": int(opts.get("max_clients", 32)),
            "out_iface": opts.get("out_iface") or lan_iface(), "autostart": bool(opts.get("autostart", True)),
            "compress": False,
        }
        self.state["server"] = srv
        self.save()
        self._write_crl()
        self._write_server_conf()
        return srv

    def _write_server_conf(self):
        s = self.state["server"]
        net = ipaddress.ip_network(s["network"])
        lan = ipaddress.ip_network(s["lan_net"])
        lines = [
            "# Generado por Bipon Net", f"port {s['port']}", f"proto {s['proto']}", "dev tun", "topology subnet",
            f"server {net.network_address} {net.netmask}", f"ca {self.pki / 'ca.crt'}", f"cert {self.pki / 'server.crt'}",
            f"key {self.pki / 'server.key'}", "dh none", "ecdh-curve prime256v1", f"tls-crypt {self.pki / 'tc.key'}",
            f"crl-verify {self.pki / 'crl.pem'}", "tls-version-min 1.2", "data-ciphers AES-256-GCM:AES-128-GCM:CHACHA20-POLY1305",
            "auth SHA256", "keepalive 10 60", "persist-key", "persist-tun", f"max-clients {s['max_clients']}",
            f"status {self.dir / 'status.log'} 5", "status-version 2", "verb 3", "explicit-exit-notify 1" if s["proto"] == "udp" else "",
            "fast-io" if s["proto"] == "udp" else "", "sndbuf 0", "rcvbuf 0", "txqueuelen 1000",
            f'push "route {lan.network_address} {lan.netmask}"',
        ]
        if s["full_tunnel"]:
            lines.append('push "redirect-gateway def1 bypass-dhcp"')
        if s.get("dns"):
            lines += [f'push "dhcp-option DNS {s["dns"]}"', 'push "block-outside-dns"']
        if s["client_to_client"]:
            lines.append("client-to-client")
        (self.dir / "server.conf").write_text("\n".join(x for x in lines if x) + "\n")

    def add_client(self, name: str) -> dict:
        if not PEER_RE.match(name or "") or " " in name:
            raise ValueError("nombre inválido (sin espacios)")
        if not self.state.get("server"):
            raise ValueError("inicializa primero el servidor")
        if any(c["name"] == name and not c.get("revoked") for c in self.state["clients"]):
            raise ValueError("ya existe un cliente activo con ese nombre")
        crt, key, serial = self._issue(name, server=False)
        d = self.dir / "clients"
        d.mkdir(exist_ok=True)
        (d / f"{name}.crt").write_bytes(crt)
        _write_secret(d / f"{name}.key", key.decode())
        c = {"name": name, "serial": str(serial), "created": time.time(), "revoked": None}
        self.state["clients"].append(c)
        self.save()
        return c

    def revoke(self, name: str) -> None:
        for c in self.state["clients"]:
            if c["name"] == name and not c.get("revoked"):
                c["revoked"] = time.time()
        self.save()
        self._write_crl()

    def export(self, name: str) -> dict:
        s = self.state["server"]
        if not s:
            raise ValueError("servidor no inicializado")
        if not s.get("endpoint"):
            raise ValueError("define el endpoint público (IP o dominio) del servidor")
        c = next((c for c in self.state["clients"] if c["name"] == name and not c.get("revoked")), None)
        if not c:
            raise KeyError("cliente no encontrado o revocado")
        d = self.dir / "clients"
        ovpn = "\n".join([
            "client", "dev tun", f"proto {s['proto']}", f"remote {s['endpoint']} {s['port']}", "resolv-retry infinite",
            "nobind", "persist-key", "persist-tun", "remote-cert-tls server", "auth SHA256",
            "data-ciphers AES-256-GCM:AES-128-GCM:CHACHA20-POLY1305", "tls-version-min 1.2", "verb 3",
            "<ca>", self._load("ca.crt").decode().strip(), "</ca>",
            "<cert>", (d / f"{name}.crt").read_text().strip(), "</cert>",
            "<key>", (d / f"{name}.key").read_text().strip(), "</key>",
            "<tls-crypt>", self._load("tc.key").decode().strip(), "</tls-crypt>",
        ]) + "\n"
        return {"name": f"{name}.ovpn", "content": ovpn}

    # ---- servidor: ciclo de vida -----------------------------------------
    async def start_server(self) -> tuple[bool, str]:
        if not self.available:
            return False, "openvpn no está instalado"
        if not self.state.get("server"):
            return False, "servidor no inicializado"
        self._write_server_conf()
        notes = await ensure_forwarding(self.state["server"]["network"], self.state["server"].get("out_iface") or lan_iface())
        if self.server_proc:
            await self.server_proc.stop()
        self.server_proc = ManagedProcess("openvpn-server", ["openvpn", "--config", str(self.dir / "server.conf")], cwd=self.dir)
        self.server_proc.start()
        self.state["server"]["autostart"] = True
        self.save()
        return True, "Servidor OpenVPN iniciado" + ("\n" + "\n".join(notes) if notes else "")

    async def stop_server(self) -> None:
        if self.server_proc:
            await self.server_proc.stop()
            self.server_proc = None
        if self.state.get("server"):
            self.state["server"]["autostart"] = False
            self.save()

    def connected(self) -> list[dict]:
        p = self.dir / "status.log"
        if not p.exists() or not (self.server_proc and self.server_proc.running):
            return []
        out = []
        for line in p.read_text(errors="ignore").splitlines():
            f = line.split(",")
            if f[0] == "CLIENT_LIST" and len(f) > 8 and f[1] != "Common Name":
                out.append({"name": f[1], "real": f[2], "virtual": f[3], "rx": int(f[5] or 0), "tx": int(f[6] or 0),
                            "since": int(f[8]) if f[8].isdigit() else f[7]})
        return out

    # ---- perfiles cliente ------------------------------------------------
    def import_profile(self, name: str, ovpn: str, username: str = "", password: str = "", autostart: bool = False) -> dict:
        _check_name(name)
        if "remote " not in ovpn:
            raise ValueError("el archivo .ovpn no contiene 'remote'")
        bad = re.search(r"(?im)^\s*(up|down|script-security|plugin|route-up|ipchange|client-connect|learn-address|tls-verify|auth-user-pass-verify)\b", ovpn)
        if bad:
            raise ValueError(f"por seguridad no se permite la directiva '{bad.group(1)}'")
        d = self.dir / "profiles"
        d.mkdir(exist_ok=True)
        _write_secret(d / f"{name}.ovpn", ovpn)
        if username:
            _write_secret(d / f"{name}.auth", f"{username}\n{password}\n")
        self.state["profiles"][name] = {"name": name, "auth": bool(username), "autostart": autostart, "created": time.time()}
        self.save()
        return self.state["profiles"][name]

    def delete_profile(self, name: str) -> None:
        self.state["profiles"].pop(name, None)
        self.save()
        for ext in (".ovpn", ".auth"):
            (self.dir / "profiles" / f"{name}{ext}").unlink(missing_ok=True)

    async def connect_profile(self, name: str) -> tuple[bool, str]:
        if name not in self.state["profiles"]:
            return False, "perfil no encontrado"
        d = self.dir / "profiles"
        args = ["openvpn", "--config", str(d / f"{name}.ovpn"), "--dev", f"tun-{name}"[:15]]
        if (d / f"{name}.auth").exists():
            args += ["--auth-user-pass", str(d / f"{name}.auth")]
        if name in self.profile_procs:
            await self.profile_procs[name].stop()
        p = ManagedProcess(f"openvpn-{name}", args, cwd=d)
        self.profile_procs[name] = p
        p.start()
        self.state["profiles"][name]["autostart"] = True
        self.save()
        return True, "Conectando…"

    async def disconnect_profile(self, name: str) -> None:
        p = self.profile_procs.pop(name, None)
        if p:
            await p.stop()
        if name in self.state["profiles"]:
            self.state["profiles"][name]["autostart"] = False
            self.save()

    async def autostart(self) -> None:
        if not self.available:
            return
        if self.state.get("server") and self.state["server"].get("autostart"):
            await self.start_server()
        for name, p in self.state["profiles"].items():
            if p.get("autostart"):
                await self.connect_profile(name)

    def status(self) -> dict:
        profiles = []
        for name, p in self.state["profiles"].items():
            proc = self.profile_procs.get(name)
            logs = list(proc.logs) if proc else []
            connected = proc is not None and proc.running and any("Initialization Sequence Completed" in l for l in logs[-200:])
            profiles.append({**p, "running": bool(proc and proc.running), "connected": connected, "log": logs[-30:]})
        return {
            "available": self.available, "tun": tun_available(),
            "server": self.state.get("server"),
            "running": bool(self.server_proc and self.server_proc.running),
            "log": list(self.server_proc.logs)[-40:] if self.server_proc else [],
            "clients": self.state["clients"], "connected": self.connected(), "profiles": profiles,
        }

    async def shutdown(self):
        if self.server_proc:
            await self.server_proc.stop()
        for p in self.profile_procs.values():
            await p.stop()


# =====================================================================
# Tailscale
# =====================================================================
class Tailscale:
    SYSTEM_SOCKETS = ("/var/run/tailscale/tailscaled.sock", "/run/tailscale/tailscaled.sock")

    def __init__(self):
        self.dir = VPN_DIR / "tailscale"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.own_socket = str(self.dir / "tailscaled.sock")
        self.daemon: ManagedProcess | None = None
        self.login_url: str | None = None
        self._up_task: asyncio.Task | None = None
        self.prefs_path = self.dir / "netping-prefs.json"

    @property
    def available(self) -> bool:
        return shutil.which("tailscale") is not None

    def socket(self) -> str:
        for s in self.SYSTEM_SOCKETS:
            if os.path.exists(s) and not (self.daemon and self.daemon.running):
                return s
        return self.own_socket

    async def ensure_daemon(self) -> None:
        if any(os.path.exists(s) for s in self.SYSTEM_SOCKETS):
            return  # tailscaled del sistema
        if self.daemon and self.daemon.running:
            return
        if not shutil.which("tailscaled"):
            raise RuntimeError("tailscaled no está instalado")
        args = ["tailscaled", f"--statedir={self.dir}", f"--socket={self.own_socket}", "--port=41641"]
        if not tun_available():
            args.append("--tun=userspace-networking")
        self.daemon = ManagedProcess("tailscaled", args)
        self.daemon.start()
        for _ in range(40):
            if os.path.exists(self.own_socket):
                break
            await asyncio.sleep(0.25)

    async def cli(self, *args: str, timeout: float = 20) -> tuple[int, str]:
        return await run("tailscale", f"--socket={self.socket()}", *args, timeout=timeout)

    async def status(self) -> dict:
        if not self.available:
            return {"available": False}
        if not os.path.exists(self.socket()):
            return {"available": True, "daemon": False, "prefs": self.prefs(), "login_url": self.login_url}
        code, out = await self.cli("status", "--json")
        try:
            j = json.loads(out)
        except Exception:
            return {"available": True, "daemon": True, "error": out.strip()[:300], "prefs": self.prefs(), "login_url": self.login_url}
        selfn = j.get("Self") or {}
        peers = []
        for p in (j.get("Peer") or {}).values():
            peers.append({
                "name": p.get("HostName"), "dns": p.get("DNSName"), "os": p.get("OS"), "ips": p.get("TailscaleIPs", []),
                "online": p.get("Online"), "exit_node": p.get("ExitNode"), "exit_option": p.get("ExitNodeOption"),
                "direct": p.get("CurAddr") or "", "relay": p.get("Relay") or "", "rx": p.get("RxBytes", 0), "tx": p.get("TxBytes", 0),
                "last_seen": p.get("LastSeen"), "routes": p.get("PrimaryRoutes") or [],
            })
        peers.sort(key=lambda x: (not x["online"], x["name"] or ""))
        state = j.get("BackendState")
        if state == "Running":
            self.login_url = None
        return {
            "available": True, "daemon": True, "state": state, "version": j.get("Version"),
            "tailnet": (j.get("CurrentTailnet") or {}).get("Name"), "magic_dns": (j.get("CurrentTailnet") or {}).get("MagicDNSSuffix"),
            "self": {"name": selfn.get("HostName"), "dns": selfn.get("DNSName"), "ips": selfn.get("TailscaleIPs", []),
                     "online": selfn.get("Online"), "relay": selfn.get("Relay")},
            "health": j.get("Health") or [], "peers": peers, "auth_url": j.get("AuthURL") or self.login_url,
            "login_url": j.get("AuthURL") or self.login_url, "prefs": self.prefs(), "userspace": not tun_available(),
        }

    def prefs(self) -> dict:
        defaults = {"hostname": "bipon", "advertise_routes": [lan_net()], "advertise_exit_node": False, "accept_routes": False,
                    "accept_dns": False, "ssh": False, "shields_up": False, "exit_node": "", "exit_node_allow_lan": True,
                    "login_server": "", "snat_subnet_routes": True, "autostart": True}
        if self.prefs_path.exists():
            defaults.update(json.loads(self.prefs_path.read_text()))
        return defaults

    def _flags(self, p: dict) -> list[str]:
        flags = [f"--hostname={p['hostname']}" if p.get("hostname") else "",
                 f"--advertise-routes={','.join(p.get('advertise_routes') or [])}",
                 f"--advertise-exit-node={'true' if p.get('advertise_exit_node') else 'false'}",
                 f"--accept-routes={'true' if p.get('accept_routes') else 'false'}",
                 f"--accept-dns={'true' if p.get('accept_dns') else 'false'}",
                 f"--ssh={'true' if p.get('ssh') else 'false'}",
                 f"--shields-up={'true' if p.get('shields_up') else 'false'}",
                 f"--exit-node={p.get('exit_node') or ''}",
                 f"--snat-subnet-routes={'true' if p.get('snat_subnet_routes', True) else 'false'}"]
        if p.get("exit_node"):
            flags.append(f"--exit-node-allow-lan-access={'true' if p.get('exit_node_allow_lan') else 'false'}")
        return [f for f in flags if f]

    def save_prefs(self, patch: dict) -> dict:
        p = self.prefs()
        for k in ("hostname", "advertise_routes", "advertise_exit_node", "accept_routes", "accept_dns", "ssh", "shields_up",
                  "exit_node", "exit_node_allow_lan", "login_server", "snat_subnet_routes", "autostart"):
            if k in patch:
                p[k] = patch[k]
        if p.get("hostname") and not re.match(r"^[a-zA-Z0-9-]{1,63}$", p["hostname"]):
            raise ValueError("hostname inválido")
        for r in p.get("advertise_routes") or []:
            ipaddress.ip_network(r, strict=False)
        if p.get("exit_node") and not re.match(r"^[\w.:-]{1,100}$", p["exit_node"]):
            raise ValueError("exit node inválido")
        if p.get("login_server") and not re.match(r"^https://[\w.:/-]+$", p["login_server"]):
            raise ValueError("login server debe ser una URL https://")
        _write_secret(self.prefs_path, json.dumps(p))
        return p

    async def up(self, authkey: str = "", patch: dict | None = None) -> dict:
        await self.ensure_daemon()
        p = self.save_prefs(patch or {})
        args = ["up", "--reset", *self._flags(p)]
        if p.get("login_server"):
            args.append(f"--login-server={p['login_server']}")
        if authkey:
            if not re.match(r"^[\w-]{10,200}$", authkey):
                raise ValueError("clave de autenticación inválida")
            args.append(f"--auth-key={authkey}")
        if p.get("advertise_routes") or p.get("advertise_exit_node"):
            await ensure_forwarding("100.64.0.0/10", lan_iface())
        self.login_url = None
        proc = await asyncio.create_subprocess_exec("tailscale", f"--socket={self.socket()}", *args,
                                                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
        lines: list[str] = []

        async def reader():
            assert proc.stdout
            async for raw in proc.stdout:
                line = raw.decode(errors="ignore").strip()
                lines.append(line)
                m = re.search(r"https://\S+", line)
                if m:
                    self.login_url = m.group(0)
            await proc.wait()

        task = asyncio.create_task(reader())
        try:
            await asyncio.wait_for(asyncio.shield(task), 12)
        except asyncio.TimeoutError:
            pass  # sigue esperando a que el usuario inicie sesión
        self._up_task = task
        done = task.done() and proc.returncode == 0
        return {"ok": done or bool(self.login_url), "login_url": self.login_url, "output": "\n".join(lines[-20:])}

    async def set(self, patch: dict) -> tuple[bool, str]:
        p = self.save_prefs(patch)
        args = ["set", *self._flags(p)]
        code, out = await self.cli(*args)
        return code == 0, out.strip() or "Preferencias aplicadas"

    async def down(self):
        return await self.cli("down")

    async def logout(self):
        return await self.cli("logout")

    async def netcheck(self) -> dict:
        await self.ensure_daemon()
        code, out = await self.cli("netcheck", timeout=40)
        derp = []
        for m in re.finditer(r"-\s*([\w-]+):\s*([\d.]+)ms\s*\(([^)]+)\)", out):
            derp.append({"id": m.group(1), "ms": float(m.group(2)), "name": m.group(3)})
        derp.sort(key=lambda x: x["ms"])
        info = {}
        for key, rx in (("udp", r"UDP:\s*(\w+)"), ("ipv4", r"IPv4:\s*(.+)"), ("ipv6", r"IPv6:\s*(.+)"),
                        ("mapping_varies", r"MappingVariesByDestIP:\s*(\w+)"), ("portmap", r"PortMapping:\s*(.*)"),
                        ("nearest", r"Nearest DERP:\s*(.+)")):
            m = re.search(rx, out)
            if m:
                info[key] = m.group(1).strip()
        return {"ok": code == 0, "derp": derp, "info": info, "raw": out[-4000:]}

    async def ping(self, target: str) -> dict:
        if not re.match(r"^[\w.:-]{1,100}$", target):
            raise ValueError("destino inválido")
        code, out = await self.cli("ping", "--c=5", "--until-direct=false", target, timeout=30)
        return {"ok": code == 0, "output": out.strip()}

    async def autostart(self):
        if not self.available:
            return
        st_file = self.dir / "tailscaled.state"
        if self.prefs().get("autostart") and (st_file.exists() or any(os.path.exists(s) for s in self.SYSTEM_SOCKETS)):
            try:
                await self.ensure_daemon()
            except Exception as exc:
                log.warning("tailscaled: %s", exc)

    async def shutdown(self):
        if self.daemon:
            await self.daemon.stop()


class VPNManager:
    def __init__(self):
        VPN_DIR.mkdir(parents=True, exist_ok=True)
        self.wg = WireGuard()
        self.ovpn = OpenVPN()
        self.ts = Tailscale()

    async def start(self):
        for coro in (self.wg.autostart(), self.ovpn.autostart(), self.ts.autostart()):
            try:
                await coro
            except Exception:
                log.exception("autoarranque VPN")

    async def shutdown(self):
        await self.ovpn.shutdown()
        await self.ts.shutdown()

    async def overview(self) -> dict:
        wg = await self.wg.status()
        ts = await self.ts.status()
        ov = self.ovpn.status()
        return {"wireguard": wg, "openvpn": ov, "tailscale": ts, "tun": tun_available(),
                "net_admin": _has_net_admin()}


def _has_net_admin() -> bool:
    try:
        with open("/proc/self/status") as fh:
            for line in fh:
                if line.startswith("CapEff:"):
                    return bool(int(line.split()[1], 16) & (1 << 12))
    except Exception:
        pass
    return False
