"""DHCP en modo principal junto al DHCP del router: toma de control y DNS de respaldo."""
import socket
import sqlite3
import struct

from bipon.dhcp import ACK, DISCOVER, NAK, OFFER, REQUEST, DHCPServer, parse_packet

MAC = "aa:bb:cc:00:00:42"


class Obj:
    def __init__(self, **kw):
        self.__dict__.update(kw)


class DB:
    def __init__(self):
        self.c = sqlite3.connect(":memory:")
        self.c.row_factory = sqlite3.Row
        self.c.execute("CREATE TABLE dhcp_leases (mac TEXT PRIMARY KEY, ip TEXT, hostname TEXT, expires REAL)")
        self.c.execute("CREATE TABLE devices (mac TEXT, hostname TEXT)")

    def execute(self, sql, p=()):
        return self.c.execute(sql, tuple(p))

    def query(self, sql, p=()):
        return [dict(r) for r in self.c.execute(sql, tuple(p)).fetchall()]

    def one(self, sql, p=()):
        r = self.query(sql, p)
        return r[0] if r else None


def make(backup=False):
    cfg = {"enabled": True, "mode": "primary", "authoritative": True, "server_ip": "192.168.18.205", "netmask": "255.255.255.0",
           "router": "192.168.18.1", "range_start": "192.168.18.100", "range_end": "192.168.18.199", "dns": ["192.168.18.205"],
           "domain": "lan", "lease_time": 3600, "static": [], "backup_dns_router": backup}
    ctx = Obj(settings=Obj(get=lambda k, d=None: cfg if k == "dhcp" else d), db=DB(),
              devices=Obj(devices={}, track_dhcp=lambda *a, **k: None), captive=Obj(cfg=lambda: {}))
    srv = DHCPServer.__new__(DHCPServer)
    srv.ctx, srv.offers, srv.stats, srv.running, srv.error, srv.other_servers = ctx, {}, {"nak": 0, "ack": 0}, True, "", []
    return srv


def packet(mtype, requested=None, server_id=None, ciaddr="0.0.0.0"):
    mac = bytes.fromhex(MAC.replace(":", ""))
    p = struct.pack("!BBBBIHH", 1, 1, 6, 0, 0x1234, 0, 0x8000) + socket.inet_aton(ciaddr) + b"\0" * 12 + mac + b"\0" * 10 + b"\0" * 192
    p += b"\x63\x82\x53\x63" + bytes([53, 1, mtype])
    if requested:
        p += bytes([50, 4]) + socket.inet_aton(requested)
    if server_id:
        p += bytes([54, 4]) + socket.inet_aton(server_id)
    return p + b"\xff"


def opts_of(reply):
    return parse_packet(reply[0])


def test_device_with_router_ip_is_moved_to_bipon(monkeypatch):
    import bipon.dhcp as d
    monkeypatch.setattr(d.netinfo, "neighbors", lambda: [])
    srv = make()
    # 1) el móvil se reconecta pidiendo la IP que le dio el router (INIT-REBOOT, sin servidor elegido)
    r = srv.handle(packet(REQUEST, requested="192.168.18.92"), None)
    assert opts_of(r)["type"] == NAK
    # 2) vuelve a empezar: Bipon le ofrece una IP de su rango
    r = srv.handle(packet(DISCOVER), None)
    offer = opts_of(r)
    assert offer["type"] == OFFER
    ip = srv.offers[MAC][0]
    assert ip.startswith("192.168.18.1") and 100 <= int(ip.rsplit(".", 1)[1]) <= 199
    # 3) lo acepta → ACK y concesión registrada
    r = srv.handle(packet(REQUEST, requested=ip, server_id="192.168.18.205"), None)
    assert opts_of(r)["type"] == ACK
    assert srv.ctx.db.one("SELECT ip FROM dhcp_leases WHERE mac=?", (MAC,))["ip"] == ip


def test_request_to_router_is_left_alone(monkeypatch):
    import bipon.dhcp as d
    monkeypatch.setattr(d.netinfo, "neighbors", lambda: [])
    srv = make()
    # el cliente eligió explícitamente la oferta del router: Bipon no interfiere
    assert srv.handle(packet(REQUEST, requested="192.168.18.92", server_id="192.168.18.1"), None) is None


def test_backup_dns_router():
    dns_opt = dict(make(backup=True)._options(3600))[6]
    assert socket.inet_ntoa(dns_opt[:4]) == "192.168.18.205" and socket.inet_ntoa(dns_opt[4:8]) == "192.168.18.1"
    assert len(dict(make(backup=False)._options(3600))[6]) == 4
