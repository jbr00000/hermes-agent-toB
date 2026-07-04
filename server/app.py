"""FastAPI application factory for the Hermes headless server."""
from __future__ import annotations

from fastapi import FastAPI

from server import auth as auth_module
from server import memory as memory_module
from server.routes import auth, chat, memory, sessions, users


def create_app() -> FastAPI:
    auth_module.init_db()    # bootstrap users.db + admin user on startup
    memory_module.init_db()  # ensure memory.db table exists
    app = FastAPI(title="Hermes Headless Server", version="0.3.0")
    app.include_router(auth.router)
    app.include_router(chat.router)
    app.include_router(sessions.router)
    app.include_router(users.router)
    app.include_router(memory.router)

    @app.get("/health")
    def health():
        return {"status": "ok"}

    return app


app = create_app()
