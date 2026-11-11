"""Rastreadores camuflados por CNAME y sugerencias de dominios que rompen apps."""
import time

from dnslib import CNAME, QTYPE, RR, A, DNSRecord

from bipon.db import init_db
from bipon.dns.filter import ST_LIST, FilterEngine, Policy
from bipon.dns.server import DNSServer
from bipon.suggest import suggestions


def engine(tmp_path, allow=()):
    lst = tmp_path / "ads.txt"
    lst.write_text("||tracker-cdn.net^\n")
    e = FilterEngine()
    e.build([(lst, "ads")], [{"value": a, "kind": "allow", "enabled": 1} for a in allow])
    return e


def reply_with_cname(name, target):
    r = DNSRecord.question(name).reply()
    r.add_answer(RR(name, QTYPE.CNAME, rdata=CNAME(target), ttl=60))
    r.add_answer(RR(target, QTYPE.A, rdata=A("203.0.113.9"), ttl=60))
    return r


def srv(e):
    s = DNSServer.__new__(DNSServer)
    s.engine, s.global_services = e, set()
    return s


def test_cname_cloaked_tracker_is_blocked(tmp_path):
    s = srv(engine(tmp_path))
    hit = s._cname_cloak("metrics.tienda.com", reply_with_cname("metrics.tienda.com", "x1.tracker-cdn.net"), Policy())
    assert hit and hit[0] == ST_LIST and "tracker-cdn.net" in hit[1]
    assert s._cname_cloak("www.tienda.com", reply_with_cname("www.tienda.com", "tienda.cdn.example"), Policy()) is None


def test_cname_respects_manual_allow(tmp_path):
    s = srv(engine(tmp_path, allow=["metrics.tienda.com"]))
    assert s._cname_cloak("metrics.tienda.com", reply_with_cname("metrics.tienda.com", "x1.tracker-cdn.net"), Policy()) is None


class Obj:
    def __init__(self, **kw):
        self.__dict__.update(kw)


def test_suggestions_keep_retries_skip_ads_and_security(tmp_path):
    db = init_db(tmp_path / "s.db")
    now = time.time()
    rows = []
    for i in range(30):
        rows.append((now - i, "192.168.18.40", "s.youtube.com", "A", ST_LIST, "Lista: anuncios", "", 0, "aa:00:00:00:00:01"))
        rows.append((now - i, "192.168.18.40", "api.miapp.com", "A", ST_LIST, "Lista: anuncios", "", 0, "aa:00:00:00:00:01"))
        rows.append((now - i, "192.168.18.40", "ads.doubleclick.net", "A", ST_LIST, "Lista: anuncios", "", 0, "aa:00:00:00:00:01"))
        rows.append((now - i, "192.168.18.40", "malware.bad", "A", ST_LIST, "Lista: seguridad", "", 0, "aa:00:00:00:00:01"))
    for i in range(3):  # pocos intentos: no es una app rota
        rows.append((now - i, "192.168.18.40", "poco.example", "A", ST_LIST, "Lista: anuncios", "", 0, "aa:00:00:00:00:01"))
    for r in rows:
        db.log_query(r)
    ctx = Obj(db=db, settings=Obj(get=lambda k, d=None: d),
              devices=Obj(devices={"aa:00:00:00:00:01": {"ip": "192.168.18.40", "name": "Móvil de Ana"}}))
    out = suggestions(ctx)
    assert [x["domain"] for x in out] == ["s.youtube.com", "api.miapp.com"]   # conocida primero
    assert out[0]["known"] and out[0]["devices"] == ["Móvil de Ana"] and out[0]["retries"] == 30
