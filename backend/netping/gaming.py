"""Modo Gaming y Streaming: latencia mínima y cero interrupciones.

- Mantiene en caché (pre-resueltos y refrescados) los dominios críticos de
  juegos online y plataformas de streaming: el DNS nunca añade espera.
- Protege esos dominios de bloqueos accidentales de las listas.
- Mide la latencia a las regiones de servidores de juego y la proximidad de
  las CDN de streaming según el DNS usado.
- QoS CAKE (control de bufferbloat) cuando NetPing actúa como router.
"""
from __future__ import annotations

import asyncio
import logging
import re
import shutil
import statistics
import time

from dnslib import QTYPE, DNSRecord

from .catalog import DNS_PRESETS
from .dns.cache import DNSCache
from .dns.upstream import query_server
from .pinger import ping, tcp_ping

log = logging.getLogger("netping.gaming")

# Nombres concretos que consultan juegos/plataformas al iniciar sesión, buscar
# partida o reproducir. Se mantienen siempre calientes en caché.
PRIORITY_DOMAINS: dict[str, list[str]] = {
    "Steam / Valve (CS2, Dota 2)": ["api.steampowered.com", "steamcommunity.com", "store.steampowered.com",
                                    "cm.steampowered.com", "client-update.akamai.steamstatic.com", "steamcdn-a.akamaihd.net"],
    "Epic Games / Fortnite": ["account-public-service-prod.ol.epicgames.com", "fortnite-public-service-prod11.ol.epicgames.com",
                              "launcher-public-service-prod06.ol.epicgames.com", "datarouter.ol.epicgames.com", "download.epicgames.com"],
    "Riot (Valorant, LoL)": ["auth.riotgames.com", "clientconfig.rpg.riotgames.com", "entitlements.auth.riotgames.com",
                             "riot-client.secure.dyn.riotcdn.net"],
    "Xbox / Call of Duty / Minecraft": ["xsts.auth.xboxlive.com", "user.auth.xboxlive.com", "title.mgt.xboxlive.com",
                                        "sessiondirectory.xboxlive.com", "profile.callofduty.com", "api.minecraftservices.com",
                                        "sessionserver.mojang.com"],
    "PlayStation Network": ["auth.api.playstation.com", "np.community.playstation.net", "ps4.np.playstation.net",
                            "gs2.ww.prod.dl.playstation.net"],
    "Nintendo": ["accounts.nintendo.com", "api.accounts.nintendo.com", "ctest.cdn.nintendo.net"],
    "Roblox": ["www.roblox.com", "apis.roblox.com", "gamejoin.roblox.com", "assetdelivery.roblox.com"],
    "EA / Battle.net / Ubisoft": ["accounts.ea.com", "gateway.ea.com", "eu.actual.battle.net", "us.actual.battle.net",
                                  "public-ubiservices.ubi.com"],
    "Discord (voz)": ["discord.com", "gateway.discord.gg", "cdn.discordapp.com", "media.discordapp.net", "latency.discord.media"],
    "YouTube": ["www.youtube.com", "youtubei.googleapis.com", "i.ytimg.com", "yt3.ggpht.com", "redirector.googlevideo.com",
                "manifest.googlevideo.com", "www.googleapis.com"],
    "Netflix": ["www.netflix.com", "api-global.netflix.com", "ichnaea-web.netflix.com", "assets.nflxext.com", "occ-0-1-1.1.nflxso.net"],
    "Twitch / Kick": ["www.twitch.tv", "gql.twitch.tv", "usher.ttvnw.net", "static-cdn.jtvnw.net", "video-weaver.fra05.hls.ttvnw.net", "kick.com"],
    "Disney+ / Prime / Max": ["www.disneyplus.com", "disney.api.edge.bamgrid.com", "www.primevideo.com", "atv-ps.amazon.com",
                              "play.max.com", "default.any-any.prd.api.max.com"],
    "Spotify": ["apresolve.spotify.com", "spclient.wg.spotify.com", "api.spotify.com", "audio-ak-spotify-com.akamaized.net"],
}

# Dominios base que nunca deben bloquearse por listas (sí por control parental)
PROTECTED_BASES = [
    "steampowered.com", "steamcommunity.com", "steamstatic.com", "steamcontent.com", "steamserver.net", "valvesoftware.com",
    "epicgames.com", "epicgames.dev", "unrealengine.com", "riotgames.com", "riotcdn.net", "leagueoflegends.com",
    "xboxlive.com", "xbox.com", "playstation.net", "playstation.com", "nintendo.net", "nintendo.com", "roblox.com", "rbxcdn.com",
    "ea.com", "battle.net", "blizzard.com", "ubi.com", "ubisoft.com", "callofduty.com", "activision.com", "mojang.com",
    "minecraftservices.com", "playfabapi.com", "discord.com", "discord.gg", "discordapp.com", "discordapp.net", "discord.media",
    "googlevideo.com", "ytimg.com", "youtubei.googleapis.com", "nflxvideo.net", "nflxso.net", "nflxext.com", "netflix.com",
    "ttvnw.net", "jtvnw.net", "twitch.tv", "bamgrid.com", "disneyplus.com", "dssott.com", "aiv-cdn.net", "primevideo.com",
    "spotify.com", "scdn.co", "spotifycdn.com", "easyanticheat.net", "battleye.com",
]

# Regiones de servidores (AWS: infraestructura usada por GameLift y gran parte de los juegos online)
GAME_REGIONS = [
    ("us-east-1", "EE. UU. Este (Virginia)"), ("us-east-2", "EE. UU. Este (Ohio)"), ("us-west-1", "EE. UU. Oeste (California)"),
    ("us-west-2", "EE. UU. Oeste (Oregón)"), ("ca-central-1", "Canadá (Montreal)"), ("mx-central-1", "México (Querétaro)"),
    ("sa-east-1", "Brasil (São Paulo)"), ("eu-west-1", "Europa (Irlanda)"), ("eu-west-2", "Europa (Londres)"),
    ("eu-west-3", "Europa (París)"), ("eu-central-1", "Europa (Fráncfort)"), ("eu-south-2", "Europa (España)"),
    ("eu-north-1", "Europa (Estocolmo)"), ("me-central-1", "Oriente Medio (EAU)"), ("ap-south-1", "Asia (Bombay)"),
    ("ap-northeast-1", "Asia (Tokio)"), ("ap-southeast-1", "Asia (Singapur)"), ("ap-southeast-2", "Oceanía (Sídney)"),
    ("af-south-1", "África (Ciudad del Cabo)"),
]

# Hostnames de CDN para medir a qué nodo te envía cada DNS
CDN_PROBES = [
    ("YouTube / Google", "www.youtube.com"), ("Netflix", "www.netflix.com"), ("Akamai (Steam, PSN, Xbox)", "a248.e.akamai.net"),
    ("Amazon CloudFront (Prime, Twitch)", "d1.awsstatic.com"), ("Fastly", "www.fastly.com"),
]


class GamingManager:
    def __init__(self, ctx):
        self.ctx = ctx
        self.last_warm = 0.0
        self.warm_ok = 0
        self.warm_fail = 0
        self.qos_note = ""

    # ---------------------------------------------------------- configuración
    def cfg(self) -> dict:
        return self.ctx.settings.get("gaming") or {}

    def domains(self) -> list[str]:
        cfg = self.cfg()
        groups = cfg.get("groups") or list(PRIORITY_DOMAINS)
        out = []
        for g in groups:
            out += PRIORITY_DOMAINS.get(g, [])
        for d in cfg.get("custom_domains") or []:
            d = d.strip().lower()
            if d and re.match(r"^[a-z0-9.-]+$", d):
                out.append(d)
        return sorted(set(out))

    def protected(self) -> set[str]:
        cfg = self.cfg()
        if not cfg.get("enabled", True) or not cfg.get("protect", True):
            return set()
        return set(PROTECTED_BASES) | {d.strip().lower() for d in cfg.get("custom_domains") or [] if d.strip()}

    def apply(self) -> None:
        self.ctx.engine.protected = self.protected()

    # ---------------------------------------------------------- precalentado
    async def warm(self) -> dict:
        """Resuelve y cachea (A + AAAA) todos los dominios prioritarios que caducan pronto."""
        dns = self.ctx.dns
        cache = self.ctx.cache
        names = self.domains()
        ok = fail = skipped = 0
        sem = asyncio.Semaphore(16)

        async def one(name: str, qtype: int):
            nonlocal ok, fail, skipped
            key = DNSCache.key(name, qtype)
            e = cache._d.get(key)
            if e and e.expires - time.monotonic() > max(15, e.ttl * 0.25):
                skipped += 1
                return
            async with sem:
                try:
                    resp, _, _ = await dns.pool.resolve(DNSRecord.question(name, QTYPE[qtype]).pack())
                    cache.put(key, DNSRecord.parse(resp), resp)
                    if key in cache._d:
                        cache._d[key].hits = max(cache._d[key].hits, 3)  # candidato a prefetch
                    ok += 1
                except Exception:
                    fail += 1

        await asyncio.gather(*(one(n, t) for n in names for t in (QTYPE.A, QTYPE.AAAA)))
        self.last_warm = time.time()
        self.warm_ok, self.warm_fail = ok, fail
        return {"domains": len(names), "refreshed": ok, "failed": fail, "fresh": skipped}

    async def loop(self) -> None:
        await asyncio.sleep(20)
        while True:
            if self.cfg().get("enabled", True):
                try:
                    await self.warm()
                except Exception:  # pragma: no cover
                    log.exception("precalentado falló")
            await asyncio.sleep(30)

    def status(self) -> dict:
        cache = self.ctx.cache
        names = self.domains()
        now = time.monotonic()
        hot = sum(1 for n in names if (e := cache._d.get(DNSCache.key(n, QTYPE.A))) and (e.expires > now or cache.optimistic))
        return {"config": self.cfg(), "groups": {g: v for g, v in PRIORITY_DOMAINS.items()}, "domains": len(names), "hot": hot,
                "last_warm": self.last_warm, "warm_ok": self.warm_ok, "warm_fail": self.warm_fail,
                "protected": len(self.protected()), "qos": self.qos_note}

    # ---------------------------------------------------------- mediciones
    async def regions(self) -> list[dict]:
        async def one(code, name):
            r = await tcp_ping(f"dynamodb.{code}.amazonaws.com", 443, count=4, timeout=2.0)
            return {"region": code, "name": name, **r}
        res = await asyncio.gather(*(one(c, n) for c, n in GAME_REGIONS))
        res.sort(key=lambda x: x["min"] if x["min"] is not None else 1e9)
        for r in res:
            m = r["min"]
            r["quality"] = None if m is None else ("excelente" if m < 30 else "buena" if m < 60 else "aceptable" if m < 100 else "alta")
        return res

    async def cdn(self, resolvers: list[dict] | None = None) -> dict:
        """Para cada DNS: resuelve hosts de CDN y mide la latencia al nodo devuelto."""
        if resolvers is None:
            resolvers = [{"id": k, "name": DNS_PRESETS[k]["name"], "server": DNS_PRESETS[k]["servers"][0]}
                         for k in ("cloudflare", "google", "quad9", "opendns", "adguard")]
            if self.ctx.unbound.running:
                resolvers.insert(0, {"id": "unbound", "name": DNS_PRESETS["unbound"]["name"],
                                     "server": f"127.0.0.1:{self.ctx.settings.get('unbound.port', 5335)}"})
        ip_cache: dict[str, dict] = {}

        async def node_latency(ip: str) -> float | None:
            if ip not in ip_cache:
                r = await ping(ip, count=3)
                if not r["received"]:
                    r = await tcp_ping(ip, 443, 3)
                ip_cache[ip] = r
            return ip_cache[ip]["avg"]

        async def per_resolver(res):
            rows = []
            for label, host in CDN_PROBES:
                try:
                    resp, _ = await query_server(res["server"], DNSRecord.question(host, "A").pack(), timeout=3)
                    ips = [str(r.rdata) for r in DNSRecord.parse(resp).rr if r.rtype == QTYPE.A]
                except Exception:
                    ips = []
                lat = await node_latency(ips[0]) if ips else None
                rows.append({"cdn": label, "host": host, "ip": ips[0] if ips else None, "latency": lat})
            vals = [r["latency"] for r in rows if r["latency"] is not None]
            return {**res, "probes": rows, "avg": round(statistics.fmean(vals), 2) if vals else None, "ok": len(vals)}

        results = await asyncio.gather(*(per_resolver(r) for r in resolvers))
        results.sort(key=lambda r: r["avg"] if r["avg"] is not None else 1e9)
        return {"results": results, "best": results[0] if results and results[0]["avg"] is not None else None}

    # ---------------------------------------------------------- QoS (CAKE)
    @staticmethod
    async def _tc(*args) -> tuple[int, str]:
        proc = await asyncio.create_subprocess_exec("tc", *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
        out, _ = await proc.communicate()
        return proc.returncode or 0, out.decode(errors="ignore")

    async def qos_status(self) -> dict:
        if not shutil.which("tc"):
            return {"available": False}
        code, out = await self._tc("-s", "qdisc", "show")
        cake = [l for l in out.splitlines() if " cake " in l]
        return {"available": True, "active": cake, "raw": out[-3000:]}

    async def qos_apply(self, iface: str, egress_mbit: float, overhead: str = "ethernet", remove: bool = False) -> dict:
        if not shutil.which("tc"):
            raise RuntimeError("tc (iproute2) no está instalado")
        if not re.match(r"^[a-zA-Z0-9_.-]{1,15}$", iface):
            raise ValueError("interfaz inválida")
        if remove:
            code, out = await self._tc("qdisc", "del", "dev", iface, "root")
            return {"ok": code == 0, "message": out.strip() or "QoS eliminado"}
        if not 0.5 <= egress_mbit <= 100000:
            raise ValueError("ancho de banda inválido")
        oh = {"ethernet": ["ethernet"], "pppoe": ["pppoe-ptm"], "docsis": ["docsis"], "fiber": ["ethernet"]}.get(overhead, ["ethernet"])
        args = ["qdisc", "replace", "dev", iface, "root", "cake", "bandwidth", f"{egress_mbit}mbit", "diffserv4",
                "dual-srchost", "nat", "ack-filter", *oh]
        code, out = await self._tc(*args)
        if code != 0:
            return {"ok": False, "message": out.strip() or "tc falló (¿módulo sch_cake disponible y CAP_NET_ADMIN?)"}
        self.qos_note = f"CAKE en {iface} a {egress_mbit} Mbit/s"
        self.ctx.db.event("info", "qos", f"QoS CAKE activado en {iface} ({egress_mbit} Mbit/s)")
        return {"ok": True, "message": self.qos_note}
