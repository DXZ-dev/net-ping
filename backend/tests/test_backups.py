"""Copias de seguridad automáticas: creación con permisos 600, retención, plazos y restauración."""
import asyncio
import json
import os
import time

from bipon import backups
from bipon.db import init_db


class Obj:
    def __init__(self, **kw):
        self.__dict__.update(kw)


class Settings(dict):
    def get(self, k, d=None):
        return super().get(k, d)

    def set(self, k, v, notify=True):
        self[k] = v

    def export(self):
        return {"dns": {"prefer_ipv4": True}}

    def import_(self, data):
        self["imported"] = data


def make(tmp_path, monkeypatch):
    monkeypatch.setattr(backups, "DIR", tmp_path / "backups")
    db = init_db(tmp_path / "b.db")
    db.execute("INSERT INTO domain_rules(value,kind,comment,enabled,created) VALUES ('ok.com','allow','',1,0)")
    loaded = []
    c = Obj(db=db, settings=Settings(), devices=Obj(load=lambda: loaded.append(1)), lists=Obj(update=lambda: asyncio.sleep(0)))
    return c


def test_create_list_and_permissions(tmp_path, monkeypatch):
    c = make(tmp_path, monkeypatch)
    r = asyncio.run(backups.create(c, auto=True))
    p = backups.DIR / r["name"]
    assert p.exists() and oct(os.stat(p).st_mode)[-3:] == "600"
    data = json.loads(p.read_text())
    assert data["settings"]["dns"]["prefer_ipv4"] and data["rules"][0]["value"] == "ok.com"
    assert backups.listing()[0]["auto"] and not backups.due(c)          # recién hecha: no toca otra
    assert backups.due(c, now=time.time() + 86400)                       # al día siguiente sí


def test_retention_keeps_last_n(tmp_path, monkeypatch):
    c = make(tmp_path, monkeypatch)
    c.settings["auto_backup"] = {"keep": 3}
    backups.DIR.mkdir(parents=True)
    for i in range(6):
        f = backups.DIR / f"bipon-2026010{i}-0300-auto.json"
        f.write_text("{}")
        os.utime(f, (time.time() - (10 - i) * 86400,) * 2)
    (backups.DIR / "bipon-20260101-1200.json").write_text("{}")          # manual: nunca se borra sola
    backups.prune(c)
    names = [b["name"] for b in backups.listing()]
    assert len([n for n in names if n.endswith("-auto.json")]) == 3 and "bipon-20260101-1200.json" in names


def test_restore_and_bad_names(tmp_path, monkeypatch):
    c = make(tmp_path, monkeypatch)
    asyncio.run(backups.restore(c, {"settings": {"x": 1}, "rules": [{"value": "nuevo.com", "kind": "deny"}]}))
    assert c.settings["imported"] == {"x": 1}
    assert c.db.one("SELECT kind FROM domain_rules WHERE value='nuevo.com'")["kind"] == "deny"
    for bad in ("../../etc/passwd", "bipon.json", "x"):
        try:
            backups.path_of(bad)
            raise AssertionError(bad)
        except (ValueError, FileNotFoundError):
            pass
