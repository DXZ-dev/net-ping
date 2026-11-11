"""Calidad de conexión: Wi-Fi débil por equipo, mapa de lag por hora, ruta a la región del juego
e informe para reclamar al proveedor.

Wi-Fi débil
  Se hacen 6 pings a cada equipo en línea. Un móvil en ahorro de energía contesta tarde al
  primero aunque tenga buena señal, así que se juzga por el MÍNIMO y por la pérdida:
    min > 15 ms o pérdida ≥ 20 %  → señal débil (en una LAN sana el mínimo es < 5 ms)
    min > 6 ms o jitter > 25 ms   → regular
Mapa de lag
  Media de la latencia a Internet (tabla «monitor») por día de la semana y hora, últimos N días.
Ruta del juego
  Cada 3 h se mide la región del juego; si sube > 30 % y > 15 ms sobre su mediana de 7 días,
  el proveedor probablemente cambió la ruta → aviso.
"""
from __future__ import annotations

import asyncio
import html
import logging
import statistics
import time

log = logging.getLogger("bipon.quality")

WEAK_MIN_MS, FAIR_MIN_MS, WEAK_LOSS, FAIR_JITTER = 15.0, 6.0, 20.0, 25.0
DAYS = ["Domingo", "Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado"]


def classify_lan(r: dict) -> str:
    if not r or r.get("received", 0) == 0:
        return "sin respuesta"
    if (r.get("min") or 0) > WEAK_MIN_MS or (r.get("loss") or 0) >= WEAK_LOSS:
        return "débil"
    if (r.get("min") or 0) > FAIR_MIN_MS or (r.get("jitter") or 0) > FAIR_JITTER:
        return "regular"
    return "buena"


class Quality:
    def __init__(self, ctx):
        self.ctx = ctx
        self.lan: dict[str, dict] = {}
        self._warned: dict[str, float] = {}
        self.last_lan = 0.0
        self.last_route = 0.0
        ctx.db.execute("""CREATE TABLE IF NOT EXISTS game_routes (ts REAL NOT NULL, region TEXT NOT NULL, ms REAL)""")
        ctx.db.execute("CREATE INDEX IF NOT EXISTS idx_game_routes ON game_routes(region, ts)")

    def cfg(self) -> dict:
        return {"lan_check": True, "route_watch": True, "game_region": "", **(self.ctx.settings.get("quality") or {})}

    # ---------------------------------------------------------------- Wi-Fi / LAN por equipo
    async def lan_check(self) -> dict:
        from .pinger import ping
        devs = self.ctx.devices
        targets = [(m, d) for m, d in devs.devices.items()
                   if d.get("online") and d.get("ip") and ":" not in d["ip"] and not m.startswith("ip:")
                   and d["ip"] not in (devs.self_ip,)]
        sem = asyncio.Semaphore(16)

        async def one(mac, d):
            async with sem:
                try:
                    r = await ping(d["ip"], 6, 0.2, 1.0)
                except Exception:
                    return
            r = {k: r.get(k) for k in ("min", "avg", "max", "jitter", "loss", "received")}
            r["status"] = classify_lan(r)
            r["ts"] = time.time()
            r["ip"] = d["ip"]
            r["wifi"] = devs._wifi_like({**d, "mac": mac}) if hasattr(devs, "_wifi_like") else None
            self.lan[mac] = r
            if r["status"] == "débil" and d.get("ip") != devs.gateway and time.time() - self._warned.get(mac, 0) > 6 * 3600:
                self._warned[mac] = time.time()
                name = d.get("name") or d.get("friendly") or d.get("hostname") or d["ip"]
                msg = (f"{name}: {r['min']} ms mínimo hasta tu red ({r['loss']} % de pérdida). "
                       "Tiene mala señal Wi-Fi: acércalo al router, usa 5 GHz o conéctalo por cable.")
                self.ctx.db.event("warning", "wifi", msg)
                self.ctx.notify.emit("wifi_weak", "Señal Wi-Fi débil", msg, key=mac)

        await asyncio.gather(*(one(m, d) for m, d in targets))
        self.last_lan = time.time()
        return self.lan_status()

    def lan_status(self) -> dict:
        devs = self.ctx.devices.devices
        rows = []
        for mac, r in self.lan.items():
            d = devs.get(mac) or {}
            rows.append({"mac": mac, "name": d.get("name") or d.get("friendly") or d.get("hostname") or r.get("ip"),
                         "brand": d.get("brand_name") or d.get("vendor") or "", **r})
        order = {"débil": 0, "sin respuesta": 1, "regular": 2, "buena": 3}
        rows.sort(key=lambda x: (order.get(x["status"], 9), -(x.get("min") or 0)))
        counts = {k: sum(1 for r in rows if r["status"] == k) for k in order}
        return {"devices": rows, "counts": counts, "ts": self.last_lan}

    # ---------------------------------------------------------------- mapa de lag
    def heatmap(self, days: int = 14) -> dict:
        since = time.time() - days * 86400
        rows = self.ctx.db.query(
            """SELECT CAST(strftime('%w', ts, 'unixepoch', 'localtime') AS INT) dow,
                      CAST(strftime('%H', ts, 'unixepoch', 'localtime') AS INT) hour,
                      AVG(latency) ms, AVG(loss) loss, COUNT(*) n
               FROM monitor WHERE ts>=? AND target NOT IN ('gateway', 'dns') AND latency IS NOT NULL
               GROUP BY dow, hour""", (since,))
        grid = [[None] * 24 for _ in range(7)]
        for r in rows:
            grid[r["dow"]][r["hour"]] = {"ms": round(r["ms"], 1), "loss": round(r["loss"] or 0, 1), "n": r["n"]}
        vals = [c["ms"] for row in grid for c in row if c]
        worst = sorted(((c["ms"], d, h) for d, row in enumerate(grid) for h, c in enumerate(row) if c), reverse=True)[:5]
        return {"days": days, "grid": grid, "day_names": DAYS, "median": round(statistics.median(vals), 1) if vals else None,
                "worst": [{"day": DAYS[d], "hour": h, "ms": ms} for ms, d, h in worst]}

    # ---------------------------------------------------------------- ruta a la región del juego
    async def route_check(self) -> dict | None:
        from .gaming import GAME_REGIONS
        from .pinger import tcp_ping
        region = self.cfg().get("game_region") or ""
        if not region:  # la mejor región medida la última vez
            row = self.ctx.db.one("SELECT region FROM game_routes ORDER BY ts DESC, ms ASC LIMIT 1")
            region = row["region"] if row else ""
        regions = [region] if region else [c for c, _ in GAME_REGIONS]
        res = []
        for code in regions:
            try:
                r = await tcp_ping(f"dynamodb.{code}.amazonaws.com", 443, count=4, timeout=2.0)
            except Exception:
                continue
            if r.get("min") is not None:
                res.append((code, r["min"]))
                self.ctx.db.execute("INSERT INTO game_routes(ts,region,ms) VALUES (?,?,?)", (time.time(), code, r["min"]))
        self.last_route = time.time()
        if not res:
            return None
        code, ms = min(res, key=lambda x: x[1])
        hist = [r["ms"] for r in self.ctx.db.query("SELECT ms FROM game_routes WHERE region=? AND ts>=? AND ts<?",
                                                   (code, time.time() - 7 * 86400, time.time() - 60))]
        out = {"region": code, "ms": ms, "median": round(statistics.median(hist), 1) if len(hist) >= 4 else None}
        if out["median"] and ms > out["median"] * 1.3 and ms - out["median"] > 15:
            name = dict(GAME_REGIONS).get(code, code)
            msg = (f"El ping a {name} pasó de {out['median']:.0f} ms a {ms:.0f} ms. Tu proveedor probablemente cambió la ruta; "
                   "si dura, guarda el informe de calidad y reclámalo.")
            self.ctx.db.event("warning", "ruta", msg)
            self.ctx.notify.emit("route_change", "Cambio de ruta a tu juego", msg, key=code)
            out["alert"] = True
        return out

    def route_history(self, days: int = 7) -> dict:
        from .gaming import GAME_REGIONS
        rows = self.ctx.db.query("SELECT region, CAST(ts/3600 AS INT)*3600 t, MIN(ms) ms FROM game_routes WHERE ts>=? GROUP BY region, t ORDER BY t",
                                 (time.time() - days * 86400,))
        series: dict[str, list] = {}
        for r in rows:
            series.setdefault(r["region"], []).append([r["t"], r["ms"]])
        names = dict(GAME_REGIONS)
        return {"series": [{"region": k, "name": names.get(k, k), "points": v} for k, v in series.items()],
                "region": self.cfg().get("game_region"), "regions": [{"code": c, "name": n} for c, n in GAME_REGIONS]}

    # ---------------------------------------------------------------- informe para el proveedor
    def report(self, days: int = 30) -> dict:
        db = self.ctx.db
        since = time.time() - days * 86400
        daily = db.query("""SELECT date(ts, 'unixepoch', 'localtime') day, AVG(latency) ms, MAX(latency) peak, AVG(loss) loss, COUNT(*) n
                            FROM monitor WHERE ts>=? AND target NOT IN ('gateway', 'dns') GROUP BY day ORDER BY day""", (since,))
        outages = db.query("""SELECT ts, message FROM events WHERE ts>=? AND category='conexión' ORDER BY ts""", (since,))
        speeds = db.query("SELECT ts, download, upload, ping, bufferbloat, grade FROM speedtests WHERE ts>=? ORDER BY ts", (since,))
        down_secs = 0
        start = None
        for e in outages:
            if "perdió" in e["message"]:
                start = e["ts"]
            elif start and "restablecida" in e["message"]:
                down_secs += e["ts"] - start
                start = None
        lat = [d["ms"] for d in daily if d["ms"]]
        dl = [s["download"] for s in speeds if s.get("download")]
        return {
            "days": days, "generated": time.time(), "daily": daily, "outages": outages, "speedtests": speeds,
            "summary": {"outages": sum(1 for e in outages if "perdió" in e["message"]), "downtime_min": round(down_secs / 60),
                        "avg_latency": round(statistics.fmean(lat), 1) if lat else None,
                        "worst_day_latency": max(lat) if lat else None,
                        "avg_loss": round(statistics.fmean([d["loss"] or 0 for d in daily]), 2) if daily else None,
                        "avg_download": round(statistics.fmean(dl), 1) if dl else None, "min_download": round(min(dl), 1) if dl else None},
        }

    def report_csv(self, days: int = 30) -> str:
        import csv
        import io
        r = self.report(days)
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["# Informe de calidad de conexión — Bipon Net", f"últimos {days} días"])
        w.writerow(["día", "latencia media (ms)", "latencia máxima (ms)", "pérdida media (%)", "muestras"])
        for d in r["daily"]:
            w.writerow([d["day"], round(d["ms"] or 0, 1), round(d["peak"] or 0, 1), round(d["loss"] or 0, 2), d["n"]])
        w.writerow([])
        w.writerow(["fecha", "evento"])
        for e in r["outages"]:
            w.writerow([time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(e["ts"])), e["message"]])
        w.writerow([])
        w.writerow(["fecha", "bajada (Mbps)", "subida (Mbps)", "ping (ms)", "bufferbloat (ms)", "nota"])
        for s in r["speedtests"]:
            w.writerow([time.strftime("%Y-%m-%d %H:%M", time.localtime(s["ts"])), s.get("download"), s.get("upload"), s.get("ping"),
                        s.get("bufferbloat"), s.get("grade")])
        return buf.getvalue()

    def report_html(self, days: int = 30, contracted: float | None = None) -> str:
        r, e = self.report(days), html.escape
        s = r["summary"]
        f = lambda v, u="": "—" if v is None else f"{v}{u}"  # noqa: E731
        rows = "".join(f"<tr><td>{e(d['day'])}</td><td>{d['ms'] or 0:.1f}</td><td>{d['peak'] or 0:.1f}</td><td>{d['loss'] or 0:.2f}</td></tr>" for d in r["daily"])
        evs = "".join(f"<tr><td>{time.strftime('%Y-%m-%d %H:%M', time.localtime(x['ts']))}</td><td>{e(x['message'])}</td></tr>" for x in r["outages"]) or "<tr><td colspan=2>Sin cortes registrados</td></tr>"
        sp = "".join(f"<tr><td>{time.strftime('%Y-%m-%d %H:%M', time.localtime(x['ts']))}</td><td>{f(x.get('download'))}</td><td>{f(x.get('upload'))}</td><td>{f(x.get('ping'))}</td><td>{e(str(x.get('grade') or ''))}</td></tr>" for x in r["speedtests"]) or "<tr><td colspan=5>Sin tests</td></tr>"
        below = ""
        if contracted and s["avg_download"]:
            pct = s["avg_download"] / contracted * 100
            below = f"<p><b>Velocidad media de bajada: {pct:.0f} % de la contratada ({contracted} Mbps).</b></p>"
        return f"""<!doctype html><html lang="es"><head><meta charset="utf-8"><title>Informe de calidad de conexión</title>
<style>body{{font:13px/1.45 system-ui,sans-serif;color:#111;margin:28px;max-width:900px}}h1{{font-size:20px}}h2{{font-size:15px;margin-top:22px}}
table{{border-collapse:collapse;width:100%;margin-top:6px}}td,th{{border:1px solid #ccc;padding:4px 6px;text-align:left}}th{{background:#f2f2f2}}
.k{{display:grid;grid-template-columns:repeat(3,1fr);gap:8px}}.k div{{border:1px solid #ddd;border-radius:8px;padding:8px}}.k b{{font-size:18px;display:block}}
@media print{{button{{display:none}}}}</style></head><body>
<button onclick="print()">Imprimir / guardar como PDF</button>
<h1>Informe de calidad de conexión</h1>
<p>Últimos {days} días · generado el {time.strftime('%Y-%m-%d %H:%M')} por Bipon Net (medición automática desde la red del cliente).</p>
<div class="k"><div><b>{s['outages']}</b>cortes de Internet</div><div><b>{s['downtime_min']} min</b>sin servicio</div><div><b>{f(s['avg_latency'], ' ms')}</b>latencia media a Internet</div>
<div><b>{f(s['avg_loss'], ' %')}</b>pérdida media</div><div><b>{f(s['avg_download'], ' Mbps')}</b>bajada media</div><div><b>{f(s['min_download'], ' Mbps')}</b>bajada mínima</div></div>
{below}
<h2>Latencia y pérdida por día</h2><table><tr><th>Día</th><th>Latencia media (ms)</th><th>Máxima (ms)</th><th>Pérdida (%)</th></tr>{rows}</table>
<h2>Cortes registrados</h2><table><tr><th>Fecha</th><th>Evento</th></tr>{evs}</table>
<h2>Tests de velocidad</h2><table><tr><th>Fecha</th><th>Bajada</th><th>Subida</th><th>Ping</th><th>Nota</th></tr>{sp}</table>
</body></html>"""

    # ---------------------------------------------------------------- bucle
    async def loop(self) -> None:
        await asyncio.sleep(120)
        while True:
            c = self.cfg()
            try:
                if c["lan_check"] and time.time() - self.last_lan > 600:
                    await self.lan_check()
                if c["route_watch"] and time.time() - self.last_route > 3 * 3600:
                    await self.route_check()
            except Exception as exc:  # pragma: no cover
                log.warning("calidad: %s", exc)
            await asyncio.sleep(60)
