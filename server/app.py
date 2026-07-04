"""FastAPI application factory for the Hermes headless server."""
from __future__ import annotations

from fastapi import FastAPI

from server.routes import chat


def create_app() -> FastAPI:
    app = FastAPI(title="Hermes Headless Server", version="0.1.0")
    app.include_router(chat.router)

    @app.get("/health")
    def health():
        return {"status": "ok"}

    return app


app = create_app()
