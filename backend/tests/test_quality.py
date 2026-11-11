"""Calidad de conexión: clasificación Wi-Fi, mapa de lag, cambio de ruta e informe para el proveedor."""
import asyncio
import time

from bipon.db import init_db
from bipon.quality import Quality, classify_lan


class Obj:
    def __init__(self, **kw):
        self.__dict__.update(kw)


def make(tmp_path):
    events, notes = [], []
    db = init_db(tmp_path / "q.db")
    real_event = db.event
    db.event = lambda *a: (events.append(a), real_event(*a))
    ctx = Obj(db=db, settings=Obj(get=lambda k, d=None: d), notify=Obj(emit=lambda *a, **k: notes.append(a)),
              devices=Obj(devices={}, self_ip="192.168.18.205", gateway="192.168.18.1"))
    return Quality(ctx), ctx, events, notes


def test_classify_lan_power_save_is_not_weak():
    # móvil en reposo: primer ping 180 ms, el resto rápidos → mínimo bajo = señal buena
    assert classify_lan({"min": 2.1, "avg": 31, "jitter": 35, "loss": 0, "received": 6}) == "regular"
    assert classify_lan({"min": 1.2, "avg": 1.8, "jitter": 0.4, "loss": 0, "received": 6}) == "buena"
    assert classify_lan({"min": 22, "avg": 40, "jitter": 9, "loss": 0, "received": 6}) == "débil"
    assert classify_lan({"min": 3, "avg": 4, "jitter": 1, "loss": 33.3, "received": 4}) == "débil"
    assert classify_lan({"received": 0}) == "sin respuesta"


def test_heatmap_groups_by_day_and_hour(tmp_path):
    q, ctx, _, _ = make(tmp_path)
    now = time.time()
    rows = [(now - i * 3600, "1.1.1.1", 20 + (i % 24), 0) for i in range(48)] + [(now, "gateway", 1, 0)]
    ctx.db.executemany("INSERT INTO monitor(ts,target,latency,loss) VALUES (?,?,?,?)", rows)
    h = q.heatmap(7)
    cells = [c for row in h["grid"] for c in row if c]
    assert len(cells) == 48 and all(c["ms"] >= 20 for c in cells)  # 2 días × 24 h; el router (1 ms) no cuenta
    assert h["worst"][0]["ms"] == max(c["ms"] for c in cells)


def test_route_change_alert(tmp_path, monkeypatch):
    q, ctx, events, notes = make(tmp_path)
    now = time.time()
    ctx.db.executemany("INSERT INTO game_routes(ts,region,ms) VALUES (?,?,?)", [(now - 3600 * (i + 1), "us-east-1", 40.0) for i in range(10)])
    q.ctx.settings = Obj(get=lambda k, d=None: {"game_region": "us-east-1"} if k == "quality" else d)

    async def fake_tcp_ping(host, port, count=4, timeout=2.0):
        return {"min": 95.0}
    import bipon.pinger as pg
    monkeypatch.setattr(pg, "tcp_ping", fake_tcp_ping)
    r = asyncio.run(q.route_check())
    assert r["alert"] and r["median"] == 40.0 and notes and notes[0][0] == "route_change"


def test_report_counts_outages(tmp_path):
    q, ctx, _, _ = make(tmp_path)
    now = time.time()
    ctx.db.execute("INSERT INTO events(ts,level,category,message) VALUES (?,?,?,?)", (now - 600, "error", "conexión", "Se perdió la conexión a Internet"))
    ctx.db.execute("INSERT INTO events(ts,level,category,message) VALUES (?,?,?,?)", (now - 300, "warning", "conexión", "Conexión a Internet restablecida tras 300 s sin servicio"))
    ctx.db.executemany("INSERT INTO monitor(ts,target,latency,loss) VALUES (?,?,?,?)", [(now - 60, "1.1.1.1", 25, 0), (now - 120, "8.8.8.8", 35, 10)])
    r = q.report(30)
    assert r["summary"]["outages"] == 1 and r["summary"]["downtime_min"] == 5 and r["summary"]["avg_latency"] == 30.0
    assert "Informe de calidad" in q.report_html(30, 100) and "1.1.1.1" not in q.report_csv(30).split("\n")[0]
