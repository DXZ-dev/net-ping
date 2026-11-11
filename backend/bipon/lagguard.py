"""Vigilante de lag: ping a Internet en vivo, detección de picos, culpables probables y «Proteger mi partida».

* Base: percentil 20 del ping de los últimos 10 minutos (lo «normal» de tu línea).
* Pico: dos muestras seguidas por encima de base × 2 y base + 25 ms (o pérdida).
* Culpables: lo que cada dispositivo está haciendo ahora mismo según su DNS
  (descargas de juegos, actualizaciones, vídeo 4K, copias en la nube, torrents…)
  y, si se puede medir, su velocidad real (Proxmox, este equipo, modo router).
* Proteger mi partida: limita temporalmente al resto de dispositivos con el
  controlador de velocidad (Proxmox, MikroTik, OpenWrt o modo router), precalienta
  el DNS de los juegos y lo deshace todo al terminar.
"""
from __future__ import annotations

import asyncio
import logging
import statistics
import time
from collections import deque

log = logging.getLogger("bipon.lag")

# dominio → (categoría, peso de impacto en el ping)
HEAVY = [
    (("steamcontent.com", "steamserver.net", "cdn.steamstatic.com", "epicgames-download1", "download.epicgames.com",
      "dl.playstation.net", "gst.prod.dl.playstation.net", "assets1.xboxlive.com", "xvcf1.xboxlive.com", "dlassets.xboxlive.com",
      "blzddist", "battle.net/download", "riotcdn.net", "nintendo.net/d", "ea.com/download", "origin-a.akamaihd.net"), "Descarga de juego", 5),
    (("windowsupdate.com", "delivery.mp.microsoft.com", "dl.delivery.mp", "update.microsoft.com", "swcdn.apple.com",
      "updates.cdn-apple.com", "mesu.apple.com", "android.clients.google.com", "dl.google.com", "gvt1.com"), "Actualización del sistema", 4),
    (("googlevideo.com", "nflxvideo.net", "aiv-cdn.net", "dssott.com", "bamgrid.com", "hbomaxcdn.com", "ttvnw.net", "twitchcdn.net",
      "akamaized.net", "tiktokcdn.com", "fbcdn.net", "cdninstagram.com", "scdn.co", "vimeocdn.com"), "Vídeo / streaming", 2),
    (("icloud-content.com", "backup.icloud.com", "drive.google.com", "googleusercontent.com", "onedrive.live.com", "sharepoint.com",
      "dropbox.com", "dropboxusercontent.com", "mega.nz", "mega.co.nz", "backblazeb2.com"), "Copia / subida a la nube", 4),
    (("tracker.", "announce", "bittorrent", "opentrackr", "torrent"), "Torrent / P2P", 5),
]


def classify(domain: str) -> tuple[str, int] | None:
    d = domain.lower()
    for pats, label, w in HEAVY:
        if any(p in d for p in pats):
            return label, w
    return None


class LagGuard:
    def __init__(self, ctx):
        self.ctx = ctx
        self.samples: deque = deque(maxlen=720)     # (ts, ms|None) ≈ 1 h a 5 s
        self.recent: deque = deque(maxlen=5000)     # (ts, ip, categoría, peso)
        self.spikes: deque = deque(maxlen=50)
        self._over = 0
        self.protect: dict | None = None

    # ---------------------------------------------------------------- datos de entrada
    def observe(self, ip: str, domain: str) -> None:
        c = classify(domain or "")
        if c:
            self.recent.append((time.time(), ip, c[0], c[1]))

    def baseline(self) -> float | None:
        now = time.time()
        vals = [v for t, v in self.samples if v is not None and now - t < 600]
        if len(vals) < 5:
            return None
        vals.sort()
        return vals[max(0, int(len(vals) * 0.2) - 1)]

    def culprits(self, window: int = 90) -> list[dict]:
        now = time.time()
        score: dict[str, dict] = {}
        for ts, ip, cat, w in self.recent:
            if now - ts > window:
                continue
            s = score.setdefault(ip, {"ip": ip, "score": 0, "reasons": {}})
            s["score"] += w
            s["reasons"][cat] = s["reasons"].get(cat, 0) + 1
        devs = self.ctx.devices
        rates = self.ctx.rates.snapshot()
        for mac, r in rates.items():  # velocidad real medida, si la hay
            d = devs.devices.get(mac) or {}
            mbps = (r.get("rx_bps", 0) + r.get("tx_bps", 0)) / 1e6
            if d.get("ip") and mbps > 5:
                s = score.setdefault(d["ip"], {"ip": d["ip"], "score": 0, "reasons": {}})
                s["score"] += min(20, mbps / 5)
                s["mbps"] = round(mbps, 1)
        out = []
        for s in score.values():
            mac = devs.mac_for(s["ip"])
            d = devs.devices.get(mac or "") or {}
            out.append({**s, "mac": mac, "name": d.get("name") or d.get("friendly") or d.get("hostname") or s["ip"],
                        "reasons": [k for k, _ in sorted(s["reasons"].items(), key=lambda x: -x[1])]})
        return sorted(out, key=lambda x: -x["score"])[:8]

    # ---------------------------------------------------------------- bucle
    async def loop(self) -> None:
        await asyncio.sleep(15)
        while True:
            try:
                self._tick()
                if self.protect and time.time() > self.protect["until"]:
                    await self.stop_protect(auto=True)
            except Exception as exc:  # pragma: no cover
                log.debug("lag: %s", exc)
            await asyncio.sleep(5)

    def _tick(self) -> None:
        ms = self.ctx.rates.ping_ms
        now = time.time()
        self.samples.append((now, ms))
        base = self.baseline()
        if base is None:
            return
        bad = ms is None or (ms > base * 2 and ms > base + 25)
        self._over = self._over + 1 if bad else 0
        if self._over == 2:
            who = self.culprits()
            spike = {"ts": now, "ms": ms, "base": round(base, 1), "culprits": who[:3]}
            self.spikes.appendleft(spike)
            names = ", ".join(f"{c['name']} ({c['reasons'][0] if c['reasons'] else 'mucho tráfico'})" for c in who[:3]) or "no identificado"
            self.ctx.db.event("warning", "lag", f"Pico de ping: {ms if ms is not None else 'pérdida'} ms (normal {base:.0f} ms). Posible causa: {names}")
            self.ctx.notify.emit("lag_spike", "Subida de ping en tu red",
                                 f"El ping pasó de {base:.0f} ms a {ms if ms is not None else '—'} ms. Posible causa: {names}.", key="lag")

    # ---------------------------------------------------------------- proteger mi partida
    async def start_protect(self, macs: list[str], minutes: int, limit_mbps: float) -> dict:
        if self.protect:
            await self.stop_protect()
        minutes = max(5, min(int(minutes or 60), 360))
        limit = max(0.5, min(float(limit_mbps or 5), 1000))
        sc = self.ctx.speedctl
        devs = self.ctx.devices.devices
        prev, applied, skipped = {}, [], 0
        for mac, d in devs.items():
            if mac in macs or not d.get("ip") or d.get("ip") in (self.ctx.devices.gateway, self.ctx.devices.self_ip):
                continue
            if not d.get("online") or not sc.method_for(mac):
                skipped += 1
                continue
            prev[mac] = (d.get("limit_down"), d.get("limit_up"))
            r = await sc.apply(mac, limit, max(0.5, limit / 2))
            if r.get("applied"):
                applied.append(mac)
        try:
            await self.ctx.gaming.warm()  # DNS de juegos listo en caché
        except Exception:
            pass
        self.protect = {"macs": macs, "until": time.time() + minutes * 60, "limit": limit, "prev": prev, "applied": applied, "started": time.time()}
        names = ", ".join((devs.get(m) or {}).get("name") or m for m in macs) or "tu equipo"
        self.ctx.db.event("info", "lag", f"Proteger mi partida activado para {names} ({minutes} min): {len(applied)} dispositivos limitados a {limit} Mbps")
        return {"applied": len(applied), "skipped": skipped, "until": self.protect["until"]}

    async def stop_protect(self, auto: bool = False) -> None:
        p, self.protect = self.protect, None
        if not p:
            return
        for mac in p["applied"]:
            down, up = p["prev"].get(mac, (None, None))
            try:
                await self.ctx.speedctl.apply(mac, down, up)  # se restaura el límite anterior (o ninguno)
            except Exception:
                pass
        self.ctx.db.event("info", "lag", "Proteger mi partida terminado" + (" (tiempo agotado)" if auto else ""))

    # ---------------------------------------------------------------- estado
    def status(self) -> dict:
        now = time.time()
        vals = [v for t, v in self.samples if v is not None and now - t < 300]
        base = self.baseline()
        jitter = round(statistics.pstdev(vals), 1) if len(vals) > 2 else None
        loss = round(100 * sum(1 for t, v in self.samples if v is None and now - t < 300) / max(1, sum(1 for t, _ in self.samples if now - t < 300)), 1)
        p = self.protect
        return {"ping": self.ctx.rates.ping_ms, "baseline": round(base, 1) if base else None, "jitter": jitter, "loss": loss,
                "history": [[round(t), v] for t, v in list(self.samples)[-120:]], "spikes": list(self.spikes)[:10],
                "culprits": self.culprits(), "protect": {**{k: p[k] for k in ("macs", "until", "limit", "started")}, "applied": len(p["applied"])} if p else None}
