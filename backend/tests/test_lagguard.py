"""Vigilante de lag: base, detección de picos y culpables por DNS."""
import time

from bipon.lagguard import LagGuard, classify


class Obj:
    def __init__(self, **kw):
        self.__dict__.update(kw)


def make():
    events, notes = [], []
    devs = Obj(devices={"aa:00:00:00:00:01": {"ip": "192.168.1.20", "name": "PC de Juan"}}, gateway="192.168.1.1", self_ip="192.168.1.2",
               mac_for=lambda ip: "aa:00:00:00:00:01" if ip == "192.168.1.20" else None)
    rates = Obj(ping_ms=12.0, snapshot=lambda: {})
    ctx = Obj(devices=devs, rates=rates, db=Obj(event=lambda *a: events.append(a)),
              notify=Obj(emit=lambda *a, **k: notes.append(a)))
    return LagGuard(ctx), ctx, events, notes


def test_classify():
    assert classify("cache1-ams1.steamcontent.com")[0] == "Descarga de juego"
    assert classify("rr3---sn-abc.googlevideo.com")[0] == "Vídeo / streaming"
    assert classify("www.wikipedia.org") is None


def test_spike_with_culprit():
    lg, ctx, events, notes = make()
    now = time.time()
    for i in range(30):
        lg.samples.append((now - 300 + i * 5, 12.0 + (i % 3)))
    assert lg.baseline() <= 13
    for _ in range(40):
        lg.observe("192.168.1.20", "cache2.steamcontent.com")
    ctx.rates.ping_ms = 95.0
    lg._tick()
    assert not notes          # una sola muestra alta no es un pico
    lg._tick()
    assert notes and "PC de Juan" in notes[0][2] and "Descarga de juego" in notes[0][2]
    st = lg.status()
    assert st["spikes"][0]["ms"] == 95.0 and st["culprits"][0]["name"] == "PC de Juan"
