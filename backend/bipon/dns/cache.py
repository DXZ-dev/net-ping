"""Caché DNS LRU con TTL, caché optimista (serve-stale) y prefetch."""
from __future__ import annotations

import time
from collections import OrderedDict
from dataclasses import dataclass

from dnslib import DNSRecord, RCODE, QTYPE

STALE_TTL = 30  # TTL que se entrega al cliente para respuestas caducadas


@dataclass
class Entry:
    wire: bytes
    stored: float
    expires: float
    ttl: int
    hits: int = 0
    refreshing: bool = False


class DNSCache:
    def __init__(self, size: int = 20000, min_ttl: int = 0, max_ttl: int = 86400, optimistic: bool = True,
                 stale_max: int = 86400):
        self.size = size
        self.min_ttl = min_ttl
        self.max_ttl = max_ttl
        self.optimistic = optimistic
        self.stale_max = stale_max
        self._d: "OrderedDict[tuple, Entry]" = OrderedDict()
        self.hits = 0
        self.misses = 0
        self.stale_hits = 0

    def configure(self, size: int, min_ttl: int, max_ttl: int, optimistic: bool) -> None:
        self.size, self.min_ttl, self.max_ttl, self.optimistic = size, min_ttl, max_ttl, optimistic
        while len(self._d) > self.size:
            self._d.popitem(last=False)

    @staticmethod
    def key(qname: str, qtype: int, extra: str = "") -> tuple:
        return (qname.lower().rstrip("."), qtype, extra)

    def _ttl_of(self, rec: DNSRecord) -> int | None:
        if rec.header.rcode not in (RCODE.NOERROR, RCODE.NXDOMAIN):
            return None
        if rec.header.tc:
            return None
        if rec.rr:
            ttl = min(r.ttl for r in rec.rr)
        else:
            # respuesta negativa / NODATA: TTL del SOA (RFC 2308), máx. 5 min
            soa = [r for r in rec.auth if r.rtype == QTYPE.SOA]
            ttl = min(soa[0].ttl, soa[0].rdata.times[-1]) if soa else 60
            ttl = min(ttl, 300)
        return max(self.min_ttl, min(self.max_ttl, int(ttl)))

    def put(self, key: tuple, rec: DNSRecord, wire: bytes | None = None) -> None:
        if self.size <= 0:
            return
        ttl = self._ttl_of(rec)
        if not ttl:
            return
        now = time.monotonic()
        old = self._d.pop(key, None)
        self._d[key] = Entry(wire or rec.pack(), now, now + ttl, ttl, hits=old.hits if old else 0)
        if len(self._d) > self.size:
            self._d.popitem(last=False)

    def get(self, key: tuple, qid: int) -> tuple[DNSRecord | None, str]:
        """Devuelve (registro, estado) con estado in {'hit','stale','prefetch','miss'}."""
        e = self._d.get(key)
        if e is None:
            self.misses += 1
            return None, "miss"
        now = time.monotonic()
        remaining = e.expires - now
        if remaining <= 0:
            if not self.optimistic or -remaining > self.stale_max:
                self._d.pop(key, None)
                self.misses += 1
                return None, "miss"
            state = "stale"
            new_ttl = STALE_TTL
            self.stale_hits += 1
        else:
            state = "hit"
            new_ttl = None
            # prefetch: entrada popular a punto de caducar (último 10 % del TTL)
            if e.hits >= 2 and remaining < max(1.0, e.ttl * 0.1):
                state = "prefetch"
        e.hits += 1
        self.hits += 1
        self._d.move_to_end(key)
        rec = DNSRecord.parse(e.wire)
        rec.header.id = qid
        elapsed = int(now - e.stored)
        for rr in rec.rr + rec.auth + rec.ar:
            if rr.rtype == QTYPE.OPT:
                continue
            rr.ttl = new_ttl if new_ttl is not None else max(0, rr.ttl - elapsed)
        return rec, state

    def mark_refreshing(self, key: tuple) -> bool:
        e = self._d.get(key)
        if e is None or e.refreshing:
            return False
        e.refreshing = True
        return True

    def clear(self) -> int:
        n = len(self._d)
        self._d.clear()
        return n

    def usage(self) -> float:
        return len(self._d) / self.size * 100 if self.size else 0.0

    def trim(self, target_pct: float = 70.0) -> int:
        """Libera espacio: primero lo caducado hace tiempo, luego lo menos usado (LRU), hasta target_pct."""
        before = len(self._d)
        now = time.monotonic()
        for k in [k for k, e in self._d.items() if now - e.expires > 600]:
            del self._d[k]
        limit = int(self.size * target_pct / 100)
        while len(self._d) > limit:
            self._d.popitem(last=False)
        return before - len(self._d)

    def stats(self) -> dict:
        total = self.hits + self.misses
        return {
            "entries": len(self._d),
            "size": self.size,
            "hits": self.hits,
            "misses": self.misses,
            "stale_hits": self.stale_hits,
            "hit_ratio": round(self.hits / total * 100, 1) if total else 0.0,
        }
