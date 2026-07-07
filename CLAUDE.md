# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Authoritative Reference

**Read [`AGENTS.md`](AGENTS.md) first.** It is the development guide and contribution rubric for this repo (rewritten for the to-B fork) and is the source of truth for: the contribution philosophy, the Footprint Ladder, plugin/skill authoring standards, dependency pinning policy, the slash-command registry, profiles, curator, cron, kanban, and known pitfalls. This file intentionally does **not** duplicate that content — it orients you to what AGENTS.md doesn't emphasize and synthesizes the cross-file architecture. For product context see [`docs/改造计划.md`](docs/改造计划.md) (9 locked architecture decisions), [`docs/项目架构详解.md`](docs/项目架构详解.md) (per-module walkthrough), and [`docs/API文档.md`](docs/API文档.md) (front-end HTTP/SSE contract).

## What Hermes Is

A **to-B, single-tenant, per-customer-deployed** agent platform — a hard fork of [NousResearch/hermes-agent](https://github.com/NousResearch/hermes-agent) v0.18.0 (MIT, Python ≥3.11 <3.14). One customer = one deployment (on-prem or dedicated VPC); 10–50 users share the instance, but sessions / memory / audit / permissions are strictly isolated by `user_id`. The fork does **not** track upstream (see [`UPSTREAM.md`](UPSTREAM.md)); security fixes are cherry-picked manually.

The product is a **headless FastAPI service (`server/`)** that acts as the front-end BFF and drives one `AIAgent` core: streaming chat (SSE), read-only queries against the customer's business DB, sandboxed code execution, plan mode, persistent per-user memory, and multi-user management. The interactive surfaces from upstream — the Ink TUI (`hermes --tui`), the Electron desktop app (`apps/desktop/`), the web dashboard (`web/`), and the ~20-platform messaging gateway — have been **physically removed**. The `hermes` CLI now serves only admin/setup, and removed-surface commands are neutralized to report "removed". A new to-B React/Vite front-end lives in `apps/web/` and talks to `server/` over HTTP/SSE.

Two properties shape nearly every design decision (treat as hard constraints when reviewing changes — see AGENTS.md):

- **Per-conversation prompt caching is sacred.** Never mutate past context, swap toolsets, or rebuild the system prompt mid-conversation. The *only* exception is context compression. Cache-breaking multiplies cost every turn.
- **The core is a narrow waist; capability lives at the edges.** Every core model tool ships on every API call, so the bar for a new *core* tool is extremely high. Use the Footprint Ladder (AGENTS.md) to pick the least-footprint rung.

Plus three to-B-specific hard constraints (see `docs/改造计划.md`):

- **Sandbox by default** — agent-generated code runs only in a Docker container; it cannot reach the host unless `features.host_terminal` is explicitly opted in.
- **DB read-only enforced at the GRANT layer** — `db_query` and the sandbox share the customer's read-only DB credentials; writes are rejected by the database itself.
- **No cloud memory / no outbound telemetry** — memory is local SQLite per `user_id`; no analytics leave the deployment without opt-in.

## Commands

### Environment
```bash
source .venv/bin/activate          # or: source venv/bin/activate
uv pip install -e ".[all,dev]"     # full editable install for development
```

### Tests — ALWAYS use the wrapper, never raw `pytest`
```bash
scripts/run_tests.sh                                  # full suite, CI-parity
scripts/run_tests.sh tests/agent/                     # one directory
scripts/run_tests.sh tests/agent/test_foo.py::test_x  # one test
scripts/run_tests.sh -v --tb=long                     # bare pytest flags pass through
scripts/run_tests.sh -k 'pattern'                     # keyword filter
```
The wrapper enforces hermetic CI parity: per-file subprocess isolation, `TZ=UTC`, `LANG=C.UTF-8`, `PYTHONHASHSEED=0`, blanked credential env vars. Direct `pytest` on a dev machine with API keys set diverges from CI and has caused repeated "works locally, fails in CI" incidents. Integration tests are auto-skipped (`-m 'not integration'`).

### Lint / typecheck
```bash
ruff check .                # only PLW1514 (unspecified-encoding) is enabled — load-bearing on Windows
ty check                    # type checker (python-version = "3.13")
```
Bare `open()`/`read_text()`/`write_text()` in text mode defaults to the system locale on Windows (cp1252) and silently corrupts non-ASCII — always pass `encoding=...`. Tests/skills/plugins are exempt.

### Running the service & admin CLI
```bash
HERMES_HOME=.hermes-dev python -m server   # ★ the product: headless FastAPI service on :8000
hermes setup        # setup wizard (model / DB / sandbox / admin password)
hermes tools        # curses UI for enabling/disabling toolsets
hermes model        # pick LLM provider/model
hermes cron ...     # scheduled jobs (delivery is stubbed; cron lands in its own session)
# Removed-surface commands — `hermes --tui` / `gateway` / `dashboard` / `acp` / `gui` — are
# neutralized: they print "removed" and exit. There is no TUI/desktop/web-dashboard binary.
```

### Front-end (`apps/web/` — to-B React/Vite app, talks to `server/` over HTTP/SSE)
```bash
cd apps/web && npm install
cd apps/web && npm run dev      # Vite dev server (ships with a mock API; see docs/B端前端设计方案.md)
```

### Docker
```bash
docker compose up          # builds hermes-agent-tob:dev, mounts $HERMES_HOME -> /data
```

## Architecture — the pieces you must hold together

### Entry points and the agent loop
- **`run_agent.py`** — `AIAgent` class, the core synchronous conversation loop. `run_conversation()` loops: call model → if `tool_calls`, dispatch each via `handle_function_call()` and append tool-result messages → else return final content. Bounded by `max_iterations` (shared with subagents) and `iteration_budget`. One-turn grace call. Messages are OpenAI-format; reasoning content lives in `assistant_msg["reasoning"]`.
- **`model_tools.py`** — tool orchestration: `discover_builtin_tools()`, `handle_function_call()`. Triggers plugin discovery as an import side effect (see pitfall below).
- **`toolsets.py`** — the single `TOOLSETS` dict; `_HERMES_CORE_TOOLS` is the default bundle. **A tool is only exposed to an agent if its name appears in a toolset** — auto-discovery imports the module but does NOT wire it in. (`kanban_*` is gated by `check_fn` and stays off by default.)
- **`server/`** — **the product surface.** FastAPI BFF: `__main__.py` (entry, validates `HERMES_HOME` → loads `.env` → `uvicorn`), `app.py` (factory; bootstraps admin, mounts routes, applies terminal backend), `auth.py`/`deps.py` (users.db + bcrypt + JWT, `get_current_user`/`require_admin`), `sessions.py`/`memory.py`/`audit.py` (per-`user_id` SQLite stores + ownership checks), `agent_factory.py` (builds an `AIAgent` per `/chat` request — prefill history + ephemeral system prompt carrying memory + plan mode), `runtime_config.py` (provider/model/reasoning for the headless path; 4 supported providers: `deepseek`/`zai`/`alibaba`/`custom`), `tool_policy.py` (**plan mode is enforced here at the toolset layer**: `plan` → `db`+`session_search` only; `execute` → adds `terminal`), `deployment_config.py` + `mcp.py` (declarative `deployment.yaml` → MCP servers), `features.py` (`host_terminal` opt-in). Routes under `server/routes/`: `auth`, `chat` (SSE), `sessions`, `memory`, `users` (admin), `features`.
- **`cli.py`** — `HermesCLI` interactive orchestrator (admin/dev use only; not shipped to end users). Rich for panels, prompt_toolkit for input.
- **`hermes_cli/main.py`** — `hermes` console-script entry. `_apply_profile_override()` sets `HERMES_HOME` *before any module imports* — this is what makes profiles work. Removed-surface subcommands are neutralized here.

### File dependency chain (load-bearing order)
```
tools/registry.py   (no deps — imported by all tool files)
       ↑
tools/*.py          (each calls registry.register() at import time)
       ↑
model_tools.py      (imports tools/registry + triggers tool discovery)
       ↑
run_agent.py, cli.py, server/agent_factory.py, tools/environments/
```

### Config — three loaders, know which one you're on
| Loader | Used by | Location |
|--------|---------|----------|
| `load_config()` | `hermes` admin subcommands (`tools`, `setup`, `cron`, `model`, …) | `hermes_cli/config.py` (`DEFAULT_CONFIG`) |
| `RuntimeConfig` | headless `server/` agent path | `server/runtime_config.py` |
| `deployment_config` | declarative per-customer delivery | `server/deployment_config.py` (`deployment.yaml`) |

User behavior config: `$HERMES_HOME/config.yaml` (never secrets). Secrets only in `$HERMES_HOME/.env`. Per-customer delivery declared in `$HERMES_HOME/deployment.yaml`. **Never add a new `HERMES_*` env var for non-secret config** — bridge from `config.yaml` if a mechanism needs one (see AGENTS.md).

### Profiles
Multiple fully isolated instances, each with its own `HERMES_HOME`. Because `_apply_profile_override()` runs before imports, any code using `get_hermes_home()` (from `hermes_constants`) automatically scopes correctly. **Never hardcode `~/.hermes` or `Path.home() / ".hermes"`** for state — it breaks profiles (source of 5 bugs in PR #3575). Use `display_hermes_home()` for user-facing strings.

### Plugins — three separate discovery systems
1. **General plugins** (`hermes_cli/plugins.py`): `~/.hermes/plugins/`, `./.hermes/plugins/`, pip entry points. Expose `register(ctx)` for lifecycle hooks, tools, CLI subcommands. **Pitfall:** `discover_plugins()` only runs as a side effect of importing `model_tools.py` — code paths reading plugin state without that import must call `discover_plugins()` explicitly (idempotent).
2. **Memory providers** (`plugins/memory/<name>/`): separate ABC + orchestrator (`agent/memory_manager.py`). The to-B fork ships **no installed cloud providers** (memory is local SQLite); the ABC is retained for a future local provider. **No new in-tree memory providers** (policy May 2026) — ship as standalone plugin repos.
3. **Model providers** (`plugins/model-providers/<name>/`): lazy, separate discovery via `providers/__init__.py._discover_providers()` on first `get_provider_profile()`. **4 kept**: `custom` (self-hosted OpenAI-compatible), `zai` (Zhipu GLM), `alibaba` (Bailian Qwen), `deepseek`. User plugins of the same name override bundled (last-writer-wins).

**Plugins MUST NOT modify core files** (`run_agent.py`, `cli.py`, `server/`, `hermes_cli/main.py`). Widen the generic plugin surface instead. **No new third-party-product plugins in-tree** (observability/SaaS/analytics) — standalone repo.

### Gateway — soft-disabled (do not extend)
The upstream messaging gateway (`gateway/`) has been gutted for the to-B fork: all ~20 platform adapters (Signal/WhatsApp/WeChat/QQ/Yuanbao/…), the relay system, the slash-command router, the stream consumer, and the kanban watcher are **physically deleted**; only `base.py` plus a few stub modules remain. The `hermes gateway` command is neutralized. **Do not add new gateway code or revive platform adapters** — all inbound interaction now arrives over the headless `server/` HTTP/SSE API. The old "two message guards" and Telegram/Slack routing notes no longer apply.

### Slash commands — one registry, CLI-only consumers
All slash commands are `CommandDef` entries in `COMMAND_REGISTRY` (`hermes_cli/commands.py`). With the gateway gone, the only consumers are CLI dispatch and autocomplete. Adding an alias = one tuple edit; adding a command = one `CommandDef` + a handler branch in `cli.py`.

## Critical invariants (do not violate)

1. **Prompt caching** — no mid-conversation context mutation, toolset swaps, or system-prompt rebuilds. Slash commands that mutate system-prompt state (skills, tools, memory) default to *deferred* invalidation (next session) with an opt-in `--now` flag.
2. **Strict message role alternation** — never two same-role messages in a row; never inject a synthetic user message mid-loop.
3. **System prompt byte-stability** for the life of a conversation.
4. **Don't hardcode `~/.hermes`** — `get_hermes_home()` / `display_hermes_home()` from `hermes_constants`.
5. **Always pass `encoding=`** to text-mode file I/O (Windows cp1252 corruption).
6. **`.env` is secrets only.** Behavioral settings → `config.yaml`; per-customer delivery → `deployment.yaml`.
7. **Dependency pinning** — all deps need upper bounds (`>=floor,<next_major` for post-1.0; `<0.(minor+2)` for pre-1.0); core deps are exact-pinned for supply-chain safety. Run `uv lock` after changing `pyproject.toml`. Never commit a bare `>=X.Y.Z`.
8. **Don't write change-detector tests** — assert invariants/relationships, not snapshots (model lists, config version literals, enumeration counts). See AGENTS.md §Testing.
9. **Tests must not write to `~/.hermes/`** — the `_isolate_hermes_home` autouse fixture redirects to a temp dir.
10. **Plugins/skills don't touch core files** — extend the generic surface.
11. **Cache-, outbound-telemetry-, and footprint-safe** — no new analytics/attribution without opt-in gating; prefer the Footprint Ladder over a new core tool. to-B default is no outbound telemetry at all.
12. **Sandbox + read-only DB** — never wire a code-execution path that bypasses the Docker sandbox, and never give `db_query` write-capable credentials (read-only is enforced at the DB GRANT layer, not just in the tool).

## Footprint Ladder (choosing where new capability lands)
Prefer the highest (least-footprint) rung that solves the problem (full detail in AGENTS.md): **extend existing code → CLI command + skill → service-gated tool (`check_fn`) → plugin → MCP server in the catalog → new core tool (last resort).** Most new capability should NOT be a core tool. For local/custom tools, do not edit core — drop a `plugin.yaml` + `__init__.py` in `~/.hermes/plugins/<name>/` and register via `ctx.register_tool(...)`. Customer-specific algorithms/data interfaces should land as MCP servers (decision 6) rather than core tools.

## Key file map (load-bearing, not exhaustive — filesystem is canonical)
```
run_agent.py            AIAgent — core conversation loop
model_tools.py          tool orchestration, discover_builtin_tools(), handle_function_call()
toolsets.py             TOOLSETS dict + _HERMES_CORE_TOOLS; kanban_* gated by check_fn
server/                 ★ the to-B product: headless FastAPI BFF (JWT, SSE, multi-user isolation)
server/agent_factory.py builds an AIAgent per /chat (prefill history + ephemeral system prompt)
server/tool_policy.py   plan-mode toolset gating (plan=db+session_search; execute=+terminal)
server/runtime_config.py provider/model/reasoning for headless path (4 providers)
server/auth.py          users.db + bcrypt + JWT; per-user isolation boundary
cli.py                  HermesCLI interactive orchestrator (admin/dev only)
hermes_state.py         SessionDB — SQLite session store with FTS5 search
hermes_constants.py     get_hermes_home(), display_hermes_home() (profile-aware paths)
hermes_cli/main.py      'hermes' entry; _apply_profile_override() (sets HERMES_HOME pre-import)
hermes_cli/commands.py  COMMAND_REGISTRY — single source for all slash commands
hermes_cli/config.py    DEFAULT_CONFIG, OPTIONAL_ENV_VARS, _config_version
agent/                  provider adapters, memory, caching, compression, curator, context engine
tools/                  tool implementations: db_query, terminal, execute_code, delegate, todo, mcp, skills…
tools/environments/     terminal backends: docker (sandbox, default) + local (host_terminal opt-in only)
cron/                   jobs.py (store) + scheduler.py (tick loop); messaging delivery stubbed
gateway/                soft-disabled — platform adapters deleted, do not extend
skills/                 built-in skills (agentskills.io compatible)
plugins/                model-providers (4 kept) · memory ABC (no installed providers) · …
apps/web/               to-B React/Vite front-end (talks to server/ over HTTP/SSE)
docs/                   改造计划.md (9 decisions) · 项目架构详解.md · API文档.md · security/ · B端前端设计方案.md
```
