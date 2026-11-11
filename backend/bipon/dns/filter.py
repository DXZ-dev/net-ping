"""Motor de filtrado: listas de bloqueo por categoría, reglas personalizadas, regex,
aplicaciones, perfiles parentales y SafeSearch."""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Iterator

from ..catalog import SAFESEARCH, SERVICE_MAP, YOUTUBE_RESTRICT

log = logging.getLogger("bipon.filter")

# Códigos de estado del registro de consultas
ST_OK, ST_CACHE, ST_LIST, ST_RULE, ST_SERVICE, ST_DEVICE, ST_LOCAL, ST_SAFE, ST_ERROR, ST_REGEX, ST_STALE, ST_GEO, ST_REBIND, ST_TLD = range(14)
STATUS_NAMES = {
    ST_OK: "Permitida", ST_CACHE: "Caché", ST_LIST: "Bloqueada (lista)", ST_RULE: "Bloqueada (regla)",
    ST_SERVICE: "Bloqueada (app)", ST_DEVICE: "Bloqueada (control parental)", ST_LOCAL: "Registro local",
    ST_SAFE: "Búsqueda segura", ST_ERROR: "Error", ST_REGEX: "Bloqueada (regex)", ST_STALE: "Caché optimista",
    ST_GEO: "Bloqueada (país)", ST_REBIND: "Bloqueada (rebinding)", ST_TLD: "Bloqueada (TLD)",
}
BLOCKED_STATUSES = (ST_LIST, ST_RULE, ST_SERVICE, ST_DEVICE, ST_REGEX, ST_GEO, ST_REBIND, ST_TLD)

CAT_ADS, CAT_SECURITY, CAT_ADULT, CAT_CUSTOM = 1, 2, 4, 8
CATEGORY_BITS = {"ads": CAT_ADS, "security": CAT_SECURITY, "adult": CAT_ADULT, "custom": CAT_CUSTOM}
CATEGORY_LABEL = {CAT_ADS: "anuncios/rastreo", CAT_SECURITY: "seguridad", CAT_ADULT: "adultos", CAT_CUSTOM: "personalizada"}

DOMAIN_RE = re.compile(r"^(?=.{1,253}$)(?!-)[a-z0-9_-]{1,63}(?<!-)(\.(?!-)[a-z0-9_-]{1,63}(?<!-))*\.?$")
_HOSTS_IPS = {"0.0.0.0", "127.0.0.1", "::", "::1", "0", "255.255.255.255", "fe80::1%lo0", "ff00::0", "ff02::1", "ff02::2"}
_SKIP_DOMAINS = {"localhost", "localhost.localdomain", "local", "broadcasthost", "ip6-localhost", "ip6-loopback",
                 "0.0.0.0", "ip6-localnet", "ip6-mcastprefix", "ip6-allnodes", "ip6-allrouters", "ip6-allhosts"}


def suffixes(name: str) -> Iterator[str]:
    """a.b.c -> a.b.c, b.c, c"""
    yield name
    i = name.find(".")
    while i != -1:
        name = name[i + 1:]
        yield name
        i = name.find(".")


def parse_list_line(line: str) -> tuple[str, bool] | None:
    """Devuelve (dominio, comodín) o None. Soporta hosts, dominios planos, ||adblock^ y *.wildcard."""
    line = line.strip()
    if not line or line[0] in "#!/[":
        return None
    if line.startswith("@@"):
        return None
    wildcard = False
    if line.startswith("||"):
        body = line[2:]
        end = body.find("^")
        if end == -1:
            return None
        opts = body[end + 1:]
        if opts and opts not in ("$important", "|") and not opts.startswith("$important"):
            return None
        dom = body[:end]
        wildcard = True
    else:
        if "#" in line:
            line = line.split("#", 1)[0].strip()
        parts = line.split()
        if not parts:
            return None
        if parts[0] in _HOSTS_IPS:
            if len(parts) < 2:
                return None
            dom = parts[1]
        else:
            dom = parts[0]
        if dom.startswith("*."):
            dom = dom[2:]
            wildcard = True
    dom = dom.lower().rstrip(".")
    if dom in _SKIP_DOMAINS or "/" in dom or "*" in dom or not DOMAIN_RE.match(dom):
        return None
    return dom, wildcard


@dataclass
class Policy:
    """Política efectiva para un cliente en un instante dado."""
    profile_id: str = "default"
    profile_name: str = "Predeterminado"
    filtering: bool = True
    threat: bool = True
    adult: bool = False
    safesearch: bool = False
    youtube: bool = False
    services: frozenset = frozenset()
    block_all: bool = False
    block_reason: str = ""
    device: str = ""
    short_ttl: bool = False  # dispositivo con control parental: TTL corto para que los cambios se apliquen al momento

    @property
    def list_mask(self) -> int:
        m = 0
        if self.filtering:
            m |= CAT_ADS | CAT_CUSTOM
        if self.threat:
            m |= CAT_SECURITY
        if self.adult:
            m |= CAT_ADULT
        return m


@dataclass
class Decision:
    status: int
    reason: str
    cname: str | None = None

    @property
    def blocked(self) -> bool:
        return self.status in BLOCKED_STATUSES


@dataclass
class _Data:
    exact: dict = field(default_factory=dict)       # dominio -> máscara de categoría
    wild: dict = field(default_factory=dict)        # dominio (incl. subdominios) -> máscara
    allow_exact: set = field(default_factory=set)
    allow_wild: set = field(default_factory=set)
    deny_wild: set = field(default_factory=set)
    allow_re: list = field(default_factory=list)
    deny_re: list = field(default_factory=list)
    service_idx: dict = field(default_factory=dict)  # dominio -> set(service ids)
    counts: dict = field(default_factory=dict)


class FilterEngine:
    def __init__(self):
        self.d = _Data()
        self.protected: set[str] = set()  # infraestructura de juegos/streaming
        self.blocked_tlds: set[str] = set()
        self._build_service_index(self.d)

    @staticmethod
    def _build_service_index(d: _Data) -> None:
        idx: dict[str, set] = {}
        for sid, svc in SERVICE_MAP.items():
            for dom in svc["domains"]:
                idx.setdefault(dom.lower(), set()).add(sid)
        d.service_idx = idx

    # ---- carga ----------------------------------------------------------
    def build(self, lists: Iterable[tuple[Path, str]], rules: Iterable[dict]) -> dict:
        d = _Data()
        self._build_service_index(d)
        per_list: dict[str, int] = {}
        for path, category in lists:
            bit = CATEGORY_BITS.get(category, CAT_CUSTOM)
            n = 0
            try:
                with open(path, "r", encoding="utf-8", errors="ignore") as fh:
                    for line in fh:
                        parsed = parse_list_line(line)
                        if not parsed:
                            continue
                        dom, wild = parsed
                        target = d.wild if wild else d.exact
                        target[dom] = target.get(dom, 0) | bit
                        n += 1
            except FileNotFoundError:
                pass
            per_list[str(path)] = n
        for r in rules:
            if not r.get("enabled", 1):
                continue
            v, kind = r["value"].strip().lower(), r["kind"]
            try:
                if kind == "allow":
                    d.allow_wild.add(v.lstrip("*.").rstrip("."))
                elif kind == "deny":
                    d.deny_wild.add(v.lstrip("*.").rstrip("."))
                elif kind == "regex_allow":
                    d.allow_re.append(re.compile(r["value"], re.I))
                elif kind == "regex_deny":
                    d.deny_re.append(re.compile(r["value"], re.I))
            except re.error as exc:
                log.warning("regex inválida %r: %s", r["value"], exc)
        d.counts = {
            "exact": len(d.exact), "wildcard": len(d.wild), "total": len(d.exact) + len(d.wild),
            "per_list": per_list, "allow": len(d.allow_wild) + len(d.allow_re),
            "deny": len(d.deny_wild) + len(d.deny_re),
        }
        self.d = d  # intercambio atómico
        return d.counts

    # ---- evaluación -----------------------------------------------------
    def _list_match(self, name: str, mask: int) -> int:
        d = self.d
        m = d.exact.get(name, 0) & mask
        if m:
            return m
        for s in suffixes(name):
            m = d.wild.get(s, 0) & mask
            if m:
                return m
        return 0

    def _services_for(self, name: str) -> set:
        out: set = set()
        idx = self.d.service_idx
        for s in suffixes(name):
            ids = idx.get(s)
            if ids:
                out |= ids
        return out

    def services_for(self, name: str) -> set:
        return self._services_for(name)

    def is_allowed(self, name: str) -> str | None:
        d = self.d
        for s in suffixes(name):
            if s in d.allow_wild:
                return s
        for rx in d.allow_re:
            if rx.search(name):
                return rx.pattern
        return None

    def check(self, name: str, policy: Policy, blocking_enabled: bool, global_services: set) -> Decision | None:
        if policy.block_all:
            return Decision(ST_DEVICE, policy.block_reason or "Internet pausado")
        allowed = self.is_allowed(name)
        if not allowed:
            svc_hits = self._services_for(name)
            if svc_hits:
                hit = svc_hits & policy.services
                if hit:
                    return Decision(ST_SERVICE, f"App: {SERVICE_MAP[next(iter(hit))]['name']} (perfil {policy.profile_name})")
                if blocking_enabled:
                    hit = svc_hits & global_services
                    if hit:
                        return Decision(ST_SERVICE, f"App: {SERVICE_MAP[next(iter(hit))]['name']}")
            protected = bool(self.protected) and any(s in self.protected for s in suffixes(name))
            if blocking_enabled and not protected:
                if self.blocked_tlds:
                    tld = name.rsplit(".", 1)[-1]
                    if tld in self.blocked_tlds:
                        return Decision(ST_TLD, f"TLD bloqueado: .{tld}")
                d = self.d
                for s in suffixes(name):
                    if s in d.deny_wild:
                        return Decision(ST_RULE, f"Regla: {s}")
                for rx in d.deny_re:
                    if rx.search(name):
                        return Decision(ST_REGEX, f"Regex: {rx.pattern}")
                m = self._list_match(name, policy.list_mask)
                if m:
                    cat = next(CATEGORY_LABEL[b] for b in (CAT_SECURITY, CAT_ADULT, CAT_ADS, CAT_CUSTOM) if m & b)
                    return Decision(ST_LIST, f"Lista: {cat}")
        if policy.safesearch and name in SAFESEARCH:
            return Decision(ST_SAFE, "Búsqueda segura forzada", cname=SAFESEARCH[name])
        if policy.youtube and name in YOUTUBE_RESTRICT:
            return Decision(ST_SAFE, "YouTube modo restringido", cname=YOUTUBE_RESTRICT[name])
        return None

    def explain(self, name: str) -> dict:
        """Diagnóstico completo de por qué un dominio se bloquearía."""
        name = name.lower().rstrip(".")
        d = self.d
        hits = []
        for s in suffixes(name):
            if s in d.allow_wild:
                hits.append({"type": "allow", "match": s})
            if s in d.deny_wild:
                hits.append({"type": "deny", "match": s})
            if d.wild.get(s):
                hits.append({"type": "list", "match": s, "categories": _mask_names(d.wild[s]), "wildcard": True})
        if d.exact.get(name):
            hits.append({"type": "list", "match": name, "categories": _mask_names(d.exact[name]), "wildcard": False})
        for rx in d.allow_re:
            if rx.search(name):
                hits.append({"type": "regex_allow", "match": rx.pattern})
        for rx in d.deny_re:
            if rx.search(name):
                hits.append({"type": "regex_deny", "match": rx.pattern})
        services = sorted(self._services_for(name))
        return {"domain": name, "matches": hits, "services": [SERVICE_MAP[s]["name"] for s in services],
                "safesearch": SAFESEARCH.get(name) or YOUTUBE_RESTRICT.get(name)}


def _mask_names(m: int) -> list[str]:
    return [CATEGORY_LABEL[b] for b in (CAT_ADS, CAT_SECURITY, CAT_ADULT, CAT_CUSTOM) if m & b]
