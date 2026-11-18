"""Ruta salto a salto continua (MTR): dónde empieza el lag — tu casa, tu proveedor o el destino.

Cada 15 min (y a demanda) se lanza mtr hacia 1.1.1.1 y hacia la región de tu juego (por cada línea del multi-WAN).
Cada salto se etiqueta con su operador (ASN vía DNS de Team Cymru) para separar «casa / proveedor / Internet».
Una subida sólo cuenta si se mantiene en los saltos siguientes: los routers que responden lento a los pings
(limitación de ICMP) no generan falsas alarmas. Si varias mediciones seguidas culpan al proveedor, avisa.
"""
from __future__ import annotations

import asyncio
import ipaddress
import json
import logging
import shutil
import time

from dnslib import QTYPE, DNSRecord

log = logging.getLogger("bipon.trace")

_ASN_CACHE: dict[str, tuple[float, dict]] = {}
SEG_LABEL = {"casa": "Tu casa (router/Wi-Fi)", "proveedor": "Tu proveedor", "internet": "Internet / destino", "?": "Sin identificar"}


def _private(ip: str) -> bool:
    try:
        a = ipaddress.ip_address(ip)
        return a.is_private or a in ipaddress.ip_network("100.64.0.0/10")
    except ValueError:
        return False


def parse_mtr(data: str | dict) -> list[dict]:
    rep = (json.loads(data) if isinstance(data, str) else data).get("report", {})
    hops = []
    for h in rep.get("hubs", []):
        host = h.get("host") or "???"
        loss = float(h.get("Loss%", 100))
        hops.append({"hop": int(h.get("count", len(hops) + 1)), "ip": None if host == "???" else host, "loss": round(loss, 1),
                     "avg": None if loss >= 100 or host == "???" else round(float(h.get("Avg", 0)), 1),
                     "best": None if loss >= 100 else round(float(h.get("Best", 0)), 1),
                     "worst": None if loss >= 100 else round(float(h.get("Wrst", 0)), 1),
                     "stdev": None if loss >= 100 else round(float(h.get("StDev", 0)), 1)})
    return hops


def analyze(hops: list[dict], isp_asn: int | None = None) -> dict:
    """Etiqueta cada salto y localiza el primer aumento de ping o de pérdida que se mantiene hasta el final."""
    first_public_asn = None
    for h in hops:
        ip = h.get("ip")
        if not ip:
            h["segment"] = "?"
            continue
        if h["hop"] == 1 or (_private(ip) and first_public_asn is None and all(_private(x["ip"]) for x in hops[:hops.index(h)] if x.get("ip"))
                               and h["hop"] <= 2 and not str(ip).startswith("100.")):
            h["segment"] = "casa" if h["hop"] == 1 or ipaddress.ip_address(ip).is_private and h["hop"] <= 2 and hops[0].get("segment") == "casa" and False else "proveedor"
        if h["hop"] == 1:
            h["segment"] = "casa"
            continue
        if _private(ip):
            h["segment"] = "proveedor"
            continue
        asn = h.get("asn")
        if first_public_asn is None and asn:
            first_public_asn = asn
        isp = isp_asn or first_public_asn
        h["segment"] = "proveedor" if (asn and isp and asn == isp) else ("internet" if asn else "?")
    # salto de ISP sin ASN conocido: los públicos anteriores al primer ASN distinto cuentan como proveedor
    if not any(h.get("asn") for h in hops):
        seen_public = 0
        for h in hops:
            if h.get("segment") == "?" and h.get("ip") and not _private(h["ip"]):
                seen_public += 1
                h["segment"] = "proveedor" if seen_public <= 2 else "internet"
    resp = [h for h in hops if h.get("avg") is not None]
    dest = hops[-1] if hops else None
    out = {"hops": hops, "dest_ms": dest.get("avg") if dest else None, "dest_loss": dest.get("loss") if dest else None,
           "lag_hop": None, "lag_segment": None, "lag_added": None, "loss_hop": None, "loss_segment": None}
    # aumento de latencia persistente
    prev = None
    for i, h in enumerate(resp):
        if prev is not None:
            jump = h["avg"] - prev
            later = [x["avg"] for x in resp[i + 1:]]
            if jump >= max(10.0, prev * 0.3) and all(v >= h["avg"] * 0.8 for v in later):
                out.update(lag_hop=h["hop"], lag_segment=h.get("segment"), lag_added=round(jump, 1))
                break
        prev = h["avg"] if prev is None else max(prev, min(h["avg"], (h["best"] or h["avg"]) * 2))
    # pérdida que empieza en un salto y llega al destino
    for i, h in enumerate(hops):
        if h.get("loss", 0) >= 10 and h.get("ip") and all(x.get("loss", 0) >= 10 for x in hops[i:] if x.get("ip")) and dest and dest.get("loss", 0) >= 10:
            out.update(loss_hop=h["hop"], loss_segment=h.get("segment"))
            break
    home = hops[0].get("avg") if hops else None
    if home is not None and home > 8:
        out["verdict"] = f"Tu red local responde lento ({home} ms hasta el router): revisa el cable, el Wi-Fi o la carga del router."
        out["culprit"] = "casa"
    elif out["loss_hop"]:
        out["verdict"] = f"Hay pérdida de paquetes desde el salto {out['loss_hop']} ({SEG_LABEL.get(out['loss_segment'] or '?')})."
        out["culprit"] = out["loss_segment"]
    elif out["lag_hop"]:
        out["verdict"] = (f"El ping sube {out['lag_added']} ms en el salto {out['lag_hop']} "
                          f"({SEG_LABEL.get(out['lag_segment'] or '?')}) y se mantiene hasta el destino.")
        out["culprit"] = out["lag_segment"]
    else:
        out["verdict"] = "Ruta limpia: ningún tramo añade lag de forma sostenida."
        out["culprit"] = None
    return out


async def _asn(ctx, ip: str) -> dict:
    """ASN y nombre del operador de una IP pública (Team Cymru por DNS, con caché de 1 día)."""
    hit = _ASN_CACHE.get(ip)
    if hit and time.time() - hit[0] < 86400:
        return hit[1]
    info: dict = {}
    try:
        rev = ".".join(reversed(ip.split(".")))
        resp, _, _ = await asyncio.wait_for(ctx.pool.resolve(DNSRecord.question(f"{rev}.origin.asn.cymru.com", "TXT").pack()), 3)
        txt = next((str(r.rdata).strip('"') for r in DNSRecord.parse(resp).rr if r.rtype == QTYPE.TXT), "")
        if txt:
            asn = int(txt.split("|")[0].split()[0])
            info["asn"] = asn
            resp, _, _ = await asyncio.wait_for(ctx.pool.resolve(DNSRecord.question(f"AS{asn}.asn.cymru.com", "TXT").pack()), 3)
            txt2 = next((str(r.rdata).strip('"') for r in DNSRecord.parse(resp).rr if r.rtype == QTYPE.TXT), "")
            if txt2:
                info["org"] = txt2.split("|")[-1].strip().split(",")[0][:60]
    except Exception:
        pass
    _ASN_CACHE[ip] = (time.time(), info)
    return info


class Tracer:
    def __init__(self, ctx):
        self.ctx = ctx
        self.running = False
        self.last: dict[str, dict] = {}
        self._provider_streak = 0

    @property
    def available(self) -> bool:
        return bool(shutil.which("mtr"))

    def cfg(self) -> dict:
        q = self.ctx.settings.get("quality") or {}
        return {"trace": q.get("trace", True), "trace_every": int(q.get("trace_every", 15)), "trace_targets": q.get("trace_targets") or []}

    def targets(self) -> list[tuple[str, str]]:
        out = [("1.1.1.1", "Internet (Cloudflare)")]
        row = self.ctx.db.one("SELECT region FROM game_routes ORDER BY ts DESC, ms ASC LIMIT 1")
        region = (self.ctx.settings.get("quality") or {}).get("game_region") or (row["region"] if row else "")
        if region:
            from .gaming import GAME_REGIONS
            out.append((f"dynamodb.{region}.amazonaws.com", f"Servidores de juego: {dict(GAME_REGIONS).get(region, region)}"))
        for t in self.cfg()["trace_targets"][:3]:
            out.append((t, t))
        return out

    async def run_one(self, target: str, label: str = "", mark: int | None = None, wan: str = "") -> dict:
        from .pinger import valid_host
        if not valid_host(target):
            raise ValueError("Destino inválido")
        args = ["mtr", "-n", "-j", "-c", "10", "-i", "0.3", "-G", "1"]
        if mark:
            args += ["-M", str(int(mark))]
        p = await asyncio.create_subprocess_exec(*args, target, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        try:
            out, err = await asyncio.wait_for(p.communicate(), 60)
        except asyncio.TimeoutError:
            p.kill()
            raise RuntimeError("mtr tardó demasiado")
        if p.returncode != 0:
            raise RuntimeError((err or out).decode(errors="ignore").strip()[:200] or "mtr falló")
        hops = parse_mtr(out.decode(errors="ignore"))
        pub = [h["ip"] for h in hops if h.get("ip") and not _private(h["ip"])]
        infos = await asyncio.gather(*(_asn(self.ctx, ip) for ip in dict.fromkeys(pub)))
        by_ip = dict(zip(dict.fromkeys(pub), infos))
        for h in hops:
            h.update(by_ip.get(h.get("ip"), {}))
        isp_asn = next((h.get("asn") for h in hops if h.get("asn")), None)
        res = analyze(hops, isp_asn)
        res.update(target=target, label=label or target, ts=time.time(), wan=wan,
                   isp=next((h.get("org") for h in hops if h.get("asn") == isp_asn and h.get("org")), None))
        self.ctx.db.execute("INSERT INTO traces(ts,target,data) VALUES (?,?,?)", (res["ts"], f"{wan}|{target}", json.dumps(res)))
        self.last[f"{wan}|{target}"] = res
        return res

    async def run_all(self) -> list[dict]:
        if self.running:
            raise RuntimeError("Ya hay una medición en curso")
        self.running = True
        try:
            mw = getattr(self.ctx, "multiwan", None)
            lines = [(int(w["mark"]), w["name"]) for w in mw.up_wans()] if mw and mw.active and len(mw.up_wans()) > 1 else [(None, "")]
            res = []
            for target, label in self.targets():
                for mark, wname in lines:
                    try:
                        res.append(await self.run_one(target, label, mark, wname))
                    except Exception as exc:
                        log.debug("traza %s: %s", target, exc)
            self._watch(res)
            return res
        finally:
            self.running = False

    def _watch(self, res: list[dict]) -> None:
        main = next((r for r in res if r["target"] == "1.1.1.1"), None)
        if not main:
            return
        self._provider_streak = self._provider_streak + 1 if main.get("culprit") == "proveedor" else 0
        if self._provider_streak == 3:
            msg = f"{main['verdict']} Visto en 3 mediciones seguidas{(' (' + main['isp'] + ')') if main.get('isp') else ''}. Guarda el informe de calidad para reclamar."
            self.ctx.db.event("warning", "ruta", msg)
            self.ctx.notify.emit("isp_lag", "El lag está en tu proveedor", msg, key="isp")

    def history(self, hours: int = 24) -> list[dict]:
        rows = self.ctx.db.query("SELECT ts, target, data FROM traces WHERE ts>=? ORDER BY ts DESC LIMIT 400", (time.time() - hours * 3600,))
        out = []
        for r in rows:
            d = json.loads(r["data"])
            out.append({"ts": r["ts"], "target": d.get("target"), "wan": d.get("wan", ""), "dest_ms": d.get("dest_ms"),
                        "culprit": d.get("culprit"), "verdict": d.get("verdict")})
        return out

    def latest(self) -> list[dict]:
        if not self.last:
            seen, out = set(), []
            for r in self.ctx.db.query("SELECT target, data FROM traces ORDER BY ts DESC LIMIT 30"):
                if r["target"] not in seen:
                    seen.add(r["target"])
                    out.append(json.loads(r["data"]))
            return out
        return sorted(self.last.values(), key=lambda r: (r.get("wan", ""), r["target"] != "1.1.1.1"))

    def summary(self, days: int = 7) -> dict:
        rows = [json.loads(r["data"]) for r in self.ctx.db.query("SELECT data FROM traces WHERE ts>=? AND target LIKE '%|1.1.1.1'",
                                                                 (time.time() - days * 86400,))]
        culprits: dict[str, int] = {}
        for d in rows:
            if d.get("culprit"):
                culprits[d["culprit"]] = culprits.get(d["culprit"], 0) + 1
        return {"runs": len(rows), "culprits": culprits}

    async def loop(self) -> None:
        await asyncio.sleep(180)
        last = 0.0
        while True:
            c = self.cfg()
            try:
                if c["trace"] and self.available and time.time() - last > max(5, c["trace_every"]) * 60:
                    last = time.time()
                    await self.run_all()
            except Exception as exc:  # pragma: no cover
                log.debug("trazas: %s", exc)
            await asyncio.sleep(60)
