"""Certificados TLS para el panel HTTPS, DNS-over-HTTPS y DNS-over-TLS.

Por defecto se genera un certificado autofirmado (válido para la IP del equipo,
bipon.lan y su hostname). Puede sustituirse por uno propio (p. ej. de
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
    names: list[x509.GeneralName] = [x509.DNSName("bipon.lan"), x509.DNSName("bipon"), x509.DNSName("netping.lan"), x509.DNSName("localhost")]
    if host and host not in ("bipon", "netping", "localhost"):
        names.append(x509.DNSName(host))
    ips = {"127.0.0.1"} | {i["ip"] for i in netinfo.interfaces() if i.get("ip")}
    names += [x509.IPAddress(ipaddress.ip_address(i)) for i in sorted(ips)]
    now = dt.datetime.now(dt.timezone.utc)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Bipon Net"), x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Bipon Net")])
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
            "self_signed": c.issuer == c.subject, "names": names,
            "signed_by_ca": CA_CERT.exists() and c.issuer == x509.load_pem_x509_certificate(CA_CERT.read_bytes()).subject, "expires": exp.isoformat(),
            "days_left": (exp - dt.datetime.now(dt.timezone.utc)).days,
            "sha256": c.fingerprint(hashes.SHA256()).hex(":").upper()}


def server_context() -> ssl.SSLContext:
    ctx = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    ctx.load_cert_chain(str(CERT), str(KEY))
    return ctx


# =====================================================================
# Autoridad certificadora local (CA)
#   Instalas UNA vez el certificado raíz de Bipon en tus equipos y todo lo
#   que firme (el propio panel, Proxmox, el NAS, la impresora…) se abre por
#   HTTPS sin avisos. La CA está restringida (Name Constraints) a IPs
#   privadas y dominios locales: aunque alguien robara su clave, no podría
#   suplantar webs de Internet.
# =====================================================================
import json as _json
import re as _re
import secrets as _secrets
import time as _time

from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization import pkcs12
from cryptography.x509.oid import ExtendedKeyUsageOID

CA_CERT = TLS_DIR / "ca.pem"
CA_KEY = TLS_DIR / "ca-key.pem"
ISSUED_DIR = TLS_DIR / "issued"
LOCAL_SUFFIXES = ["lan", "local", "home", "home.arpa", "internal", "localdomain", "localhost", "intranet", "corp", "ts.net"]
PRIVATE_NETS = ["10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "127.0.0.0/8", "100.64.0.0/10", "169.254.0.0/16",
                "fc00::/7", "fe80::/10", "::1/128"]
NAME_RE = _re.compile(r"^(\*\.)?[a-z0-9]([a-z0-9-]{0,62})(\.[a-z0-9]([a-z0-9-]{0,62}))*$")
_LIVE: list[ssl.SSLContext] = []


def register_context(ctx_: ssl.SSLContext | None) -> None:
    """Contextos TLS en uso (panel HTTPS, DoT): se recargan en caliente al cambiar el certificado."""
    if ctx_ is not None:
        _LIVE.append(ctx_)


def reload_live() -> int:
    n = 0
    for c in _LIVE:
        try:
            c.load_cert_chain(str(CERT), str(KEY))
            n += 1
        except Exception:  # pragma: no cover
            pass
    return n


def _load_ca():
    if not (CA_CERT.exists() and CA_KEY.exists()):
        return None, None
    return (x509.load_pem_x509_certificate(CA_CERT.read_bytes()),
            serialization.load_pem_private_key(CA_KEY.read_bytes(), None))


def ca_info() -> dict:
    ca, _ = _load_ca()
    if ca is None:
        return {"present": False}
    try:
        nc = ca.extensions.get_extension_for_class(x509.NameConstraints).value
        permitted = [str(n.value) for n in (nc.permitted_subtrees or [])]
    except x509.ExtensionNotFound:
        permitted = []
    exp = ca.not_valid_after_utc
    return {"present": True, "subject": ca.subject.rfc4514_string(), "expires": exp.isoformat(),
            "days_left": (exp - dt.datetime.now(dt.timezone.utc)).days, "constrained": bool(permitted),
            "permitted": permitted, "sha256": ca.fingerprint(hashes.SHA256()).hex(":").upper(),
            "sha1": ca.fingerprint(hashes.SHA1()).hex(":").upper()}  # noqa: S303 (Windows muestra la huella SHA-1)


def create_ca(extra_domains: list[str] | None = None, constrained: bool = True, force: bool = False) -> dict:
    if CA_CERT.exists() and not force:
        return ca_info()
    key = ec.generate_private_key(ec.SECP384R1())
    host = (socket.gethostname() or "bipon").split(".")[0]
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, f"Bipon Net Local CA ({host})"),
                      x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Bipon Net"),
                      x509.NameAttribute(NameOID.ORGANIZATIONAL_UNIT_NAME, "Red local")])
    now = dt.datetime.now(dt.timezone.utc)
    b = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
         .serial_number(x509.random_serial_number()).not_valid_before(now - dt.timedelta(days=1))
         .not_valid_after(now + dt.timedelta(days=3650))
         .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
         .add_extension(x509.KeyUsage(digital_signature=True, key_cert_sign=True, crl_sign=True, content_commitment=False,
                                      key_encipherment=False, data_encipherment=False, key_agreement=False,
                                      encipher_only=False, decipher_only=False), critical=True)
         .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False))
    if constrained:
        doms = sorted({*LOCAL_SUFFIXES, "bipon", host.lower(), *[d.strip(".").lower() for d in (extra_domains or []) if d.strip()]})
        for d in doms:
            if not NAME_RE.match(d):
                raise ValueError(f"Dominio no válido: {d}")
        permitted = [x509.DNSName(d) for d in doms] + [x509.IPAddress(ipaddress.ip_network(n)) for n in PRIVATE_NETS]
        b = b.add_extension(x509.NameConstraints(permitted_subtrees=permitted, excluded_subtrees=None), critical=True)
    ca = b.sign(key, hashes.SHA384())
    _write_key(CA_KEY, key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    CA_CERT.write_bytes(ca.public_bytes(serialization.Encoding.PEM))
    return ca_info()


def ca_der() -> bytes:
    return x509.load_pem_x509_certificate(CA_CERT.read_bytes()).public_bytes(serialization.Encoding.DER)


def ca_mobileconfig() -> bytes:
    """Perfil de configuración para iPhone/iPad/Mac (instala el certificado raíz)."""
    import base64
    import uuid
    data = base64.b64encode(ca_der()).decode()
    u1, u2 = str(uuid.uuid4()).upper(), str(uuid.uuid4()).upper()
    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>PayloadContent</key><array><dict>
<key>PayloadCertificateFileName</key><string>bipon-ca.cer</string>
<key>PayloadContent</key><data>{data}</data>
<key>PayloadDisplayName</key><string>Bipon Net Local CA</string>
<key>PayloadIdentifier</key><string>net.bipon.ca.{u1}</string>
<key>PayloadType</key><string>com.apple.security.root</string>
<key>PayloadUUID</key><string>{u1}</string><key>PayloadVersion</key><integer>1</integer>
</dict></array>
<key>PayloadDescription</key><string>Certificado raíz de tu red local (Bipon Net)</string>
<key>PayloadDisplayName</key><string>Bipon Net — certificado de la red local</string>
<key>PayloadIdentifier</key><string>net.bipon.profile.{u2}</string>
<key>PayloadType</key><string>Configuration</string><key>PayloadUUID</key><string>{u2}</string>
<key>PayloadVersion</key><integer>1</integer>
</dict></plist>"""
    return xml.encode()


def _ca_allows(ca, names: list[str], ips: list[str]) -> list[str]:
    """Nombres que la CA restringida NO podría firmar (el certificado sería rechazado)."""
    try:
        nc = ca.extensions.get_extension_for_class(x509.NameConstraints).value
    except x509.ExtensionNotFound:
        return []
    doms = [str(s.value) for s in nc.permitted_subtrees if isinstance(s, x509.DNSName)]
    nets = [s.value for s in nc.permitted_subtrees if isinstance(s, x509.IPAddress)]
    bad = [n for n in names if not any(n.lstrip("*.") == d or n.endswith("." + d) for d in doms)]
    bad += [i for i in ips if not any(ipaddress.ip_address(i) in net for net in nets if net.version == ipaddress.ip_address(i).version)]
    return bad


def _clean(names: list[str], ips: list[str]) -> tuple[list[str], list[str]]:
    ns, ip_out = [], []
    for n in names:
        n = (n or "").strip().lower().rstrip(".")
        if not n:
            continue
        try:
            ip_out.append(str(ipaddress.ip_address(n)))
            continue
        except ValueError:
            pass
        if not NAME_RE.match(n):
            raise ValueError(f"Nombre no válido: {n}")
        ns.append(n)
    for i in ips:
        i = (i or "").strip()
        if i:
            ip_out.append(str(ipaddress.ip_address(i)))
    return list(dict.fromkeys(ns)), list(dict.fromkeys(ip_out))


def sign_leaf(names: list[str], ips: list[str], days: int = 825, key_type: str = "ec", cn: str = ""):
    """Firma un certificado de servidor con la CA local. Devuelve (cert, clave)."""
    ca, ca_key = _load_ca()
    if ca is None:
        raise ValueError("Primero crea la autoridad certificadora local")
    names, ips = _clean(names, ips)
    if not names and not ips:
        raise ValueError("Indica al menos un nombre o una IP")
    bad = _ca_allows(ca, names, ips)
    if bad:
        raise ValueError("La CA sólo firma IPs privadas y dominios locales (.lan, .local, .home.arpa…). No permitido: " + ", ".join(bad))
    days = max(1, min(int(days or 825), 825))
    key = rsa.generate_private_key(65537, 2048) if key_type == "rsa" else ec.generate_private_key(ec.SECP256R1())
    now = dt.datetime.now(dt.timezone.utc)
    sans = [x509.DNSName(n) for n in names] + [x509.IPAddress(ipaddress.ip_address(i)) for i in ips]
    cn = (cn or (names[0] if names else ips[0]))[:64]
    cert = (x509.CertificateBuilder()
            .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn), x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Bipon Net")]))
            .issuer_name(ca.subject).public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now - dt.timedelta(hours=1)).not_valid_after(now + dt.timedelta(days=days))
            .add_extension(x509.SubjectAlternativeName(sans), critical=False)
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .add_extension(x509.KeyUsage(digital_signature=True, key_encipherment=key_type == "rsa", content_commitment=False,
                                         data_encipherment=False, key_agreement=False, key_cert_sign=False, crl_sign=False,
                                         encipher_only=False, decipher_only=False), critical=True)
            .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
            .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
            .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), critical=False)
            .sign(ca_key, hashes.SHA256()))
    return cert, key


def _pem_key(key) -> bytes:
    return key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())


def default_names() -> tuple[list[str], list[str]]:
    host = (socket.gethostname() or "").split(".")[0].lower()
    names = ["bipon.lan", "bipon", "netping.lan", "localhost"] + ([host, f"{host}.lan"] if host and host not in ("bipon", "localhost") else [])
    ips = ["127.0.0.1"] + sorted({i["ip"] for i in netinfo.interfaces() if i.get("ip")})
    return list(dict.fromkeys(names)), ips


def issue_bipon(extra: list[str] | None = None) -> dict:
    """Certificado del propio panel firmado por la CA (sin avisos en los equipos con la CA instalada)."""
    names, ips = default_names()
    en, ei = _clean(extra or [], [])
    ca, _ = _load_ca()
    names = [n for n in names + en if not _ca_allows(ca, [n], [])]
    ips = [i for i in ips + ei if not _ca_allows(ca, [], [i])]
    cert, key = sign_leaf(names, ips, 825)
    _write_key(KEY, _pem_key(key))
    CERT.write_bytes(cert.public_bytes(serialization.Encoding.PEM) + CA_CERT.read_bytes())
    reload_live()
    return info()


def needs_renewal(extra: list[str] | None = None) -> bool:
    """El certificado del panel (firmado por la CA) caduca pronto o le falta alguna IP actual del equipo."""
    if not CERT.exists() or not CA_CERT.exists():
        return False
    i = info()
    ca = ca_info()
    if i.get("issuer") != ca.get("subject"):
        return False  # certificado propio (Let's Encrypt…) o autofirmado: no se toca
    _, ips = default_names()
    return i["days_left"] < 30 or any(ip not in i["names"] for ip in ips)


# ---------------------------------------------------------------- certificados emitidos para otros equipos
def _meta_path(cid: str) -> Path:
    if not _re.match(r"^[a-f0-9]{12}$", cid):
        raise ValueError("Identificador no válido")
    return ISSUED_DIR / cid


def issue(label: str, names: list[str], ips: list[str], days: int = 825, key_type: str = "ec") -> dict:
    cert, key = sign_leaf(names, ips, days, key_type, cn=label)
    cid = _secrets.token_hex(6)
    d = _meta_path(cid)
    d.mkdir(parents=True, exist_ok=True)
    (d / "cert.pem").write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    _write_key(d / "key.pem", _pem_key(key))
    meta = {"id": cid, "label": (label or "")[:80], "created": _time.time(), "key_type": key_type}
    (d / "meta.json").write_text(_json.dumps(meta))
    return _describe(cid)


def _describe(cid: str) -> dict:
    d = _meta_path(cid)
    meta = _json.loads((d / "meta.json").read_text())
    c = x509.load_pem_x509_certificate((d / "cert.pem").read_bytes())
    san = c.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    exp = c.not_valid_after_utc
    return {**meta, "names": [str(n.value) for n in san], "expires": exp.isoformat(),
            "days_left": (exp - dt.datetime.now(dt.timezone.utc)).days, "sha256": c.fingerprint(hashes.SHA256()).hex(":").upper()}


def issued() -> list[dict]:
    if not ISSUED_DIR.exists():
        return []
    out = []
    for d in ISSUED_DIR.iterdir():
        try:
            out.append(_describe(d.name))
        except Exception:
            continue
    return sorted(out, key=lambda x: -x["created"])


def delete_issued(cid: str) -> None:
    import shutil
    shutil.rmtree(_meta_path(cid), ignore_errors=True)


def renew_issued(cid: str) -> dict:
    old = _describe(cid)
    names = [n for n in old["names"] if not _is_ip(n)]
    ips = [n for n in old["names"] if _is_ip(n)]
    cert, key = sign_leaf(names, ips, 825, old.get("key_type", "ec"), cn=old["label"])
    d = _meta_path(cid)
    (d / "cert.pem").write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    _write_key(d / "key.pem", _pem_key(key))
    return _describe(cid)


def _is_ip(v: str) -> bool:
    try:
        ipaddress.ip_address(v)
        return True
    except ValueError:
        return False


def export_issued(cid: str, fmt: str, password: str = "") -> tuple[bytes, str, str]:
    """Descarga: pem (cert+cadena), key, bundle (cert+clave+CA) o pfx (Windows/IIS, con contraseña)."""
    d = _meta_path(cid)
    cert_pem = (d / "cert.pem").read_bytes()
    key_pem = (d / "key.pem").read_bytes()
    base = _re.sub(r"[^a-zA-Z0-9.-]+", "-", _describe(cid)["label"] or cid).strip("-") or cid
    if fmt == "pem":
        return cert_pem + CA_CERT.read_bytes(), f"{base}-fullchain.pem", "application/x-pem-file"
    if fmt == "key":
        return key_pem, f"{base}-key.pem", "application/x-pem-file"
    if fmt == "bundle":
        return key_pem + cert_pem + CA_CERT.read_bytes(), f"{base}-bundle.pem", "application/x-pem-file"
    if fmt == "pfx":
        enc = serialization.BestAvailableEncryption(password.encode()) if password else serialization.NoEncryption()
        data = pkcs12.serialize_key_and_certificates(base.encode(), serialization.load_pem_private_key(key_pem, None),
                                                    x509.load_pem_x509_certificate(cert_pem), [x509.load_pem_x509_certificate(CA_CERT.read_bytes())], enc)
        return data, f"{base}.pfx", "application/x-pkcs12"
    raise ValueError("Formato no válido")
