"""Multi-WAN, anti-lag (SQM/DSCP), clasificación por DNS, ruta salto a salto e interfaces de red."""
import asyncio
import shutil
import subprocess
from collections import deque

import pytest

from bipon import qos
from bipon.multiwan import MultiWAN, weight_map
from bipon.trace import analyze, parse_mtr


class Obj:
    def __init__(self, **kw):
        self.__dict__.update(kw)


class Settings(dict):
    def get(self, k, d=None):
        cur = self
        for p in k.split("."):
            if not isinstance(cur, dict) or p not in cur:
                return d
            cur = cur[p]
        return cur

    def set(self, k, v, notify=True):
        self[k] = v


WANS = [{"id": "w1", "name": "Fibra", "iface": "eth0", "gateway": "192.168.18.1", "priority": 1, "weight": 3},
        {"id": "w2", "name": "4G", "iface": "eth0", "gateway": "192.168.18.2", "priority": 2, "weight": 1}]


def make_mw(mode="failover", **extra):
    events = []
    devs = Obj(devices={"aa:00:00:00:00:01": {"ip": "192.168.18.40"}, "aa:00:00:00:00:02": {"ip": "192.168.18.41"},
                        "aa:00:00:00:00:03": {"ip": "192.168.18.42"}}, self_ip="192.168.18.205", gateway="192.168.18.1")
    settings = Settings({"router": {"enabled": True, "wan": "eth0", "lan": "eth0"},
                         "multiwan": {"enabled": True, "mode": mode, "wans": [dict(w) for w in WANS], **extra}})
    ctx = Obj(settings=settings, devices=devs, db=Obj(event=lambda *a: events.append(a), execute=lambda *a: None),
              notify=Obj(emit=lambda *a, **k: None))
    ctx.router = Obj(cfg=lambda: settings["router"], usage=lambda h=24: [])
    mw = MultiWAN(ctx)
    settings["multiwan"] = mw.validate(settings["multiwan"])
    return mw, ctx, events


def test_marks_are_unique_and_stable():
    mw, ctx, _ = make_mw()
    marks = [w["mark"] for w in ctx.settings["multiwan"]["wans"]]
    assert len(set(marks)) == 2 and all(200 < m < 251 for m in marks)
    again = mw.validate(dict(ctx.settings["multiwan"]))
    assert [w["mark"] for w in again["wans"]] == marks


def test_weight_map_covers_0_99_and_skips_down_lines():
    wans = [{"id": "a", "weight": 3, "mark": 201}, {"id": "b", "weight": 1, "mark": 202}]
    m = weight_map(wans, {})
    assert m[0] == (0, 74, 201) and m[-1] == (75, 99, 202)
    assert weight_map(wans, {"a": {"up": False}}) == [(0, 99, 202)]


def test_failover_rules_use_primary_then_backup():
    mw, _, _ = make_mw("failover")
    rules = "\n".join(mw.nft_lines(["eth0"], "192.168.18.0/24", []))
    assert "meta mark set 201" in rules and "jhash" not in rules
    mw.state["w1"] = {"up": False}
    rules = "\n".join(mw.nft_lines(["eth0"], "192.168.18.0/24", []))
    assert "meta mark set 202" in rules and "ct mark { 202 }" in rules  # las conexiones de la línea caída no se respetan


def test_balance_by_consumption_and_device_rules():
    mw, ctx, _ = make_mw("balance", balance_by="device", device_rules={"aa:00:00:00:00:03": "w2"}, smart_gaming=False)
    ctx.router.usage = lambda h=24: [{"mac": "aa:00:00:00:00:01", "rx": 9e9, "tx": 0}, {"mac": "aa:00:00:00:00:02", "rx": 1e9, "tx": 0}]
    rules = "\n".join(mw.nft_lines(["eth0"], "192.168.18.0/24", []))
    assert "ether saddr aa:00:00:00:00:03 meta mark set 202" in rules  # la regla del usuario manda
    # con peso 3:1 el que más consume va a la línea grande; el resto también cabe en ella
    assert "192.168.18.40 : 201" in rules
    assert "jhash ip saddr mod 100" in rules  # equipos desconocidos


def test_smart_gaming_sends_games_to_lowest_ping_line():
    mw, _, _ = make_mw("failover", smart_gaming=True)
    mw.state = {"w1": {"up": True, "latency": 40.0, "samples": deque()}, "w2": {"up": True, "latency": 12.0, "samples": deque()}}
    rules = "\n".join(mw.nft_lines(["eth0"], "192.168.18.0/24", ["bb:00:00:00:00:09"]))
    assert "udp dport @mwan_game_udp meta mark set 202" in rules
    assert "ether saddr @mwan_consoles meta mark set 202" in rules


def test_health_state_machine_failover_and_flap_damping():
    mw, _, _ = make_mw()
    c = mw.cfg()
    w = mw.wans()[0]
    assert [mw._decide(w, 10.0, c) for _ in range(3)] == [None, None, None]
    assert [mw._decide(w, None, c) for _ in range(3)][-1] == "down"
    assert [mw._decide(w, 10.0, c) for _ in range(c["recover_after"])][-1] == "up"
    for _ in range(3):  # cae y vuelve varias veces: se exige más tiempo estable
        for _ in range(3):
            mw._decide(w, None, c)
        for _ in range(c["recover_after"] * 3):
            if mw._decide(w, 10.0, c) == "up":
                break
    for _ in range(3):
        mw._decide(w, None, c)
    res = [mw._decide(w, 10.0, c) for _ in range(c["recover_after"])]
    assert "up" not in res  # línea inestable: con el tiempo normal ya no vuelve


def test_best_line_needs_clear_and_repeated_advantage():
    mw, _, _ = make_mw(smart_gaming=True)
    mw.state = {"w1": {"up": True, "samples": deque([(0, 20.0)] * 10)}, "w2": {"up": True, "samples": deque([(0, 19.0)] * 10)}}
    mw._pick_best()
    first = mw.best
    for _ in range(10):
        mw._pick_best()
    assert mw.best == first  # 1 ms de diferencia no hace saltar las partidas de línea
    mw.state["w2"]["samples"] = deque([(0, 5.0)] * 10)
    mw.best = "w1"
    changes = [mw._pick_best() for _ in range(5)]
    assert changes == [False, False, False, False, True] and mw.best == "w2"


@pytest.mark.parametrize("mode", ["failover", "balance"])
def test_multiwan_rules_load_in_nftables(mode):
    nft = shutil.which("nft")
    if not nft:
        pytest.skip("sin nft")
    mw, _, _ = make_mw(mode, smart_gaming=True)
    mw.state = {"w1": {"up": True, "latency": 30.0, "samples": deque()}, "w2": {"up": True, "latency": 10.0, "samples": deque()}}
    text = "table inet bipon {\n" + "\n".join(mw.nft_lines(["eth0"], "192.168.18.0/24", ["bb:00:00:00:00:09"])) + "\n}\n"
    res = subprocess.run([nft, "-c", "-f", "-"], input=text, capture_output=True, text=True)
    if "Operation not permitted" in res.stderr or "Could not process rule: No such file" in res.stderr:
        pytest.skip("nft sin permisos/núcleo")
    assert res.returncode == 0, res.stderr


# ------------------------------------------------------------------ anti-lag
def test_sqm_plan_one_arm_splits_download_and_upload():
    p = qos.plan(lan="eth0", lan_net="192.168.18.0/24", wan_ifs=["eth0"], down=90, up=18, sqm=True, cake=True,
                 limits=[("192.168.18.41", 20, 5)], wan_ups=[(201, 18), (202, 9)])
    flat = [" ".join(c) for c in p["egress"]]
    assert p["one_arm"] and p["leaf"] == "cake"
    assert any("classid 1:10 htb rate 90mbit" in c for c in flat) and any("classid 1:20 htb rate 18mbit" in c for c in flat)
    assert any("match ip dst 192.168.18.0/24 flowid 1:19" in c for c in flat)  # bajada = destino red local
    assert any("match ip dst 192.168.18.41/32 flowid 1:100" in c for c in flat)  # límite por dispositivo
    assert any("match mark 202 0xff flowid 1:22" in c for c in flat)  # subida por línea del multi-WAN
    assert any("cake unlimited diffserv4 dual-dsthost nat" in c for c in flat)
    assert p["ifb"] and "match ip src 192.168.18.41/32" in " ".join(p["ifb"][-1])


def test_sqm_plan_two_nics_uses_cake_on_wan_and_falls_back():
    p = qos.plan(lan="eth1", lan_net="192.168.1.0/24", wan_ifs=["eth0"], down=300, up=50, sqm=True, cake=True, limits=[])
    assert not p["one_arm"] and "50mbit" in p["wan"][0]
    assert any(c[:6] == ["qdisc", "replace", "dev", "eth0", "root", "cake"] for c in p["wan"])
    p2 = qos.plan(lan="eth1", lan_net="192.168.1.0/24", wan_ifs=["eth0"], down=300, up=50, sqm=True, cake="pfifo", limits=[])
    assert p2["leaf"] == "pfifo" and not any("cake" in c for cmd in p2["egress"] + p2["wan"] for c in cmd)
    assert not p2["ifb"]  # sin límites de subida por dispositivo no hace falta ifb


def test_dns_classification_and_priority_order():
    assert qos.category("rr3---sn-abc.googlevideo.com") == "stream"
    assert qos.category("gateway.discord.media") == "call"
    assert qos.category("cache.steamcontent.com") == "bulk"
    assert qos.category("www.wikipedia.org") is None
    ctx = Obj(settings=Settings({"sqm": {"dscp": True}}))
    lines = qos.dscp_lines(ctx, ["bb:00:00:00:00:09"], ["192.168.18.50"])
    body = [x.strip() for x in lines if x.strip().startswith(("ip ", "udp ", "ct ", "ether ", "meta "))]
    idx = lambda key: next(i for i, x in enumerate(body) if key in x)  # noqa: E731
    # el último que marca gana: descargas < streaming < llamadas/juegos
    assert idx("ct bytes > ") < idx("@qos_stream") < idx("@qos_game") and idx("@qos_bulk") < idx("@qos_stream")
    nft = shutil.which("nft")
    if nft:
        res = subprocess.run([nft, "-c", "-f", "-"], input="table inet bipon {\n" + "\n".join(lines) + "\n}\n", capture_output=True, text=True)
        if "Operation not permitted" not in res.stderr:
            assert res.returncode == 0, res.stderr


def test_classifier_batches_ips_into_nft(monkeypatch):
    from dnslib import RR, A, DNSRecord
    sent = []

    class P:
        async def communicate(self, data):
            sent.append(data.decode())
            return b"", b""

    async def fake_exec(*a, **k):
        return P()
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    ctx = Obj(settings=Settings({"sqm": {"dscp": True}}), router=Obj(cfg=lambda: {"enabled": True}, available=True))
    cl = qos.Classifier(ctx)
    rec = DNSRecord.question("abc.nflxvideo.net")
    rep = rec.reply()
    rep.add_answer(RR("abc.nflxvideo.net", rdata=A("45.57.1.1")))

    async def go():
        cl.on_answer("abc.nflxvideo.net", rep.pack())
        cl.on_answer("www.example.com", rep.pack())
        await asyncio.sleep(1.2)
    asyncio.run(go())
    assert sent == ["add element inet bipon qos_stream { 45.57.1.1 }"]


# ------------------------------------------------------------------ ruta salto a salto
def _hops(*rows):
    return [{"hop": i + 1, "ip": ip, "loss": loss, "avg": avg, "best": avg, "worst": avg, "stdev": 0, "asn": asn}
            for i, (ip, loss, avg, asn) in enumerate(rows)]


def test_trace_blames_isp_when_jump_persists():
    hops = _hops(("192.168.18.1", 0, 1.0, None), ("10.20.0.1", 0, 4.0, None), ("181.1.1.1", 0, 45.0, 6400),
                 ("181.1.2.1", 0, 46.0, 6400), ("1.1.1.1", 0, 47.0, 13335))
    r = analyze(hops, 6400)
    assert r["lag_hop"] == 3 and r["culprit"] == "proveedor" and "Tu proveedor" in r["verdict"]


def test_trace_ignores_icmp_rate_limited_router():
    hops = _hops(("192.168.18.1", 0, 1.0, None), ("181.1.1.1", 0, 6.0, 6400), ("181.1.2.1", 60, 90.0, 6400),
                 ("1.1.1.1", 0, 8.0, 13335))
    r = analyze(hops, 6400)
    assert r["culprit"] is None and "limpia" in r["verdict"]  # un salto lento que no se mantiene no es el culpable


def test_trace_local_problem_and_parse():
    hops = _hops(("192.168.18.1", 0, 25.0, None), ("1.1.1.1", 0, 30.0, 13335))
    assert analyze(hops)["culprit"] == "casa"
    parsed = parse_mtr({"report": {"hubs": [{"count": 1, "host": "192.168.18.1", "Loss%": 0.0, "Avg": 0.5, "Best": 0.4, "Wrst": 0.9, "StDev": 0.1},
                                            {"count": 2, "host": "???", "Loss%": 100.0}]}})
    assert parsed[0]["avg"] == 0.5 and parsed[1]["ip"] is None and parsed[1]["avg"] is None


# ------------------------------------------------------------------ interfaces de red
def test_interface_validation():
    from bipon.ifaces import Interfaces
    i = Interfaces.__new__(Interfaces)
    assert i.validate("eth1", {"mode": "static", "address": "192.168.8.10/24", "gateway": "192.168.8.1", "mtu": 1500})["gateway"] == "192.168.8.1"
    for bad in ({"mode": "static", "address": "192.168.8.10"}, {"mode": "static", "address": "192.168.8.10/24", "gateway": "10.0.0.1"},
                {"mode": "dhcp", "mtu": 100}, {"mode": "loquesea"}):
        with pytest.raises(ValueError):
            i.validate("eth1", bad)
    with pytest.raises(ValueError):
        i.validate("eth1;rm", {"mode": "dhcp"})
