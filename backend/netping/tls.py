"""Certificados TLS para el panel HTTPS, DNS-over-HTTPS y DNS-over-TLS.

Por defecto se genera un certificado autofirmado (válido para la IP del equipo,
netping.lan y su hostname). Puede sustituirse por uno propio (p. ej. de
Let's Encrypt) subiendo el certificado y la clave desde la web."""
from __future__ import annotations

import datetime as dt
import ipaddress
import os
import socket
import ssl
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from . import netinfo
from .config import DATA_DIR

TLS_DIR = DATA_DIR / "tls"
CERT = TLS_DIR / "cert.pem"
KEY = TLS_DIR / "key.pem"


def _write_key(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as fh:
        fh.write(data)


def generate_self_signed(force: bool = False) -> None:
    if CERT.exists() and KEY.exists() and not force:
        return
    key = ec.generate_private_key(ec.SECP256R1())
    host = socket.gethostname()
    names: list[x509.GeneralName] = [x509.DNSName("netping.lan"), x509.DNSName("netping"), x509.DNSName("localhost")]
    if host and host not in ("netping", "localhost"):
        names.append(x509.DNSName(host))
    ips = {"127.0.0.1"} | {i["ip"] for i in netinfo.interfaces() if i.get("ip")}
    names += [x509.IPAddress(ipaddress.ip_address(i)) for i in sorted(ips)]
    now = dt.datetime.now(dt.timezone.utc)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "NetPing"), x509.NameAttribute(NameOID.ORGANIZATION_NAME, "NetPing")])
    cert = (x509.CertificateBuilder().subject_name(subject).issuer_name(subject).public_key(key.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(now - dt.timedelta(days=1))
            .not_valid_after(now + dt.timedelta(days=825))
            .add_extension(x509.SubjectAlternativeName(names), critical=False)
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .add_extension(x509.ExtendedKeyUsage([x509.oid.ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
            .sign(key, hashes.SHA256()))
    _write_key(KEY, key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    CERT.write_bytes(cert.public_bytes(serialization.Encoding.PEM))


def install_custom(cert_pem: str, key_pem: str) -> dict:
    """Valida y guarda un certificado propio (cadena completa) y su clave."""
    certs = x509.load_pem_x509_certificates(cert_pem.encode())
    key = serialization.load_pem_private_key(key_pem.encode(), None)
    pub = lambda k: k.public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)  # noqa: E731
    if pub(certs[0].public_key()) != pub(key.public_key()):
        raise ValueError("La clave privada no corresponde al certificado")
    _write_key(KEY, key_pem.encode())
    CERT.write_text(cert_pem)
    return info()


def info() -> dict:
    if not CERT.exists():
        return {"present": False}
    c = x509.load_pem_x509_certificate(CERT.read_bytes())
    try:
        san = c.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
        names = [str(n.value) for n in san]
    except x509.ExtensionNotFound:
        names = []
    exp = c.not_valid_after_utc
    return {"present": True, "subject": c.subject.rfc4514_string(), "issuer": c.issuer.rfc4514_string(),
            "self_signed": c.issuer == c.subject, "names": names, "expires": exp.isoformat(),
            "days_left": (exp - dt.datetime.now(dt.timezone.utc)).days,
            "sha256": c.fingerprint(hashes.SHA256()).hex(":").upper()}


def server_context() -> ssl.SSLContext:
    ctx = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    ctx.load_cert_chain(str(CERT), str(KEY))
    return ctx
