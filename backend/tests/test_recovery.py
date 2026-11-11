"""FIX (reparación), volver a la versión anterior y restablecer de fábrica."""
import asyncio

import bipon.factory as factory
import bipon.repair as repair
import bipon.updater as updater_mod
from bipon.updater import Updater


class Obj:
    def __init__(self, **kw):
        self.__dict__.update(kw)


class Settings(dict):
    def get(self, k, d=None):
        return super().get(k, d)

    def set(self, k, v, notify=True):
        self[k] = v


def test_factory_moves_data_but_keeps_backups(tmp_path):
    (tmp_path / "settings.json").write_text("{}")
    (tmp_path / "netping.db").write_text("x")
    (tmp_path / "tls").mkdir()
    (tmp_path / "backups").mkdir()
    (tmp_path / "backups" / "bipon-20260101-0000.json").write_text("{}")
    (tmp_path / "manuf.txt").write_text("oui")
    dest = factory.move_data(tmp_path)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["backups", "manuf.txt"]
    assert (dest / "settings.json").exists() and (dest / "tls").is_dir()
    assert (tmp_path / "backups" / "bipon-20260101-0000.json").exists()


def test_factory_keeps_only_last_two_reset_folders(tmp_path):
    b = tmp_path / "backups"
    for i in range(4):
        (b / f"factory-2026010{i}-000000").mkdir(parents=True)
    factory._prune_old(b)
    assert sorted(p.name for p in b.iterdir()) == ["factory-20260102-000000", "factory-20260103-000000"]


def _updater(monkeypatch, history, current="c" * 40):
    ctx = Obj(settings=Settings({"updates": {"history": history}}), db=Obj(event=lambda *a: None))
    u = Updater(ctx)
    monkeypatch.setattr(updater_mod, "local_commit", lambda: current)
    monkeypatch.setattr(updater_mod, "source_dir", lambda: None)
    targets = []

    async def fake_apply(target=None):
        targets.append(target)
        return {"ok": True}
    monkeypatch.setattr(u, "apply", fake_apply)
    return u, targets


def test_rollback_picks_previous_version(monkeypatch):
    hist = [{"commit": "c" * 40}, {"commit": "b" * 40}, {"commit": "a" * 40}]
    u, targets = _updater(monkeypatch, hist)
    asyncio.run(u.rollback())
    assert targets == ["b" * 40]  # nunca «vuelve» a la versión que ya está instalada
    asyncio.run(u.rollback("aaaaaaa"))
    assert targets[-1] == "a" * 40


def test_rollback_without_history_is_explained(monkeypatch):
    u, targets = _updater(monkeypatch, [])
    r = asyncio.run(u.rollback())
    assert not r["ok"] and not targets


def test_remember_current_keeps_newest_first(monkeypatch):
    u, _ = _updater(monkeypatch, [{"commit": "a" * 40}], current="b" * 40)
    u._remember_current()
    u._remember_current()
    assert [h["commit"][0] for h in u.history()] == ["b", "a"]


def test_repair_cache_and_summary(monkeypatch):
    trimmed = []
    cache = Obj(usage=lambda: 95.0, trim=lambda pct: trimmed.append(pct) or 1234)
    c = Obj(cache=cache, db=Obj(event=lambda *a: None))
    step = asyncio.run(repair.check_cache(c))
    assert step["status"] == "fixed" and trimmed == [60]

    async def ok(c):
        return repair._step("a", "A", "ok")

    async def broken(c):
        raise RuntimeError("boom")
    monkeypatch.setattr(repair, "STEPS", [ok, repair.check_cache, broken])
    res = asyncio.run(repair.run(c))
    assert res["fixed"] == 1 and res["warnings"] == 1 and len(res["steps"]) == 3


def test_repair_restarts_dead_dns_server(monkeypatch):
    calls = []
    answers = iter([None, 3.2])

    async def fake_answers(port, timeout=2.0):
        return next(answers)
    monkeypatch.setattr(repair, "_dns_answers", fake_answers)

    async def stop():
        calls.append("stop")

    async def start(host, port):
        calls.append(("start", port))
    c = Obj(settings=Settings({"dns": {"port": 5353, "listen": "0.0.0.0"}}), dns=Obj(stop=stop, start=start), dns_error="x")
    step = asyncio.run(repair.check_dns_server(c))
    assert step["status"] == "fixed" and calls == ["stop", ("start", 5353)] and c.dns_error is None


def test_dns_answers_from_emergency_when_unbound_hangs(monkeypatch):
    """Unbound colgado: tras 3 fallos se deja de esperarle y se responde al instante con el respaldo."""
    from bipon.dns import upstream as up_mod
    pool = up_mod.UpstreamPool()
    pool.configure(["127.0.0.1:5335"], "fastest", 2000)
    calls = []

    async def fake_query(self, wire, timeout, http=None):
        calls.append(self.spec)
        if self.spec == "127.0.0.1:5335":
            raise asyncio.TimeoutError()
        return b"ok"
    monkeypatch.setattr(up_mod.Upstream, "query", fake_query)

    async def go():
        for _ in range(5):
            resp, used, _ = await pool.resolve(b"q")
            assert resp == b"ok" and used.spec == "1.1.1.1"
        await asyncio.sleep(0)
    asyncio.run(go())
    # 3 intentos bloqueantes al principal; después solo sondeos en segundo plano
    assert pool.upstreams[0].down and pool.emergency_used == 5
