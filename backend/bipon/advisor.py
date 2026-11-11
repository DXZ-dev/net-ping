"""Asistente inteligente: analiza la red del usuario y recomienda (y aplica) la
configuración óptima de Bipon Net para su caso concreto."""
from __future__ import annotations

import asyncio
import os
import time
from collections import Counter

from . import nat, tools
from .catalog import DNS_PRESETS, RECOMMENDED_LISTS


def _mem_total() -> int:
    try:
        with open("/proc/meminfo") as fh:
            for line in fh:
                if line.startswith("MemTotal:"):
                    return int(line.split()[1]) * 1024
    except Exception:
        pass
    return 1 << 30


class Advisor:
    def __init__(self, ctx):
        self.ctx = ctx
        self.last: dict | None = None
        self.running = False
        self.progress: list[str] = []

    # ------------------------------------------------------------------ análisis
    async def analyze(self) -> dict:
        if self.running:
            raise RuntimeError("Ya hay un análisis en curso")
        self.running = True
        self.progress = []
        try:
            return await self._analyze()
        finally:
            self.running = False

    def _step(self, msg: str):
        self.progress.append(msg)

    async def _analyze(self) -> dict:
        c = self.ctx
        t0 = time.time()
        self._step("Analizando dispositivos de la red…")
        if not c.devices.devices or time.time() - c.devices.last_scan > 900:
            try:
                await c.devices.scan()
            except Exception:
                pass
        devices = list(c.devices.devices.values())
        kinds = Counter(d.get("kind") or "unknown" for d in devices)

        self._step("Midiendo latencia y diagnóstico de conexión…")
        cands = tools.optimizer_candidates(c.unbound.running)
        diag_t = asyncio.create_task(tools.diagnose(int(c.settings.get("dns.port")), c.settings.get("unbound.port") if c.unbound.running else None))
        self._step("Midiendo los servidores DNS más rápidos desde tu ubicación…")
        bench_t = asyncio.create_task(tools.dns_benchmark(cands))
        self._step("Detectando CGNAT, tipo de NAT, UPnP y MTU…")
        nat_t = asyncio.create_task(nat.analyze())
        self._step("Comprobando qué DNS te lleva a los servidores CDN más cercanos (streaming)…")
        cdn_t = asyncio.create_task(c.gaming.cdn())
        diag, bench, natr, cdn = await asyncio.gather(diag_t, bench_t, nat_t, cdn_t, return_exceptions=True)
        cdn = cdn if isinstance(cdn, dict) else {"results": []}
        # puntuación combinada: latencia DNS + 50 % de la latencia al nodo CDN asignado
        cdn_by_id = {r["id"]: r for r in cdn.get("results", [])}
        if isinstance(bench, list):
            for b in bench:
                cr = cdn_by_id.get(b.get("id"))
                b["cdn_avg"] = cr["avg"] if cr else None
                if b.get("median") is not None:
                    b["combined"] = round(b["median"] + 0.5 * (b["cdn_avg"] if b["cdn_avg"] is not None else b["median"] * 2), 2)
            bench.sort(key=lambda b: b.get("combined") if b.get("combined") is not None else 1e9)
        diag = diag if isinstance(diag, dict) else {"checks": [], "status": "unknown"}
        bench = bench if isinstance(bench, list) else []
        natr = natr if isinstance(natr, dict) else {}
        self._step("Calculando la configuración óptima…")

        speed = c.db.one("SELECT * FROM speedtests ORDER BY ts DESC LIMIT 1")
        hw = {"cpus": os.cpu_count() or 1, "memory": _mem_total()}
        recs = self._recommend(devices, kinds, diag, bench, natr, speed, hw)
        score, factors = self._score(diag, natr, bench, speed)
        self.last = {
            "ts": time.time(), "seconds": round(time.time() - t0, 1), "score": score, "factors": factors,
            "summary": self._summary(score, natr, bench, devices),
            "network": {"devices": len(devices), "online": sum(1 for d in devices if d.get("online")),
                        "kinds": dict(kinds), "gateway": c.devices.gateway, "ip": c.devices.self_ip},
            "hardware": hw, "diagnosis": diag, "dns_benchmark": bench, "nat": natr, "speed": speed, "cdn": cdn,
            "recommendations": recs,
        }
        return self.last

    # ------------------------------------------------------------------ reglas
    def _recommend(self, devices, kinds, diag, bench, natr, speed, hw) -> list[dict]:
        c = self.ctx
        s = c.settings
        recs: list[dict] = []

        def add(rid, area, title, why, *, current=None, proposed=None, apply=None, priority="media", impact=""):
            recs.append({"id": rid, "area": area, "title": title, "why": why, "current": current, "proposed": proposed,
                         "apply": apply, "auto": apply is not None, "priority": priority, "impact": impact})

        # --- 1. DNS upstream más rápido -------------------------------------
        ok = [b for b in bench if b.get("median") is not None and b.get("reliability", 0) >= 95]
        cur_ups = s.get("dns.upstreams") or []
        if ok:
            best = ok[0]
            unb = next((b for b in ok if b["id"] == "unbound"), None)
            if unb and unb["median"] <= best["median"] + 3:
                # Unbound igual de rápido: máxima privacidad sin coste
                target = [f"127.0.0.1:{s.get('unbound.port', 5335)}"]
                backup = next((b for b in ok if b["id"] != "unbound"), None)
                if backup:
                    target.append(DNS_PRESETS[backup["id"]]["servers"][0])
                why = (f"Unbound responde en {unb['median']} ms, a la par del público más rápido ({best['name']}, {best['median']} ms). "
                       "Resolver tú mismo da máxima privacidad (ningún tercero ve tus consultas) sin perder velocidad.")
                title = "Usar Unbound (privado) con respaldo rápido"
            else:
                target = list(DNS_PRESETS[best["id"]]["servers"])
                second = next((b for b in ok[1:] if b["id"] not in ("unbound", best["id"])), None)
                if second:
                    target.append(DNS_PRESETS[second["id"]]["servers"][0])
                cdn_txt = f" y te asigna nodos CDN a {best['cdn_avg']} ms (streaming más estable)" if best.get("cdn_avg") is not None else ""
                why = (f"Desde tu ubicación, {best['name']} ofrece la mejor combinación: {best['median']} ms de respuesta DNS{cdn_txt}, "
                       f"{best['reliability']} % de fiabilidad" + (f"; {second['name']} queda como respaldo ({second['median']} ms)." if second else "."))
                if unb:
                    why += f" Unbound tarda {unb['median']} ms: más privado pero más lento en tu red."
                title = f"DNS más rápido para tu zona: {best['name']}"
            if sorted(target) != sorted(cur_ups):
                add("dns_upstream", "DNS", title, why, current=", ".join(cur_ups), proposed=", ".join(target),
                    apply={"type": "dns", "patch": {"upstreams": target, "strategy": "fastest", "preset": "advisor"}},
                    priority="alta", impact="Menor latencia en cada web que abres")
        elif bench:
            add("dns_unreachable", "DNS", "Ningún DNS público responde",
                "No hubo respuesta de los resolvers públicos: puede que tu ISP bloquee el puerto 53 saliente. Usa DNS cifrado (DoH/DoT).",
                proposed="Cloudflare DNS-over-HTTPS", priority="alta",
                apply={"type": "dns", "patch": {"upstreams": DNS_PRESETS["cloudflare-doh"]["servers"], "strategy": "failover", "preset": "cloudflare-doh"}})

        # --- 2. caché y rendimiento DNS -------------------------------------
        n = max(1, len(devices))
        mem = hw["memory"]
        cache = min(200_000 if mem > 2 << 30 else 50_000, max(10_000, n * 2_000))
        dns = s.get("dns")
        patch = {}
        if dns.get("cache_size", 0) < cache:
            patch["cache_size"] = cache
        if not dns.get("optimistic_cache"):
            patch["optimistic_cache"] = True
        if not dns.get("prefetch"):
            patch["prefetch"] = True
        if dns.get("cache_min_ttl", 0) < 60:
            patch["cache_min_ttl"] = 60
        if dns.get("strategy") != "fastest" and len(cur_ups) > 1:
            patch["strategy"] = "fastest"
        if patch:
            add("dns_cache", "DNS", "Caché agresiva para respuestas en <1 ms",
                f"Con {n} dispositivos, una caché de {cache:,} entradas, caché optimista y TTL mínimo de 60 s hacen que la mayoría de "
                "consultas se respondan desde memoria (≈0,3 ms) en lugar de ir a Internet.",
                current=f"caché {dns.get('cache_size'):,}, optimista {'sí' if dns.get('optimistic_cache') else 'no'}, TTL mín {dns.get('cache_min_ttl')} s",
                proposed=", ".join(f"{k}={v}" for k, v in patch.items()), apply={"type": "dns", "patch": patch},
                priority="alta", impact="Navegación más ágil")

        # --- 3. Unbound dimensionado al hardware -------------------------
        if c.unbound.available:
            u = s.get("unbound")
            threads = max(1, min(hw["cpus"], 4))
            if mem < 1 << 30:
                msg, rr = "16m", "32m"
            elif mem < 4 << 30:
                msg, rr = "64m", "128m"
            else:
                msg, rr = "128m", "256m"
            upatch = {}
            for k, v in (("num_threads", threads), ("msg_cache_size", msg), ("rrset_cache_size", rr), ("prefetch", True),
                         ("serve_expired", True), ("aggressive_nsec", True), ("qname_minimisation", True), ("enabled", True)):
                if u.get(k) != v:
                    upatch[k] = v
            mtu = (natr.get("mtu") or {}).get("mtu")
            if mtu and mtu < 1500 and u.get("edns_buffer_size", 1232) > 1232:
                upatch["edns_buffer_size"] = 1232
            if upatch:
                add("unbound_tune", "Unbound", f"Ajustar Unbound a tu servidor ({hw['cpus']} CPU, {mem / (1 << 30):.1f} GB RAM)",
                    "Hilos y cachés adaptados al hardware, prefetch y serve-expired para que los dominios populares estén siempre listos.",
                    current=f"hilos {u.get('num_threads')}, caché {u.get('msg_cache_size')}/{u.get('rrset_cache_size')}",
                    proposed=", ".join(f"{k}={v}" for k, v in upatch.items()), apply={"type": "unbound", "patch": upatch})
        else:
            add("unbound_missing", "Unbound", "Instala Unbound para resolver sin intermediarios",
                "Unbound no está disponible en este sistema; Bipon Net usa un DNS público como respaldo. Con la imagen Docker o el instalador se incluye automáticamente.",
                priority="baja")

        # --- 4. listas de seguridad --------------------------------------
        lists = c.db.query("SELECT url, category, enabled FROM blocklists")
        if not any(l["category"] == "security" and l["enabled"] for l in lists):
            sec = next(r for r in RECOMMENDED_LISTS if r["category"] == "security")
            add("lists_security", "Protección", "Activar protección contra malware y phishing",
                "No tienes listas de seguridad activas. Bloquear dominios maliciosos protege a todos los dispositivos, incluidos los que no admiten antivirus (TV, IoT).",
                proposed=sec["name"], apply={"type": "list", "list": sec}, priority="alta")
        if len(devices) >= 12 and not any("hagezi" in l["url"] and "pro" in l["url"] for l in lists):
            pro = next(r for r in RECOMMENDED_LISTS if "pro.txt" in r["url"])
            add("lists_pro", "Protección", "Lista de anuncios más completa (HaGeZi Pro)",
                f"Con {len(devices)} dispositivos hay mucho rastreo de apps y Smart TV. HaGeZi Pro bloquea más sin romper webs.",
                proposed=pro["name"], apply={"type": "list", "list": pro})

        # --- 5. perfiles parentales ---------------------------------------
        profiles = s.get("profiles") or []
        kid_like = kinds.get("tablet", 0) + kinds.get("console", 0)
        if kid_like and not any(p["id"] != "default" and p.get("safesearch") for p in profiles):
            add("profile_kids", "Control parental", "Crear perfil «Niños»",
                f"Se detectaron {kid_like} consolas/tablets. Un perfil con búsqueda segura, YouTube restringido, bloqueo de contenido adulto y "
                "horario de dormir (22:00–07:00) protege a los menores; luego asígnalo a sus dispositivos.",
                apply={"type": "profile", "template": "kids", "name": "Niños"})
        if kinds.get("iot", 0) + kinds.get("camera", 0) >= 2 and not any(p["id"].startswith("iot") for p in profiles):
            add("profile_iot", "Seguridad", "Perfil «IoT» para cámaras y domótica",
                "Agrupa los dispositivos IoT con protección contra amenazas activada; así ves de un vistazo qué hacen y puedes cortarles Internet si se comportan raro.",
                apply={"type": "profile", "template": "iot", "name": "IoT"})
        if not s.get("dns.block_doh_canary"):
            add("doh_canary", "Control parental", "Evitar que los navegadores salten el filtro",
                "Firefox y Chrome pueden usar DNS cifrado propio e ignorar Bipon Net. Este ajuste les indica que usen el DNS de la red.",
                apply={"type": "dns", "patch": {"block_doh_canary": True}})

        # --- 5b. gaming / streaming ---------------------------------------
        g = s.get("gaming") or {}
        if kinds.get("console", 0) or kinds.get("tv", 0) or kinds.get("computer", 0):
            if not g.get("enabled", True) or not g.get("protect", True):
                add("gaming_mode", "Gaming y streaming", "Activar Modo Gaming y Streaming",
                    f"Hay {kinds.get('console', 0)} consolas y {kinds.get('tv', 0)} TV/streaming. El modo mantiene pre-resueltos los servidores "
                    "de login, matchmaking y CDN (0 ms de espera DNS) y evita que una lista de bloqueo rompa un juego o una plataforma.",
                    apply={"type": "gaming", "patch": {"enabled": True, "protect": True}}, priority="alta", impact="Sin lag de DNS ni cortes")
            if kinds.get("console", 0) and not any(p["id"].startswith("gamer") for p in profiles):
                add("profile_gamer", "Gaming y streaming", "Perfil «Gamer» para consolas y PC de juego",
                    "Perfil sin filtros que puedan interferir con juegos (sólo protección contra amenazas). Asígnalo a consolas y PC gaming.",
                    apply={"type": "profile", "template": "gamer", "name": "Gamer"})

        # --- 6. optimizador automático ---------------------------------
        if not (s.get("dns.auto_optimize") or {}).get("enabled"):
            add("auto_optimize", "DNS", "Re-optimizar el DNS automáticamente cada día",
                "Las rutas de Internet cambian (mantenimiento del ISP, nodos saturados). Bipon Net medirá a diario y cambiará al resolver más rápido.",
                apply={"type": "dns", "patch": {"auto_optimize": {**(s.get('dns.auto_optimize') or {}), "enabled": True, "interval_hours": 24}}})

        # --- 7. conexión / NAT / CGNAT ------------------------------------
        checks = {ch["id"]: ch for ch in diag.get("checks", [])}
        gw = checks.get("gateway", {})
        if gw.get("status") in ("warn", "fail") and isinstance(gw.get("value"), dict):
            v = gw["value"]
            add("gateway_latency", "Conexión", "Latencia alta hacia el router",
                f"El router responde en {v.get('avg')} ms con {v.get('loss')} % de pérdida (lo ideal es <2 ms por cable, <10 ms por Wi-Fi). "
                "Si Bipon Net va por Wi-Fi, conéctalo por cable; si no, revisa el canal Wi-Fi (usa 5 GHz y canales libres) o reinicia el router.",
                priority="alta")
        inet = checks.get("internet", {})
        if isinstance(inet.get("value"), list):
            loss = max((r.get("loss") or 0) for r in inet["value"])
            if loss >= 2:
                add("packet_loss", "Conexión", f"Pérdida de paquetes del {loss} %",
                    "La pérdida de paquetes provoca lag en juegos y cortes en videollamadas. Revisa cables, el módem del ISP y abre una incidencia si persiste.",
                    priority="alta")
        verdict = natr.get("verdict", "unknown")
        if verdict in ("cgnat", "cgnat_possible", "double_nat", "double_nat_possible"):
            top = (natr.get("recommendations") or [{}])[0]
            add("nat_" + verdict, "Acceso remoto", f"{natr.get('label')}: {top.get('title', 'ver soluciones')}",
                natr.get("summary", "") + " " + top.get("why", ""), priority="media", impact="Acceso remoto y NAT para juegos",
                proposed=top.get("title"))
        mtu = natr.get("mtu") or {}
        if mtu.get("mtu") and mtu["mtu"] < 1492:
            add("mtu", "Conexión", f"Ajusta el MTU del router a {mtu['mtu']}", mtu.get("detail", ""), priority="media",
                proposed=f"MTU {mtu['mtu']} / MSS {mtu.get('mss')}")
        if natr.get("nat", {}).get("type") == "symmetric":
            add("nat_strict", "Juegos", "NAT estricto: mejora la conexión de consolas",
                "Activa UPnP en el router y reserva IP fija para las consolas; si estás tras CGNAT pide IP pública a tu ISP.")

        # --- 8. bufferbloat -------------------------------------------
        if speed and speed.get("grade") in ("C", "D", "F"):
            add("sqm", "Velocidad", f"Bufferbloat nota {speed['grade']}: el ping sube {speed.get('bufferbloat')} ms al usar la red",
                f"Cuando alguien descarga o sube, tu ping pasa de {speed.get('ping')} ms a {speed.get('loaded_ping')} ms: lag en juegos y "
                "videollamadas. Activa SQM/QoS (CAKE o fq_codel) en el router limitando al 90-95 % de tu velocidad "
                f"(≈{int((speed.get('download') or 0) * 0.92)} / {int((speed.get('upload') or 0) * 0.92)} Mbps).",
                priority="alta", impact="Ping estable aunque la red esté saturada",
                proposed=f"SQM: bajada {int((speed.get('download') or 0) * 0.92)} Mbps, subida {int((speed.get('upload') or 0) * 0.92)} Mbps")
        if not speed:
            add("speedtest", "Velocidad", "Haz un test de velocidad",
                "El test mide también el bufferbloat (cuánto sube el ping al saturar la línea). Con ese dato el asistente puede recomendar QoS/SQM.",
                priority="baja", proposed="Abrir test de velocidad")
        return recs

    # ------------------------------------------------------------------ puntuación
    def _score(self, diag, natr, bench, speed) -> tuple[int, list[dict]]:
        score = 100
        factors = []

        def hit(points, text):
            nonlocal score
            score -= points
            factors.append({"points": -points, "text": text})

        checks = {ch["id"]: ch for ch in diag.get("checks", [])}
        for cid, label, pts in (("gateway", "Router", 15), ("internet", "Internet", 25), ("dns_local", "DNS de Bipon Net", 15), ("http", "Navegación", 10)):
            st = checks.get(cid, {}).get("status")
            if st == "fail":
                hit(pts, f"{label}: falla")
            elif st == "warn":
                hit(pts // 3, f"{label}: mejorable")
        ok = [b for b in bench if b.get("median") is not None]
        if ok:
            best = ok[0]["median"]
            if best > 60:
                hit(10, f"DNS lento desde tu zona ({best} ms)")
            elif best > 25:
                hit(4, f"DNS algo lento ({best} ms)")
        if natr.get("verdict", "").startswith("cgnat"):
            hit(5, "Detrás de CGNAT")
        elif natr.get("verdict", "").startswith("double_nat"):
            hit(5, "Doble NAT")
        if natr.get("nat", {}).get("type") == "symmetric":
            hit(3, "NAT estricto")
        if speed and speed.get("ping") and speed["ping"] > 50:
            hit(5, f"Ping alto en el test de velocidad ({speed['ping']} ms)")
        return max(0, score), factors

    def _summary(self, score, natr, bench, devices) -> str:
        level = "excelente" if score >= 90 else "buena" if score >= 75 else "mejorable" if score >= 50 else "con problemas"
        ok = [b for b in bench if b.get("median") is not None]
        parts = [f"Tu red está {level} ({score}/100) con {len(devices)} dispositivos detectados."]
        if ok:
            parts.append(f"El DNS más rápido desde tu ubicación es {ok[0]['name']} ({ok[0]['median']} ms).")
        if natr.get("label"):
            parts.append(f"Conectividad: {natr['label']}.")
        return " ".join(parts)

    # ------------------------------------------------------------------ aplicar
    async def apply(self, ids: list[str]) -> list[dict]:
        if not self.last:
            raise RuntimeError("Primero ejecuta el análisis")
        c = self.ctx
        results = []
        for rec in self.last["recommendations"]:
            if rec["id"] not in ids or not rec.get("apply"):
                continue
            a = rec["apply"]
            try:
                if a["type"] == "dns":
                    c.settings.update("dns", a["patch"])
                    c.cache.clear()
                elif a["type"] == "gaming":
                    c.settings.update("gaming", a["patch"])
                elif a["type"] == "unbound":
                    cfg = c.settings.get("unbound")
                    cfg.update(a["patch"])
                    ok, msg = await c.unbound.apply(cfg)
                    if not ok:
                        raise RuntimeError(msg)
                    c.apply_dns_settings()
                elif a["type"] == "list":
                    lst = a["list"]
                    lid = c.db.execute("INSERT OR IGNORE INTO blocklists(url,name,category,enabled) VALUES (?,?,?,1)",
                                       (lst["url"], lst["name"], lst["category"])).lastrowid
                    c.db.execute("UPDATE blocklists SET enabled=1 WHERE url=?", (lst["url"],))
                    asyncio.create_task(c.lists.update())
                elif a["type"] == "profile":
                    from .api import create_profile
                    create_profile(c, {"name": a["name"], "template": a["template"]})
                rec["applied"] = True
                results.append({"id": rec["id"], "ok": True})
                c.db.event("info", "asistente", f"Aplicado: {rec['title']}")
            except Exception as exc:
                results.append({"id": rec["id"], "ok": False, "error": str(exc)})
        return results
