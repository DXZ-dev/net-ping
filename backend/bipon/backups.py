"""Copias de seguridad: manuales y automáticas (diarias o semanales) con retención.

Las copias guardan configuración, reglas, listas y dispositivos con nombre/perfil (sin contraseñas de usuarios
ni claves VPN). Se escriben en <datos>/backups con permisos 600 y, opcionalmente, en una carpeta extra
(p. ej. un disco o NAS montado) para sobrevivir a la pérdida del equipo.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
import time
from pathlib import Path

from .config import DATA_DIR

log = logging.getLogger("bipon.backups")
DIR = DATA_DIR / "backups"
NAME_RE = re.compile(r"^bipon-\d{8}-\d{4}(-auto)?\.json$")


async def build(c) -> dict:
    from . import __version__
    return {"version": __version__, "created": time.time(), "settings": c.settings.export(),
            "rules": await c.db.aquery("SELECT value,kind,comment,enabled FROM domain_rules"),
            "lists": await c.db.aquery("SELECT url,name,category,enabled FROM blocklists"),
            "devices": await c.db.aquery("SELECT mac,name,kind,profile,notes FROM devices WHERE name<>'' OR profile<>'default' OR notes<>''")}


async def restore(c, body: dict) -> None:
    if "settings" not in body:
        raise ValueError("Copia de seguridad no válida")
    c.settings.import_(body["settings"])
    for r in body.get("rules", []):
        await c.db.aexecute("INSERT OR IGNORE INTO domain_rules(value,kind,comment,enabled,created) VALUES (?,?,?,?,?)",
                            (r["value"], r["kind"], r.get("comment", ""), r.get("enabled", 1), time.time()))
    for li in body.get("lists", []):
        await c.db.aexecute("INSERT OR IGNORE INTO blocklists(url,name,category,enabled) VALUES (?,?,?,?)",
                            (li["url"], li["name"], li.get("category", "ads"), li.get("enabled", 1)))
    for d in body.get("devices", []):
        await c.db.aexecute("INSERT INTO devices(mac,name,kind,profile,notes,first_seen,last_seen) VALUES (?,?,?,?,?,?,?) "
                            "ON CONFLICT(mac) DO UPDATE SET name=excluded.name, kind=excluded.kind, profile=excluded.profile, notes=excluded.notes",
                            (d["mac"], d.get("name", ""), d.get("kind", ""), d.get("profile", "default"), d.get("notes", ""), time.time(), 0))
    c.devices.load()
    asyncio.create_task(c.lists.update())


def cfg(c) -> dict:
    return {"enabled": True, "every": "daily", "keep": 14, "copy_to": "", **(c.settings.get("auto_backup") or {})}


def _write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as fh:
        fh.write(data)
    tmp.replace(path)


async def create(c, auto: bool = False) -> dict:
    data = json.dumps(await build(c), indent=1, ensure_ascii=False).encode()
    name = f"bipon-{time.strftime('%Y%m%d-%H%M')}{'-auto' if auto else ''}.json"
    _write(DIR / name, data)
    extra = cfg(c).get("copy_to")
    copied = None
    if extra:
        try:
            dest = Path(extra)
            if dest.is_dir():
                _write(dest / name, data)
                copied = str(dest / name)
        except OSError as exc:
            log.warning("no se pudo copiar la copia a %s: %s", extra, exc)
    prune(c)
    return {"name": name, "size": len(data), "copied_to": copied}


def listing() -> list[dict]:
    if not DIR.exists():
        return []
    out = []
    for p in DIR.iterdir():
        if NAME_RE.match(p.name):
            st = p.stat()
            out.append({"name": p.name, "size": st.st_size, "ts": st.st_mtime, "auto": p.name.endswith("-auto.json")})
    return sorted(out, key=lambda x: -x["ts"])


def path_of(name: str) -> Path:
    if not NAME_RE.match(name or ""):
        raise ValueError("Nombre de copia no válido")
    p = DIR / name
    if not p.exists():
        raise FileNotFoundError(name)
    return p


def prune(c) -> int:
    keep = max(1, int(cfg(c)["keep"]))
    autos = [b for b in listing() if b["auto"]]
    n = 0
    for b in autos[keep:]:
        try:
            (DIR / b["name"]).unlink()
            n += 1
        except OSError:
            pass
    return n


def due(c, now: float | None = None) -> bool:
    k = cfg(c)
    if not k["enabled"]:
        return False
    now = now or time.time()
    autos = [b for b in listing() if b["auto"]]
    every = 7 * 86400 if k["every"] == "weekly" else 86400
    return not autos or now - autos[0]["ts"] >= every - 600


async def loop(c) -> None:
    await asyncio.sleep(600)
    while True:
        try:
            if due(c):
                r = await create(c, auto=True)
                log.info("copia de seguridad automática: %s", r["name"])
        except Exception as exc:  # pragma: no cover
            log.warning("copia automática: %s", exc)
            c.db.event("warning", "sistema", f"Falló la copia de seguridad automática: {exc}")
        await asyncio.sleep(3600)


_ = shutil
