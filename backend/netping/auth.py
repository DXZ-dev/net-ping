"""Autenticación: contraseña de administrador (scrypt) y sesión firmada (HMAC)."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time

from fastapi import HTTPException, Request

from .config import get_settings

COOKIE = "netping_session"
SESSION_TTL = 7 * 86400
_failures: dict[str, list[float]] = {}


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.scrypt(password.encode(), salt=salt, n=2 ** 14, r=8, p=1, dklen=32)
    return "scrypt$" + base64.b64encode(salt).decode() + "$" + base64.b64encode(dk).decode()


def verify_password(password: str, stored: str) -> bool:
    try:
        _, salt_b64, dk_b64 = stored.split("$")
        salt = base64.b64decode(salt_b64)
        dk = hashlib.scrypt(password.encode(), salt=salt, n=2 ** 14, r=8, p=1, dklen=32)
        return hmac.compare_digest(dk, base64.b64decode(dk_b64))
    except Exception:
        return False


def apply_env_password() -> None:
    pw = os.environ.get("NETPING_PASSWORD")
    s = get_settings()
    if pw and not verify_password(pw, s.get("auth.password_hash") or ""):
        s.set("auth.password_hash", hash_password(pw), notify=False)


def password_set() -> bool:
    return bool(get_settings().get("auth.password_hash"))


def _sign(payload: bytes) -> str:
    key = get_settings().get("secret").encode()
    return base64.urlsafe_b64encode(hmac.new(key, payload, hashlib.sha256).digest()).decode().rstrip("=")


def make_token() -> str:
    # incluye una huella del hash de contraseña: cambiarla invalida todas las sesiones
    fp = hashlib.sha256((get_settings().get("auth.password_hash") or "").encode()).hexdigest()[:12]
    payload = json.dumps({"exp": int(time.time()) + SESSION_TTL, "fp": fp}).encode()
    b = base64.urlsafe_b64encode(payload).decode().rstrip("=")
    return f"{b}.{_sign(payload)}"


def check_token(token: str | None) -> bool:
    if not token or "." not in token:
        return False
    b, sig = token.rsplit(".", 1)
    try:
        payload = base64.urlsafe_b64decode(b + "=" * (-len(b) % 4))
    except Exception:
        return False
    if not hmac.compare_digest(sig, _sign(payload)):
        return False
    data = json.loads(payload)
    fp = hashlib.sha256((get_settings().get("auth.password_hash") or "").encode()).hexdigest()[:12]
    return data.get("exp", 0) > time.time() and data.get("fp") == fp


def throttle(ip: str) -> None:
    now = time.time()
    fails = [t for t in _failures.get(ip, []) if now - t < 300]
    _failures[ip] = fails
    if len(fails) >= 8:
        raise HTTPException(429, "Demasiados intentos. Espera unos minutos.")


def record_failure(ip: str) -> None:
    _failures.setdefault(ip, []).append(time.time())


def require_auth(request: Request) -> None:
    if not password_set():
        raise HTTPException(428, "Configura la contraseña de administrador")
    if not check_token(request.cookies.get(COOKIE)):
        raise HTTPException(401, "No autenticado")
