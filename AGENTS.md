# AGENTS.md — Hermes to-B Agent (development guide)

This is a **hard fork of [NousResearch/hermes-agent](https://github.com/NousResearch/hermes-agent) v0.18.0**, transformed into a private, to-B (enterprise) agent framework. It is **not** vanilla Hermes — many OSS surfaces (TUI, messaging gateway, desktop app, dashboard, ~24 of 28 model providers, all cloud memory) were removed or soft-disabled. See [`UPSTREAM.md`](UPSTREAM.md) for the fork baseline.

## Where to look

- **[`改造计划.md`](改造计划.md)** — the full transformation plan: requirements + 9 locked architecture decisions + step-by-step record.
- **[`CLAUDE.md`](CLAUDE.md)** — orientation for working in this repo (commands, architecture, invariants).
- **`server/`** — the headless FastAPI server (the to-B product surface; the frontend's BFF).
- **`docs/security/network-egress-isolation.md`** + **`docs/session-lifecycle.md`** — the two kept reference docs.

## Architecture (big picture)

A headless FastAPI server (`server/`) is the BFF for your frontend. It authenticates users (JWT + bcrypt, per-user isolated), drives the `AIAgent` core (`run_agent.py`) per chat, persists sessions + memory in SQLite under `HERMES_HOME`, and exposes capabilities: `db_query` (read-only DB), `terminal`/`execute_code` (Docker sandbox), plan mode, persistent memory. The agent runs code **only** in a Docker sandbox (never the host, unless `features.host_terminal` is opted in). Containerized via `Dockerfile` + `docker-compose.yml`.

```
frontend ──HTTP/SSE──> server/ (FastAPI, JWT auth)
                          │
                          ├── AIAgent (run_agent.py) ── db_query (read-only DB)
                          ├── terminal/execute_code ── Docker sandbox
                          ├── SessionDB (state.db) ── per-user sessions
                          └── memory.db ── per-user persistent memory
```

## Run / test

```bash
# Dev env: conda env `hermes` (Python 3.12). HERMES_HOME=.hermes-dev holds
# config.yaml / .env / users.db / state.db / memory.db.
HERMES_HOME=.hermes-dev python -m server        # headless server on :8000
HERMES_HOME=.hermes-dev hermes -z "Reply PONG"  # CLI one-shot smoke test
docker compose up                                # containerized (Dockerfile)
```

API surface: `POST /auth/login`, `POST /chat` (SSE), `GET/POST /sessions`, `GET/POST/DELETE /memory`, `GET/POST/DELETE /users` (admin), `GET /features`, `GET /health`. See `server/routes/`.

## Hard invariants (do not violate)

1. **Sandbox-only by default** — the agent runs shell/code in Docker, never the host. `features.host_terminal` is the only opt-in to host access.
2. **DB read-only at the GRANT layer** — `db_query` and the sandbox share the customer's read-only DB credentials; read-only is enforced by the database, not by tool code (which generated sandbox code could bypass).
3. **Per-user isolation** — sessions, memory, and audit are scoped by `user_id` (decision 2/3: one shared instance per customer, 10–50 users must not cross).
4. **No cloud memory / no telemetry** — memory is local SQLite; no outbound analytics without an explicit opt-in.
5. **Prompt caching is sacred** (inherited from Hermes) — don't mutate past context, swap toolsets, or rebuild the system prompt mid-conversation.

## Code conventions

- **Python**: PEP 8, type hints on signatures, explicit error handling, no hardcoded secrets, no `~/.hermes` literal paths (use `get_hermes_home()` from `hermes_constants`).
- **Dependencies**: exact-pinned in `pyproject.toml` (supply-chain posture); run `uv lock` after changes. Never commit a bare `>=X.Y.Z`.
- **Tests**: `scripts/run_tests.sh` (the hermetic CI-parity wrapper), not raw `pytest`.
- **Commits**: conventional (`feat:`, `fix:`, `refactor:`, `docs:`), one logical change per commit, validated before push.
- **Validation lesson**: lazy imports only fire at runtime — for deletions, run the real path (`hermes -z` / the server) to exercise them, not just `import`. Never mask a validation command's exit code with a pipe.
