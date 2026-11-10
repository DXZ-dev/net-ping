"""Utilidades de ping/latencia (ICMP via iputils-ping, con alternativa TCP)."""
from __future__ import annotations

import asyncio
import re
import shutil
import statistics
import time

_TIME_RE = re.compile(r"time[=<]([\d.]+)\s*ms")
HOST_RE = re.compile(r"^(?!-)[A-Za-z0-9.:_-]{1,253}$")


def valid_host(host: str) -> bool:
    return bool(host) and bool(HOST_RE.match(host)) and not host.startswith("-")


def summarize(times: list[float], sent: int) -> dict:
    recv = len(times)
    out = {"sent": sent, "received": recv, "loss": round((sent - recv) / sent * 100, 1) if sent else 100.0,
           "min": None, "avg": None, "max": None, "jitter": None, "times": [round(t, 2) for t in times]}
    if times:
        out["min"] = round(min(times), 2)
        out["avg"] = round(statistics.fmean(times), 2)
        out["max"] = round(max(times), 2)
        if len(times) > 1:
            diffs = [abs(times[i] - times[i - 1]) for i in range(1, len(times))]
            out["jitter"] = round(statistics.fmean(diffs), 2)
        else:
            out["jitter"] = 0.0
    return out


async def ping(host: str, count: int = 3, interval: float = 0.2, timeout: float = 1.0) -> dict:
    if not valid_host(host):
        raise ValueError("host inválido")
    count = max(1, min(count, 100))
    if not shutil.which("ping"):
        return await tcp_ping(host, 443, count)
    args = ["ping", "-n", "-c", str(count), "-i", str(max(0.2, interval)), "-W", str(max(1, int(timeout))), host]
    if ":" in host:
        args.insert(1, "-6")
    proc = await asyncio.create_subprocess_exec(*args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), count * max(0.2, interval) + timeout + 5)
    except asyncio.TimeoutError:
        proc.kill()
        out = b""
    times = [float(m) for m in _TIME_RE.findall(out.decode(errors="ignore"))]
    return summarize(times, count)


async def tcp_ping(host: str, port: int = 443, count: int = 3, timeout: float = 1.5) -> dict:
    times = []
    for _ in range(count):
        t0 = time.perf_counter()
        try:
            _, w = await asyncio.wait_for(asyncio.open_connection(host, port), timeout)
            times.append((time.perf_counter() - t0) * 1000)
            w.close()
        except Exception:
            pass
        await asyncio.sleep(0.1)
    return summarize(times, count)
