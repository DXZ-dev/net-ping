"""Pruebas del núcleo: parser de listas, motor de filtrado, horarios, caché,
configuración de Unbound y generador WireGuard."""
import time
from datetime import datetime
from pathlib import Path

import pytest
from dnslib import QTYPE, RR, A, DNSRecord

from bipon.devices import schedule_active
from bipon.dns.cache import DNSCache
from bipon.dns.filter import (ST_LIST, ST_RULE, ST_SAFE, ST_SERVICE, ST_DEVICE, FilterEngine, Policy,
                                parse_list_line)
from bipon.dns.upstream import Upstream
from bipon.unbound import render_config
from bipon.wireguard import generate


# ------------------------------------------------------------------ listas
@pytest.mark.parametrize("line,expected", [
    ("0.0.0.0 ads.example.com", ("ads.example.com", False)),
    ("127.0.0.1 tracker.net # comentario", ("tracker.net", False)),
    ("||doubleclick.net^", ("doubleclick.net", True)),
    ("||ads.com^$important", ("ads.com", True)),
    ("plain-domain.org", ("plain-domain.org", False)),
    ("*.wild.io", ("wild.io", True)),
    ("# comentario", None), ("! adblock", None), ("@@||allowed.com^", None),
    ("0.0.0.0 localhost", None), ("||ads.com^$third-party", None), ("/regex/", None), ("", None),
])
def test_parse_list_line(line, expected):
    assert parse_list_line(line) == expected


@pytest.fixture
def engine(tmp_path: Path):
    ads = tmp_path / "ads.txt"
    ads.write_text("0.0.0.0 ads.example.com\n||tracker.net^\n")
    sec = tmp_path / "sec.txt"
    sec.write_text("malware.bad\n")
    adult = tmp_path / "adult.txt"
    adult.write_text("adult.site\n")
    e = FilterEngine()
    e.build([(ads, "ads"), (sec, "security"), (adult, "adult")], [
        {"value": "blocked.org", "kind": "deny", "enabled": 1},
        {"value": "ok.tracker.net", "kind": "allow", "enabled": 1},
        {"value": r"^ad[0-9]+\.", "kind": "regex_deny", "enabled": 1},
    ])
    return e


def test_lists_exact_and_wildcard(engine):
    p = Policy()
    assert engine.check("ads.example.com", p, True, set()).status == ST_LIST
    assert engine.check("sub.ads.example.com", p, True, set()) is None  # hosts = exacto
    assert engine.check("x.tracker.net", p, True, set()).status == ST_LIST  # ||dominio^ = comodín
    assert engine.check("example.com", p, True, set()) is None


def test_allow_rule_wins(engine):
    assert engine.check("ok.tracker.net", Policy(), True, set()) is None


def test_custom_rules_and_regex(engine):
    assert engine.check("www.blocked.org", Policy(), True, set()).status == ST_RULE
    assert engine.check("ad12.cdn.com", Policy(), True, set()).blocked


def test_categories_per_profile(engine):
    assert engine.check("adult.site", Policy(adult=False), True, set()) is None
    assert engine.check("adult.site", Policy(adult=True), True, set()).status == ST_LIST
    assert engine.check("malware.bad", Policy(threat=False), True, set()) is None


def test_pause_keeps_parental(engine):
    p = Policy(services=frozenset({"tiktok"}))
    assert engine.check("ads.example.com", p, False, set()) is None  # bloqueo pausado
    assert engine.check("www.tiktok.com", p, False, set()).status == ST_SERVICE  # control parental sigue
    assert engine.check("anything.com", Policy(block_all=True), False, set()).status == ST_DEVICE


def test_global_services(engine):
    assert engine.check("rbxcdn.com", Policy(), True, {"roblox"}).status == ST_SERVICE


def test_safesearch(engine):
    d = engine.check("www.google.com", Policy(safesearch=True), True, set())
    assert d.status == ST_SAFE and d.cname == "forcesafesearch.google.com"


def test_protected_gaming_domains(engine, tmp_path):
    f = tmp_path / "bad.txt"
    f.write_text("||steampowered.com^\n")
    engine.build([(f, "ads")], [])
    assert engine.check("api.steampowered.com", Policy(), True, set()).status == ST_LIST
    engine.protected = {"steampowered.com"}
    assert engine.check("api.steampowered.com", Policy(), True, set()) is None
    # el control parental sí puede bloquearlo
    assert engine.check("api.steampowered.com", Policy(services=frozenset({"steam"})), True, set()).status == ST_SERVICE


# ------------------------------------------------------------------ horarios
def test_schedule_cross_midnight():
    s = {"start": "22:00", "end": "07:00", "days": [0]}  # lunes por la noche
    assert schedule_active(s, datetime(2026, 11, 9, 23, 0))   # lunes 23:00
    assert schedule_active(s, datetime(2026, 11, 10, 6, 59))  # martes 06:59 (continúa la noche del lunes)
    assert not schedule_active(s, datetime(2026, 11, 10, 7, 0))
    assert not schedule_active(s, datetime(2026, 11, 10, 23, 0))  # martes no está en days


def test_schedule_same_day():
    s = {"start": "16:00", "end": "19:00", "days": [0, 1, 2, 3, 4]}
    assert schedule_active(s, datetime(2026, 11, 11, 17, 0))
    assert not schedule_active(s, datetime(2026, 11, 14, 17, 0))  # sábado


# ------------------------------------------------------------------ caché
def _answer(name="example.com", ttl=60):
    q = DNSRecord.question(name)
    r = q.reply()
    r.add_answer(RR(name, QTYPE.A, rdata=A("1.2.3.4"), ttl=ttl))
    return r


def test_cache_hit_and_ttl_decrement(monkeypatch):
    c = DNSCache(size=10)
    k = DNSCache.key("example.com", QTYPE.A)
    c.put(k, _answer())
    rec, state = c.get(k, 1234)
    assert state == "hit" and rec.header.id == 1234 and rec.rr[0].ttl == 60


def test_cache_optimistic_stale(monkeypatch):
    c = DNSCache(size=10, optimistic=True)
    k = DNSCache.key("example.com", QTYPE.A)
    c.put(k, _answer(ttl=1))
    c._d[k].expires = time.monotonic() - 5
    rec, state = c.get(k, 1)
    assert state == "stale" and rec.rr[0].ttl == 30
    c.optimistic = False
    assert c.get(k, 1)[1] == "miss"


def test_cache_lru_eviction():
    c = DNSCache(size=2)
    for n in ("a.com", "b.com", "c.com"):
        c.put(DNSCache.key(n, QTYPE.A), _answer(n))
    assert len(c._d) == 2 and DNSCache.key("a.com", QTYPE.A) not in c._d


# ------------------------------------------------------------------ upstream
@pytest.mark.parametrize("spec,proto,host,port", [
    ("1.1.1.1", "udp", "1.1.1.1", 53), ("127.0.0.1:5335", "udp", "127.0.0.1", 5335),
    ("tcp://9.9.9.9", "tcp", "9.9.9.9", 53), ("tls://1.1.1.1#cloudflare-dns.com", "dot", "1.1.1.1", 853),
    ("https://1.1.1.1/dns-query", "doh", "1.1.1.1", 443), ("[2606:4700::1111]:53", "udp", "2606:4700::1111", 53),
])
def test_upstream_parse(spec, proto, host, port):
    u = Upstream.parse(spec)
    assert (u.proto, u.host, u.port) == (proto, host, port)


# ------------------------------------------------------------------ unbound / wireguard
def test_unbound_config(tmp_path):
    cfg = {"port": 5335, "mode": "forward-tls", "forward_servers": ["1.1.1.1@853#cloudflare-dns.com"], "num_threads": 2,
           "msg_cache_size": "64m", "rrset_cache_size": "128m", "so_rcvbuf": "1m"}
    text = render_config(cfg, tmp_path)
    assert "port: 5335" in text and "forward-tls-upstream: yes" in text and "num-threads: 2" in text
    with pytest.raises(ValueError):
        render_config({**cfg, "forward_servers": ["1.1.1.1; rm -rf /"]}, tmp_path)
    with pytest.raises(ValueError):
        render_config({**cfg, "msg_cache_size": "64m\ninclude: /etc/shadow"}, tmp_path)


def test_wireguard_relay():
    r = generate({"mode": "relay", "endpoint": "203.0.113.5", "clients": ["movil"],
                  "forwards": [{"proto": "udp", "port": 3074, "target": "192.168.1.50"}]})
    names = [f["name"] for f in r["files"]]
    assert names == ["wg0-vps.conf", "wg0-casa.conf", "movil.conf"]
    vps = r["files"][0]["content"]
    assert "DNAT --to-destination 192.168.1.50:3074" in vps and "PersistentKeepalive" in r["files"][1]["content"]
    with pytest.raises(ValueError):
        generate({"mode": "home", "endpoint": "bad host; rm"})


def test_cache_trim_and_usage():
    c = DNSCache(size=10)
    for i in range(10):
        c.put(DNSCache.key(f"d{i}.com", QTYPE.A), _answer(f"d{i}.com"))
    assert c.usage() == 100
    freed = c.trim(70)
    assert freed == 3 and c.usage() == 70 and DNSCache.key("d9.com", QTYPE.A) in c._d  # se conserva lo más reciente


def test_parse_size():
    from bipon.cacheguard import parse_size
    assert parse_size("64m") == 64 * 1024 ** 2 and parse_size("1g") == 1024 ** 3 and parse_size("512k") == 512 * 1024
