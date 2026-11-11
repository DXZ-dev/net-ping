"""Notificaciones: Telegram, Discord, Slack, ntfy, Gotify, email (SMTP),
webhooks firmados y Web Push del navegador.

Uso:  ctx.notify.emit("device_new", "Nuevo dispositivo", "…", data={…}, key="mac")
Cada evento puede activarse/desactivarse y dirigirse a canales concretos.
Incluye antispam (un mismo evento+clave no se repite antes de su cooldown) y
horas de silencio."""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import html
import json
import logging
import smtplib
import ssl
import time
from datetime import datetime
from email.message import EmailMessage

import httpx

from . import webpush

log = logging.getLogger("bipon.notify")

EVENTS: dict[str, dict] = {
    "device_new": {"label": "Nuevo dispositivo conectado", "cooldown": 0, "default": True, "priority": "default", "icon": "📱"},
    "internet_down": {"label": "Caída de Internet", "cooldown": 300, "default": True, "priority": "high", "icon": "🔴"},
    "internet_up": {"label": "Internet restablecido", "cooldown": 0, "default": True, "priority": "default", "icon": "🟢"},
    "lag_spike": {"label": "Subida de ping (lag) y su posible causa", "cooldown": 600, "default": True, "priority": "high", "icon": "🎮"},
    "wifi_weak": {"label": "Equipo con señal Wi-Fi débil", "cooldown": 21600, "default": True, "priority": "default", "icon": "📶"},
    "route_change": {"label": "Cambio de ruta a tu juego (más ping)", "cooldown": 10800, "default": True, "priority": "default", "icon": "🛣️"},
    "quarantine": {"label": "Dispositivo puesto en cuarentena (malware)", "cooldown": 0, "default": True, "priority": "high", "icon": "☣️"},
    "speed_low": {"label": "Velocidad por debajo del umbral", "cooldown": 3600, "default": True, "priority": "default", "icon": "🐢"},
    "threat_blocked": {"label": "Amenaza bloqueada (malware/phishing)", "cooldown": 3600, "default": True, "priority": "high", "icon": "🛡️"},
    "anomaly": {"label": "Comportamiento anómalo de un dispositivo", "cooldown": 3600, "default": True, "priority": "high", "icon": "⚠️"},
    "screentime_limit": {"label": "Límite de tiempo de pantalla alcanzado", "cooldown": 0, "default": True, "priority": "default", "icon": "⏰"},
    "dns_down": {"label": "DNS o Unbound caído", "cooldown": 600, "default": True, "priority": "high", "icon": "🚨"},
    "lists_failed": {"label": "Error al actualizar listas", "cooldown": 86400, "default": False, "priority": "low", "icon": "📋"},
    "login_failed": {"label": "Intentos de acceso fallidos al panel", "cooldown": 1800, "default": True, "priority": "high", "icon": "🔐"},
    "dhcp_conflict": {"label": "Otro servidor DHCP detectado", "cooldown": 3600, "default": True, "priority": "high", "icon": "⚡"},
    "report": {"label": "Informe semanal", "cooldown": 0, "default": True, "priority": "low", "icon": "📊"},
    "sync": {"label": "Fallo de sincronización con el nodo secundario", "cooldown": 3600, "default": False, "priority": "default", "icon": "🔁"},
}

CHANNEL_TYPES = {
    "telegram": {"label": "Telegram", "fields": ["bot_token", "chat_id"]},
    "discord": {"label": "Discord", "fields": ["webhook_url"]},
    "slack": {"label": "Slack", "fields": ["webhook_url"]},
    "ntfy": {"label": "ntfy (app móvil)", "fields": ["server", "topic", "token"]},
    "gotify": {"label": "Gotify", "fields": ["url", "token"]},
    "email": {"label": "Email (SMTP)", "fields": ["host", "port", "security", "username", "password", "sender", "to"]},
    "webhook": {"label": "Webhook (JSON)", "fields": ["url", "secret"]},
}
SECRET_FIELDS = {"bot_token", "token", "password", "secret", "webhook_url"}


class Notifier:
    def __init__(self, ctx):
        self.ctx = ctx
        self._last: dict[str, float] = {}
        self._http: httpx.AsyncClient | None = None
        self.vapid = webpush.VapidKeys()
        self._tasks: set = set()

    # ---------------------------------------------------------------- config
    def cfg(self) -> dict:
        c = self.ctx.settings.get("notify") or {}
        c.setdefault("channels", [])
        c.setdefault("events", {})
        c.setdefault("quiet", {"enabled": False, "start": "23:00", "end": "07:00", "allow_high": True})
        c.setdefault("webpush", True)
        return c

    def public_cfg(self) -> dict:
        c = self.cfg()
        chans = []
        for ch in c["channels"]:
            conf = {k: ("••••••" if k in SECRET_FIELDS and v else v) for k, v in (ch.get("config") or {}).items()}
            chans.append({**ch, "config": conf})
        return {**c, "channels": chans, "event_catalog": EVENTS, "channel_types": CHANNEL_TYPES,
                "vapid_public_key": self.vapid.public_key,
                "push_subscriptions": self.ctx.db.scalar("SELECT COUNT(*) FROM push_subs") or 0}

    def save_channel(self, ch: dict) -> dict:
        c = self.cfg()
        if ch.get("type") not in CHANNEL_TYPES:
            raise ValueError("Tipo de canal desconocido")
        existing = next((x for x in c["channels"] if x["id"] == ch.get("id")), None)
        conf = {k: str(v).strip() for k, v in (ch.get("config") or {}).items() if k in CHANNEL_TYPES[ch["type"]]["fields"]}
        if existing:
            # los secretos enmascarados no se sobrescriben
            for k, v in list(conf.items()):
                if v == "••••••":
                    conf[k] = existing["config"].get(k, "")
            existing.update(name=ch.get("name") or existing["name"], enabled=bool(ch.get("enabled", True)), config=conf)
            out = existing
        else:
            out = {"id": hashlib.sha1(f"{time.time()}{ch['type']}".encode()).hexdigest()[:8], "type": ch["type"],
                   "name": ch.get("name") or CHANNEL_TYPES[ch["type"]]["label"], "enabled": bool(ch.get("enabled", True)), "config": conf}
            c["channels"].append(out)
        self.ctx.settings.set("notify", c)
        return out

    def delete_channel(self, cid: str) -> None:
        c = self.cfg()
        c["channels"] = [x for x in c["channels"] if x["id"] != cid]
        self.ctx.settings.set("notify", c)

    def event_enabled(self, event: str) -> tuple[bool, list | None]:
        c = self.cfg()
        e = c["events"].get(event)
        if e is None:
            return EVENTS.get(event, {}).get("default", True), None
        if isinstance(e, bool):
            return e, None
        return bool(e.get("enabled", True)), e.get("channels")

    def _quiet_now(self) -> bool:
        q = self.cfg()["quiet"]
        if not q.get("enabled"):
            return False
        from .devices import schedule_active
        return schedule_active({"start": q.get("start", "23:00"), "end": q.get("end", "07:00")}, datetime.now())

    # ---------------------------------------------------------------- emisión
    def emit(self, event: str, title: str, message: str, data: dict | None = None, key: str = "", force: bool = False) -> None:
        enabled, only = self.event_enabled(event)
        if not enabled and not force:
            return
        meta = EVENTS.get(event, {})
        dedupe = f"{event}:{key}"
        now = time.time()
        if not force and now - self._last.get(dedupe, 0) < meta.get("cooldown", 0):
            return
        self._last[dedupe] = now
        if not force and self._quiet_now() and not (meta.get("priority") == "high" and self.cfg()["quiet"].get("allow_high", True)):
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        t = loop.create_task(self._dispatch(event, title, message, data or {}, only))
        self._tasks.add(t)
        t.add_done_callback(self._tasks.discard)

    async def _dispatch(self, event, title, message, data, only):
        meta = EVENTS.get(event, {})
        c = self.cfg()
        targets = [ch for ch in c["channels"] if ch.get("enabled", True) and (only is None or ch["id"] in only)]
        results = await asyncio.gather(*(self._send(ch, event, title, message, data, meta) for ch in targets), return_exceptions=True)
        for ch, r in zip(targets, results):
            ok = r is True
            err = "" if ok else (str(r) if isinstance(r, Exception) else str(r))[:300]
            self.ctx.db.execute("INSERT INTO notify_log(ts,event,channel,title,ok,error) VALUES (?,?,?,?,?,?)",
                                (time.time(), event, ch["name"], title, 1 if ok else 0, err))
            if not ok:
                log.warning("notificación %s por %s falló: %s", event, ch["name"], err)
        if c.get("webpush", True) and (only is None or "webpush" in only):
            await self._webpush(title, message, event, meta)

    def http(self) -> httpx.AsyncClient:
        if self._http is None:
            self._http = httpx.AsyncClient(timeout=15, headers={"user-agent": "Bipon Net"})
        return self._http

    async def _send(self, ch, event, title, message, data, meta) -> bool | str:
        cfg = ch.get("config") or {}
        t = ch["type"]
        icon = meta.get("icon", "")
        h = self.http()
        if t == "telegram":
            text = f"{icon} <b>{html.escape(title)}</b>\n{html.escape(message)}"
            r = await h.post(f"https://api.telegram.org/bot{cfg['bot_token']}/sendMessage",
                             json={"chat_id": cfg["chat_id"], "text": text, "parse_mode": "HTML", "disable_web_page_preview": True})
        elif t == "discord":
            color = {"high": 0xEF4444, "default": 0x3B82F6, "low": 0x64748B}.get(meta.get("priority"), 0x3B82F6)
            r = await h.post(cfg["webhook_url"], json={"username": "Bipon Net", "embeds": [{"title": f"{icon} {title}", "description": message, "color": color,
                                                                                            "footer": {"text": "Bipon Net"}, "timestamp": datetime.utcnow().isoformat()}]})
        elif t == "slack":
            r = await h.post(cfg["webhook_url"], json={"text": f"{icon} *{title}*\n{message}"})
        elif t == "ntfy":
            server = (cfg.get("server") or "https://ntfy.sh").rstrip("/")
            headers = {"Title": title.encode("utf-8").decode("latin-1", "ignore"), "Tags": event,
                       "Priority": {"high": "high", "low": "low"}.get(meta.get("priority"), "default")}
            if cfg.get("token"):
                headers["Authorization"] = f"Bearer {cfg['token']}"
            r = await h.post(f"{server}/{cfg['topic']}", content=f"{icon} {message}".encode(), headers=headers)
        elif t == "gotify":
            r = await h.post(f"{cfg['url'].rstrip('/')}/message", params={"token": cfg["token"]},
                             json={"title": f"{icon} {title}", "message": message, "priority": {"high": 8, "low": 2}.get(meta.get("priority"), 5)})
        elif t == "webhook":
            body = json.dumps({"event": event, "title": title, "message": message, "data": data, "ts": time.time()}, ensure_ascii=False).encode()
            headers = {"content-type": "application/json"}
            if cfg.get("secret"):
                headers["X-Bipon-Signature"] = "sha256=" + hmac.new(cfg["secret"].encode(), body, hashlib.sha256).hexdigest()
            r = await h.post(cfg["url"], content=body, headers=headers)
        elif t == "email":
            await asyncio.to_thread(self._smtp, cfg, f"{icon} Bipon Net: {title}", message, data)
            return True
        else:
            return f"tipo desconocido {t}"
        return True if r.status_code < 300 else f"HTTP {r.status_code}: {r.text[:150]}"

    @staticmethod
    def _smtp(cfg, subject, message, data):
        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = cfg.get("sender") or cfg.get("username")
        msg["To"] = cfg["to"]
        msg.set_content(message)
        if data.get("html"):
            msg.add_alternative(data["html"], subtype="html")
        port = int(cfg.get("port") or 587)
        sec = cfg.get("security", "starttls")
        ctx = ssl.create_default_context()
        if sec == "ssl":
            s = smtplib.SMTP_SSL(cfg["host"], port, context=ctx, timeout=20)
        else:
            s = smtplib.SMTP(cfg["host"], port, timeout=20)
            if sec == "starttls":
                s.starttls(context=ctx)
        try:
            if cfg.get("username"):
                s.login(cfg["username"], cfg.get("password", ""))
            s.send_message(msg)
        finally:
            s.quit()

    async def _webpush(self, title, message, event, meta):
        subs = self.ctx.db.query("SELECT id, data FROM push_subs")
        for row in subs:
            try:
                code = await webpush.send(json.loads(row["data"]), {"title": f"{meta.get('icon', '')} {title}", "body": message, "tag": event},
                                          self.vapid, self.http())
                if code in (404, 410):  # suscripción caducada
                    self.ctx.db.execute("DELETE FROM push_subs WHERE id=?", (row["id"],))
            except Exception as exc:
                log.debug("web push falló: %s", exc)

    async def test(self, cid: str) -> dict:
        ch = next((x for x in self.cfg()["channels"] if x["id"] == cid), None)
        if not ch:
            raise KeyError("canal no encontrado")
        try:
            r = await self._send(ch, "test", "Prueba de Bipon Net", "Si lees esto, las notificaciones funcionan ✅", {}, {"icon": "🔔"})
        except Exception as exc:
            r = f"{type(exc).__name__}: {exc}"
        return {"ok": r is True, "error": None if r is True else str(r)}

    async def close(self):
        if self._http:
            await self._http.aclose()
