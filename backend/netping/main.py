"""Punto de entrada: servidor web (FastAPI, HTTP + HTTPS) + servicios de red."""
from __future__ import annotations

import asyncio
import base64
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response

from . import __version__
from .api import api, public
from . import api_ext  # noqa: F401  (registra rutas adicionales)
from .config import FRONTEND_DIR, WEB_HOST, WEB_PORT
from .core import Context

logging.basicConfig(
    level=os.environ.get("NETPING_LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
)
log = logging.getLogger("netping")


@asynccontextmanager
async def lifespan(app: FastAPI):
    ctx = Context()
    app.state.ctx = ctx
    await ctx.start()
    log.info("NetPing %s listo — interfaz web en http://%s:%s", __version__, ctx.devices.self_ip or WEB_HOST, WEB_PORT)
    try:
        yield
    finally:
        await ctx.stop()


app = FastAPI(title="NetPing", version=__version__, lifespan=lifespan, docs_url="/api/docs", redoc_url=None,
              openapi_url="/api/openapi.json")
app.include_router(public)
app.include_router(api)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    ctx = getattr(request.app.state, "ctx", None)
    # redirección opcional HTTP → HTTPS (excepto la API para integraciones antiguas)
    if (ctx and request.url.scheme == "http" and (ctx.settings.get("tls") or {}).get("redirect_http")
            and not request.url.path.startswith(("/api/", "/dns-query"))):
        port = (ctx.settings.get("tls") or {}).get("port", 8443)
        return RedirectResponse(f"https://{request.url.hostname}:{port}{request.url.path}", status_code=307)
    resp = await call_next(request)
    # registro de auditoría: toda modificación hecha por la API
    if ctx and request.method in ("POST", "PUT", "PATCH", "DELETE") and request.url.path.startswith("/api/") \
            and not request.url.path.startswith(("/api/auth/login", "/api/auth/logout", "/api/lan/", "/api/push/")):
        user = getattr(request.state, "user", None) or {}
        try:
            ctx.db.execute("INSERT INTO audit(ts,username,ip,method,path,status) VALUES (?,?,?,?,?,?)",
                           (__import__("time").time(), user.get("username", "—"), request.client.host if request.client else "",
                            request.method, request.url.path, resp.status_code))
        except Exception:
            pass
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("X-Frame-Options", "DENY")
    resp.headers.setdefault("Referrer-Policy", "same-origin")
    if request.url.path.startswith("/api/"):
        resp.headers.setdefault("Cache-Control", "no-store")
    return resp


@app.exception_handler(Exception)
async def unhandled(request: Request, exc: Exception):  # pragma: no cover
    log.exception("error no controlado en %s", request.url.path)
    return JSONResponse({"detail": f"Error interno: {type(exc).__name__}"}, status_code=500)


# ---------------------------------------------------------------- DNS-over-HTTPS (RFC 8484)
@app.api_route("/dns-query", methods=["GET", "POST"], include_in_schema=False)
async def doh(request: Request):
    ctx = request.app.state.ctx
    if not (ctx.settings.get("tls") or {}).get("doh", True):
        return Response(status_code=404)
    if request.method == "POST":
        if request.headers.get("content-type", "").split(";")[0] != "application/dns-message":
            return Response(status_code=415)
        wire = await request.body()
    else:
        q = request.query_params.get("dns")
        if not q:
            return Response(status_code=400)
        try:
            wire = base64.urlsafe_b64decode(q + "=" * (-len(q) % 4))
        except Exception:
            return Response(status_code=400)
    if not wire or len(wire) > 65535:
        return Response(status_code=400)
    client = request.client.host if request.client else "doh"
    out = await ctx.dns.handle(wire, client, tcp=True)
    if out is None:
        return Response(status_code=400)
    return Response(out, media_type="application/dns-message", headers={"cache-control": "max-age=0"})


if FRONTEND_DIR.exists():
    from fastapi.staticfiles import StaticFiles

    app.mount("/assets", StaticFiles(directory=FRONTEND_DIR), name="assets")

    @app.get("/sw.js", include_in_schema=False)
    async def service_worker():
        return FileResponse(FRONTEND_DIR / "sw.js", media_type="application/javascript",
                            headers={"cache-control": "no-cache", "service-worker-allowed": "/"})

    @app.get("/manifest.webmanifest", include_in_schema=False)
    async def manifest():
        return FileResponse(FRONTEND_DIR / "manifest.webmanifest", media_type="application/manifest+json")

    @app.get("/{path:path}", include_in_schema=False)
    async def spa(path: str):
        return FileResponse(FRONTEND_DIR / "index.html", headers={"cache-control": "no-cache"})


async def _serve() -> None:
    import uvicorn

    from . import tls
    from .config import init_settings

    servers = [uvicorn.Server(uvicorn.Config(app, host=WEB_HOST, port=WEB_PORT, log_level="warning", proxy_headers=False,
                                             lifespan="on"))]
    s = init_settings().get("tls") or {}
    if s.get("enabled", True):
        try:
            tls.generate_self_signed()
            servers.append(uvicorn.Server(uvicorn.Config(app, host=WEB_HOST, port=int(s.get("port", 8443)), log_level="warning",
                                                         ssl_certfile=str(tls.CERT), ssl_keyfile=str(tls.KEY), lifespan="off")))
        except Exception as exc:
            log.error("HTTPS desactivado: %s", exc)
    await asyncio.gather(*(srv.serve() for srv in servers))


def run() -> None:
    asyncio.run(_serve())


if __name__ == "__main__":
    run()
