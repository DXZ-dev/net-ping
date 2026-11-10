"""Configuración persistente (JSON) + variables de entorno."""
from __future__ import annotations

import copy
import json
import logging
import os
import secrets
import threading
from pathlib import Path
from typing import Any

log = logging.getLogger("netping.config")

DATA_DIR = Path(os.environ.get("NETPING_DATA_DIR", "/data")).resolve()
WEB_HOST = os.environ.get("NETPING_WEB_HOST", "0.0.0.0")
WEB_PORT = int(os.environ.get("NETPING_WEB_PORT", "8080"))
DNS_PORT_ENV = os.environ.get("NETPING_DNS_PORT")
FRONTEND_DIR = Path(
    os.environ.get(
        "NETPING_FRONTEND_DIR",
        str(Path(__file__).resolve().parent.parent.parent / "frontend"),
    )
)
UNBOUND_BIN = os.environ.get("NETPING_UNBOUND_BIN", "unbound")
MANAGE_UNBOUND = os.environ.get("NETPING_MANAGE_UNBOUND", "1") not in ("0", "false", "no")

DEFAULT_PROFILE_ID = "default"

DEFAULTS: dict[str, Any] = {
    "secret": "",
    "auth": {"password_hash": ""},
    "dns": {
        "listen": "0.0.0.0",
        "port": 53,
        # lista de servidores upstream (udp ip[:puerto], tcp://, tls://ip#sni, https://)
        "upstreams": ["127.0.0.1:5335"],
        "preset": "unbound",
        # failover | fastest | parallel
        "strategy": "fastest",
        "timeout_ms": 2000,
        "cache_size": 20000,
        "cache_min_ttl": 0,
        "cache_max_ttl": 86400,
        # sirve respuestas caducadas al instante y refresca en segundo plano
        "optimistic_cache": True,
        # refresca entradas populares antes de que caduquen
        "prefetch": True,
        "blocking_mode": "null",  # null (0.0.0.0 / ::) | nxdomain
        "block_ttl": 60,
        "log_queries": True,
        "block_doh_canary": True,  # use-application-dns.net -> fuerza a navegadores a usar este DNS
        "block_icloud_relay": False,
        "conditional_forwarding": {"enabled": False, "domain": "lan", "server": ""},
        "local_records": [],  # [{"name": "nas.lan", "type": "A", "value": "192.168.1.10"}]
        # optimizador automático de latencia
        "auto_optimize": {"enabled": False, "interval_hours": 24, "last_run": 0, "last_result": None},
    },
    "blocking": {"enabled": True, "paused_until": 0},
    "profiles": [
        {
            "id": DEFAULT_PROFILE_ID,
            "name": "Predeterminado",
            "color": "#3b82f6",
            "filtering": True,
            "safesearch": False,
            "youtube_restricted": False,
            "threat_protection": True,
            "adult_filter": False,
            "blocked_services": [],
            "schedules": [],  # [{"days":[0..6], "start":"22:00", "end":"07:00", "action":"block_all"}]
            "block_all": False,
        }
    ],
    "unbound": {
        "enabled": True,
        "port": 5335,
        "mode": "recursive",  # recursive | forward | forward-tls
        "forward_servers": [],
        "dnssec": True,
        "prefetch": True,
        "prefetch_key": True,
        "serve_expired": True,
        "serve_expired_ttl": 86400,
        "serve_expired_client_timeout": 1800,
        "qname_minimisation": True,
        "aggressive_nsec": True,
        "num_threads": 1,
        "msg_cache_size": "64m",
        "rrset_cache_size": "128m",
        "cache_min_ttl": 0,
        "cache_max_ttl": 86400,
        "edns_buffer_size": 1232,
        "do_ip6": False,
        "hide_identity": True,
        "rebind_protection": True,
        "harden_glue": True,
        "harden_dnssec_stripped": True,
        "use_caps_for_id": False,
        "so_rcvbuf": "1m",
        "verbosity": 1,
        "custom": "",
    },
    "devices": {"scan_interval": 300, "subnets": [], "resolve_names": True},
    "gaming": {"enabled": True, "protect": True, "groups": [], "custom_domains": []},
    "monitor": {"enabled": True, "interval": 60, "targets": ["1.1.1.1", "8.8.8.8"]},
    "retention_days": 30,
    "blocked_services": [],  # servicios bloqueados globalmente
}


def _deep_merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


class Settings:
    """Almacén de configuración seguro entre hilos, persistido en settings.json."""

    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.RLock()
        self._data: dict[str, Any] = copy.deepcopy(DEFAULTS)
        self._listeners: list = []
        self.load()

    def load(self) -> None:
        with self._lock:
            if self.path.exists():
                try:
                    raw = json.loads(self.path.read_text("utf-8"))
                    self._data = _deep_merge(DEFAULTS, raw)
                except Exception as exc:  # pragma: no cover
                    log.error("settings.json corrupto (%s); usando valores por defecto", exc)
            if not self._data.get("secret"):
                self._data["secret"] = secrets.token_hex(32)
            if DNS_PORT_ENV:
                self._data["dns"]["port"] = int(DNS_PORT_ENV)
            if not any(p["id"] == DEFAULT_PROFILE_ID for p in self._data["profiles"]):
                self._data["profiles"].insert(0, copy.deepcopy(DEFAULTS["profiles"][0]))
            self.save()

    def save(self) -> None:
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self._data, indent=2, ensure_ascii=False), "utf-8")
            os.replace(tmp, self.path)
            try:
                os.chmod(self.path, 0o600)
            except OSError:
                pass

    def get(self, key: str, default: Any = None) -> Any:
        with self._lock:
            cur: Any = self._data
            for part in key.split("."):
                if not isinstance(cur, dict) or part not in cur:
                    return default
                cur = cur[part]
            return copy.deepcopy(cur)

    def set(self, key: str, value: Any, notify: bool = True) -> None:
        with self._lock:
            parts = key.split(".")
            cur = self._data
            for part in parts[:-1]:
                cur = cur.setdefault(part, {})
            cur[parts[-1]] = copy.deepcopy(value)
            self.save()
        if notify:
            for cb in list(self._listeners):
                try:
                    cb(parts[0])
                except Exception:  # pragma: no cover
                    log.exception("listener de configuración falló")

    def update(self, key: str, patch: dict) -> dict:
        cur = self.get(key, {}) or {}
        cur.update(patch)
        self.set(key, cur)
        return cur

    def on_change(self, cb) -> None:
        self._listeners.append(cb)

    def export(self) -> dict:
        with self._lock:
            data = copy.deepcopy(self._data)
        data.pop("secret", None)
        data.pop("auth", None)
        return data

    def import_(self, data: dict) -> None:
        data = {k: v for k, v in data.items() if k not in ("secret", "auth")}
        with self._lock:
            self._data = _deep_merge(self._data, data)
            self.save()
        for cb in list(self._listeners):
            cb("*")


settings: Settings | None = None


def init_settings() -> Settings:
    global settings
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    settings = Settings(DATA_DIR / "settings.json")
    return settings


def get_settings() -> Settings:
    assert settings is not None, "settings no inicializado"
    return settings
