"""Telegram con botones: el aviso lleva Permitir/Bloquear y las pulsaciones se aplican (solo desde el chat configurado)."""
import asyncio
import json

import httpx

from bipon.notify import TelegramBot, telegram_buttons

MAC = "aa:bb:cc:00:00:07"


class Obj:
    def __init__(self, **kw):
        self.__dict__.update(kw)


def test_buttons_only_for_device_events():
    kb = telegram_buttons("device_new", {"mac": MAC})
    assert [b["callback_data"] for b in kb[0]] == [f"allow:{MAC}", f"block:{MAC}"]
    assert telegram_buttons("quarantine", {"mac": MAC})[0][0]["callback_data"] == f"release:{MAC}"
    assert telegram_buttons("internet_down", {}) is None


def make():
    devices = {MAC: {"ip": "192.168.18.40", "name": "Tablet nueva"}}
    updates, applied, sent, events = [], [], [], []

    def update(mac, patch):
        updates.append((mac, patch))
        devices[mac].update(patch)
    channel = {"type": "telegram", "enabled": True, "config": {"bot_token": "123:ABC", "chat_id": "555"}}
    ctx = Obj(devices=Obj(devices=devices, update=update), apply_policy_now=lambda m: applied.append(m),
              db=Obj(event=lambda *a: events.append(a)), notify=Obj(cfg=lambda: {"channels": [channel]}), captive=None, guard=None)
    pending = [
        {"update_id": 10, "callback_query": {"id": "q1", "data": f"block:{MAC}", "from": {"first_name": "Darlin"},
                                             "message": {"message_id": 99, "chat": {"id": 555}}}},
        {"update_id": 11, "callback_query": {"id": "q2", "data": f"allow:{MAC}", "from": {"first_name": "Intruso"},
                                             "message": {"message_id": 98, "chat": {"id": 777}}}},  # otro chat → ignorado
    ]

    def handler(req: httpx.Request):
        sent.append((req.url.path.rsplit("/", 1)[-1], json.loads(req.content) if req.content else dict(req.url.params)))
        if req.url.path.endswith("/getUpdates"):
            res, pending[:] = list(pending), []
            return httpx.Response(200, json={"ok": True, "result": res})
        return httpx.Response(200, json={"ok": True, "result": True})
    return TelegramBot(ctx), channel, updates, applied, sent, events, httpx.MockTransport(handler)


def test_block_from_button_and_ignore_other_chats():
    bot, ch, updates, applied, sent, events, transport = make()

    async def run():
        async with httpx.AsyncClient(transport=transport) as h:
            return await bot.poll_once(ch, h, timeout=0)
    n = asyncio.run(run())
    assert n == 1                                            # solo la del chat configurado
    assert updates == [(MAC, {"paused": 1, "paused_until": 0})] and applied == [[MAC]]
    assert bot.offsets["123:ABC"] == 12                      # no se vuelven a procesar
    methods = [m for m, _ in sent]
    assert "answerCallbackQuery" in methods and "editMessageReplyMarkup" in methods
    assert any("bloqueado" in str(body.get("text", "")) for m, body in sent if m == "sendMessage")
