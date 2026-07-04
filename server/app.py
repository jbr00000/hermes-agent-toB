"""FastAPI application factory for the Hermes headless server."""
from __future__ import annotations

from fastapi import FastAPI

from server import auth as auth_module
from server.routes import auth, chat


def create_app() -> FastAPI:
    auth_module.init_db()  # bootstrap users.db + admin user on startup
    app = FastAPI(title="Hermes Headless Server", version="0.2.0")
    app.include_router(auth.router)
    app.include_router(chat.router)

    @app.get("/health")
    def health():
        return {"status": "ok"}

    return app


app = create_app()
