# Upstream Baseline Record

This project is a **hard fork** of [NousResearch/hermes-agent](https://github.com/NousResearch/hermes-agent),
transformed into a private, to-B-oriented agent framework. See [`改造计划.md`](./docs/改造计划.md)
for the full transformation plan and requirements.

## Baseline

| Field | Value |
|---|---|
| Upstream repo | https://github.com/NousResearch/hermes-agent |
| Upstream version | **0.18.0** (from `pyproject.toml` `[project] version`) |
| Fork date | 2026-07-03 |
| Source nature | Source-tree snapshot — **no git history inherited** (the snapshot was taken outside git, so the exact upstream commit SHA is not recoverable; version 0.18.0 is the authoritative reference point) |
| Fork policy | **Hard fork — does not track upstream.** No `git merge upstream`. |

## Why hard fork

The transformation deletes ~60–70% of the codebase (TUI, 20 messaging platforms, desktop
app, dashboard, computer-use, cloud memory providers, ~24 of 28 model providers, all
terminal backends except Docker, etc.). At that scale, upstream merges would conflict on
nearly every file — the merge cost vastly exceeds the value.

## How security fixes are applied

Hermes responds to CVEs and supply-chain incidents quickly (see the dense CVE/pin comments
in `pyproject.toml`). This fork does **not** auto-merge those, but should cherry-pick
critical fixes manually:

1. Watch the upstream repo's releases / security advisories.
2. For a relevant fix, locate the change in the upstream `main` at/after v0.18.0.
3. Re-apply the specific change here (cherry-pick the commit's *content* — do NOT merge).
4. Bump the affected pin in `pyproject.toml` + regenerate `uv.lock` if it's a dependency fix.

## License

Hermes Agent is MIT-licensed. The original [`LICENSE`](./LICENSE) and copyright notice are
preserved as required by the MIT license, even though this is a private fork.
