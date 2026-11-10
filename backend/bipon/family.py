"""Tiempo de pantalla y acciones familiares.

El uso se estima a partir del tráfico DNS: cada minuto en el que un dispositivo
consulta dominios de una app cuenta como un minuto de uso de esa app (y de
Internet en general). Los perfiles pueden fijar límites diarios por app o un
límite total; al agotarse, la app (o Internet) se bloquea hasta medianoche.
Los padres pueden conceder minutos extra para el día."""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import date

from .catalog import SERVICE_MAP

log = logging.getLogger("bipon.family")

ALL = "__internet__"


class FamilyManager:
    def __init__(self, ctx):
        self.ctx = ctx
        self.counts: dict[tuple[str, str, str], int] = {}   # (día, mac, servicio) -> minutos
        self._seen: set[tuple[str, str, int]] = set()        # (mac, servicio, minuto) ya contados
        self._dirty: set[tuple[str, str, str]] = set()
        self._notified: set[tuple[str, str, str]] = set()
        self._day = ""
        self.load_today()

    @staticmethod
    def today() -> str:
        return date.today().isoformat()

    def load_today(self) -> None:
        self._day = self.today()
        self.counts = {(r["day"], r["mac"], r["service"]): r["minutes"]
                       for r in self.ctx.db.query("SELECT * FROM usage WHERE day=?", (self._day,))}
        self._seen.clear()
        self._notified.clear()

    # ---------------------------------------------------------------- registro de uso
    def record(self, client_ip: str, domain: str, blocked: bool) -> None:
        if blocked:
            return
        mac = self.ctx.devices.mac_for(client_ip)
        if not mac:
            return
        day = self.today()
        if day != self._day:
            self.flush()
            self.load_today()
        minute = int(time.time() // 60)
        services = self.ctx.engine.services_for(domain)
        for svc in (*services, ALL):
            k = (mac, svc, minute)
            if k in self._seen:
                continue
            self._seen.add(k)
            ck = (day, mac, svc)
            self.counts[ck] = self.counts.get(ck, 0) + 1
            self._dirty.add(ck)
        if len(self._seen) > 200_000:
            self._seen = {k for k in self._seen if k[2] >= minute - 1}

    def flush(self) -> None:
        if not self._dirty:
            return
        rows = [(d, m, s, self.counts.get((d, m, s), 0)) for d, m, s in self._dirty]
        self._dirty.clear()
        self.ctx.db.executemany("INSERT INTO usage(day,mac,service,minutes) VALUES (?,?,?,?) "
                                "ON CONFLICT(day,mac,service) DO UPDATE SET minutes=excluded.minutes", rows)

    async def loop(self) -> None:
        while True:
            await asyncio.sleep(30)
            try:
                await asyncio.to_thread(self.flush)
                self.check_limits()
            except Exception:  # pragma: no cover
                log.exception("tiempo de pantalla")

    # ---------------------------------------------------------------- límites
    def bonus(self, mac: str, svc: str) -> int:
        b = (self.ctx.settings.get("family.bonus") or {}).get(self.today(), {})
        return int(b.get(mac, {}).get(svc, 0))

    def used(self, mac: str, svc: str) -> int:
        return self.counts.get((self.today(), mac, svc), 0)

    def exceeded(self, mac: str, profile: dict) -> tuple[set, bool, str]:
        """Devuelve (apps agotadas, internet agotado, motivo)."""
        limits = profile.get("limits") or {}
        out: set = set()
        all_out = False
        reason = ""
        for svc, mins in limits.items():
            if not mins:
                continue
            allowed = int(mins) + self.bonus(mac, svc)
            if self.used(mac, svc) >= allowed:
                if svc == ALL:
                    all_out, reason = True, f"Tiempo de Internet agotado ({allowed} min)"
                else:
                    out.add(svc)
        return out, all_out, reason

    def check_limits(self) -> None:
        devices = self.ctx.devices
        profiles = devices.profiles()
        for mac, d in devices.devices.items():
            prof = profiles.get(d.get("profile") or "default")
            if not prof or not prof.get("limits"):
                continue
            svcs, all_out, _ = self.exceeded(mac, prof)
            for svc in (*svcs, *([ALL] if all_out else [])):
                key = (self.today(), mac, svc)
                if key in self._notified:
                    continue
                self._notified.add(key)
                name = d.get("name") or d.get("hostname") or d.get("ip") or mac
                what = "Internet" if svc == ALL else SERVICE_MAP.get(svc, {}).get("name", svc)
                self.ctx.notify.emit("screentime_limit", f"Tiempo agotado: {what}",
                                     f"{name} ha agotado su tiempo diario de {what}. Puedes darle minutos extra desde Bipon Net.",
                                     key=f"{mac}:{svc}")
                devices.invalidate()

    def grant(self, mac: str, svc: str, minutes: int) -> int:
        b = self.ctx.settings.get("family.bonus") or {}
        today = self.today()
        b = {today: b.get(today, {})}  # sólo se conserva el día actual
        cur = b[today].setdefault(mac, {})
        cur[svc] = max(0, int(cur.get(svc, 0)) + int(minutes))
        self.ctx.settings.set("family.bonus", b, notify=False)
        self._notified.discard((today, mac, svc))
        self.ctx.devices.invalidate()
        return cur[svc]

    def summary(self, mac: str | None = None) -> list[dict]:
        devices = self.ctx.devices
        profiles = devices.profiles()
        today = self.today()
        out = []
        for m, d in devices.devices.items():
            if mac and m != mac:
                continue
            prof = profiles.get(d.get("profile") or "default") or {}
            limits = prof.get("limits") or {}
            apps = {}
            for (day, dm, svc), mins in self.counts.items():
                if day == today and dm == m:
                    apps[svc] = mins
            if not apps and not limits:
                continue
            rows = []
            for svc in sorted(set(apps) | set(limits), key=lambda s: -apps.get(s, 0)):
                lim = int(limits.get(svc) or 0)
                rows.append({"service": svc, "name": "Internet (total)" if svc == ALL else SERVICE_MAP.get(svc, {}).get("name", svc),
                             "minutes": apps.get(svc, 0), "limit": lim or None, "bonus": self.bonus(m, svc),
                             "remaining": max(0, lim + self.bonus(m, svc) - apps.get(svc, 0)) if lim else None})
            out.append({"mac": m, "name": d.get("name") or d.get("hostname") or d.get("ip") or m, "profile": prof.get("name"),
                        "total": apps.get(ALL, 0), "apps": rows})
        out.sort(key=lambda x: -x["total"])
        return out

    def history(self, mac: str, days: int = 7) -> list[dict]:
        return self.ctx.db.query("SELECT day, service, minutes FROM usage WHERE mac=? AND day >= date('now', ?) ORDER BY day",
                                 (mac, f"-{days} days"))

    # ---------------------------------------------------------------- pausa familiar
    def pause(self, profiles: list[str] | None, minutes: int) -> int:
        until = time.time() + minutes * 60 if minutes > 0 else 0
        n = 0
        for mac, d in self.ctx.devices.devices.items():
            if profiles and (d.get("profile") or "default") not in profiles:
                continue
            if d.get("ip") in (self.ctx.devices.gateway, self.ctx.devices.self_ip) or d.get("kind") in ("router", "server"):
                continue
            self.ctx.db.execute("UPDATE devices SET paused_until=?, paused=? WHERE mac=?", (until, 1 if minutes == 0 else 0, mac))
            n += 1
        self.ctx.devices.load()
        self.ctx.apply_policy_now(None)
        return n

    def resume(self, profiles: list[str] | None) -> int:
        n = 0
        for mac, d in self.ctx.devices.devices.items():
            if profiles and (d.get("profile") or "default") not in profiles:
                continue
            self.ctx.db.execute("UPDATE devices SET paused_until=0, paused=0 WHERE mac=?", (mac,))
            n += 1
        self.ctx.devices.load()
        self.ctx.apply_policy_now(None)
        return n
