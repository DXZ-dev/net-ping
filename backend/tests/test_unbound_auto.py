"""Autoconfiguración de Unbound: elección del modo y de los servidores más cercanos."""
from bipon.unbound import render_config
from bipon.unbound_auto import choose, score


def row(provider, ip, tls, median, reliability=100.0, cold=None):
    return {"provider": provider, "name": provider.title(), "ip": ip, "tls_name": tls, "median": median,
            "reliability": reliability, "cold": cold or median * 3}


def test_picks_two_fastest_providers_in_order(tmp_path):
    dot = [row("google", "8.8.8.8", "dns.google", 30), row("cloudflare", "1.1.1.1", "cloudflare-dns.com", 8),
           row("cloudflare", "1.0.0.1", "cloudflare-dns.com", 9), row("quad9", "9.9.9.9", "dns.quad9.net", 12),
           row("adguard", "94.140.14.140", "unfiltered.adguard-dns.com", 5, reliability=60)]  # rápido pero poco fiable
    p = choose(dot, None)
    assert p["mode"] == "forward-tls" and p["chosen"] == ["cloudflare", "quad9"]
    assert p["forward_servers"][:2] == ["1.1.1.1@853#cloudflare-dns.com", "1.0.0.1@853#cloudflare-dns.com"]
    assert p["forward_servers"][2] == "9.9.9.9@853#dns.quad9.net"
    txt = render_config({"mode": p["mode"], "forward_servers": p["forward_servers"], "msg_cache_size": "64m",
                         "rrset_cache_size": "128m", "so_rcvbuf": "1m"}, tmp_path)
    assert "forward-tls-upstream: yes" in txt and "forward-addr: 1.1.1.1@853#cloudflare-dns.com" in txt


def test_falls_back_to_plain_then_recursive():
    blocked = [{"provider": "cloudflare", "name": "C", "ip": "1.1.1.1", "tls_name": "x", "reliability": 0.0}]
    plain = [row("cloudflare", "1.1.1.1", "", 10), row("google", "8.8.8.8", "", 14)]
    p = choose(blocked, plain)
    assert p["mode"] == "forward" and p["forward_servers"][0] == "1.1.1.1"
    assert choose(blocked, blocked)["mode"] == "recursive"


def test_unreliable_scores_last():
    assert score({"median": 3, "reliability": 50}) == 1e9 and score({"median": 20, "reliability": 100}) < 1e9


def test_custom_config_stays_in_server_block(tmp_path):
    """La configuración avanzada no debe caer dentro de forward-zone/remote-control (validado con unbound-checkconf)."""
    import shutil
    import subprocess
    txt = render_config({"mode": "forward-tls", "forward_servers": ["1.1.1.1@853#cloudflare-dns.com"], "msg_cache_size": "64m",
                         "rrset_cache_size": "128m", "so_rcvbuf": "1m", "dnssec": False,
                         "custom": "do-not-query-localhost: no\nlocal-zone: \"example.\" refuse"}, tmp_path)
    assert txt.rstrip().endswith('local-zone: "example." refuse') and "\nserver:\n    do-not-query-localhost: no" in txt
    chk = shutil.which("unbound-checkconf")
    if chk:
        f = tmp_path / "u.conf"
        f.write_text(txt.replace(f'directory: "{tmp_path}"', f'directory: "{tmp_path}"'))
        r = subprocess.run([chk, str(f)], capture_output=True, text=True)
        assert r.returncode == 0, r.stdout + r.stderr
