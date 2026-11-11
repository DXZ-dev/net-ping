"""Perfiles de configuración: instantáneas con nombre de toda la configuración
para cambiar entre escenarios (p. ej. «Vacaciones», «Exámenes», «Pruebas»)
o probar cambios con vuelta atrás garantizada.

Al aplicar un perfil se guarda antes una copia automática del estado actual."""
from __future__ import annotations

import json
import time

from .sync import import_state

EXCLUDED = ("secret", "auth", "scheduler_state", "router_changes", "family", "setup")


def snapshot(ctx) -> dict:
    s = ctx.settings
    data = {k: v for k, v in s.export().items() if k not in EXCLUDED}
    return {
        "version": 2, "ts": time.time(), "settings": data,
        "rules": ctx.db.query("SELECT value, kind, comment, enabled FROM domain_rules"),
        "lists": ctx.db.query("SELECT url, name, category, enabled FROM blocklists"),
        "devices": ctx.db.query("SELECT mac, name, kind, profile, notes, paused, paused_until, parent, limit_down, limit_up FROM devices"),
    }


async def restore(ctx, snap: dict) -> dict:
    s = ctx.settings
    for k, v in (snap.get("settings") or {}).items():
        if k in EXCLUDED:
            continue
        s.set(k, v, notify=False)
    # import_state se encarga de reglas, listas y dispositivos y vuelve a aplicar DNS/filtros
    res = await import_state(ctx, {"settings": {}, "rules": snap.get("rules", []), "lists": snap.get("lists", []),
                                   "devices": snap.get("devices", [])})
    for d in snap.get("devices", []):
        ctx.db.execute("UPDATE devices SET parent=?, limit_down=?, limit_up=? WHERE mac=?",
                       (d.get("parent"), d.get("limit_down"), d.get("limit_up"), d["mac"]))
    ctx.devices.load()
    for cb in list(s._listeners):  # reaplica todas las secciones
        cb("*")
    if ctx.unbound.available:
        await ctx.unbound.apply(s.get("unbound"))
    await ctx.dhcp.restart()
    return res


def diff(a: dict, b: dict) -> list[dict]:
    """Diferencias de alto nivel entre dos instantáneas (para revisar antes de aplicar)."""
    out = []
    sa, sb = a.get("settings", {}), b.get("settings", {})
    for k in sorted(set(sa) | set(sb)):
        if json.dumps(sa.get(k), sort_keys=True) != json.dumps(sb.get(k), sort_keys=True):
            out.append({"section": k, "change": "modificado" if k in sa and k in sb else ("añadido" if k in sb else "eliminado")})
    for key in ("rules", "lists"):
        ka = {json.dumps(x, sort_keys=True) for x in a.get(key, [])}
        kb = {json.dumps(x, sort_keys=True) for x in b.get(key, [])}
        if ka != kb:
            out.append({"section": key, "change": f"+{len(kb - ka)} / -{len(ka - kb)}"})
    da = {d["mac"]: d for d in a.get("devices", [])}
    dbb = {d["mac"]: d for d in b.get("devices", [])}
    changed = sum(1 for m, d in dbb.items() if da.get(m) != d)
    if changed:
        out.append({"section": "devices", "change": f"{changed} dispositivo(s)"})
    return out


class ConfigProfiles:
    def __init__(self, ctx):
        self.ctx = ctx

    def list(self) -> list[dict]:
        return self.ctx.db.query("SELECT id, name, description, created, auto, length(data) size FROM config_profiles ORDER BY created DESC")

    def save(self, name: str, description: str = "", auto: bool = False, data: dict | None = None) -> dict:
        name = (name or "").strip()[:60]
        if not name:
            raise ValueError("Pon un nombre al perfil")
        data = data or snapshot(self.ctx)
        self.ctx.db.execute("INSERT INTO config_profiles(name,description,created,data,auto) VALUES (?,?,?,?,?) "
                            "ON CONFLICT(name) DO UPDATE SET description=excluded.description, created=excluded.created, data=excluded.data",
                            (name, description[:200], time.time(), json.dumps(data, ensure_ascii=False), 1 if auto else 0))
        # se conservan sólo las 10 copias automáticas más recientes
        self.ctx.db.execute("DELETE FROM config_profiles WHERE auto=1 AND id NOT IN (SELECT id FROM config_profiles WHERE auto=1 ORDER BY created DESC LIMIT 10)")
        return self.ctx.db.one("SELECT id, name, description, created, auto FROM config_profiles WHERE name=?", (name,))

    def get(self, pid: int) -> dict:
        row = self.ctx.db.one("SELECT * FROM config_profiles WHERE id=?", (pid,))
        if not row:
            raise KeyError("Perfil no encontrado")
        row["data"] = json.loads(row["data"])
        return row

    def compare(self, pid: int) -> list[dict]:
        return diff(snapshot(self.ctx), self.get(pid)["data"])

    async def apply(self, pid: int) -> dict:
        prof = self.get(pid)
        self.save(f"Auto · antes de «{prof['name']}» · {time.strftime('%d/%m %H:%M')}", "Copia automática previa", auto=True)
        res = await restore(self.ctx, prof["data"])
        self.ctx.settings.set("active_config_profile", {"id": pid, "name": prof["name"], "ts": time.time()}, notify=False)
        self.ctx.db.event("info", "configuración", f"Perfil de configuración aplicado: {prof['name']}")
        return res

    def delete(self, pid: int) -> None:
        self.ctx.db.execute("DELETE FROM config_profiles WHERE id=?", (pid,))
