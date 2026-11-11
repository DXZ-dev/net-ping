"""Autenticación y autorización.

- Usuarios con roles: admin (todo), parent (familia: dispositivos, perfiles,
  tiempo de pantalla, pausas) y viewer (sólo lectura).
- Contraseñas con scrypt, 2FA TOTP (RFC 6238) opcional por usuario.
- Sesiones en cookie firmada con HMAC (se invalidan al cambiar la contraseña).
- Tokens de API (Authorization: Bearer np_…) con rol propio.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import struct
import time

from fastapi import HTTPException, Request

from .config import get_settings
from .db import get_db

COOKIE = "bipon_session"
SESSION_TTL = 7 * 86400
ROLES = ("admin", "parent", "viewer")
ROLE_LABEL = {"admin": "Administrador", "parent": "Padres", "viewer": "Sólo lectura"}
USERNAME_RE = re.compile(r"^[a-zA-Z0-9_.-]{2,32}$")
_failures: dict[str, list[float]] = {}


# ------------------------------------------------------------------ contraseñas
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


# ------------------------------------------------------------------ TOTP (2FA)
def totp_secret() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def totp_code(secret: str, t: float | None = None, step: int = 30) -> str:
    key = base64.b32decode(secret + "=" * (-len(secret) % 8), casefold=True)
    counter = int((t if t is not None else time.time()) // step)
    digest = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    off = digest[-1] & 0x0F
    code = (struct.unpack(">I", digest[off:off + 4])[0] & 0x7FFFFFFF) % 1_000_000
    return f"{code:06d}"


def totp_verify(secret: str, code: str, window: int = 1) -> bool:
    code = (code or "").strip().replace(" ", "")
    if not code.isdigit() or len(code) != 6:
        return False
    now = time.time()
    return any(hmac.compare_digest(totp_code(secret, now + i * 30), code) for i in range(-window, window + 1))


def totp_uri(secret: str, username: str) -> str:
    return f"otpauth://totp/Bipon Net:{username}?secret={secret}&issuer=Bipon Net&digits=6&period=30"


# ------------------------------------------------------------------ usuarios
def users_count() -> int:
    return get_db().scalar("SELECT COUNT(*) FROM users") or 0


def get_user(uid: int | None = None, username: str | None = None) -> dict | None:
    db = get_db()
    if uid is not None:
        return db.one("SELECT * FROM users WHERE id=?", (uid,))
    return db.one("SELECT * FROM users WHERE username=? COLLATE NOCASE", (username,))


def create_user(username: str, password: str, role: str = "admin") -> dict:
    if not USERNAME_RE.match(username or ""):
        raise ValueError("Usuario inválido (2-32 caracteres: letras, números, _ . -)")
    if role not in ROLES:
        raise ValueError("Rol inválido")
    if len(password or "") < 8:
        raise ValueError("La contraseña debe tener al menos 8 caracteres")
    if get_user(username=username):
        raise ValueError("Ese usuario ya existe")
    get_db().execute("INSERT INTO users(username,password_hash,role,created) VALUES (?,?,?,?)",
                     (username, hash_password(password), role, time.time()))
    return public_user(get_user(username=username))


def public_user(u: dict | None) -> dict | None:
    if not u:
        return None
    return {"id": u["id"], "username": u["username"], "role": u["role"], "role_label": ROLE_LABEL.get(u["role"], u["role"]),
            "totp_enabled": bool(u["totp_enabled"]), "created": u["created"], "last_login": u["last_login"]}


def migrate_legacy() -> None:
    """Versión 1.0 tenía una única contraseña en settings: se convierte en el usuario 'admin'."""
    s = get_settings()
    legacy = s.get("auth.password_hash")
    if legacy and not users_count():
        get_db().execute("INSERT INTO users(username,password_hash,role,created) VALUES ('admin',?, 'admin', ?)", (legacy, time.time()))
    if legacy:
        s.set("auth.password_hash", "", notify=False)


def apply_env_password() -> None:
    pw = (os.environ.get("BIPON_PASSWORD") or os.environ.get("NETPING_PASSWORD"))
    if not pw:
        return
    u = get_user(username="admin")
    if u is None:
        if len(pw) >= 8:
            get_db().execute("INSERT INTO users(username,password_hash,role,created) VALUES ('admin',?,'admin',?)",
                             (hash_password(pw), time.time()))
    elif not verify_password(pw, u["password_hash"]):
        get_db().execute("UPDATE users SET password_hash=? WHERE id=?", (hash_password(pw), u["id"]))


def password_set() -> bool:
    return users_count() > 0


# ------------------------------------------------------------------ sesiones
def _sign(payload: bytes) -> str:
    key = get_settings().get("secret").encode()
    return base64.urlsafe_b64encode(hmac.new(key, payload, hashlib.sha256).digest()).decode().rstrip("=")


def _fp(u: dict) -> str:
    return hashlib.sha256((u["password_hash"] + str(u["totp_enabled"])).encode()).hexdigest()[:12]


def make_token(u: dict) -> str:
    payload = json.dumps({"exp": int(time.time()) + SESSION_TTL, "uid": u["id"], "fp": _fp(u)}).encode()
    b = base64.urlsafe_b64encode(payload).decode().rstrip("=")
    return f"{b}.{_sign(payload)}"


def user_from_session(token: str | None) -> dict | None:
    if not token or "." not in token:
        return None
    b, sig = token.rsplit(".", 1)
    try:
        payload = base64.urlsafe_b64decode(b + "=" * (-len(b) % 4))
    except Exception:
        return None
    if not hmac.compare_digest(sig, _sign(payload)):
        return None
    data = json.loads(payload)
    if data.get("exp", 0) < time.time():
        return None
    u = get_user(uid=data.get("uid"))
    if not u or data.get("fp") != _fp(u):
        return None
    return u


# ------------------------------------------------------------------ tokens de API
def create_api_token(name: str, role: str) -> str:
    if role not in ROLES:
        raise ValueError("Rol inválido")
    token = "np_" + secrets.token_urlsafe(32)
    get_db().execute("INSERT INTO api_tokens(name,token_hash,role,created) VALUES (?,?,?,?)",
                     (name[:60] or "token", hashlib.sha256(token.encode()).hexdigest(), role, time.time()))
    return token


def user_from_api_token(token: str) -> dict | None:
    h = hashlib.sha256(token.encode()).hexdigest()
    row = get_db().one("SELECT * FROM api_tokens WHERE token_hash=?", (h,))
    if not row:
        return None
    get_db().execute("UPDATE api_tokens SET last_used=? WHERE id=?", (time.time(), row["id"]))
    return {"id": -row["id"], "username": f"token:{row['name']}", "role": row["role"], "token": True}


# ------------------------------------------------------------------ permisos
# Rutas que el rol "parent" puede MODIFICAR (prefijo, métodos)
PARENT_WRITE = [
    ("/api/devices/", ("PATCH", "POST")),     # pausar, renombrar, asignar perfil, ping
    ("/api/profiles", ("PUT",)),              # editar perfiles existentes
    ("/api/screentime", ("POST", "PUT")),     # bonus de tiempo, cuotas
    ("/api/family/", ("POST",)),              # pausa general
    ("/api/blocking", ("POST",)),             # pausar bloqueo
    ("/api/auth/", ("POST",)),                # cambiar su contraseña / 2FA
    ("/api/push/", ("POST", "DELETE")),
]
# Rutas que el rol "parent" puede LEER
PARENT_READ = ("/api/devices", "/api/profiles", "/api/services", "/api/stats", "/api/screentime", "/api/blocking",
               "/api/auth/", "/api/queries", "/api/system", "/api/events", "/api/family", "/api/reports", "/api/push/")
# Nunca accesibles salvo admin (contienen secretos)
ADMIN_ONLY_READ = ("/api/backup", "/api/users", "/api/tokens", "/api/vpn/wireguard/", "/api/vpn/openvpn/clients/",
                   "/api/notify", "/api/sync/export", "/api/tls", "/api/integrations/config", "/api/updates", "/api/configs/",
                   "/api/routerctl", "/api/audit", "/api/export/")


def allowed(role: str, method: str, path: str) -> bool:
    if role == "admin":
        return True
    if path == "/api/system/prefs" and method in ("GET", "PUT"):
        return True  # cada usuario guarda su tema, idioma y panel
    if method in ("GET", "HEAD"):
        if any(path.startswith(p) for p in ADMIN_ONLY_READ):
            return False
        if role == "viewer":
            return True
        return any(path.startswith(p) for p in PARENT_READ)
    if role == "parent":
        if path == "/api/devices/scan" or path.endswith("/ports"):
            return False  # escaneos de red: sólo admin
        return any(path.startswith(p) and method in m for p, m in PARENT_WRITE)
    return path.startswith("/api/auth/") and method == "POST"  # viewer: sólo su propia cuenta


def throttle(ip: str) -> None:
    now = time.time()
    fails = [t for t in _failures.get(ip, []) if now - t < 300]
    _failures[ip] = fails
    if len(fails) >= 8:
        raise HTTPException(429, "Demasiados intentos. Espera unos minutos.")


def record_failure(ip: str) -> None:
    _failures.setdefault(ip, []).append(time.time())


def current_user(request: Request) -> dict | None:
    authz = request.headers.get("authorization", "")
    if authz.lower().startswith("bearer np_"):
        return user_from_api_token(authz[7:].strip())
    return user_from_session(request.cookies.get(COOKIE))


def require_auth(request: Request) -> dict:
    if not password_set():
        raise HTTPException(428, "Crea el usuario administrador")
    u = current_user(request)
    if not u:
        raise HTTPException(401, "No autenticado")
    if not allowed(u["role"], request.method, request.url.path):
        raise HTTPException(403, "Tu usuario no tiene permiso para esta acción")
    request.state.user = u
    return u
