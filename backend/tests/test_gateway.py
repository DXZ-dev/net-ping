"""Puerta de enlace con un solo cable: reglas nftables, reversión segura y DNS de respaldo."""
import asyncio
import shutil
import subprocess

import pytest

import bipon.router as router_mod
from bipon.router import RouterManager


class Obj:
    def __init__(self, **kw):
        self.__dict__.update(kw)


class Settings(dict):
    def get(self, k, d=None):
        return super().get(k, d)

    def set(self, k, v, notify=True):
        self[k] = v


def make(monkeypatch):
    monkeypatch.setattr(router_mod.netinfo, "interfaces", lambda: [{"name": "eth0", "ip": "192.168.18.205", "prefix": 24,
                                                                    "network": "192.168.18.0/24", "mac": "02:00:00:00:00:01"}])
    monkeypatch.setattr(router_mod.netinfo, "primary_ip", lambda: "192.168.18.205")
    devs = Obj(devices={"aa:00:00:00:00:01": {"ip": "192.168.18.40"}}, self_ip="192.168.18.205", gateway="192.168.18.1",
               policy_for=lambda ip: Obj(block_all=False))
    ctx = Obj(settings=Settings({"dns": {"port": 53}}), devices=devs, db=Obj(event=lambda *a: None), captive=None)
    ctx.settings["dns.port"] = 53
    r = RouterManager.__new__(RouterManager)
    r.ctx, r.pending, r._rollback_task, r.last_error = ctx, None, None, ""
    return r, ctx


def test_one_arm_ruleset(monkeypatch):
    r, _ = make(monkeypatch)
    c = {**r.cfg(), "enabled": True, "wan": "eth0", "lan": "eth0",
         "port_forwards": [{"name": "Xbox", "proto": "udp", "port": 3074, "to_ip": "192.168.18.50"}]}
    rules = r.ruleset(c)
    # NAT sólo para lo que sale de la LAN hacia fuera (la respuesta vuelve por Bipon); lo local no se toca
    assert 'oifname "eth0" ip saddr 192.168.18.0/24 ip daddr != 192.168.18.0/24 masquerade' in rules
    # la redirección de puertos sólo captura lo dirigido a Bipon, no el tráfico entre equipos de casa
    assert 'iifname "eth0" ip daddr 192.168.18.205 udp dport 3074' in rules
    nft = shutil.which("nft")
    if nft:
        res = subprocess.run([nft, "-c", "-f", "-"], input=rules, capture_output=True, text=True)
        if "Operation not permitted" not in res.stderr:
            assert res.returncode == 0, res.stderr


def test_two_nics_keep_classic_nat(monkeypatch):
    r, _ = make(monkeypatch)
    rules = r.ruleset({**r.cfg(), "enabled": True, "wan": "eth1", "lan": "eth0"})
    assert 'oifname "eth1" masquerade' in rules and "gateway un brazo" not in rules


def test_rollback_restores_saved_config(monkeypatch):
    r, ctx = make(monkeypatch)
    ctx.settings["router"] = {"enabled": False}
    calls = []

    async def fake_run(*args, input=None, timeout=20):
        calls.append(args)
        return 0, ""
    monkeypatch.setattr(router_mod, "_run", fake_run)
    monkeypatch.setattr(router_mod, "ROLLBACK_SECONDS", 0)
    monkeypatch.setattr(RouterManager, "available", property(lambda self: True))

    async def go():
        res = await r.apply({**r.cfg(), "enabled": True, "wan": "eth0", "lan": "eth0"}, confirm_required=True)
        assert res["pending"] and ctx.settings["router"]["enabled"]
        await asyncio.sleep(0.05)  # la reversión automática se ejecuta (sin confirmar)
    asyncio.run(go())
    assert ctx.settings["router"]["enabled"] is False and r.pending is None  # lo no confirmado no se reaplica


@pytest.mark.parametrize("gateway_mode", [True, False])
def test_backup_dns_points_to_isp_router(gateway_mode):
    import socket
    from bipon.dhcp import DHCPServer
    cfg = {"server_ip": "192.168.18.205", "netmask": "255.255.255.0", "router": "192.168.18.205" if gateway_mode else "192.168.18.1",
           "dns": ["192.168.18.205"], "backup_dns_router": True, **({"isp_router": "192.168.18.1"} if gateway_mode else {})}
    srv = DHCPServer.__new__(DHCPServer)
    srv.ctx = Obj(settings=Obj(get=lambda k, d=None: cfg if k == "dhcp" else d), captive=Obj(cfg=lambda: {}))
    opts = dict(srv._options(1800))
    assert socket.inet_ntoa(opts[3]) == cfg["router"]
    assert socket.inet_ntoa(opts[6][4:8]) == "192.168.18.1"
