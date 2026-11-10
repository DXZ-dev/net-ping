"""Gestor de Unbound integrado: genera la configuración, valida, arranca, supervisa
y expone estadísticas mediante unbound-control."""
from __future__ import annotations

import asyncio
import logging
import os
import re
import shutil
import time
from collections import deque
from pathlib import Path

from .config import DATA_DIR, MANAGE_UNBOUND, UNBOUND_BIN, Settings

log = logging.getLogger("bipon.unbound")

SIZE_RE = re.compile(r"^\d+[kmg]?$", re.I)
CONTROL_PORT = 8953

ROOT_KEY_FALLBACKS = ["/usr/share/dns/root.key", "/var/lib/unbound/root.key", "/etc/unbound/root.key"]


def _yn(v: bool) -> str:
    return "yes" if v else "no"


def render_config(cfg: dict, base: Path) -> str:
    port = int(cfg.get("port", 5335))
    for k in ("msg_cache_size", "rrset_cache_size", "so_rcvbuf"):
        if not SIZE_RE.match(str(cfg.get(k, ""))):
            raise ValueError(f"valor inválido para {k}")
    threads = max(1, min(int(cfg.get("num_threads", 1)), 16))
    slabs = 1 << (threads - 1).bit_length()
    lines = [
        "# Generado por Bipon Net — no editar a mano (usa la interfaz web)",
        "server:",
        "    interface: 127.0.0.1",
        f"    port: {port}",
        "    do-ip4: yes",
        f"    do-ip6: {_yn(cfg.get('do_ip6', False))}",
        "    prefer-ip6: no",
        "    do-udp: yes",
        "    do-tcp: yes",
        "    access-control: 127.0.0.0/8 allow",
        "    username: \"\"",
        "    chroot: \"\"",
        f"    directory: \"{base}\"",
        "    pidfile: \"\"",
        "    use-syslog: no",
        "    logfile: \"\"",
        f"    verbosity: {int(cfg.get('verbosity', 1))}",
        f"    num-threads: {threads}",
        f"    msg-cache-slabs: {slabs}",
        f"    rrset-cache-slabs: {slabs}",
        f"    infra-cache-slabs: {slabs}",
        f"    key-cache-slabs: {slabs}",
        f"    msg-cache-size: {cfg.get('msg_cache_size', '64m')}",
        f"    rrset-cache-size: {cfg.get('rrset_cache_size', '128m')}",
        f"    so-rcvbuf: {cfg.get('so_rcvbuf', '1m')}",
        f"    cache-min-ttl: {int(cfg.get('cache_min_ttl', 0))}",
        f"    cache-max-ttl: {int(cfg.get('cache_max_ttl', 86400))}",
        f"    edns-buffer-size: {int(cfg.get('edns_buffer_size', 1232))}",
        f"    prefetch: {_yn(cfg.get('prefetch', True))}",
        f"    prefetch-key: {_yn(cfg.get('prefetch_key', True))}",
        f"    serve-expired: {_yn(cfg.get('serve_expired', True))}",
        f"    serve-expired-ttl: {int(cfg.get('serve_expired_ttl', 86400))}",
        f"    serve-expired-client-timeout: {int(cfg.get('serve_expired_client_timeout', 1800))}",
        f"    qname-minimisation: {_yn(cfg.get('qname_minimisation', True))}",
        f"    aggressive-nsec: {_yn(cfg.get('aggressive_nsec', True))}",
        f"    hide-identity: {_yn(cfg.get('hide_identity', True))}",
        f"    hide-version: {_yn(cfg.get('hide_identity', True))}",
        f"    harden-glue: {_yn(cfg.get('harden_glue', True))}",
        f"    harden-dnssec-stripped: {_yn(cfg.get('harden_dnssec_stripped', True))}",
        f"    use-caps-for-id: {_yn(cfg.get('use_caps_for_id', False))}",
        "    harden-below-nxdomain: yes",
        "    harden-referral-path: no",
        "    minimal-responses: yes",
        "    rrset-roundrobin: yes",
        "    outgoing-range: 8192",
        "    num-queries-per-thread: 4096",
        "    infra-keep-probing: yes",
        "    tls-cert-bundle: \"/etc/ssl/certs/ca-certificates.crt\"",
    ]
    if cfg.get("dnssec", True):
        lines.append(f"    auto-trust-anchor-file: \"{base / 'root.key'}\"")
    else:
        lines.append("    module-config: \"iterator\"")
    if cfg.get("rebind_protection", True):
        for net in ("192.168.0.0/16", "169.254.0.0/16", "172.16.0.0/12", "10.0.0.0/8", "fd00::/8", "fe80::/10", "100.64.0.0/10"):
            lines.append(f"    private-address: {net}")
    lines += [
        "",
        "remote-control:",
        "    control-enable: yes",
        "    control-interface: 127.0.0.1",
        f"    control-port: {CONTROL_PORT}",
        "    control-use-cert: no",
    ]
    mode = cfg.get("mode", "recursive")
    servers = [s.strip() for s in cfg.get("forward_servers", []) if s.strip()]
    if mode in ("forward", "forward-tls") and servers:
        lines += ["", "forward-zone:", '    name: "."']
        if mode == "forward-tls":
            lines.append("    forward-tls-upstream: yes")
        for s in servers:
            if not re.match(r"^[0-9a-fA-F:.]+(@\d+)?(#[A-Za-z0-9.-]+)?$", s):
                raise ValueError(f"servidor de reenvío inválido: {s}")
            lines.append(f"    forward-addr: {s}")
    custom = (cfg.get("custom") or "").strip()
    if custom:
        lines += ["", "# --- configuración personalizada ---", custom]
    return "\n".join(lines) + "\n"


class UnboundManager:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.base = DATA_DIR / "unbound"
        self.base.mkdir(parents=True, exist_ok=True)
        self.conf = self.base / "unbound.conf"
        self.proc: asyncio.subprocess.Process | None = None
        self.logs: deque[str] = deque(maxlen=500)
        self.started_at = 0.0
        self.restarts = 0
        self.last_error = ""
        self._stop = False
        self._supervisor: asyncio.Task | None = None
        self.on_crash = None

    @property
    def available(self) -> bool:
        return MANAGE_UNBOUND and shutil.which(UNBOUND_BIN) is not None

    def _bin(self, name: str) -> str | None:
        p = shutil.which(name)
        if p:
            return p
        cand = Path(shutil.which(UNBOUND_BIN) or "/usr/sbin/unbound").parent / name
        return str(cand) if cand.exists() else None

    async def _run(self, *args, timeout: float = 15) -> tuple[int, str]:
        proc = await asyncio.create_subprocess_exec(*args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
        try:
            out, _ = await asyncio.wait_for(proc.communicate(), timeout)
        except asyncio.TimeoutError:
            proc.kill()
            return 124, "tiempo agotado"
        return proc.returncode or 0, out.decode(errors="ignore")

    async def ensure_root_key(self) -> None:
        key = self.base / "root.key"
        anchor = self._bin("unbound-anchor")
        if anchor:
            await self._run(anchor, "-a", str(key), timeout=30)
        if not key.exists() or key.stat().st_size == 0:
            for fb in ROOT_KEY_FALLBACKS:
                if os.path.exists(fb):
                    shutil.copy(fb, key)
                    break

    async def write_config(self, cfg: dict | None = None) -> tuple[bool, str]:
        cfg = cfg or self.settings.get("unbound")
        try:
            text = render_config(cfg, self.base)
        except ValueError as exc:
            return False, str(exc)
        tmp = self.base / "unbound.conf.new"
        tmp.write_text(text)
        checker = self._bin("unbound-checkconf")
        if checker:
            code, out = await self._run(checker, str(tmp))
            if code != 0:
                return False, out.strip()
        tmp.replace(self.conf)
        return True, "OK"

    async def start(self) -> None:
        if not self.available:
            log.warning("Unbound no disponible (binario no encontrado o gestión desactivada)")
            return
        if not self.settings.get("unbound.enabled", True):
            return
        self._stop = False
        await self.ensure_root_key()
        ok, msg = await self.write_config()
        if not ok:
            self.last_error = msg
            log.error("configuración de Unbound inválida: %s", msg)
            return
        if self._supervisor is None or self._supervisor.done():
            self._supervisor = asyncio.create_task(self._supervise())

    async def _supervise(self) -> None:
        backoff = 1
        while not self._stop:
            self.proc = await asyncio.create_subprocess_exec(
                shutil.which(UNBOUND_BIN), "-d", "-p", "-c", str(self.conf),
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
            )
            self.started_at = time.time()
            log.info("Unbound iniciado (pid %s)", self.proc.pid)
            assert self.proc.stdout
            async for line in self.proc.stdout:
                self.logs.append(line.decode(errors="ignore").rstrip())
            code = await self.proc.wait()
            if self._stop:
                break
            self.last_error = f"Unbound terminó con código {code}"
            log.warning(self.last_error)
            if self.on_crash:
                self.on_crash(self.last_error)
            self.restarts += 1
            await asyncio.sleep(backoff)
            backoff = 1 if time.time() - self.started_at > 60 else min(backoff * 2, 60)

    async def stop(self) -> None:
        self._stop = True
        if self.proc and self.proc.returncode is None:
            self.proc.terminate()
            try:
                await asyncio.wait_for(self.proc.wait(), 5)
            except asyncio.TimeoutError:
                self.proc.kill()
        if self._supervisor:
            self._supervisor.cancel()
            self._supervisor = None

    async def apply(self, cfg: dict) -> tuple[bool, str]:
        ok, msg = await self.write_config(cfg)
        if not ok:
            return False, msg
        self.settings.set("unbound", cfg)
        await self.stop()
        if cfg.get("enabled", True):
            await self.start()
        return True, "Configuración aplicada"

    @property
    def running(self) -> bool:
        return bool(self.proc and self.proc.returncode is None)

    async def control(self, *args: str) -> tuple[int, str]:
        ctl = self._bin("unbound-control")
        if not ctl or not self.running:
            return 1, "unbound-control no disponible"
        return await self._run(ctl, "-c", str(self.conf), *args, timeout=10)

    async def stats(self) -> dict:
        code, out = await self.control("stats_noreset")
        if code != 0:
            return {}
        raw = {}
        for line in out.splitlines():
            if "=" in line:
                k, v = line.split("=", 1)
                try:
                    raw[k] = float(v)
                except ValueError:
                    pass
        g = lambda k: raw.get(k, 0)  # noqa: E731
        q = g("total.num.queries")
        return {
            "queries": int(q),
            "cache_hits": int(g("total.num.cachehits")),
            "cache_miss": int(g("total.num.cachemiss")),
            "prefetch": int(g("total.num.prefetch")),
            "expired_served": int(g("total.num.expired")),
            "recursion_avg_ms": round(g("total.recursion.time.avg") * 1000, 1),
            "recursion_median_ms": round(g("total.recursion.time.median") * 1000, 1),
            "hit_ratio": round(g("total.num.cachehits") / q * 100, 1) if q else 0,
            "mem_cache_rrset": int(g("mem.cache.rrset")),
            "mem_cache_msg": int(g("mem.cache.message")),
            "uptime": int(g("time.up")),
            "secure": int(g("num.answer.secure")),
            "bogus": int(g("num.answer.bogus")),
            "nxdomain": int(g("num.answer.rcode.NXDOMAIN")),
            "servfail": int(g("num.answer.rcode.SERVFAIL")),
        }

    def status(self) -> dict:
        return {
            "available": self.available,
            "running": self.running,
            "pid": self.proc.pid if self.running and self.proc else None,
            "uptime": int(time.time() - self.started_at) if self.running else 0,
            "restarts": self.restarts,
            "last_error": self.last_error,
            "port": self.settings.get("unbound.port", 5335),
            "config_path": str(self.conf),
            "binary": shutil.which(UNBOUND_BIN),
        }
