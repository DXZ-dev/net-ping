"""Región del usuario y asistente de configuración inicial (onboarding)."""
from __future__ import annotations

import asyncio
import logging
import os
import socket
import time
from pathlib import Path

from . import netinfo
from .catalog import RECOMMENDED_LISTS

log = logging.getLogger("bipon.region")

# listas regionales (formato Adblock: se aprovechan las reglas ||dominio^)
REGIONAL_LISTS = {
    "es": {"name": "EasyList Spanish (anuncios en español)", "url": "https://easylist-downloads.adblockplus.org/easylistspanish.txt"},
    "pt": {"name": "AdGuard Spanish/Portuguese", "url": "https://filters.adtidy.org/extension/ublock/filters/9.txt"},
    "de": {"name": "EasyList Germany", "url": "https://easylist.to/easylistgermany/easylistgermany.txt"},
    "fr": {"name": "Liste FR", "url": "https://easylist-downloads.adblockplus.org/liste_fr.txt"},
    "it": {"name": "EasyList Italy", "url": "https://easylist-downloads.adblockplus.org/easylistitaly.txt"},
}

# país → (nombre, zonas horarias, idioma de contenidos, región de juego AWS más cercana)
COUNTRIES: dict[str, tuple[str, list[str], str, str]] = {
    "DO": ("República Dominicana", ["America/Santo_Domingo"], "es", "us-east-1"),
    "MX": ("México", ["America/Mexico_City", "America/Monterrey", "America/Tijuana", "America/Cancun", "America/Hermosillo"], "es", "mx-central-1"),
    "ES": ("España", ["Europe/Madrid", "Atlantic/Canary"], "es", "eu-south-2"),
    "AR": ("Argentina", ["America/Argentina/Buenos_Aires"], "es", "sa-east-1"),
    "CO": ("Colombia", ["America/Bogota"], "es", "us-east-1"),
    "CL": ("Chile", ["America/Santiago"], "es", "sa-east-1"),
    "PE": ("Perú", ["America/Lima"], "es", "sa-east-1"),
    "VE": ("Venezuela", ["America/Caracas"], "es", "us-east-1"),
    "EC": ("Ecuador", ["America/Guayaquil"], "es", "us-east-1"),
    "BO": ("Bolivia", ["America/La_Paz"], "es", "sa-east-1"),
    "PY": ("Paraguay", ["America/Asuncion"], "es", "sa-east-1"),
    "UY": ("Uruguay", ["America/Montevideo"], "es", "sa-east-1"),
    "CR": ("Costa Rica", ["America/Costa_Rica"], "es", "us-east-1"),
    "PA": ("Panamá", ["America/Panama"], "es", "us-east-1"),
    "GT": ("Guatemala", ["America/Guatemala"], "es", "us-east-1"),
    "SV": ("El Salvador", ["America/El_Salvador"], "es", "us-east-1"),
    "HN": ("Honduras", ["America/Tegucigalpa"], "es", "us-east-1"),
    "NI": ("Nicaragua", ["America/Managua"], "es", "us-east-1"),
    "CU": ("Cuba", ["America/Havana"], "es", "us-east-1"),
    "PR": ("Puerto Rico", ["America/Puerto_Rico"], "es", "us-east-1"),
    "US": ("Estados Unidos", ["America/New_York", "America/Chicago", "America/Denver", "America/Los_Angeles", "America/Phoenix",
                              "America/Anchorage", "Pacific/Honolulu"], "en", "us-east-1"),
    "CA": ("Canadá", ["America/Toronto", "America/Vancouver", "America/Edmonton", "America/Winnipeg", "America/Halifax"], "en", "ca-central-1"),
    "BR": ("Brasil", ["America/Sao_Paulo", "America/Manaus", "America/Fortaleza"], "pt", "sa-east-1"),
    "PT": ("Portugal", ["Europe/Lisbon"], "pt", "eu-west-3"),
    "GB": ("Reino Unido", ["Europe/London"], "en", "eu-west-2"),
    "IE": ("Irlanda", ["Europe/Dublin"], "en", "eu-west-1"),
    "FR": ("Francia", ["Europe/Paris"], "fr", "eu-west-3"),
    "DE": ("Alemania", ["Europe/Berlin"], "de", "eu-central-1"),
    "IT": ("Italia", ["Europe/Rome"], "it", "eu-south-1"),
    "NL": ("Países Bajos", ["Europe/Amsterdam"], "en", "eu-west-1"),
    "OTHER": ("Otro país", [], "en", "us-east-1"),
}


def available_timezones() -> list[str]:
    try:
        import zoneinfo
        return sorted(zoneinfo.available_timezones())
    except Exception:
        return sorted({tz for c in COUNTRIES.values() for tz in c[1]})


def apply_timezone(tz: str | None) -> None:
    """Aplica la zona horaria al proceso (horarios del control parental, informes…)."""
    if not tz:
        return
    if not Path(f"/usr/share/zoneinfo/{tz}").exists():
        log.warning("zona horaria desconocida: %s", tz)
        return
    os.environ["TZ"] = tz
    try:
        time.tzset()
    except AttributeError:  # pragma: no cover
        pass


async def detect_country() -> str | None:
    import httpx
    try:
        async with httpx.AsyncClient(timeout=3) as c:
            r = await c.get("https://1.1.1.1/cdn-cgi/trace")
            kv = dict(line.split("=", 1) for line in r.text.splitlines() if "=" in line)
            return kv.get("loc")
    except Exception:
        return None


def network_info() -> dict:
    ifs = netinfo.interfaces()
    return {"hostname": socket.gethostname(), "interfaces": ifs, "ip": ifs[0]["ip"] if ifs else netinfo.primary_ip(),
            "gateway": netinfo.default_gateway(), "dns_of_host": netinfo.dns_servers_of_host()}


def countries_payload() -> list[dict]:
    return [{"code": k, "name": v[0], "timezones": v[1], "lang": v[2]} for k, v in COUNTRIES.items()]


async def complete_setup(ctx, body: dict) -> dict:
    """Aplica las elecciones del asistente inicial."""
    s = ctx.settings
    country = (body.get("country") or "OTHER").upper()
    tz = body.get("timezone")
    meta = COUNTRIES.get(country, COUNTRIES["OTHER"])
    lang = meta[2]
    mode = body.get("mode", "recommended")
    connection = body.get("connection", "router")
    notes: list[str] = []

    s.set("region", {"country": country, "timezone": tz, "lang": lang, "game_region": meta[3],
                     "owner": {"name": (body.get("name") or "")[:60], "email": (body.get("email") or "")[:120]}}, notify=False)
    apply_timezone(tz)


    # listas: SÓLO las que el usuario marcó en el registro
    chosen = set(body.get("lists") or [])
    regional = REGIONAL_LISTS.get(lang)
    catalog = list(RECOMMENDED_LISTS) + ([{**regional, "category": "ads"}] if regional else [])
    for l in catalog:
        on = 1 if l["url"] in chosen else 0
        ctx.db.execute("INSERT INTO blocklists(url,name,category,enabled) VALUES (?,?,?,?) "
                       "ON CONFLICT(url) DO UPDATE SET enabled=excluded.enabled", (l["url"], l["name"], l["category"], on))
    if chosen:
        asyncio.create_task(ctx.lists.update())
    else:
        await ctx.lists.rebuild()

    if mode in ("recommended", "auto"):
        s.update("dns", {"optimistic_cache": True, "prefetch": True, "strategy": "fastest", "cache_min_ttl": 60, "block_doh_canary": True,
                         "auto_optimize": {**(s.get("dns.auto_optimize") or {}), "enabled": True, "interval_hours": 24}})
        s.update("gaming", {"enabled": True, "protect": True})
        s.update("security", {"anomaly_detection": True, "rebind_protection": True})
        s.set("quality", {"enabled": True, "interval": 300, "targets": []}, notify=False)
        s.set("speed_schedule", {"enabled": True, "time": "04:13", "days": list(range(7))}, notify=False)
        s.set("reports", {"enabled": True, "days": [0], "time": "09:00"}, notify=False)
        notes.append("Configuración recomendada aplicada: caché optimista, modo gaming, seguridad, test de velocidad nocturno e informes semanales.")
    from .api import create_profile
    names = [p["id"] for p in s.get("profiles")]
    if body.get("family") and not any(n.startswith("ninos") for n in names):
        create_profile(ctx, {"name": "Niños", "template": "kids"})

    if connection == "auto":
        s.set("autodns", {**(s.get("autodns") or {}), "enabled": True}, notify=False)
        notes.append("Modo automático activado: los dispositivos usarán Bipon Net sin tocar el router (vía IPv6).")
    elif connection == "dhcp":
        others = [o for o in await ctx.dhcp.probe() if not o.get("own") and not o.get("error")]
        sug = ctx.dhcp.suggest()
        if others:
            s.set("dhcp", {**sug, "enabled": False, "static": []}, notify=False)
            notes.append(f"El router ({others[0]['server']}) tiene su DHCP activo: desactívalo y después activa el DHCP de Bipon Net en «DHCP».")
        else:
            s.set("dhcp", {**sug, "enabled": True, "static": [], "authoritative": True}, notify=False)
            await ctx.dhcp.restart()
            notes.append("Servidor DHCP de Bipon Net activado.")

    if mode == "auto":
        async def run_advisor():
            try:
                r = await ctx.advisor.analyze()
                await ctx.advisor.apply([x["id"] for x in r["recommendations"] if x.get("auto")])
            except Exception as exc:  # pragma: no cover
                log.warning("autoconfiguración: %s", exc)
        asyncio.create_task(run_advisor())
        notes.append("Autoconfiguración en curso: el asistente está analizando tu red y aplicará lo mejor para ti (≈1 min).")

    s.set("setup", {"completed": True, "mode": mode, "connection": connection, "ts": time.time()}, notify=False)
    ctx.apply_dns_settings()
    return {"ok": True, "notes": notes}
