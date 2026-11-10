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
            branch = c.get("branch") or r.json().get("default_branch", "main")
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

    async def apply(self) -> dict:
        mode = install_mode()
        info = self.last_check or await self.check()
        if mode == "docker":
            return {"ok": False, "mode": mode,
                    "message": "En Docker la actualización se hace desde el equipo anfitrión: "
                               "git pull && docker compose up -d --build   (o activa Watchtower para hacerlo automático)."}
        src = source_dir()
        if not src:
            return {"ok": False, "mode": mode, "message": "No se encontró la copia git de Bipon Net. Reinstala con deploy/linux/install.sh."}
        script = src / "deploy" / "linux" / "update.sh"
        env = {**os.environ, "BIPON_BRANCH": info.get("branch") or "main"}
        c = self.cfg()
        if c.get("token"):
            env["BIPON_GIT_TOKEN"] = c["token"]
        log_file = "/var/log/bipon-update.log"
        if shutil.which("systemd-run"):
            args = ["systemd-run", "--unit", f"bipon-update-{int(time.time())}", "--collect",
                    f"--setenv=BIPON_BRANCH={env['BIPON_BRANCH']}"]
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
        self.ctx.db.event("info", "actualización", f"Actualizando a {info.get('remote_commit', '')[:7]}: el servicio se reiniciará")
        return {"ok": True, "mode": mode, "message": "Actualización en curso. Bipon Net se reiniciará en 1-2 minutos.", "log": log_file}

    def status(self) -> dict:
        c = self.cfg()
        return {"config": {**c, "token": "••••••" if c.get("token") else ""}, "last_check": self.last_check, "mode": install_mode(),
                "version": __version__, "commit": local_commit()}

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
