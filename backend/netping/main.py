"""Punto de entrada: servidor web (FastAPI) + servicios de red."""
from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import __version__
from .api import api, public
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
    resp = await call_next(request)
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


if FRONTEND_DIR.exists():
    app.mount("/assets", StaticFiles(directory=FRONTEND_DIR), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    async def spa(path: str):
        return FileResponse(FRONTEND_DIR / "index.html", headers={"cache-control": "no-cache"})


def run() -> None:
    import uvicorn
    uvicorn.run(app, host=WEB_HOST, port=WEB_PORT, log_level="warning", proxy_headers=False)


if __name__ == "__main__":
    run()
