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
        cols = {r[1] for r in self.ctx.db.conn.execute("PRAGMA table_info(captive_accepts)")}
        if "trusted" not in cols:  # «dispositivos registrados»: nunca se les vuelve a pedir
            self.ctx.db.execute("ALTER TABLE captive_accepts ADD COLUMN trusted INTEGER DEFAULT 0")
        self._arp_miss: dict[str, float] = {}

    def cfg(self) -> dict:
        return {**DEFAULTS, **(self.ctx.settings.get("captive") or {})}

    # ---------------------------------------------------------------- estado de aceptación
    def _mac(self, ip: str) -> str | None:
        """MAC del cliente. Si el escáner aún no lo conoce, se lee al momento la tabla ARP del sistema
        (un dispositivo nuevo tiene que reconocerse por su MAC desde la primera consulta)."""
        if ip.startswith("::ffff:"):
            ip = ip[7:]
        mac = self.ctx.devices.mac_for(ip)
        if mac:
            return mac
        now = time.time()
        if now - self._arp_miss.get(ip, 0) < 3:
            return None
        if ":" in ip:  # IPv6 (p. ej. modo automático): tabla de vecinos al momento
            from . import netinfo
            for n in netinfo.neighbors6():
                self.ctx.devices.ip6_to_mac[n["ip"]] = n["mac"]
            mac = self.ctx.devices.ip6_to_mac.get(ip)
            if not mac:
                self._arp_miss[ip] = now
            return mac
        try:
            with open("/proc/net/arp") as fh:
                next(fh)
                for line in fh:
                    f = line.split()
                    if len(f) >= 4 and f[0] == ip and f[3] != "00:00:00:00:00:00":
                        mac = f[3].lower()
                        self.ctx.devices.ip_to_mac[ip] = mac
                        return mac
        except OSError:
            pass
        self._arp_miss[ip] = now
        return None

    def _key(self, ip: str) -> str:
        return self._mac(ip) or f"ip:{ip}"

    def _row(self, key: str, ip: str):
        row = self.ctx.db.one("SELECT * FROM captive_accepts WHERE mac=?", (key,))
        if row is None and not key.startswith("ip:"):
            # aceptó antes de conocerse su MAC → se pasa el registro a la MAC
            row = self.ctx.db.one("SELECT * FROM captive_accepts WHERE mac=?", (f"ip:{ip}",))
            if row is not None:
                self.ctx.db.execute("UPDATE OR REPLACE captive_accepts SET mac=? WHERE mac=?", (key, f"ip:{ip}"))
        return row

    def accepted(self, ip: str) -> bool:
        if not self.enabled:  # ruta rápida: sin coste por consulta cuando está desactivado
            return True
        if ip.startswith("::ffff:"):
            ip = ip[7:]
        if ip.startswith("127.") or ip in ("::1", self.ctx.devices.self_ip):
            return True
        c = self.cfg()
        key = self._key(ip)
        now = time.time()
        hit = self._accepted_cache.get(key)
        if hit and now - hit < 30:
            return True
        # perfiles exentos (p. ej. servidores, IoT), el router y los servidores
        d = self.ctx.devices.devices.get(key)
        if d and ((d.get("profile") or "default") in c["exempt_profiles"] or d.get("kind") in ("router", "server")
                  or d.get("ip") == self.ctx.devices.gateway):
            self._accepted_cache[key] = now
            return True
        if ip == self.ctx.devices.gateway:
            return True
        row = self._row(key, ip)
        if row and (row["trusted"] or not c["expiry_days"] or now - row["ts"] < c["expiry_days"] * 86400):
            self._accepted_cache[key] = now
            return True
        return False

    def accept(self, ip: str, name: str = "", email: str = "", ua: str = "", trusted: bool = False) -> str:
        if ip.startswith("::ffff:"):
            ip = ip[7:]
        self._arp_miss.pop(ip, None)
        key = self._key(ip)
        self.ctx.db.execute("INSERT OR REPLACE INTO captive_accepts(mac,ip,ts,name,email,user_agent,trusted) VALUES (?,?,?,?,?,?,"
                            "COALESCE((SELECT trusted FROM captive_accepts WHERE mac=?), ?))",
                            (key, ip, time.time(), name[:80], email[:120], ua[:200], key, int(trusted)))
        if not key.startswith("ip:"):
            self.ctx.db.execute("DELETE FROM captive_accepts WHERE mac=?", (f"ip:{ip}",))
        self._accepted_cache[key] = time.time()
        self.ctx.db.event("info", "portal", f"Términos aceptados por {name or key} ({ip})")
        self._changed()
        return key

    def allow(self, mac: str, trusted: bool = False) -> None:
        """Aprobación manual desde el panel (sin pasar por la página)."""
        d = self.ctx.devices.devices.get(mac) or {}
        self.ctx.db.execute("INSERT OR REPLACE INTO captive_accepts(mac,ip,ts,name,trusted) VALUES (?,?,?,?,?)",
                            (mac, d.get("ip"), time.time(), "(aprobado por el administrador)", int(trusted)))
        self._accepted_cache[mac] = time.time()
        self._changed()

    def trust(self, mac: str, on: bool) -> None:
        if on and not self.ctx.db.one("SELECT 1 FROM captive_accepts WHERE mac=?", (mac,)):
            return self.allow(mac, trusted=True)
        self.ctx.db.execute("UPDATE captive_accepts SET trusted=? WHERE mac=?", (int(on), mac))

    def accept_existing(self) -> int:
        n = 0
        for mac, d in self.ctx.devices.devices.items():
            self.ctx.db.execute("INSERT OR IGNORE INTO captive_accepts(mac,ip,ts,name) VALUES (?,?,?,?)",
                                (mac, d.get("ip"), time.time(), "(dispositivo existente)"))
            n += 1
        return n

    def reset(self, mac: str | None = None) -> None:
        """Vuelve a pedir los términos. Los dispositivos registrados se conservan salvo que se elija uno concreto."""
        if mac:
            self.ctx.db.execute("DELETE FROM captive_accepts WHERE mac=?", (mac,))
        else:
            self.ctx.db.execute("DELETE FROM captive_accepts WHERE COALESCE(trusted,0)=0")
        self._accepted_cache.clear()
        self._changed(cut=mac or "")

    def pending_macs(self) -> list[str]:
        if not self.enabled:
            return []
        return [m for m, d in self.ctx.devices.devices.items()
                if not m.startswith("ip:") and d.get("ip") and not self.accepted(d["ip"])]

    def _changed(self, cut: str | None = None) -> None:
        """Aplica el cambio al momento: en modo router actualiza el firewall y corta las conexiones abiertas."""
        import asyncio
        router = getattr(self.ctx, "router", None)
        if router is None:
            return
        try:
            asyncio.get_running_loop().create_task(router.enforce_captive(cut))
        except RuntimeError:
            pass

    def portal_url(self) -> str:
        ip = self.ctx.devices.self_ip or "127.0.0.1"
        port = int(self.cfg()["port"])
        return f"http://{ip}/" if port == 80 else f"http://{ip}:{port}/"

    # ---------------------------------------------------------------- página
    def page(self, ip: str, done: bool = False, error: str = "", preview: bool = False, go: str = "") -> str:
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
        cont = "Continuar" if c["lang"] == "es" else "Continue"
        wait = "Conectando… si la página no carga, espera unos segundos." if c["lang"] == "es" else "Connecting… if the page doesn't load, wait a few seconds."
        refresh = f'<meta http-equiv="refresh" content="4;url={e(go)}">' if done and go else ""
        content = (f'<div class="ok">✓</div><h1 style="text-align:center">{e(c["success_text"])}</h1>'
                   + (f'<p class="foot">{e(wait)}</p><a class="btn" href="{e(go)}">{e(cont)}</a>' if go else "") if done else f"""
            {logo}<h1>{e(c['title'])}</h1><div class="terms">{body}</div>
            {f'<p class="err">{e(error)}</p>' if error else ''}
            <form method="post" action="/accept">{fields}
              <label class="chk"><input type="checkbox" name="agree" required> {e(c['checkbox_label'])}</label>
              <button {'disabled' if preview else ''}>{e(c['accept_label'])}</button></form>""")
        return f"""<!doctype html><html lang="{e(c['lang'])}"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{e(c['title'])}</title>{refresh}<style>
*{{box-sizing:border-box}}:root{{color-scheme:dark}}html{{background:#0b1020}}body{{margin:0;font:15px/1.55 system-ui,-apple-system,Segoe UI,Roboto,sans-serif;background:linear-gradient(160deg,#0b1020,#1e1b4b 60%,#0e7490);color:#e6eaf5;min-height:100vh;display:grid;place-items:center;padding:18px}}
.card{{width:min(560px,100%);background:rgba(255,255,255,.07);border:1px solid rgba(255,255,255,.12);border-radius:18px;padding:26px;backdrop-filter:blur(18px);-webkit-backdrop-filter:blur(18px);box-shadow:0 20px 60px rgba(0,0,0,.35)}}
h1{{font-size:21px;margin:0 0 12px}}.terms{{max-height:42vh;overflow:auto;background:rgba(0,0,0,.2);border-radius:12px;padding:4px 14px;font-size:14px;color:#cbd5e1}}
input:not([type=checkbox]){{width:100%;margin-top:10px;padding:11px 12px;border-radius:10px;border:1px solid rgba(255,255,255,.18);background:rgba(0,0,0,.25);color:#fff;font:inherit}}
.chk{{display:flex;gap:9px;align-items:flex-start;margin:14px 0;font-size:14px}}button{{width:100%;padding:12px;border:0;border-radius:11px;background:{color};color:#fff;font:600 15px system-ui;cursor:pointer}}
.ok{{width:64px;height:64px;border-radius:50%;background:#22c55e;display:grid;place-items:center;font-size:34px;margin:0 auto 12px}}.err{{color:#f87171}}
a.btn{{display:block;text-align:center;margin-top:14px;padding:12px;border-radius:11px;background:{color};color:#fff;text-decoration:none;font:600 15px system-ui}}
.foot{{margin-top:14px;font-size:11.5px;color:#94a3b8;text-align:center}}</style></head>
<body><div class="card">{content}<div class="foot">Bipon Net</div></div></body></html>"""

    # ---------------------------------------------------------------- servidor HTTP del portal
    def app(self) -> FastAPI:
        app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
        portal = self

        CHECK_HOSTS = ("captive.apple.com", "www.apple.com", "connectivitycheck.gstatic.com", "connectivitycheck.android.com",
                       "clients3.google.com", "www.gstatic.com", "www.msftconnecttest.com", "www.msftncsi.com", "detectportal.firefox.com",
                       "nmcheck.gnome.org", "network-test.debian.org", "connectivity-check.ubuntu.com")

        def check_ok(host: str, path: str):
            """Respuesta «hay Internet» a las comprobaciones de conectividad de cada sistema."""
            if "generate_204" in path or path.endswith("gen_204"):
                return Response(status_code=204)
            if host in ("captive.apple.com", "www.apple.com") or "hotspot-detect" in path or "library/test/success" in path:
                return HTMLResponse("<HTML><HEAD><TITLE>Success</TITLE></HEAD><BODY>Success</BODY></HTML>")
            if path.endswith("connecttest.txt"):
                return Response("Microsoft Connect Test", media_type="text/plain")
            if path.endswith("ncsi.txt"):
                return Response("Microsoft NCSI", media_type="text/plain")
            if "success.txt" in path:
                return Response("success\n", media_type="text/plain")
            if host in ("nmcheck.gnome.org", "network-test.debian.org", "connectivity-check.ubuntu.com"):
                return Response(status_code=204)
            return None

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
            return HTMLResponse(portal.page(ip, done=True, go="http://neverssl.com/"), headers={"cache-control": "no-store"})

        @app.api_route("/{path:path}", methods=["GET", "HEAD", "POST"])
        async def anything(request: Request, path: str):
            ip = request.client.host if request.client else ""
            host = (request.headers.get("host") or "").split(":")[0].lower()
            own = host in ("", portal.ctx.devices.self_ip or "", "bipon", "bipon.lan")
            nocache = {"cache-control": "no-store"}
            if portal.accepted(ip):
                ok = check_ok(host, path)
                if ok is not None:
                    return ok
                if own:
                    return HTMLResponse(portal.page(ip, done=True), headers=nocache)
                # el navegador aún tiene en caché la IP del portal para esta web: se reintenta solo en unos segundos
                return HTMLResponse(portal.page(ip, done=True, go=str(request.url)), headers=nocache)
            if not own:
                # cualquier web o comprobación de conectividad → al portal (así el sistema abre «Iniciar sesión en la red»)
                return RedirectResponse(portal.portal_url(), status_code=302, headers=nocache)
            return HTMLResponse(portal.page(ip), headers=nocache)
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
        devs = self.ctx.devices.devices
        names = {m: d.get("name") or d.get("hostname") or d.get("ip") for m, d in devs.items()}
        for r in rows:
            r["device"] = names.get(r["mac"]) or r["ip"]
            r["online"] = bool(devs.get(r["mac"], {}).get("online"))
            r["trusted"] = bool(r.get("trusted"))
        pending = [{"mac": m, "name": names.get(m), "ip": d.get("ip"), "brand": d.get("brand_name") or d.get("vendor")}
                   for m, d in devs.items() if d.get("online") and d.get("ip") and not self.accepted(d["ip"])] if self.cfg()["enabled"] else []
        auto = (self.ctx.settings.get("autodns") or {}).get("enabled") and not (self.ctx.settings.get("dhcp") or {}).get("enabled")
        router = getattr(self.ctx, "router", None)
        enforced = bool(router and router.cfg().get("enabled"))
        return {"config": self.cfg(), "accepts": [r for r in rows if not r["trusted"]], "registered": [r for r in rows if r["trusted"]],
                "pending": pending, "portal_url": self.portal_url(), "running": self.running, "error": self.error,
                "weak_dns": bool(auto), "firewall": enforced, "scanning": self.ctx.devices.scanning}
