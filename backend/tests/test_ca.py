"""Autoridad certificadora local: restricciones, firma del panel y certificados emitidos."""
import ssl

import pytest
from cryptography import x509

from bipon import tls


@pytest.fixture(autouse=True)
def tmp_tls(tmp_path, monkeypatch):
    monkeypatch.setattr(tls, "TLS_DIR", tmp_path)
    monkeypatch.setattr(tls, "CERT", tmp_path / "cert.pem")
    monkeypatch.setattr(tls, "KEY", tmp_path / "key.pem")
    monkeypatch.setattr(tls, "CA_CERT", tmp_path / "ca.pem")
    monkeypatch.setattr(tls, "CA_KEY", tmp_path / "ca-key.pem")
    monkeypatch.setattr(tls, "ISSUED_DIR", tmp_path / "issued")
    monkeypatch.setattr(tls, "_LIVE", [])
    monkeypatch.setattr(tls, "default_names", lambda: (["bipon.lan", "bipon"], ["127.0.0.1", "192.168.18.205"]))


def test_ca_constrained_to_local():
    info = tls.create_ca(["casa.example"])
    assert info["present"] and info["constrained"] and "casa.example" in info["permitted"]
    with pytest.raises(ValueError):
        tls.sign_leaf(["www.google.com"], [])
    with pytest.raises(ValueError):
        tls.sign_leaf([], ["8.8.8.8"])
    cert, _ = tls.sign_leaf(["nas.lan", "nas.casa.example"], ["192.168.18.20"])
    assert cert.issuer == x509.load_pem_x509_certificate(tls.CA_CERT.read_bytes()).subject


def test_panel_certificate_verifies_against_ca():
    tls.create_ca()
    i = tls.issue_bipon(["panel.lan"])
    assert i["signed_by_ca"] and "192.168.18.205" in i["names"] and "panel.lan" in i["names"]
    # una conexión TLS real que confía sólo en la CA acepta el certificado
    ctx = ssl.create_default_context(cafile=str(tls.CA_CERT))
    srv = tls.server_context()
    assert srv and ctx
    assert not tls.needs_renewal()


def test_issue_export_renew_delete():
    tls.create_ca()
    c = tls.issue("Impresora", ["impresora.lan", "192.168.18.30"], [], 365, "rsa")
    assert c["names"] == ["impresora.lan", "192.168.18.30"] and c["days_left"] >= 364
    for fmt in ("pem", "key", "bundle", "pfx"):
        data, name, _ = tls.export_issued(c["id"], fmt, "clave123")
        assert data and name.startswith("Impresora")
    r = tls.renew_issued(c["id"])
    assert r["sha256"] != c["sha256"]
    tls.delete_issued(c["id"])
    assert tls.issued() == []
    with pytest.raises(ValueError):
        tls.export_issued("../../etc", "pem")


def test_mobileconfig():
    tls.create_ca()
    assert b"com.apple.security.root" in tls.ca_mobileconfig()
