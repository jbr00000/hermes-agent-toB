"""Entry point: ``python -m server`` (or ``hermes serve`` once wired).

Loads .env (API keys) from HERMES_HOME, then runs uvicorn.
HERMES_HOME must be set in the environment before launch.
"""
from __future__ import annotations

import os


def _bootstrap() -> None:
    home = os.environ.get("HERMES_HOME")
    if not home:
        raise SystemExit("HERMES_HOME must be set (e.g. export HERMES_HOME=$(pwd)/.hermes-dev)")

    # Load .env (API keys) from HERMES_HOME.
    try:
        from dotenv import load_dotenv

        load_dotenv(os.path.join(home, ".env"))
    except Exception:
        pass


_bootstrap()

import uvicorn  # noqa: E402

from server.app import app  # noqa: E402


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8000, log_level="info")
