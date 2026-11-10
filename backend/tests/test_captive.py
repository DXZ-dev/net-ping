"""Portal cautivo: identificación por MAC, dispositivos registrados y respuestas HTTP."""
import sqlite3
import time

from fastapi.testclient import TestClient

from bipon.captive import CaptivePortal


class FakeDB:
    def __init__(self):
        self.conn = sqlite3.connect(":memory:", check_same_thread=False)
        self.conn.row_factory = sqlite3.Row

    def execute(self, sql, params=()):
        return self.conn.execute(sql, tuple(params))

    def one(self, sql, params=()):
        r = self.conn.execute(sql, tuple(params)).fetchone()
        return dict(r) if r else None

    def query(self, sql, params=()):
        return [dict(r) for r in self.conn.execute(sql, tuple(params)).fetchall()]

    def event(self, *a):
        pass


class FakeDevices:
    def __init__(self):
        self.devices, self.ip_to_mac, self.ip6_to_mac = {}, {}, {}
        self.self_ip, self.gateway, self.scanning = "192.168.1.2", "192.168.1.1", False

    def mac_for(self, ip):
        return self.ip_to_mac.get(ip)


class FakeSettings(dict):
    def get(self, k, d=None):
        return super().get(k, d)


class Ctx:
    def __init__(self):
        self.db, self.devices = FakeDB(), FakeDevices()
        self.settings = FakeSettings(captive={"enabled": True})


def portal():
    p = CaptivePortal(Ctx())
    p._arp_miss = {}
    return p


def test_accept_before_mac_known_then_mac_appears():
    p = portal()
    p._arp_miss["192.168.1.50"] = time.time()  # sin ARP en el entorno de pruebas
    assert not p.accepted("192.168.1.50")
    p.accept("192.168.1.50")
    p._arp_miss["192.168.1.50"] = time.time()
    assert p.accepted("192.168.1.50")
    # el escáner descubre su MAC: la aceptación se conserva
    p.ctx.devices.ip_to_mac["192.168.1.50"] = "aa:bb:cc:00:00:01"
    p._accepted_cache.clear()
    assert p.accepted("192.168.1.50")
    assert p.ctx.db.one("SELECT mac FROM captive_accepts")["mac"] == "aa:bb:cc:00:00:01"


def test_reset_all_keeps_registered_and_reask_one():
    p = portal()
    d = p.ctx.devices
    for i, mac in enumerate(("aa:00:00:00:00:01", "aa:00:00:00:00:02")):
        d.ip_to_mac[f"192.168.1.{10 + i}"] = mac
        d.devices[mac] = {"ip": f"192.168.1.{10 + i}", "online": True}
        p.accept(f"192.168.1.{10 + i}")
    p.trust("aa:00:00:00:00:01", True)
    p.reset()
    assert p.accepted("192.168.1.10")       # registrado: no se le vuelve a pedir
    assert not p.accepted("192.168.1.11")   # el resto vuelve al portal
    p.reset("aa:00:00:00:00:01")            # volver a pedir a uno concreto sí lo quita
    assert not p.accepted("192.168.1.10")
    assert set(p.pending_macs()) == {"aa:00:00:00:00:01", "aa:00:00:00:00:02"}


def test_http_flow():
    p = portal()
    p.ctx.devices.ip_to_mac["testclient"] = "aa:00:00:00:00:09"
    c = TestClient(p.app(), follow_redirects=False)
    r = c.get("/generate_204", headers={"host": "connectivitycheck.gstatic.com"})
    assert r.status_code == 302                      # sin aceptar → al portal (el sistema abre la ventana)
    r = c.get("/", headers={"host": "192.168.1.2"})
    assert r.status_code == 200 and "agree" in r.text and "html{background" in r.text
    r = c.post("/accept", data={"agree": "on"}, headers={"host": "192.168.1.2"})
    assert r.status_code == 200 and "✓" in r.text
    assert c.get("/generate_204", headers={"host": "connectivitycheck.gstatic.com"}).status_code == 204
    assert c.get("/connecttest.txt", headers={"host": "www.msftconnecttest.com"}).text == "Microsoft Connect Test"
    r = c.get("/noticias", headers={"host": "example.com"})  # DNS aún en caché → reintento automático
    assert r.status_code == 200 and 'http-equiv="refresh"' in r.text and "example.com/noticias" in r.text
