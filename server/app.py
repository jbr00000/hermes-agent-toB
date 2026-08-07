"""FastAPI application factory for the Hermes headless server."""
from __future__ import annotations

import os

from fastapi import FastAPI

from server import audit as audit_module
from server import auth as auth_module
from server import features as features_module
from server import memory as memory_module
from server.storage import database_health, get_runtime_store
from server.routes import auth, chat, features, memory, sessions, tasks, users


def create_app() -> FastAPI:
    os.environ.setdefault("HERMES_HEADLESS", "1")
    auth_module.init_db()    # initialize shared storage and bootstrap the admin user
    memory_module.init_db()
    audit_module.init_db()
    features_module.apply_terminal_backend()  # TERMINAL_ENV = docker (default) | local (if host_terminal opted in)
    app = FastAPI(title="Cortex Agent Server", version="0.4.0")
    app.include_router(auth.router)
    app.include_router(chat.router)
    app.include_router(sessions.router)
    app.include_router(tasks.router)
    app.include_router(users.router)
    app.include_router(memory.router)
    app.include_router(features.router)

    @app.get("/health/live")
    def liveness():
        return {"status": "ok"}

    @app.get("/health")
    def health():
        database_ok = database_health()
        runtime_store = get_runtime_store()
        redis_ok = runtime_store.health()
        worker_ok = runtime_store.worker_health()
        worker_required = os.environ.get("HERMES_REQUIRE_AGENT_WORKER", "0") == "1"
        healthy = database_ok and redis_ok is not False
        if worker_required:
            healthy = healthy and worker_ok is True
        return {
            "status": "ok" if healthy else "degraded",
            "components": {
                "database": "ok" if database_ok else "error",
                "redis": "disabled" if redis_ok is None else ("ok" if redis_ok else "error"),
                "agent_worker": (
                    "disabled"
                    if worker_ok is None and not worker_required
                    else "ok"
                    if worker_ok
                    else "missing"
                ),
            },
        }

    return app


app = create_app()
