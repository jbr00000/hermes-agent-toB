# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Authoritative Reference

**Read [`AGENTS.md`](AGENTS.md) first.** It is the 1300+ line development guide and contribution rubric for this repo and is the source of truth for: the contribution philosophy, the Footprint Ladder, plugin/skill authoring standards, dependency pinning policy, the slash-command registry, skin engine, profiles, curator, cron, kanban, and known pitfalls. This file intentionally does **not** duplicate that content — it orients you to what AGENTS.md doesn't emphasize and synthesizes the cross-file architecture.

## What Hermes Is

A self-improving personal AI agent (Nous Research, MIT, Python ≥3.11 <3.14). One agent core drives five surfaces: interactive CLI (`hermes`), a messaging gateway (~20 platforms: Telegram, Discord, Slack, WhatsApp, Signal, Matrix, …), a React/Ink TUI (`hermes --tui`), an Electron desktop app (`apps/desktop/`), and a web dashboard (`web/` + `hermes dashboard`). It learns across sessions (memory + skills), delegates to subagents, runs scheduled jobs (cron + webhooks), and drives a real terminal and browser. Capability is extended via **plugins and skills**, not by growing the core.

Two properties shape nearly every design decision (treat as hard constraints when reviewing changes — see AGENTS.md §"What Hermes Is"):

- **Per-conversation prompt caching is sacred.** Never mutate past context, swap toolsets, or rebuild the system prompt mid-conversation. The *only* exception is context compression. Cache-breaking multiplies cost every turn.
- **The core is a narrow waist; capability lives at the edges.** Every core model tool ships on every API call, so the bar for a new *core* tool is extremely high. Use the Footprint Ladder (AGENTS.md) to pick the least-footprint rung.

## Commands

### Environment
```bash
source .venv/bin/activate          # or: source venv/bin/activate
uv pip install -e ".[all,dev]"     # full editable install for development
```

### Tests — ALWAYS use the wrapper, never raw `pytest`
```bash
scripts/run_tests.sh                                  # full suite, CI-parity
scripts/run_tests.sh tests/gateway/                   # one directory
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

### Running the surfaces
```bash
hermes              # interactive CLI
hermes --tui        # Ink TUI (Node front-end + Python JSON-RPC backend)
hermes gateway      # messaging gateway
hermes dashboard    # web dashboard (embeds the real `hermes --tui` via xterm.js PTY)
hermes setup        # full setup wizard
hermes tools        # curses UI for enabling/disabling toolsets per platform
hermes model        # pick LLM provider/model
```

### TUI / desktop / web (Node workspaces — root `package.json`)
```bash
cd ui-tui && npm run dev        # watch mode (rebuilds hermes-ink + tsx --watch)
cd ui-tui && npm run typecheck  # tsc --noEmit
cd ui-tui && npm run lint && npm run fmt && npm test   # eslint / prettier / vitest
cd apps/desktop && npm run <…>  # Electron + React desktop app
cd web && npm run <…>           # Vite dashboard frontend
```

### Docker
```bash
docker compose up -d                            # linux
docker compose -f docker-compose.windows.yml up # native windows
```

## Architecture — the pieces you must hold together

### Entry points and the agent loop
- **`run_agent.py`** — `AIAgent` class, the core synchronous conversation loop (~12k LOC). `run_conversation()` loops: call model → if `tool_calls`, dispatch each via `handle_function_call()` and append tool-result messages → else return final content. Bounded by `max_iterations` (shared with subagents) and `iteration_budget`. One-turn grace call. Messages are OpenAI-format; reasoning content lives in `assistant_msg["reasoning"]`.
- **`model_tools.py`** — tool orchestration: `discover_builtin_tools()`, `handle_function_call()`. Triggers plugin discovery as an import side effect (see pitfall below).
- **`toolsets.py`** — the single `TOOLSETS` dict; `_HERMES_CORE_TOOLS` is the default bundle most platforms inherit. **A tool is only exposed to an agent if its name appears in a toolset** — auto-discovery imports the module but does NOT wire it in.
- **`cli.py`** — `HermesCLI` interactive orchestrator (~11k LOC). Rich for panels, prompt_toolkit for input.
- **`hermes_cli/main.py`** — `hermes` console-script entry. `_apply_profile_override()` sets `HERMES_HOME` *before any module imports* — this is what makes profiles work.

### File dependency chain (load-bearing order)
```
tools/registry.py   (no deps — imported by all tool files)
       ↑
tools/*.py          (each calls registry.register() at import time)
       ↑
model_tools.py      (imports tools/registry + triggers tool discovery)
       ↑
run_agent.py, cli.py, batch_runner.py, tools/environments/
```

### Config — three loaders, know which one you're on
| Loader | Used by | Location |
|--------|---------|----------|
| `load_cli_config()` | CLI mode | `cli.py` |
| `load_config()` | `hermes tools`, `hermes setup`, most subcommands | `hermes_cli/config.py` (`DEFAULT_CONFIG`) |
| Direct YAML load | Gateway runtime | `gateway/run.py` + `gateway/config.py` |
If the CLI sees a key but the gateway doesn't (or vice versa), you're on the wrong loader. User config: `~/.hermes/config.yaml` (behavior — never secrets). Secrets only in `~/.hermes/.env`. **Never add a new `HERMES_*` env var for non-secret config** — bridge from `config.yaml` if a mechanism needs one (see AGENTS.md).

### Profiles
Multiple fully isolated instances, each with its own `HERMES_HOME`. Because `_apply_profile_override()` runs before imports, any code using `get_hermes_home()` (from `hermes_constants`) automatically scopes correctly. **Never hardcode `~/.hermes` or `Path.home() / ".hermes"`** for state — it breaks profiles (source of 5 bugs in PR #3575). Use `display_hermes_home()` for user-facing strings.

### Plugins — three separate discovery systems
1. **General plugins** (`hermes_cli/plugins.py`): `~/.hermes/plugins/`, `./.hermes/plugins/`, pip entry points. Expose `register(ctx)` for lifecycle hooks, tools, CLI subcommands. **Pitfall:** `discover_plugins()` only runs as a side effect of importing `model_tools.py` — code paths reading plugin state without that import must call `discover_plugins()` explicitly (idempotent).
2. **Memory providers** (`plugins/memory/<name>/`): separate ABC + orchestrator (`agent/memory_manager.py`). **No new in-tree memory providers** (policy May 2026) — ship as standalone plugin repos.
3. **Model providers** (`plugins/model-providers/<name>/`): lazy, separate discovery via `providers/__init__.py._discover_providers()` on first `get_provider_profile()`. User plugins of the same name override bundled (last-writer-wins).

**Plugins MUST NOT modify core files** (`run_agent.py`, `cli.py`, `gateway/run.py`, `hermes_cli/main.py`). Widen the generic plugin surface instead. **No new third-party-product plugins in-tree** (observability/SaaS/analytics) — standalone repo.

### Gateway — two message guards
When an agent is running, inbound messages pass through two sequential guards that both must bypass approval/control commands: (1) the base adapter (`gateway/platforms/base.py`) queues in `_pending_messages`, and (2) `gateway/run.py` intercepts `/stop`, `/new`, `/queue`, `/status`, `/approve`, `/deny`. Any new command that must reach the runner while the agent is blocked must bypass **both** and dispatch inline. Cron deliveries are deliberately NOT mirrored into gateway sessions — they land in their own cron session to preserve message-role alternation.

### Slash commands — one registry, many consumers
All slash commands are `CommandDef` entries in `COMMAND_REGISTRY` (`hermes_cli/commands.py`). CLI dispatch, gateway help, Telegram BotCommand menu, Slack subcommand routing, and autocomplete **all derive from this registry automatically**. Adding an alias = one tuple edit; adding a command = one `CommandDef` + a handler branch in `cli.py` (and `gateway/run.py` if gateway-exposed).

## Critical invariants (do not violate)

1. **Prompt caching** — no mid-conversation context mutation, toolset swaps, or system-prompt rebuilds. Slash commands that mutate system-prompt state (skills, tools, memory) default to *deferred* invalidation (next session) with an opt-in `--now` flag.
2. **Strict message role alternation** — never two same-role messages in a row; never inject a synthetic user message mid-loop.
3. **System prompt byte-stability** for the life of a conversation.
4. **Don't hardcode `~/.hermes`** — `get_hermes_home()` / `display_hermes_home()` from `hermes_constants`.
5. **Always pass `encoding=`** to text-mode file I/O (Windows cp1252 corruption).
6. **`.env` is secrets only.** Behavioral settings → `config.yaml`.
7. **Dependency pinning** — all deps need upper bounds (`>=floor,<next_major` for post-1.0; `<0.(minor+2)` for pre-1.0); core deps are exact-pinned for supply-chain safety. Run `uv lock` after changing `pyproject.toml`. Never commit a bare `>=X.Y.Z`.
8. **Don't write change-detector tests** — assert invariants/relationships, not snapshots (model lists, config version literals, enumeration counts). See AGENTS.md §Testing.
9. **Tests must not write to `~/.hermes/`** — the `_isolate_hermes_home` autouse fixture redirects to a temp dir.
10. **Plugins/skills don't touch core files** — extend the generic surface.
11. **Cache-, outbound-telemetry-, and footprint-safe** — no new analytics/attribution without opt-in gating; prefer the Footprint Ladder over a new core tool.

## Footprint Ladder (choosing where new capability lands)
Prefer the highest (least-footprint) rung that solves the problem (full detail in AGENTS.md): **extend existing code → CLI command + skill → service-gated tool (`check_fn`) → plugin → MCP server in the catalog → new core tool (last resort).** Most new capability should NOT be a core tool. For local/custom tools, do not edit core — drop a `plugin.yaml` + `__init__.py` in `~/.hermes/plugins/<name>/` and register via `ctx.register_tool(...)`.

## Key file map (load-bearing, not exhaustive — filesystem is canonical)
```
run_agent.py            AIAgent — core conversation loop
model_tools.py          tool orchestration, discover_builtin_tools(), handle_function_call()
toolsets.py             TOOLSETS dict + _HERMES_CORE_TOOLS
cli.py                  HermesCLI interactive orchestrator
hermes_state.py         SessionDB — SQLite session store with FTS5 search
hermes_constants.py     get_hermes_home(), display_hermes_home() (profile-aware paths)
hermes_cli/main.py      'hermes' entry; _apply_profile_override() (sets HERMES_HOME pre-import)
hermes_cli/commands.py  COMMAND_REGISTRY — single source for all slash commands
hermes_cli/config.py    DEFAULT_CONFIG, OPTIONAL_ENV_VARS, _config_version
hermes_cli/skin_engine.py  data-driven CLI theming (pure data, no code per skin)
agent/                  provider adapters, memory, caching, compression, curator, context engine
tools/                  tool implementations, auto-discovered via tools/registry.py
tools/environments/     terminal backends: local, docker, ssh, modal, daytona, singularity
gateway/run.py + session.py + platforms/   messaging gateway (20 platform adapters)
gateway/platforms/ADDING_A_PLATFORM.md     how to add a platform adapter
cron/                   jobs.py (store) + scheduler.py (tick loop)
acp_adapter/            ACP server (VS Code / Zed / JetBrains)
ui-tui/ + tui_gateway/  Ink (React) TUI front-end + Python JSON-RPC backend
apps/desktop/           Electron desktop chat app (own composer/transcript; NOT embedded TUI)
apps/shared/            framework-agnostic WS/JSON-RPC client (@hermes/shared)
web/                    Vite dashboard frontend
website/                Docusaurus docs site
skills/ + optional-skills/   built-in vs. opt-in skills (agentskills.io compatible)
plugins/                plugin surfaces (memory, model-providers, image_gen, context_engine, …)
```
