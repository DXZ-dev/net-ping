"""Generador de configuraciones WireGuard.

Modos:
  home  — servidor en casa (requiere IP pública o IPv6 + puerto UDP abierto).
  relay — VPS con IP pública como punto de entrada; la casa se conecta saliendo
          (funciona detrás de CGNAT) y el VPS reenvía puertos hacia la LAN.
"""
from __future__ import annotations

import base64
import io
import ipaddress
import re

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey

try:
    import qrcode
    import qrcode.image.svg
except ImportError:  # pragma: no cover
    qrcode = None

IFACE_RE = re.compile(r"^[a-zA-Z0-9_.-]{1,15}$")
ENDPOINT_RE = re.compile(r"^[A-Za-z0-9.\-:\[\]]{1,253}$")


def keypair() -> tuple[str, str]:
    k = X25519PrivateKey.generate()
    priv = k.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw, serialization.NoEncryption())
    pub = k.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return base64.b64encode(priv).decode(), base64.b64encode(pub).decode()


def psk() -> str:
    import os
    return base64.b64encode(os.urandom(32)).decode()


def qr_svg(text: str) -> str | None:
    if qrcode is None:
        return None
    img = qrcode.make(text, image_factory=qrcode.image.svg.SvgPathImage, box_size=8, border=2)
    buf = io.BytesIO()
    img.save(buf)
    return buf.getvalue().decode()


def generate(opts: dict) -> dict:
    mode = opts.get("mode", "home")
    endpoint = (opts.get("endpoint") or "").strip()
    if not endpoint or not ENDPOINT_RE.match(endpoint):
        raise ValueError("Indica un endpoint válido (IP pública, dominio DDNS o IP del VPS)")
    port = int(opts.get("port", 51820))
    if not 1 <= port <= 65535:
        raise ValueError("puerto inválido")
    tunnel = ipaddress.ip_network(opts.get("tunnel_net", "10.8.0.0/24"), strict=False)
    lan = ipaddress.ip_network(opts.get("lan_net", "192.168.1.0/24"), strict=False)
    dns = (opts.get("dns") or "").strip()
    if dns:
        ipaddress.ip_address(dns)
    lan_iface = opts.get("lan_iface", "eth0")
    wan_iface = opts.get("wan_iface", "eth0")
    for i in (lan_iface, wan_iface):
        if not IFACE_RE.match(i):
            raise ValueError("nombre de interfaz inválido")
    full = bool(opts.get("full_tunnel", True))
    names = [n.strip() for n in opts.get("clients", ["movil", "portatil"]) if n.strip()][:50] or ["cliente"]
    mtu = int(opts.get("mtu", 1420))
    hosts = list(tunnel.hosts())
    prefix = tunnel.prefixlen

    files: list[dict] = []
    s_priv, s_pub = keypair()
    client_allowed = "0.0.0.0/0, ::/0" if full else f"{tunnel}, {lan}"
    dns_line = f"DNS = {dns}\n" if dns else ""

    if mode == "home":
        server = (f"# Servidor WireGuard en casa — /etc/wireguard/wg0.conf\n[Interface]\nAddress = {hosts[0]}/{prefix}\n"
                  f"ListenPort = {port}\nPrivateKey = {s_priv}\nMTU = {mtu}\n"
                  f"PostUp = sysctl -w net.ipv4.ip_forward=1; iptables -A FORWARD -i %i -j ACCEPT; iptables -A FORWARD -o %i -j ACCEPT; iptables -t nat -A POSTROUTING -s {tunnel} -o {lan_iface} -j MASQUERADE\n"
                  f"PostDown = iptables -D FORWARD -i %i -j ACCEPT; iptables -D FORWARD -o %i -j ACCEPT; iptables -t nat -D POSTROUTING -s {tunnel} -o {lan_iface} -j MASQUERADE\n")
        clients = []
        for i, name in enumerate(names):
            c_priv, c_pub = keypair()
            k = psk()
            addr = hosts[i + 1]
            server += f"\n[Peer]\n# {name}\nPublicKey = {c_pub}\nPresharedKey = {k}\nAllowedIPs = {addr}/32\n"
            conf = (f"[Interface]\n# {name}\nPrivateKey = {c_priv}\nAddress = {addr}/32\n{dns_line}MTU = {mtu}\n\n"
                    f"[Peer]\nPublicKey = {s_pub}\nPresharedKey = {k}\nEndpoint = {endpoint}:{port}\n"
                    f"AllowedIPs = {client_allowed}\nPersistentKeepalive = 25\n")
            clients.append({"name": f"{name}.conf", "content": conf, "qr": qr_svg(conf), "role": "client"})
        files.append({"name": "wg0-casa.conf", "content": server, "role": "server"})
        files += clients
        steps = [
            "Instala WireGuard en el equipo de Bipon Net: apt install wireguard",
            "Copia wg0-casa.conf a /etc/wireguard/wg0.conf y ejecuta: systemctl enable --now wg-quick@wg0",
            f"Abre el puerto UDP {port} en el router hacia la IP de este equipo (o usa UPnP en la pestaña Puertos)",
            "En el móvil/portátil, importa el .conf o escanea el QR con la app WireGuard",
        ]
    elif mode == "relay":
        h_priv, h_pub = keypair()
        home_addr, vps_addr = hosts[1], hosts[0]
        forwards = []
        for f in opts.get("forwards", []):
            proto = f.get("proto", "tcp").lower()
            if proto not in ("tcp", "udp"):
                continue
            ext = int(f["port"])
            target = str(ipaddress.ip_address(f["target"]))
            tport = int(f.get("target_port") or ext)
            forwards.append((proto, ext, target, tport))
        up = [f"sysctl -w net.ipv4.ip_forward=1", f"iptables -A FORWARD -i %i -j ACCEPT", f"iptables -A FORWARD -o %i -j ACCEPT",
              f"iptables -t nat -A POSTROUTING -s {tunnel} -o {wan_iface} -j MASQUERADE"]
        down = [f"iptables -D FORWARD -i %i -j ACCEPT", f"iptables -D FORWARD -o %i -j ACCEPT",
                f"iptables -t nat -D POSTROUTING -s {tunnel} -o {wan_iface} -j MASQUERADE"]
        for proto, ext, target, tport in forwards:
            up.append(f"iptables -t nat -A PREROUTING -i {wan_iface} -p {proto} --dport {ext} -j DNAT --to-destination {target}:{tport}")
            down.append(f"iptables -t nat -D PREROUTING -i {wan_iface} -p {proto} --dport {ext} -j DNAT --to-destination {target}:{tport}")
        if forwards:
            up.append(f"iptables -t nat -A POSTROUTING -o %i -d {lan} -j MASQUERADE")
            down.append(f"iptables -t nat -D POSTROUTING -o %i -d {lan} -j MASQUERADE")
        vps = (f"# VPS de relevo — /etc/wireguard/wg0.conf\n[Interface]\nAddress = {vps_addr}/{prefix}\nListenPort = {port}\n"
               f"PrivateKey = {s_priv}\nMTU = {mtu}\nPostUp = {'; '.join(up)}\nPostDown = {'; '.join(down)}\n\n"
               f"[Peer]\n# casa (Bipon Net)\nPublicKey = {h_pub}\nAllowedIPs = {home_addr}/32, {lan}\n")
        home = (f"# Casa — /etc/wireguard/wg0.conf (sale hacia el VPS: funciona detrás de CGNAT)\n[Interface]\n"
                f"Address = {home_addr}/{prefix}\nPrivateKey = {h_priv}\nMTU = {mtu}\n"
                f"PostUp = sysctl -w net.ipv4.ip_forward=1; iptables -A FORWARD -i %i -j ACCEPT; iptables -A FORWARD -o %i -j ACCEPT; iptables -t nat -A POSTROUTING -s {tunnel} -o {lan_iface} -j MASQUERADE\n"
                f"PostDown = iptables -D FORWARD -i %i -j ACCEPT; iptables -D FORWARD -o %i -j ACCEPT; iptables -t nat -D POSTROUTING -s {tunnel} -o {lan_iface} -j MASQUERADE\n\n"
                f"[Peer]\n# VPS\nPublicKey = {s_pub}\nEndpoint = {endpoint}:{port}\nAllowedIPs = {tunnel}\nPersistentKeepalive = 25\n")
        clients = []
        for i, name in enumerate(names):
            c_priv, c_pub = keypair()
            addr = hosts[i + 2]
            vps += f"\n[Peer]\n# {name}\nPublicKey = {c_pub}\nAllowedIPs = {addr}/32\n"
            allowed = "0.0.0.0/0, ::/0" if full else f"{tunnel}, {lan}"
            conf = (f"[Interface]\n# {name}\nPrivateKey = {c_priv}\nAddress = {addr}/32\n{dns_line}MTU = {mtu}\n\n"
                    f"[Peer]\nPublicKey = {s_pub}\nEndpoint = {endpoint}:{port}\nAllowedIPs = {allowed}\nPersistentKeepalive = 25\n")
            clients.append({"name": f"{name}.conf", "content": conf, "qr": qr_svg(conf), "role": "client"})
        files += [{"name": "wg0-vps.conf", "content": vps, "role": "server"},
                  {"name": "wg0-casa.conf", "content": home, "role": "home"}] + clients
        steps = [
            "Contrata un VPS pequeño con IP pública lo más cerca posible de tu ciudad (menor ping)",
            "En el VPS: apt install wireguard; copia wg0-vps.conf a /etc/wireguard/wg0.conf; systemctl enable --now wg-quick@wg0",
            f"Abre el puerto UDP {port} en el firewall del VPS" + (" y los puertos reenviados" if forwards else ""),
            "En casa (equipo de Bipon Net): instala WireGuard y usa wg0-casa.conf",
            "Los clientes se conectan al VPS y llegan a tu LAN; los puertos reenviados quedan accesibles en la IP del VPS",
        ]
    else:
        raise ValueError("modo desconocido")
    return {"mode": mode, "files": files, "steps": steps, "server_public_key": s_pub}
