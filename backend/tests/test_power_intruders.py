"""Auto-optimización de Unbound, sugerencia de método de energía y clasificación de intrusos."""
import ipaddress

from bipon.unbound import recommend_config


def test_unbound_scales_with_hardware():
    big = recommend_config({"do_ip6": False}, 8, 16 * 1024 ** 3)["config"]
    small = recommend_config({"do_ip6": False}, 1, 512 * 1024 ** 2)["config"]
    assert big["num_threads"] == 8 and small["num_threads"] == 1
    assert int(big["rrset_cache_size"][:-1]) > int(small["rrset_cache_size"][:-1])
    assert small["cache_min_ttl"] == 60 and small["prefetch"] and small["serve_expired"]
    # los slabs del config renderizado son potencia de 2 >= hilos
    from bipon.unbound import render_config
    from pathlib import Path
    txt = render_config(big, Path("/tmp"))
    assert "num-threads: 8" in txt and "msg-cache-slabs: 8" in txt


def test_intruder_is_outside_local_nets():
    nets = [ipaddress.ip_network("192.168.18.0/24")]
    assert any(ipaddress.ip_address("192.168.18.50") in n for n in nets)       # local
    assert not any(ipaddress.ip_address("10.0.0.9") in n for n in nets)        # intruso (otra subred)


class _Ctx:
    def __init__(self, brand, kind="phone", ha=False):
        self.devices = type("D", (), {"devices": {"m": {"brand_name": brand, "kind": kind}}})()
        self._ha = ha
        self.settings = type("S", (), {"get": lambda _self, k, d=None: {"homeassistant": {"url": "x"}} if (k == "integrations" and ha) else {}})()


def test_power_suggestions():
    from bipon.power import PowerControl
    assert PowerControl(_Ctx("Sony PlayStation", "console")).suggest("m")[0] in ("wol", "playstation")
    assert "ha" in PowerControl(_Ctx("Shelly")).suggest("m")
    # un PC sin marca conocida sugiere WoL
    assert PowerControl(_Ctx("", "pc")).suggest("m") == ["wol"]
