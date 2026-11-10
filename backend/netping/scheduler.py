"""Tareas programadas: test de velocidad automático con alertas, sondeos de
calidad hacia servicios de juego/streaming e informe semanal."""
from __future__ import annotations

import asyncio
import html
import json
import logging
import time
from datetime import datetime

from .dns.filter import BLOCKED_STATUSES
from .family import ALL
from .pinger import tcp_ping

log = logging.getLogger("netping.scheduler")
BLOCKED_SQL = "(" + ",".join(str(s) for s in BLOCKED_STATUSES) + ")"

DEFAULT_QUALITY_TARGETS = [
    {"name": "Cloudflare", "host": "1.1.1.1", "port": 443},
    {"name": "Google", "host": "8.8.8.8", "port": 443},
    {"name": "Steam", "host": "api.steampowered.com", "port": 443},
    {"name": "YouTube", "host": "www.youtube.com", "port": 443},
    {"name": "Netflix", "host": "www.netflix.com", "port": 443},
    {"name": "Discord", "host": "gateway.discord.gg", "port": 443},
]


def _due(cfg: dict, last: float) -> bool:
    """¿Toca ejecutar? cfg = {time: 'HH:MM', days: [0..6]} — una vez por día programado."""
    now = datetime.now()
    try:
        hh, mm = (int(x) for x in cfg.get("time", "03:30").split(":"))
    except ValueError:
        return False
    if now.weekday() not in (cfg.get("days") or list(range(7))):
        return False
    target = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
    return now >= target and last < target.timestamp()


class Scheduler:
    def __init__(self, ctx):
        self.ctx = ctx
        self.state = ctx.settings.get("scheduler_state") or {}

    def _mark(self, key: str) -> None:
        self.state[key] = time.time()
        self.ctx.settings.set("scheduler_state", self.state, notify=False)

    # ---------------------------------------------------------------- velocidad
    async def run_speedtest(self) -> dict | None:
        c = self.ctx
        if c.speedtest.running:
            return None
        result = None
        async for ev in c.speedtest.run():
            if ev.get("phase") == "done":
                result = ev["result"]
        if not result:
            return None
        await c.db.aexecute("INSERT INTO speedtests(ts,download,upload,ping,jitter,loss,server,ip,isp,bufferbloat,grade,loaded_ping) "
                            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                            (time.time(), result["download"], result["upload"], result["ping"], result["jitter"], result["loss"],
                             result["server"], result["ip"], result["isp"], result.get("bufferbloat"), result.get("grade"), result.get("loaded_ping")))
        self.check_speed(result)
        return result

    def check_speed(self, r: dict) -> None:
        cfg = self.ctx.settings.get("speed_schedule") or {}
        problems = []
        if cfg.get("min_download") and r["download"] < float(cfg["min_download"]):
            problems.append(f"descarga {r['download']:.0f} Mbps (mínimo {cfg['min_download']})")
        if cfg.get("min_upload") and r["upload"] < float(cfg["min_upload"]):
            problems.append(f"subida {r['upload']:.0f} Mbps (mínimo {cfg['min_upload']})")
        if cfg.get("max_ping") and r["ping"] and r["ping"] > float(cfg["max_ping"]):
            problems.append(f"ping {r['ping']:.0f} ms (máximo {cfg['max_ping']})")
        if problems:
            self.ctx.notify.emit("speed_low", "Tu conexión va por debajo de lo esperado", "; ".join(problems) + f". Servidor {r.get('server')}.",
                                 data=r, key="speed")

    # ---------------------------------------------------------------- calidad
    async def quality_sample(self) -> list[dict]:
        cfg = self.ctx.settings.get("quality") or {}
        targets = cfg.get("targets") or DEFAULT_QUALITY_TARGETS
        res = await asyncio.gather(*(tcp_ping(t["host"], int(t.get("port", 443)), count=3, timeout=2.0) for t in targets))
        now = time.time()
        rows = [(now, f"q:{t['name']}", r["avg"], r["loss"]) for t, r in zip(targets, res)]
        self.ctx.db.executemany("INSERT INTO monitor(ts,target,latency,loss) VALUES (?,?,?,?)", rows)
        return [{"name": t["name"], **r} for t, r in zip(targets, res)]

    # ---------------------------------------------------------------- informe
    def build_report(self, days: int = 7) -> dict:
        db = self.ctx.db
        end = time.time()
        start = end - days * 86400
        db.flush_queries()
        q = db.one(f"SELECT COUNT(*) total, SUM(status IN {BLOCKED_SQL}) blocked, COUNT(DISTINCT client) clients FROM queries WHERE ts>=?", (start,))
        top_blocked = db.query(f"SELECT domain, COUNT(*) n FROM queries WHERE ts>=? AND status IN {BLOCKED_SQL} GROUP BY domain ORDER BY n DESC LIMIT 10", (start,))
        per_client = db.query(f"SELECT client, COUNT(*) n, SUM(status IN {BLOCKED_SQL}) blocked FROM queries WHERE ts>=? GROUP BY client ORDER BY n DESC LIMIT 15", (start,))
        names = {d.get("ip"): (d.get("name") or d.get("hostname") or d.get("ip")) for d in self.ctx.devices.devices.values() if d.get("ip")}
        macnames = {m: (d.get("name") or d.get("hostname") or d.get("ip") or m) for m, d in self.ctx.devices.devices.items()}
        for r in per_client:
            r["name"] = names.get(r["client"], r["client"])
        since_day = datetime.fromtimestamp(start).date().isoformat()
        screen = db.query("SELECT mac, SUM(minutes) minutes FROM usage WHERE day>=? AND service=? GROUP BY mac ORDER BY minutes DESC LIMIT 15",
                          (since_day, ALL))
        for r in screen:
            r["name"] = macnames.get(r["mac"], r["mac"])
            r["avg_day"] = round(r["minutes"] / days)
        top_apps = db.query("SELECT service, SUM(minutes) minutes FROM usage WHERE day>=? AND service<>? GROUP BY service ORDER BY minutes DESC LIMIT 10",
                            (since_day, ALL))
        from .catalog import SERVICE_MAP
        for r in top_apps:
            r["name"] = SERVICE_MAP.get(r["service"], {}).get("name", r["service"])
        speed = db.one("SELECT COUNT(*) n, AVG(download) down, AVG(upload) up, AVG(ping) ping, MIN(download) min_down FROM speedtests WHERE ts>=?", (start,))
        mon = db.query("SELECT target, AVG(latency) latency, AVG(loss) loss, SUM(loss>=100)*100.0/COUNT(*) down FROM monitor WHERE ts>=? AND target NOT LIKE 'q:%' GROUP BY target", (start,))
        outages = db.query("SELECT ts, message FROM events WHERE ts>=? AND category='conexión' AND level='warning' ORDER BY ts", (start,))
        anomalies = db.scalar("SELECT COUNT(*) FROM anomalies WHERE ts>=?", (start,)) or 0
        new_devices = db.query("SELECT mac, ip, hostname, vendor, first_seen FROM devices WHERE first_seen>=? ORDER BY first_seen", (start,))
        threats = db.scalar("SELECT COUNT(*) FROM queries WHERE ts>=? AND status=2 AND reason LIKE '%seguridad%'", (start,)) or 0
        data = {
            "start": start, "end": end, "days": days,
            "queries": {"total": q["total"] or 0, "blocked": q["blocked"] or 0, "clients": q["clients"] or 0,
                        "blocked_pct": round((q["blocked"] or 0) / q["total"] * 100, 1) if q["total"] else 0},
            "top_blocked": top_blocked, "devices": per_client, "screen_time": screen, "top_apps": top_apps,
            "speed": {k: (round(v, 1) if isinstance(v, float) else v) for k, v in (speed or {}).items()},
            "connection": [{**m, "latency": round(m["latency"] or 0, 1), "loss": round(m["loss"] or 0, 2), "down": round(m["down"] or 0, 2)} for m in mon],
            "outages": outages, "anomalies": anomalies, "threats": threats, "new_devices": new_devices,
        }
        db.execute("INSERT INTO reports(ts,start,end,data) VALUES (?,?,?,?)", (end, start, end, json.dumps(data, ensure_ascii=False)))
        return data

    @staticmethod
    def report_text(d: dict) -> str:
        q = d["queries"]
        lines = [f"Consultas: {q['total']:,} · bloqueadas {q['blocked']:,} ({q['blocked_pct']} %)".replace(",", "."),
                 f"Amenazas bloqueadas: {d['threats']} · anomalías: {d['anomalies']} · dispositivos nuevos: {len(d['new_devices'])}"]
        sp = d["speed"]
        if sp.get("n"):
            lines.append(f"Velocidad media: ↓{sp['down']} / ↑{sp['up']} Mbps · ping {sp['ping']} ms ({sp['n']} tests)")
        inet = [c for c in d["connection"] if c["target"] not in ("gateway", "dns")]
        if inet:
            lines.append(f"Disponibilidad de Internet: {100 - max(c['down'] for c in inet):.2f} % · cortes: {len(d['outages'])}")
        if d["screen_time"]:
            lines.append("Tiempo de Internet (media/día): " + ", ".join(f"{s['name']} {s['avg_day']} min" for s in d["screen_time"][:5]))
        if d["top_apps"]:
            lines.append("Apps más usadas: " + ", ".join(f"{a['name']} {round(a['minutes'] / 60, 1)} h" for a in d["top_apps"][:5]))
        return "\n".join(lines)

    @staticmethod
    def report_html(d: dict) -> str:
        e = html.escape
        rows = lambda items, cols: "".join("<tr>" + "".join(f"<td style='padding:4px 10px;border-bottom:1px solid #eee'>{e(str(i.get(c, '')))}</td>" for c in cols) + "</tr>" for i in items)
        q = d["queries"]
        return f"""<div style="font-family:system-ui,sans-serif;max-width:640px">
<h2 style="color:#3b82f6">📊 Informe semanal de NetPing</h2>
<p><b>{q['total']:,}</b> consultas · <b>{q['blocked']:,}</b> bloqueadas ({q['blocked_pct']} %) · {d['threats']} amenazas bloqueadas · {d['anomalies']} anomalías</p>
<h3>Tiempo de Internet por dispositivo</h3><table>{rows(d['screen_time'], ['name', 'avg_day'])}</table><p style="color:#888">minutos de media al día</p>
<h3>Apps más usadas (minutos)</h3><table>{rows(d['top_apps'], ['name', 'minutes'])}</table>
<h3>Dominios más bloqueados</h3><table>{rows(d['top_blocked'], ['domain', 'n'])}</table>
<h3>Conexión</h3><table>{rows(d['connection'], ['target', 'latency', 'loss', 'down'])}</table>
<p style="color:#888">latencia media (ms) · pérdida (%) · tiempo sin conexión (%)</p></div>"""

    def send_report(self, d: dict) -> None:
        self.ctx.notify.emit("report", f"Informe semanal ({datetime.fromtimestamp(d['start']).strftime('%d/%m')} – {datetime.fromtimestamp(d['end']).strftime('%d/%m')})",
                             self.report_text(d), data={"html": self.report_html(d)}, key=str(int(d["end"])))

    # ---------------------------------------------------------------- bucle
    async def loop(self) -> None:
        await asyncio.sleep(30)
        last_quality = 0.0
        while True:
            s = self.ctx.settings
            try:
                sp = s.get("speed_schedule") or {}
                if sp.get("enabled") and _due(sp, self.state.get("speed", 0)):
                    self._mark("speed")
                    await self.run_speedtest()
                rp = s.get("reports") or {"enabled": True, "days": [0], "time": "09:00"}
                if rp.get("enabled", True) and _due({"days": rp.get("days", [0]), "time": rp.get("time", "09:00")}, self.state.get("report", 0)):
                    self._mark("report")
                    data = await asyncio.to_thread(self.build_report, 7)
                    self.send_report(data)
                qc = s.get("quality") or {}
                if qc.get("enabled", True) and time.time() - last_quality >= max(60, int(qc.get("interval", 300))):
                    last_quality = time.time()
                    await self.quality_sample()
            except Exception:  # pragma: no cover
                log.exception("tarea programada")
            await asyncio.sleep(30)
