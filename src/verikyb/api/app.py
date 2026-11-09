"""The FastAPI service."""

from fastapi import FastAPI

from verikyb import __version__
from verikyb.api.routes import health


def create_app() -> FastAPI:
    app = FastAPI(title="VeriKYB API", version=__version__)
    app.include_router(health.router)
    return app
