"""Seguridad avanzada: equipos que se saltan Bipon, auditoría UPnP y cuarentena."""
import asyncio
import time

from bipon.db import init_db
from bipon.guard import Guard


class Obj:
    def __init__(self, **kw):
        self.__dict__.update(kw)


class Settings(dict):
    def get(self, k, d=None):
        return super().get(k, d)

    def set(self, k, v, notify=True):
        self[k] = v


def make(tmp_path):
    db = init_db(tmp_path / "g.db")
    now = time.time()
    devices = {
        "aa:00:00:00:00:01": {"ip": "192.168.18.30", "name": "TV sin Bipon", "online": 1, "first_seen": now - 86400},
        "aa:00:00:00:00:02": {"ip": "192.168.18.31", "name": "PC con DoH", "online": 1, "first_seen": now - 86400},
        "aa:00:00:00:00:03": {"ip": "192.168.18.32", "name": "Móvil normal", "online": 1, "first_seen": now - 86400},
        "aa:00:00:00:00:04": {"ip": "192.168.18.33", "name": "Recién llegado", "online": 1, "first_seen": now - 600},
    }
    updates, applied, notes = [], [], []

    def update(mac, patch):
        updates.append((mac, patch))
        devices[mac].update(patch)
        return devices[mac]
    ctx = Obj(db=db, settings=Settings(), devices=Obj(devices=devices, gateway="192.168.18.1", self_ip="192.168.18.205", update=update),
              router=Obj(cfg=lambda: {}), apply_policy_now=lambda macs: applied.append(macs),
              notify=Obj(emit=lambda *a, **k: notes.append(a)))
    for i in range(5):
        db.log_query((now - 60 * i, "192.168.18.31", "dns.google", "A", 0, "", "1.1.1.1", 1.0, "aa:00:00:00:00:02"))
        db.log_query((now - 60 * i, "192.168.18.32", "whatsapp.net", "A", 0, "", "1.1.1.1", 1.0, "aa:00:00:00:00:03"))
    db.flush_queries()
    return Guard(ctx), ctx, updates, applied, notes


def test_bypass_detection(tmp_path):
    g, *_ = make(tmp_path)
    b = g.bypass(24)
    assert [x["name"] for x in b["no_dns"]] == ["TV sin Bipon"]          # el recién llegado aún no cuenta
    assert [x["name"] for x in b["encrypted"]] == ["PC con DoH"] and b["encrypted"][0]["domains"][0]["domain"] == "dns.google"


def test_quarantine_on_malware_and_release(tmp_path):
    g, ctx, updates, applied, notes = make(tmp_path)
    g.on_anomaly("aa:00:00:00:00:03", "192.168.18.32", "burst", "ráfaga")      # no es malware → nada
    assert not updates
    g.on_anomaly("aa:00:00:00:00:03", "192.168.18.32", "dga", "dominios aleatorios")
    assert updates[-1][1]["paused_until"] > time.time() + 5 * 3600 and applied[-1] == ["aa:00:00:00:00:03"]
    assert notes[-1][0] == "quarantine" and [q["mac"] for q in g.quarantined()] == ["aa:00:00:00:00:03"]
    g.release("aa:00:00:00:00:03")
    assert updates[-1][1]["paused_until"] == 0 and g.quarantined() == []
    ctx.settings["guard"] = {"quarantine": False}
    g.on_anomaly("aa:00:00:00:00:01", "192.168.18.30", "dga", "x")
    assert updates[-1][0] != "aa:00:00:00:00:01"


def test_upnp_risk_classification(tmp_path, monkeypatch):
    g, *_ = make(tmp_path)

    class IGD:
        name = "Huawei"

        async def mappings(self):
            return [{"external_port": 8080, "internal_port": 80, "protocol": "TCP", "internal_ip": "192.168.18.30"},
                    {"external_port": 3389, "internal_port": 3389, "protocol": "TCP", "internal_ip": "192.168.18.31"},
                    {"external_port": 3074, "internal_port": 3074, "protocol": "UDP", "internal_ip": "192.168.18.32"}]

    async def find():
        return IGD()
    import bipon.nat as nat
    monkeypatch.setattr(nat, "find_igd", find)
    r = asyncio.run(g.upnp_audit())
    assert [m["risk"] for m in r["mappings"]] == ["high", "medium", "info"] and r["mappings"][0]["device"] == "PC con DoH"
