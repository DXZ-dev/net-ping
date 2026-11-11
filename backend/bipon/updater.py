"""Actualizaciones desde GitHub.

* Linux / Proxmox LXC (instalación con install.sh): descarga la nueva versión
  (git), reinstala dependencias y reinicia el servicio. Los datos se conservan.
* Docker: un contenedor no debe reemplazarse a sí mismo; se indica el comando
  (o usar Watchtower) y se avisa cuando hay versión nueva.
"""
from __future__ import annotations

import asyncio
import logging
import os
import shutil
import subprocess
import time
from datetime import datetime
from pathlib import Path

import httpx

from . import __version__

log = logging.getLogger("bipon.updater")
DEFAULT_REPO = "DXZ-dev/net-ping"
DEFAULT_BRANCH = "main"
HISTORY_MAX = 10


def source_dir() -> Path | None:
    for p in (os.environ.get("BIPON_SOURCE_DIR"), "/opt/bipon-net/src", str(Path(__file__).resolve().parents[2])):
        if p and (Path(p) / ".git").exists():
            return Path(p)
    return None


def install_mode() -> str:
    if Path("/.dockerenv").exists() or os.environ.get("container") == "docker":
        return "docker"
    if source_dir() and (shutil.which("systemd-run") or shutil.which("nohup")) and Path("/etc/systemd/system/bipon-net.service").exists():
        return "systemd"
    if source_dir():
        return "git"
    return "unknown"


def local_commit() -> str | None:
    src = source_dir()
    if src:
        try:
            return subprocess.run(["git", "-C", str(src), "rev-parse", "HEAD"], capture_output=True, text=True, timeout=5).stdout.strip() or None
        except Exception:
            pass
    f = Path(__file__).resolve().parents[2] / "COMMIT"
    return f.read_text().strip() if f.exists() else None


class Updater:
    def __init__(self, ctx):
        self.ctx = ctx
        self.last_check: dict = {}
        self.running = False

    def cfg(self) -> dict:
        c = self.ctx.settings.get("updates") or {}
        c.setdefault("auto", False)
        c.setdefault("repo", DEFAULT_REPO)
        c.setdefault("branch", "")
        c.setdefault("token", "")
        c.setdefault("time", "03:41")
        return c

    async def check(self) -> dict:
        c = self.cfg()
        headers = {"accept": "application/vnd.github+json", "user-agent": "Bipon-Net-Updater"}
        if c.get("token"):
            headers["authorization"] = f"Bearer {c['token']}"
        async with httpx.AsyncClient(timeout=15, headers=headers) as h:
            r = await h.get(f"https://api.github.com/repos/{c['repo']}")
            if r.status_code == 404:
                raise RuntimeError("Repositorio no encontrado. Si es privado, añade un token de GitHub de sólo lectura.")
            r.raise_for_status()
            branch = c.get("branch") or DEFAULT_BRANCH
            cm = (await h.get(f"https://api.github.com/repos/{c['repo']}/commits/{branch}")).json()
            rel = await h.get(f"https://api.github.com/repos/{c['repo']}/releases/latest")
            ver = None
            vr = await h.get(f"https://api.github.com/repos/{c['repo']}/contents/backend/bipon/__init__.py", params={"ref": branch},
                             headers={"accept": "application/vnd.github.raw"})
            if vr.status_code == 200:
                import re
                m = re.search(r'__version__\s*=\s*"([^"]+)"', vr.text)
                ver = m.group(1) if m else None
        remote_sha = cm.get("sha")
        local = local_commit()
        available = bool(remote_sha and (local != remote_sha if local else ver and ver != __version__))
        self.last_check = {
            "ts": time.time(), "branch": branch, "remote_commit": remote_sha, "remote_version": ver,
            "remote_message": ((cm.get("commit") or {}).get("message") or "").split("\n")[0][:200],
            "remote_date": ((cm.get("commit") or {}).get("committer") or {}).get("date"),
            "release": rel.json().get("tag_name") if rel.status_code == 200 else None,
            "local_commit": local, "local_version": __version__, "available": available, "mode": install_mode(),
        }
        return self.last_check

    def history(self) -> list[dict]:
        return list((self.ctx.settings.get("updates") or {}).get("history") or [])

    def _remember_current(self) -> None:
        """Guarda la versión que está instalada ahora para poder volver a ella."""
        cur = local_commit()
        if not cur:
            return
        hist = [h for h in self.history() if h.get("commit") != cur]
        hist.insert(0, {"commit": cur, "version": __version__, "ts": time.time()})
        upd = dict(self.ctx.settings.get("updates") or {})
        upd["history"] = hist[:HISTORY_MAX]
        self.ctx.settings.set("updates", upd, notify=False)

    async def _backup_first(self, why: str) -> None:
        try:
            from . import backups
            await backups.create(self.ctx)
        except Exception as exc:  # pragma: no cover
            log.warning("copia previa a %s: %s", why, exc)

    async def rollback(self, commit: str | None = None) -> dict:
        """Vuelve a una versión instalada antes (por defecto, la inmediatamente anterior)."""
        cur = local_commit()
        hist = [h for h in self.history() if h.get("commit") and h["commit"] != cur]
        if commit:
            hist = [h for h in hist if h["commit"].startswith(commit)]
        if not hist and not commit and source_dir():
            # sin historial (instalado antes de que existiera): se vuelve al cambio anterior del repositorio
            try:
                prev = subprocess.run(["git", "-C", str(source_dir()), "rev-parse", "HEAD~1"], capture_output=True, text=True, timeout=5).stdout.strip()
            except Exception:
                prev = ""
            if prev:
                hist = [{"commit": prev}]
        if not hist:
            return {"ok": False, "mode": install_mode(), "message": "No hay ninguna versión anterior registrada en este equipo."}
        target = hist[0]["commit"]
        return await self.apply(target=target)

    async def apply(self, target: str | None = None) -> dict:
        mode = install_mode()
        info = {"branch": self.cfg().get("branch") or DEFAULT_BRANCH, "remote_commit": target} if target else (self.last_check or await self.check())
        if mode == "docker" and target:
            return {"ok": False, "mode": mode,
                    "message": f"En Docker se vuelve atrás desde el equipo anfitrión: git checkout {target[:12]} && docker compose up -d --build"}
        if mode == "docker":
            return {"ok": False, "mode": mode,
                    "message": "En Docker la actualización se hace desde el equipo anfitrión: "
                               "git pull && docker compose up -d --build   (o activa Watchtower para hacerlo automático)."}
        src = source_dir()
        if not src:
            return {"ok": False, "mode": mode, "message": "No se encontró la copia git de Bipon Net. Reinstala con deploy/linux/install.sh."}
        script = src / "deploy" / "linux" / "update.sh"
        env = {**os.environ, "BIPON_BRANCH": info.get("branch") or DEFAULT_BRANCH, "BIPON_REPO": self.cfg().get("repo") or DEFAULT_REPO}
        if target:
            if not all(ch in "0123456789abcdef" for ch in target) or not 7 <= len(target) <= 40:
                return {"ok": False, "mode": mode, "message": "Versión no válida"}
            env["BIPON_TARGET"] = target
        c = self.cfg()
        if c.get("token"):
            env["BIPON_GIT_TOKEN"] = c["token"]
        await self._backup_first("actualizar")
        self._remember_current()
        log_file = "/var/log/bipon-update.log"
        if shutil.which("systemd-run"):
            args = ["systemd-run", "--unit", f"bipon-update-{int(time.time())}", "--collect",
                    f"--setenv=BIPON_BRANCH={env['BIPON_BRANCH']}", f"--setenv=BIPON_REPO={env['BIPON_REPO']}"]
            if target:
                args.append(f"--setenv=BIPON_TARGET={target}")
            if c.get("token"):
                args.append(f"--setenv=BIPON_GIT_TOKEN={c['token']}")
            args += ["/bin/bash", "-c", f"bash {script} >> {log_file} 2>&1"]
            proc = await asyncio.create_subprocess_exec(*args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
            out, _ = await proc.communicate()
            if proc.returncode != 0:
                return {"ok": False, "mode": mode, "message": out.decode(errors="ignore")[:300]}
        else:
            subprocess.Popen(["nohup", "bash", str(script)], env=env, stdout=open(log_file, "a"), stderr=subprocess.STDOUT,
                             start_new_session=True)
        verb = "Volviendo a la versión" if target else "Actualizando a"
        self.ctx.db.event("info", "actualización", f"{verb} {(info.get('remote_commit') or '')[:7]}: el servicio se reiniciará")
        return {"ok": True, "mode": mode, "log": log_file,
                "message": ("Volviendo a la versión anterior" if target else "Actualización en curso") + ". Bipon Net se reiniciará en 1-2 minutos."}

    def status(self) -> dict:
        c = self.cfg()
        return {"config": {**c, "token": "••••••" if c.get("token") else ""}, "last_check": self.last_check, "mode": install_mode(),
                "version": __version__, "commit": local_commit(), "history": self.history()}

    async def loop(self) -> None:
        await asyncio.sleep(300)
        while True:
            try:
                info = await self.check()
                c = self.cfg()
                if info.get("available") and c.get("auto") and install_mode() in ("systemd", "git"):
                    hh, mm = (int(x) for x in c.get("time", "03:41").split(":"))
                    now = datetime.now()
                    if now.hour == hh and now.minute >= mm or now.hour == (hh + 1) % 24 and now.minute < mm:
                        await self.apply()
                elif info.get("available"):
                    self.ctx.notify.emit("report", "Nueva versión de Bipon Net disponible",
                                         f"{info.get('remote_message') or ''} — actualiza desde Ajustes → Actualizaciones.",
                                         key=info.get("remote_commit", ""))
            except Exception as exc:
                log.debug("comprobación de actualizaciones: %s", exc)
            await asyncio.sleep(3600)
