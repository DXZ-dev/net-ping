"""Web Push (RFC 8030/8291/8292) sin dependencias externas: cifrado aes128gcm y
autenticación VAPID (JWT ES256), implementados con `cryptography`."""
from __future__ import annotations

import base64
import json
import os
import struct
import time
from pathlib import Path
from urllib.parse import urlparse

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from .config import DATA_DIR


def b64u(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def b64u_dec(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _hkdf(salt: bytes, ikm: bytes, info: bytes, length: int) -> bytes:
    return HKDF(algorithm=hashes.SHA256(), length=length, salt=salt, info=info).derive(ikm)


class VapidKeys:
    def __init__(self, path: Path | None = None):
        self.path = path or DATA_DIR / "vapid.pem"
        if self.path.exists():
            self.key = serialization.load_pem_private_key(self.path.read_bytes(), None)
        else:
            self.key = ec.generate_private_key(ec.SECP256R1())
            self.path.write_bytes(self.key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                                         serialization.NoEncryption()))
            os.chmod(self.path, 0o600)

    @property
    def public_key(self) -> str:
        """Clave pública (punto sin comprimir, base64url) para applicationServerKey."""
        return b64u(self.key.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint))

    def jwt(self, audience: str, subject: str = "mailto:admin@bipon.local") -> str:
        header = b64u(json.dumps({"typ": "JWT", "alg": "ES256"}).encode())
        claims = b64u(json.dumps({"aud": audience, "exp": int(time.time()) + 12 * 3600, "sub": subject}).encode())
        signing_input = f"{header}.{claims}".encode()
        der = self.key.sign(signing_input, ec.ECDSA(hashes.SHA256()))
        r, s = decode_dss_signature(der)
        return f"{header}.{claims}.{b64u(r.to_bytes(32, 'big') + s.to_bytes(32, 'big'))}"


def encrypt(payload: bytes, p256dh: str, auth: str) -> bytes:
    """Cifrado aes128gcm (RFC 8291) de un mensaje para una suscripción."""
    ua_pub_bytes = b64u_dec(p256dh)
    auth_secret = b64u_dec(auth)
    ua_pub = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), ua_pub_bytes)
    as_priv = ec.generate_private_key(ec.SECP256R1())
    as_pub_bytes = as_priv.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    shared = as_priv.exchange(ec.ECDH(), ua_pub)
    ikm = _hkdf(auth_secret, shared, b"WebPush: info\x00" + ua_pub_bytes + as_pub_bytes, 32)
    salt = os.urandom(16)
    cek = _hkdf(salt, ikm, b"Content-Encoding: aes128gcm\x00", 16)
    nonce = _hkdf(salt, ikm, b"Content-Encoding: nonce\x00", 12)
    record = payload + b"\x02"  # delimitador del último registro
    ciphertext = AESGCM(cek).encrypt(nonce, record, None)
    rs = 4096
    header = salt + struct.pack("!IB", rs, len(as_pub_bytes)) + as_pub_bytes
    return header + ciphertext


async def send(subscription: dict, message: dict, vapid: VapidKeys, client: httpx.AsyncClient, ttl: int = 3600) -> int:
    endpoint = subscription["endpoint"]
    keys = subscription.get("keys") or {}
    body = encrypt(json.dumps(message, ensure_ascii=False).encode(), keys["p256dh"], keys["auth"])
    u = urlparse(endpoint)
    headers = {
        "TTL": str(ttl), "Content-Encoding": "aes128gcm", "Content-Type": "application/octet-stream", "Urgency": "high",
        "Authorization": f"vapid t={vapid.jwt(f'{u.scheme}://{u.netloc}')}, k={vapid.public_key}",
    }
    r = await client.post(endpoint, content=body, headers=headers, timeout=15)
    return r.status_code
