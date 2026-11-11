"""Registro de la aplicación en memoria (para verlo desde Ajustes → Registros, también en Docker)."""
from __future__ import annotations

import logging
import time
from collections import deque

BUFFER: deque = deque(maxlen=3000)


class RingHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = record.getMessage()
            if record.exc_info:
                msg += " | " + logging.Formatter().formatException(record.exc_info).splitlines()[-1]
            BUFFER.append({"ts": record.created, "level": record.levelname.lower(), "source": record.name, "message": msg[:2000]})
        except Exception:  # pragma: no cover
            pass


def install() -> None:
    root = logging.getLogger()
    if not any(isinstance(h, RingHandler) for h in root.handlers):
        h = RingHandler(logging.INFO)
        root.addHandler(h)


def read(level: str = "", q: str = "", limit: int = 300, since: float = 0) -> list[dict]:
    order = {"debug": 0, "info": 1, "warning": 2, "error": 3, "critical": 4}
    lv = order.get(level, 0)
    q = q.lower()
    out = [r for r in BUFFER if order.get(r["level"], 1) >= lv and r["ts"] > since and (not q or q in r["message"].lower() or q in r["source"])]
    return out[-limit:][::-1]


_ = time
