"""FastAPI application wiring.

The same app object serves locally under uvicorn and in AWS via Mangum
(see lambda_handler.py).
"""

from __future__ import annotations

import logging
from pathlib import Path
from urllib.parse import urlparse

from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from gamatrix import __version__
from gamatrix.auth.dependencies import RedirectToLogin
from gamatrix.auth.routes import router as auth_router
from gamatrix.games.routes import router as games_router
from gamatrix.preferences import router as preferences_router
from gamatrix.upload import router as upload_router

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
    level=logging.INFO,
    datefmt="%Y-%m-%d %H:%M:%S",
)

app = FastAPI(title="gamatrix", version=__version__)

STATIC_DIR = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

app.include_router(auth_router)
app.include_router(games_router)
app.include_router(preferences_router)
app.include_router(upload_router)


@app.middleware("http")
async def _canonical_domain(request: Request, call_next):
    """Keep production WebAuthn ceremonies scoped to one stable hostname."""
    from gamatrix.config import get_settings

    settings = get_settings()
    canonical = urlparse(settings.app_base_url)
    if (
        not settings.local_dev
        and canonical.hostname
        and request.url.hostname != canonical.hostname
    ):
        target = f"{settings.app_base_url.rstrip('/')}{request.url.path}"
        if request.url.query:
            target = f"{target}?{request.url.query}"
        return RedirectResponse(url=target, status_code=308)
    return await call_next(request)


@app.exception_handler(RedirectToLogin)
async def _redirect_to_login(request: Request, exc: RedirectToLogin):
    return RedirectResponse(url="/auth/login", status_code=302)


@app.get("/")
def root(request: Request):
    return RedirectResponse(url="/games", status_code=302)


@app.get("/healthz")
def healthz():
    return {"status": "ok", "version": __version__}
