"""«¿Algo dejó de funcionar?»: dominios bloqueados que probablemente rompen una app.

Una app que no llega a un dominio que necesita lo reintenta muchas veces seguidas; un anuncio
simplemente no se muestra. Se buscan dominios bloqueados por listas o reglas con muchos reintentos
del mismo equipo en las últimas horas, se descartan los que son claramente publicidad/rastreo y
nunca se proponen los bloqueados por seguridad (malware, phishing).
"""
from __future__ import annotations

import re
import time

from .dns.filter import ST_LIST, ST_RULE

AD_LIKE = re.compile(r"(^|[.-])(ads?|adserver|adservice|advert|doubleclick|track(ing|er)?|analytics|metrics|telemetry|pixel|beacon|"
                     r"stats?|counter|affiliate|banner|promo|sponsor|crashlytics|appsflyer|adjust|branch|segment|mixpanel)([.-]|\d|$)")
# dominios que las listas bloquean a menudo y que algunas apps necesitan de verdad
KNOWN_BREAKERS = {
    "s.youtube.com": "Historial y «seguir viendo» de YouTube",
    "graph.facebook.com": "Inicio de sesión con Facebook en otras apps",
    "graph.instagram.com": "Funciones de Instagram",
    "clients4.google.com": "Sincronización de Chrome / servicios de Google",
    "www.googleadservices.com": "Enlaces patrocinados en los resultados de Google",
    "click.redditmail.com": "Enlaces de los correos de Reddit",
    "dl.dropboxusercontent.com": "Descargas de Dropbox",
    "cdn.optimizely.com": "Webs que no cargan sin este script",
    "app.link": "Enlaces que abren apps (branch.io)",
    "api.segment.io": "Algunas apps no arrancan sin él",
    "firebaselogging.googleapis.com": "Apps Android con Firebase",
    "msedge.net": "Actualizaciones y funciones de Microsoft Edge",
    "login.live.com": "Inicio de sesión de Microsoft",
}
MIN_RETRIES = 15


def suggestions(ctx, hours: int = 3, limit: int = 12) -> list[dict]:
    db = ctx.db
    db.flush_queries()
    since = time.time() - hours * 3600
    rows = db.query(f"""SELECT domain, COALESCE(mac, client) who, MAX(client) client, COUNT(*) n, MAX(reason) reason, MAX(ts) last
                       FROM queries WHERE ts>=? AND status IN ({ST_LIST}, {ST_RULE})
                       GROUP BY domain, who HAVING n >= ? ORDER BY n DESC LIMIT 400""", (since, MIN_RETRIES))
    dismissed = set(ctx.settings.get("dismissed_suggestions") or [])
    names = {}
    for m, d in ctx.devices.devices.items():
        nm = d.get("name") or d.get("friendly") or d.get("hostname") or d.get("ip") or m
        names[m] = nm
        if d.get("ip"):
            names[d["ip"]] = nm
    agg: dict[str, dict] = {}
    for r in rows:
        dom = r["domain"]
        reason = (r["reason"] or "").lower()
        if dom in dismissed or "seguridad" in reason or "adult" in reason:
            continue
        known = next((v for k, v in KNOWN_BREAKERS.items() if dom == k or dom.endswith("." + k)), None)
        if not known and AD_LIKE.search(dom):
            continue
        a = agg.setdefault(dom, {"domain": dom, "retries": 0, "devices": [], "reason": r["reason"], "known": known, "last": 0})
        a["retries"] += r["n"]
        a["last"] = max(a["last"], r["last"])
        who = names.get(r["who"]) or names.get(r["client"]) or r["client"]
        if who not in a["devices"]:
            a["devices"].append(who)
    out = sorted(agg.values(), key=lambda x: (x["known"] is None, -x["retries"]))
    return out[:limit]
