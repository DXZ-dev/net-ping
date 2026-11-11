"""Seguridad de red: detección de anomalías, bloqueo por país (GeoIP) y por TLD,
y protección contra DNS rebinding."""
from __future__ import annotations

import asyncio
import bisect
import csv
import gzip
import io
import ipaddress
import logging
import math
import time
from collections import Counter, defaultdict
from datetime import date

import httpx

from .config import DATA_DIR

log = logging.getLogger("bipon.security")

WINDOW = 600  # s analizados en cada pasada

ANOMALY_LABELS = {
    "dga": "Posible malware (dominios generados aleatoriamente)",
    "tunnel": "Posible túnel DNS (exfiltración de datos)",
    "burst": "Ráfaga anómala de consultas",
    "threats": "Varios accesos a dominios maliciosos",
    "iot_spread": "Dispositivo IoT contactando muchos dominios nuevos",
}

COUNTRY_NAMES = {
    "CN": "China", "RU": "Rusia", "KP": "Corea del Norte", "IR": "Irán", "BY": "Bielorrusia", "VN": "Vietnam",
    "IN": "India", "BR": "Brasil", "NG": "Nigeria", "UA": "Ucrania", "RO": "Rumanía", "TR": "Turquía", "PK": "Pakistán",
    "ID": "Indonesia", "TH": "Tailandia", "BD": "Bangladés", "HK": "Hong Kong", "TW": "Taiwán", "KR": "Corea del Sur",
    "US": "Estados Unidos", "MX": "México", "ES": "España", "DE": "Alemania", "FR": "Francia", "GB": "Reino Unido",
    "NL": "Países Bajos", "SG": "Singapur", "JP": "Japón", "CA": "Canadá", "AR": "Argentina", "CO": "Colombia",
    "DO": "Rep. Dominicana", "CL": "Chile", "PE": "Perú", "VE": "Venezuela",
}
RISKY_TLDS = ["zip", "mov", "top", "xyz", "click", "loan", "work", "gq", "tk", "ml", "cf", "ga", "country", "kim", "men",
              "stream", "download", "racing", "review", "date", "faith", "accountant", "cricket", "science", "party", "bid", "trade", "win"]


def entropy(s: str) -> float:
    if not s:
        return 0.0
    c = Counter(s)
    n = len(s)
    return -sum(v / n * math.log2(v / n) for v in c.values())


def looks_generated(domain: str) -> bool:
    """Heurística DGA: etiqueta principal larga, muy aleatoria y con pocas vocales."""
    parts = domain.split(".")
    if len(parts) < 2:
        return False
    label = parts[-2] if len(parts[-1]) <= 3 or len(parts) == 2 else parts[-2]
    if len(label) < 12 or any(ch.isdigit() for ch in label[:1]):
        return False
    vowels = sum(ch in "aeiou" for ch in label) / len(label)
    digits = sum(ch.isdigit() for ch in label) / len(label)
    return entropy(label) > 3.6 and (vowels < 0.25 or digits > 0.3)


# ------------------------------------------------------------------ GeoIP
class GeoIP:
    """Base de datos país-IP (DB-IP Lite, licencia CC-BY 4.0) para IPv4."""

    def __init__(self):
        self.path = DATA_DIR / "geoip-country.csv.gz"
        self.starts: list[int] = []
        self.ends: list[int] = []
        self.cc: list[str] = []
        self.loaded = False
        self.updated = 0.0

    def load(self) -> bool:
        if not self.path.exists():
            return False
        starts, ends, cc = [], [], []
        with gzip.open(self.path, "rt", encoding="utf-8", errors="ignore") as fh:
            for row in csv.reader(fh):
                if len(row) < 3 or ":" in row[0]:
                    continue
                try:
                    starts.append(int(ipaddress.IPv4Address(row[0])))
                    ends.append(int(ipaddress.IPv4Address(row[1])))
                    cc.append(row[2])
                except ValueError:
                    continue
        order = sorted(range(len(starts)), key=starts.__getitem__)
        self.starts = [starts[i] for i in order]
        self.ends = [ends[i] for i in order]
        self.cc = [cc[i] for i in order]
        self.loaded = bool(self.starts)
        self.updated = self.path.stat().st_mtime
        log.info("GeoIP cargada: %d rangos", len(self.starts))
        return self.loaded

    async def download(self) -> bool:
        today = date.today()
        months = [today.strftime("%Y-%m"), (date(today.year - (today.month == 1), (today.month - 2) % 12 + 1, 1)).strftime("%Y-%m")]
        async with httpx.AsyncClient(timeout=120, follow_redirects=True) as c:
            for m in months:
                try:
                    r = await c.get(f"https://download.db-ip.com/free/dbip-country-lite-{m}.csv.gz")
                    if r.status_code == 200 and len(r.content) > 1_000_000:
                        tmp = self.path.with_suffix(".tmp")
                        tmp.write_bytes(r.content)
                        tmp.replace(self.path)
                        return await asyncio.to_thread(self.load)
                except Exception as exc:
                    log.warning("descarga GeoIP %s: %s", m, exc)
        return False

    def country(self, ip: str) -> str | None:
        if not self.loaded:
            return None
        try:
            n = int(ipaddress.IPv4Address(ip))
        except ValueError:
            return None
        i = bisect.bisect_right(self.starts, n) - 1
        if i >= 0 and self.starts[i] <= n <= self.ends[i]:
            return self.cc[i]
        return None


# ------------------------------------------------------------------ gestor
class SecurityManager:
    def __init__(self, ctx):
        self.ctx = ctx
        self.geo = GeoIP()
        self.baseline: dict[str, float] = {}
        self.seen_domains: dict[str, set] = defaultdict(set)
        self.last_run = 0.0
        self._threat_hits: dict[str, list[float]] = defaultdict(list)

    def cfg(self) -> dict:
        c = self.ctx.settings.get("security") or {}
        c.setdefault("anomaly_detection", True)
        c.setdefault("blocked_tlds", [])
        c.setdefault("blocked_countries", [])
        c.setdefault("rebind_protection", True)
        c.setdefault("rebind_allow", [])
        return c

    def apply(self) -> None:
        c = self.cfg()
        self.ctx.engine.blocked_tlds = {t.lower().strip(".") for t in c["blocked_tlds"] if t.strip(".")}
        self.ctx.dns.blocked_countries = set(c["blocked_countries"]) if self.geo.loaded else set()
        self.ctx.dns.geo = self.geo
        self.ctx.dns.rebind_protection = bool(c["rebind_protection"])
        self.ctx.dns.rebind_allow = {d.lower().strip(".") for d in c["rebind_allow"]}

    async def start(self) -> None:
        loaded = await asyncio.to_thread(self.geo.load)
        if self.cfg()["blocked_countries"] and (not loaded or time.time() - self.geo.updated > 35 * 86400):
            await self.geo.download()
        self.apply()

    def threat_hit(self, client: str, domain: str) -> None:
        mac = self.ctx.devices.mac_for(client)
        d = self.ctx.devices.devices.get(mac) if mac else None
        name = (d or {}).get("name") or (d or {}).get("hostname") or client
        self.ctx.notify.emit("threat_blocked", "Amenaza bloqueada",
                             f"{name} intentó acceder a {domain} (dominio de malware/phishing). Bipon Net lo bloqueó.",
                             data={"client": client, "domain": domain}, key=f"{client}:{domain}")
        hits = self._threat_hits[client]
        hits.append(time.time())
        self._threat_hits[client] = [t for t in hits if time.time() - t < WINDOW]

    # ---------------------------------------------------------------- anomalías
    def _record(self, client: str, kind: str, detail: str) -> None:
        mac = self.ctx.devices.mac_for(client)
        recent = self.ctx.db.scalar("SELECT COUNT(*) FROM anomalies WHERE client=? AND kind=? AND ts>?", (client, kind, time.time() - 3600))
        if recent:
            return
        self.ctx.db.execute("INSERT INTO anomalies(ts,mac,client,kind,detail) VALUES (?,?,?,?,?)", (time.time(), mac, client, kind, detail))
        d = self.ctx.devices.devices.get(mac) if mac else None
        name = (d or {}).get("name") or (d or {}).get("hostname") or client
        self.ctx.notify.emit("anomaly", ANOMALY_LABELS.get(kind, kind), f"{name}: {detail}", data={"client": client, "kind": kind},
                             key=f"{client}:{kind}")
        log.warning("anomalía %s en %s: %s", kind, client, detail)
        guard = getattr(self.ctx, "guard", None)
        if guard is not None:
            guard.on_anomaly(mac, client, kind, detail)

    def analyze(self) -> list[dict]:
        since = time.time() - WINDOW
        self.ctx.db.flush_queries()
        rows = self.ctx.db.query("SELECT client, domain, qtype, status FROM queries WHERE ts>=?", (since,))
        per: dict[str, list] = defaultdict(list)
        for r in rows:
            per[r["client"]].append(r)
        found = []
        for client, qs in per.items():
            if client.startswith("127.") or client == "::1":
                continue
            n = len(qs)
            domains = {q["domain"] for q in qs}
            # 1. DGA
            gen = sorted({d for d in domains if looks_generated(d)})
            if len(gen) >= 8:
                found.append((client, "dga", f"{len(gen)} dominios con aspecto aleatorio en 10 min (p. ej. {', '.join(gen[:3])})"))
            # 2. túnel DNS
            long_names = [d for d in domains if any(len(lbl) > 45 for lbl in d.split("."))]
            txt = sum(1 for q in qs if q["qtype"] in ("TXT", "NULL"))
            bases = Counter(".".join(d.split(".")[-2:]) for d in long_names)
            if bases and bases.most_common(1)[0][1] >= 20 or txt >= 150:
                base = bases.most_common(1)[0][0] if bases else "varios"
                found.append((client, "tunnel", f"{len(long_names)} nombres muy largos hacia {base} y {txt} consultas TXT"))
            # 3. ráfaga respecto a su línea base
            base = self.baseline.get(client)
            if base and n > max(400, base * 8):
                found.append((client, "burst", f"{n} consultas en 10 min (lo normal es ~{int(base)})"))
            self.baseline[client] = n if base is None else 0.9 * base + 0.1 * n
            # 4. varias amenazas
            if len(self._threat_hits.get(client, [])) >= 5:
                found.append((client, "threats", f"{len(self._threat_hits[client])} intentos de acceso a dominios maliciosos en 10 min"))
                self._threat_hits[client] = []
            # 5. IoT expandiéndose
            mac = self.ctx.devices.mac_for(client)
            kind = (self.ctx.devices.devices.get(mac) or {}).get("kind") if mac else None
            if kind in ("iot", "camera", "speaker", "tv", "printer"):
                known = self.seen_domains[client]
                new = domains - known
                if len(known) > 30 and len(new) > 60:
                    found.append((client, "iot_spread", f"{len(new)} dominios nuevos en 10 min (normalmente usa {len(known)})"))
                known |= domains
                if len(known) > 5000:
                    self.seen_domains[client] = set(list(known)[-2000:])
        for client, kind, detail in found:
            self._record(client, kind, detail)
        self.last_run = time.time()
        return [{"client": c, "kind": k, "detail": d} for c, k, d in found]

    async def loop(self) -> None:
        await asyncio.sleep(120)
        while True:
            if self.cfg()["anomaly_detection"]:
                try:
                    await asyncio.to_thread(self.analyze)
                except Exception:  # pragma: no cover
                    log.exception("análisis de anomalías")
            if self.cfg()["blocked_countries"] and time.time() - self.geo.updated > 35 * 86400:
                if await self.geo.download():
                    self.apply()
            await asyncio.sleep(300)

    def status(self) -> dict:
        names = {d["mac"]: d.get("name") or d.get("hostname") or d.get("ip") for d in self.ctx.devices.devices.values()}
        rows = self.ctx.db.query("SELECT * FROM anomalies ORDER BY ts DESC LIMIT 100")
        for r in rows:
            r["device"] = names.get(r["mac"]) or r["client"]
            r["label"] = ANOMALY_LABELS.get(r["kind"], r["kind"])
        return {"config": self.cfg(), "anomalies": rows, "last_run": self.last_run,
                "geo": {"loaded": self.geo.loaded, "ranges": len(self.geo.starts), "updated": self.geo.updated},
                "countries": COUNTRY_NAMES, "risky_tlds": RISKY_TLDS}
