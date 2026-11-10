"""Instalación de componentes desde la web (Debian/Ubuntu, también LXC de Proxmox).

Tailscale se instala desde su repositorio oficial (siempre la última versión
estable); WireGuard y OpenVPN desde los repositorios de la distribución."""
from __future__ import annotations

import asyncio
import os
import shutil
import time
from collections import deque
from pathlib import Path

TOOLS = {
    "tailscale": {"bin": "tailscale", "name": "Tailscale"},
    "wireguard": {"bin": "wg", "name": "WireGuard"},
    "openvpn": {"bin": "openvpn", "name": "OpenVPN"},
    "conntrack": {"bin": "conntrack", "name": "conntrack"},
    "nftables": {"bin": "nft", "name": "nftables"},
}


def os_release() -> dict:
    out = {}
    try:
        for line in Path("/etc/os-release").read_text().splitlines():
            if "=" in line:
                k, v = line.split("=", 1)
                out[k] = v.strip('"')
    except OSError:
        pass
    return out


def in_container() -> str | None:
    if Path("/.dockerenv").exists():
        return "docker"
    try:
        env = Path("/proc/1/environ").read_bytes()
        if b"container=lxc" in env:
            return "lxc"
    except OSError:
        pass
    try:
        if "lxc" in Path("/proc/1/cgroup").read_text():
            return "lxc"
    except OSError:
        pass
    return None


class Installer:
    def __init__(self, ctx):
        self.ctx = ctx
        self.jobs: dict[str, dict] = {}

    def script(self, tool: str) -> str:
        osr = os_release()
        dist, codename = osr.get("ID", "debian"), osr.get("VERSION_CODENAME", "bookworm")
        if dist not in ("debian", "ubuntu", "raspbian"):
            dist = "debian"
        base = "export DEBIAN_FRONTEND=noninteractive; set -e; apt-get update -q; "
        if tool == "tailscale":
            return base + (
                "apt-get install -y -q curl ca-certificates gnupg; "
                f"curl -fsSL https://pkgs.tailscale.com/stable/{dist}/{codename}.noarmor.gpg -o /usr/share/keyrings/tailscale-archive-keyring.gpg; "
                f"curl -fsSL https://pkgs.tailscale.com/stable/{dist}/{codename}.tailscale-keyring.list -o /etc/apt/sources.list.d/tailscale.list; "
                "apt-get update -q; apt-get install -y -q tailscale; "
                # Bipon gestiona su propio tailscaled si el servicio del sistema no puede arrancar (p. ej. LXC sin TUN)
                "systemctl enable --now tailscaled 2>/dev/null || true; tailscale version")
        pkgs = {"wireguard": "wireguard-tools", "openvpn": "openvpn", "conntrack": "conntrack", "nftables": "nftables"}[tool]
        return base + f"apt-get install -y -q --no-install-recommends {pkgs}; echo instalado"

    async def install(self, tool: str) -> dict:
        if tool not in TOOLS:
            raise ValueError("Componente desconocido")
        if os.geteuid() != 0:
            raise RuntimeError("Bipon necesita ejecutarse como root para instalar paquetes")
        if not shutil.which("apt-get"):
            raise RuntimeError("Instalación automática sólo disponible en Debian/Ubuntu")
        if in_container() == "docker":
            raise RuntimeError("En Docker los componentes vienen en la imagen: actualiza la imagen en lugar de instalar")
        job = self.jobs.get(tool)
        if job and job["running"]:
            return job
        job = {"tool": tool, "running": True, "ok": None, "log": deque(maxlen=200), "started": time.time()}
        self.jobs[tool] = job

        async def run():
            proc = await asyncio.create_subprocess_exec("bash", "-c", self.script(tool), stdout=asyncio.subprocess.PIPE,
                                                        stderr=asyncio.subprocess.STDOUT)
            assert proc.stdout
            async for line in proc.stdout:
                job["log"].append(line.decode(errors="ignore").rstrip())
            code = await proc.wait()
            job["ok"] = code == 0 and shutil.which(TOOLS[tool]["bin"]) is not None
            job["running"] = False
            self.ctx.db.event("info" if job["ok"] else "error", "instalación",
                              f"{TOOLS[tool]['name']}: {'instalado' if job['ok'] else 'falló la instalación'}")
            if job["ok"] and tool == "tailscale":
                try:
                    await self.ctx.vpn.ts.ensure_daemon()
                except Exception:
                    pass
        asyncio.create_task(run())
        return self.public(job)

    @staticmethod
    def public(job: dict) -> dict:
        return {**{k: v for k, v in job.items() if k != "log"}, "log": list(job["log"])[-60:]}

    def status(self) -> dict:
        return {"tools": {k: {"name": v["name"], "installed": shutil.which(v["bin"]) is not None} for k, v in TOOLS.items()},
                "jobs": {k: self.public(j) for k, j in self.jobs.items()}, "container": in_container(),
                "tun": os.path.exists("/dev/net/tun"), "os": os_release().get("PRETTY_NAME"), "hostname": os.uname().nodename}
