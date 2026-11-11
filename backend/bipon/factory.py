"""Restablecer de fábrica y reiniciar Bipon.

Nada se borra de verdad: la configuración y los datos se mueven a <datos>/backups/factory-<fecha>/ (además
de una copia normal), así un restablecimiento por error se puede deshacer. Después Bipon se reinicia en su
propio proceso y aparece el asistente de primera configuración.
"""
from __future__ import annotations

import asyncio
import logging
import os
import shutil
import sys
import time
from pathlib import Path

from .config import DATA_DIR

log = logging.getLogger("bipon.factory")
# descargas que no son configuración: se conservan para no volver a bajarlas
KEEP = {"backups", "manuf.txt", "geoip-country.csv.gz"}
KEEP_FACTORY_DIRS = 2


def restart_process(delay: float = 1.5) -> None:
    """Reinicia Bipon reemplazando el proceso (funciona con systemd, Docker o a mano)."""
    def _exec():
        log.warning("reiniciando Bipon Net")
        logging.shutdown()
        os.execv(sys.executable, [sys.executable, "-m", "bipon"])
    asyncio.get_running_loop().call_later(delay, _exec)


def _prune_old(base: Path) -> None:
    olds = sorted(p for p in base.glob("factory-*") if p.is_dir())
    for p in olds[:-KEEP_FACTORY_DIRS]:
        shutil.rmtree(p, ignore_errors=True)


def move_data(data_dir: Path = DATA_DIR) -> Path:
    dest = data_dir / "backups" / f"factory-{time.strftime('%Y%m%d-%H%M%S')}"
    dest.mkdir(parents=True, exist_ok=True)
    os.chmod(dest, 0o700)
    for p in data_dir.iterdir():
        if p.name in KEEP:
            continue
        shutil.move(str(p), str(dest / p.name))
    _prune_old(data_dir / "backups")
    return dest


async def reset(c, user: str) -> dict:
    from . import backups
    try:
        await backups.create(c)
    except Exception as exc:  # pragma: no cover
        log.warning("copia previa al restablecimiento: %s", exc)
    c.db.event("warning", "sistema", f"Restablecimiento de fábrica solicitado por {user}")
    # se deshace lo que Bipon cambió en la red, para que nadie se quede sin Internet al reiniciar
    try:
        if c.router.available:
            from .router import _run
            await _run("nft", "delete", "table", "inet", "bipon")
            lan = c.router.cfg().get("lan")
            if lan and shutil.which("tc"):
                await _run("tc", "qdisc", "del", "dev", lan, "root")
                await _run("tc", "qdisc", "del", "dev", lan, "ingress")
    except Exception:  # pragma: no cover
        pass
    try:
        await asyncio.wait_for(c.stop(), 15)
    except Exception as exc:  # pragma: no cover
        log.warning("parada antes del restablecimiento: %s", exc)
    try:
        c.db.conn.close()
    except Exception:  # pragma: no cover
        pass
    dest = move_data()
    restart_process()
    return {"ok": True, "saved_to": str(dest),
            "message": "Bipon se está restableciendo y reiniciará en unos segundos. Verás el asistente de primera configuración."}
