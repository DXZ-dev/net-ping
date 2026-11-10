"""Portal cautivo: página de términos y condiciones al conectarse por primera vez.

Cómo funciona (sin hardware adicional):
  1. Mientras un dispositivo no haya aceptado, el DNS de Bipon responde a sus
     consultas con la IP de Bipon (jardín vallado).
  2. Los sistemas operativos comprueban la conexión con URL HTTP conocidas
     (captive.apple.com, connectivitycheck.gstatic.com, msftconnecttest.com…);
     al recibir la página de Bipon muestran «Iniciar sesión en la red».
  3. El usuario acepta → se registra su MAC → el DNS vuelve a la normalidad.
Si Bipon es el servidor DHCP, además se envía la opción 114 (RFC 8910).
Requiere que los dispositivos usen Bipon como DNS (DNS del router o DHCP de Bipon).
"""
from __future__ import annotations

import html
import logging
import re
import time

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

log = logging.getLogger("bipon.captive")

DEFAULTS = {
    "enabled": False, "port": 80, "title": "Bienvenido a nuestra red Wi-Fi",
    "body": "Al conectarte aceptas usar esta red de forma responsable:\n\n• No realizar actividades ilegales.\n"
            "• No descargar ni compartir contenido protegido sin permiso.\n• El tráfico puede filtrarse para proteger a todos los usuarios.\n\n"
            "El administrador puede registrar las webs visitadas (sólo nombres de dominio) por motivos de seguridad.",
    "accept_label": "Acepto los términos", "checkbox_label": "He leído y acepto los términos y condiciones",
    "ask_name": False, "ask_email": False, "logo_url": "", "color": "#3b82f6", "expiry_days": 0,
    "exempt_profiles": [], "success_text": "¡Listo! Ya puedes navegar.", "lang": "es",
}


class CaptivePortal:
    def __init__(self, ctx):
        self.ctx = ctx
        self._accepted_cache: dict[str, float] = {}
        self.error = ""
        self.running = False
        self.enabled = bool((ctx.settings.get("captive") or {}).get("enabled"))
        self.ctx.db.execute("""CREATE TABLE IF NOT EXISTS captive_accepts (
            mac TEXT PRIMARY KEY, ip TEXT, ts REAL NOT NULL, name TEXT, email TEXT, user_agent TEXT)""")

    def cfg(self) -> dict:
        return {**DEFAULTS, **(self.ctx.settings.get("captive") or {})}

    # ---------------------------------------------------------------- estado de aceptación
    def _key(self, ip: str) -> str:
        return self.ctx.devices.mac_for(ip) or f"ip:{ip}"

    def accepted(self, ip: str) -> bool:
        if not self.enabled:  # ruta rápida: sin coste por consulta cuando está desactivado
            return True
        c = self.cfg()
        if ip.startswith("127.") or ip in ("::1", self.ctx.devices.self_ip):
            return True
        key = self._key(ip)
        now = time.time()
        hit = self._accepted_cache.get(key)
        if hit and now - hit < 60:
            return True
        # perfiles exentos (p. ej. servidores, IoT)
        d = self.ctx.devices.devices.get(key)
        if d and ((d.get("profile") or "default") in c["exempt_profiles"] or d.get("kind") in ("router", "server")
                  or d.get("ip") == self.ctx.devices.gateway):
            self._accepted_cache[key] = now
            return True
        row = self.ctx.db.one("SELECT ts FROM captive_accepts WHERE mac=?", (key,))
        if row and (not c["expiry_days"] or now - row["ts"] < c["expiry_days"] * 86400):
            self._accepted_cache[key] = now
            return True
        return False

    def accept(self, ip: str, name: str = "", email: str = "", ua: str = "") -> None:
        key = self._key(ip)
        self.ctx.db.execute("INSERT OR REPLACE INTO captive_accepts(mac,ip,ts,name,email,user_agent) VALUES (?,?,?,?,?,?)",
                            (key, ip, time.time(), name[:80], email[:120], ua[:200]))
        self._accepted_cache[key] = time.time()
        self.ctx.db.event("info", "portal", f"Términos aceptados por {name or key} ({ip})")

    def accept_existing(self) -> int:
        n = 0
        for mac, d in self.ctx.devices.devices.items():
            self.ctx.db.execute("INSERT OR IGNORE INTO captive_accepts(mac,ip,ts,name) VALUES (?,?,?,?)",
                                (mac, d.get("ip"), time.time(), "(dispositivo existente)"))
            n += 1
        return n

    def reset(self, mac: str | None = None) -> None:
        if mac:
            self.ctx.db.execute("DELETE FROM captive_accepts WHERE mac=?", (mac,))
        else:
            self.ctx.db.execute("DELETE FROM captive_accepts")
        self._accepted_cache.clear()

    def portal_url(self) -> str:
        ip = self.ctx.devices.self_ip or "127.0.0.1"
        port = int(self.cfg()["port"])
        return f"http://{ip}/" if port == 80 else f"http://{ip}:{port}/"

    # ---------------------------------------------------------------- página
    def page(self, ip: str, done: bool = False, error: str = "", preview: bool = False) -> str:
        c = self.cfg()
        e = html.escape
        body = "".join(f"<p>{e(p)}</p>" for p in c["body"].split("\n\n"))
        body = body.replace("\n", "<br>")
        color = c["color"] if re.match(r"^#[0-9a-fA-F]{6}$", c["color"] or "") else "#3b82f6"
        logo = f'<img src="{e(c["logo_url"])}" alt="" style="max-height:56px;max-width:200px;margin-bottom:10px">' if c["logo_url"].startswith(("https://", "data:image/")) else ""
        fields = ""
        if c["ask_name"]:
            fields += '<input name="name" placeholder="Nombre" required>' if c["lang"] == "es" else '<input name="name" placeholder="Name" required>'
        if c["ask_email"]:
            fields += '<input name="email" type="email" placeholder="Email" required>'
        content = (f'<div class="ok">✓</div><h1>{e(c["success_text"])}</h1>' if done else f"""
            {logo}<h1>{e(c['title'])}</h1><div class="terms">{body}</div>
            {f'<p class="err">{e(error)}</p>' if error else ''}
            <form method="post" action="/accept">{fields}
              <label class="chk"><input type="checkbox" name="agree" required> {e(c['checkbox_label'])}</label>
              <button {'disabled' if preview else ''}>{e(c['accept_label'])}</button></form>""")
        return f"""<!doctype html><html lang="{e(c['lang'])}"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{e(c['title'])}</title><style>
*{{box-sizing:border-box}}body{{margin:0;font:15px/1.55 system-ui,-apple-system,Segoe UI,Roboto,sans-serif;background:linear-gradient(160deg,#0b1020,#1e1b4b 60%,#0e7490);color:#e6eaf5;min-height:100vh;display:grid;place-items:center;padding:18px}}
.card{{width:min(560px,100%);background:rgba(255,255,255,.07);border:1px solid rgba(255,255,255,.12);border-radius:18px;padding:26px;backdrop-filter:blur(18px);-webkit-backdrop-filter:blur(18px);box-shadow:0 20px 60px rgba(0,0,0,.35)}}
h1{{font-size:21px;margin:0 0 12px}}.terms{{max-height:42vh;overflow:auto;background:rgba(0,0,0,.2);border-radius:12px;padding:4px 14px;font-size:14px;color:#cbd5e1}}
input:not([type=checkbox]){{width:100%;margin-top:10px;padding:11px 12px;border-radius:10px;border:1px solid rgba(255,255,255,.18);background:rgba(0,0,0,.25);color:#fff;font:inherit}}
.chk{{display:flex;gap:9px;align-items:flex-start;margin:14px 0;font-size:14px}}button{{width:100%;padding:12px;border:0;border-radius:11px;background:{color};color:#fff;font:600 15px system-ui;cursor:pointer}}
.ok{{width:64px;height:64px;border-radius:50%;background:#22c55e;display:grid;place-items:center;font-size:34px;margin:0 auto 12px}}.err{{color:#f87171}}
.foot{{margin-top:14px;font-size:11.5px;color:#94a3b8;text-align:center}}</style></head>
<body><div class="card">{content}<div class="foot">Bipon Net</div></div></body></html>"""

    # ---------------------------------------------------------------- servidor HTTP del portal
    def app(self) -> FastAPI:
        app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
        portal = self

        @app.post("/accept")
        async def accept(request: Request):
            form = await request.form()
            ip = request.client.host if request.client else ""
            c = portal.cfg()
            if not form.get("agree"):
                return HTMLResponse(portal.page(ip, error="Debes aceptar los términos."), status_code=400)
            if c["ask_email"] and not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", form.get("email", "")):
                return HTMLResponse(portal.page(ip, error="Email no válido."), status_code=400)
            portal.accept(ip, form.get("name", ""), form.get("email", ""), request.headers.get("user-agent", ""))
            return HTMLResponse(portal.page(ip, done=True))

        @app.api_route("/{path:path}", methods=["GET", "HEAD", "POST"])
        async def anything(request: Request, path: str):
            ip = request.client.host if request.client else ""
            host = (request.headers.get("host") or "").split(":")[0]
            if portal.accepted(ip):
                # ya aceptado: respuestas «correctas» a las comprobaciones de conectividad
                if "generate_204" in path or path.endswith("gen_204"):
                    return Response(status_code=204)
                if host in ("captive.apple.com", "www.apple.com") or "hotspot-detect" in path:
                    return HTMLResponse("<HTML><HEAD><TITLE>Success</TITLE></HEAD><BODY>Success</BODY></HTML>")
                if "connecttest" in path:
                    return Response("Microsoft Connect Test", media_type="text/plain")
                if "ncsi" in path:
                    return Response("Microsoft NCSI", media_type="text/plain")
                return HTMLResponse(portal.page(ip, done=True))
            if host and host not in (portal.ctx.devices.self_ip or "",) and not path:
                return RedirectResponse(portal.portal_url(), status_code=302)
            if path and "accept" not in path and host and host != (portal.ctx.devices.self_ip or ""):
                return RedirectResponse(portal.portal_url(), status_code=302)
            return HTMLResponse(portal.page(ip), headers={"cache-control": "no-store"})
        return app

    async def loop(self) -> None:
        """Arranca/detiene el servidor del portal según la configuración."""
        import asyncio
        import contextlib
        import uvicorn
        server = None
        task = None
        while True:
            c = self.cfg()
            want = self.enabled = bool(c["enabled"])
            if want and server is None:
                try:
                    cfg = uvicorn.Config(self.app(), host="0.0.0.0", port=int(c["port"]), log_level="warning", lifespan="off")
                    server = uvicorn.Server(cfg)
                    server.capture_signals = contextlib.nullcontext  # las señales las gestiona el servidor principal
                    task = asyncio.create_task(server.serve())
                    await asyncio.sleep(1.5)
                    if task.done():
                        raise RuntimeError(f"no se pudo abrir el puerto {c['port']} (¿otro servidor web lo usa?)")
                    self.running, self.error = True, ""
                    log.info("portal cautivo activo en el puerto %s", c["port"])
                except Exception as exc:
                    self.error = str(exc)
                    self.running = False
                    server = None
                    log.warning("portal cautivo: %s", exc)
            elif not want and server is not None:
                server.should_exit = True
                with contextlib.suppress(Exception):
                    await asyncio.wait_for(task, 5)
                server, task, self.running = None, None, False
            await asyncio.sleep(5)

    def status(self) -> dict:
        rows = self.ctx.db.query("SELECT * FROM captive_accepts ORDER BY ts DESC LIMIT 500")
        names = {m: d.get("name") or d.get("hostname") or d.get("ip") for m, d in self.ctx.devices.devices.items()}
        for r in rows:
            r["device"] = names.get(r["mac"]) or r["ip"]
        pending = [{"mac": m, "name": names.get(m), "ip": d.get("ip")} for m, d in self.ctx.devices.devices.items()
                   if d.get("online") and d.get("ip") and not self.accepted(d["ip"])] if self.cfg()["enabled"] else []
        return {"config": self.cfg(), "accepts": rows, "pending": pending, "portal_url": self.portal_url(), "running": self.running, "error": self.error}
