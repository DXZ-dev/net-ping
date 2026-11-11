"""Controlador de velocidad: elección de método y llamadas a MikroTik / Proxmox (simulados)."""
import asyncio
import json

import httpx

from bipon.speedctl import SpeedControl

MAC_PHONE, MAC_VM = "aa:bb:cc:00:00:01", "bc:24:11:00:00:02"


class Obj:
    def __init__(self, **kw):
        self.__dict__.update(kw)


def make_ctx(router_type=None, calls=None):
    calls = calls if calls is not None else []
    queues = []

    def handler(req: httpx.Request):
        calls.append((req.method, req.url.path, req.content.decode() if req.content else ""))
        if req.url.path.endswith("/rest/queue/simple") and req.method == "GET":
            return httpx.Response(200, json=queues)
        if req.url.path.endswith("/rest/queue/simple") and req.method == "PUT":
            queues.append({".id": "*1", **json.loads(req.content)})
            return httpx.Response(201, json={})
        if "/qemu/" in req.url.path and req.method == "GET":
            return httpx.Response(200, json={"data": {"net0": f"virtio={MAC_VM.upper()},bridge=vmbr0,rate=5"}})
        return httpx.Response(200, json={"data": None})

    transport = httpx.MockTransport(handler)
    rc = Obj(cfg=lambda: {"type": router_type, "url": "http://192.168.88.1"} if router_type else {},
             _client=lambda: httpx.AsyncClient(transport=transport))
    settings = {"integrations": {"proxmox": {"url": "https://pve:8006", "token_id": "root@pam!bipon", "secret": "x"}}}
    ctx = Obj(settings=Obj(get=lambda k, d=None: settings.get(k, d)), routerctl=rc,
              router=Obj(cfg=lambda: {}),
              wifi=Obj(proxmox={MAC_VM: {"node": "homelab", "vmid": 104, "type": "qemu"}}),
              devices=Obj(devices={MAC_PHONE: {"ip": "192.168.18.50"}, MAC_VM: {"ip": "192.168.18.60"}}))
    return ctx, calls, transport


def test_methods():
    ctx, _, _ = make_ctx()
    sc = SpeedControl(ctx)
    assert sc.method_for(MAC_VM) == "proxmox"
    assert sc.method_for(MAC_PHONE) is None
    ctx2, _, _ = make_ctx("mikrotik")
    assert SpeedControl(ctx2).method_for(MAC_PHONE) == "mikrotik"


def test_mikrotik_queue():
    ctx, calls, _ = make_ctx("mikrotik")
    r = asyncio.run(SpeedControl(ctx).apply(MAC_PHONE, 10, 2))
    assert r["applied"] and r["method"] == "mikrotik"
    put = [c for c in calls if c[0] == "PUT"][0]
    body = json.loads(put[2])
    assert body["target"] == "192.168.18.50/32" and body["max-limit"] == "2000000/10000000"


def test_proxmox_rate(monkeypatch):
    ctx, calls, transport = make_ctx()
    real = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: real(transport=transport, headers=kw.get("headers")))
    r = asyncio.run(SpeedControl(ctx).apply(MAC_VM, 16, 4))
    assert r["applied"], r
    put = [c for c in calls if c[0] == "PUT"][0]
    assert "rate%3D2.0" in put[2] and "rate%3D5" not in put[2]  # 16 Mbps → 2 MB/s, sustituye al anterior


def test_no_method_saves_only():
    ctx, _, _ = make_ctx()
    r = asyncio.run(SpeedControl(ctx).apply(MAC_PHONE, 5, 1))
    assert r["ok"] and not r["applied"] and r["method"] is None
