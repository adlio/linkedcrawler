# Agent guide

Guidance for coding agents (Claude Code, etc.) working in this repo.

## Prefer Makefile wrappers over ad-hoc shell

Common diagnostic and inspection tasks have Makefile targets. They are pre-approved in `.claude/settings.json`, so they won't trigger permission prompts. Use them first — fall back to raw shell only when a wrapper doesn't cover what you need.

| Target | Purpose | Variables |
|---|---|---|
| `make test` | Run pytest in the venv | — |
| `make diag` | Run a diag crawl (writes to `output/diag/<timestamp>/`) | `URL=`, `ROUNDS=`, `DELAY=` |
| `make inspect-diag` | Per-round post counts, `events.jsonl`, `summary.json`, `stdout.log` tail | `DIR=` (defaults to newest) |
| `make db-stats` | Tables + row counts for `seen_activities`, `exported_notes`, `crawl_runs` | `DB=` (defaults to newest `data/*.sqlite3`) |
| `make inspect-error` | `error.log` tail + `page.html` heuristics (title, size, URN count, auth markers) | `DIR=` (defaults to newest) |

Full syntax and examples: see the "Diagnostics and inspection" section of `README.md`.

## What is intentionally not pre-approved

- `make install` — mutates the venv. Ask before running.
- Arbitrary `python3 -c ...`, one-off `sqlite3` queries, raw `bash scripts/diag.sh` invocations, etc.

If you find yourself running the same raw command repeatedly, propose adding a new Makefile target rather than widening the allowlist.

## Where things live

- Extraction logic: `src/linkedcrawler/extractors.py` (pure, fixture-driven)
- Orchestration: `src/linkedcrawler/orchestration.py` (browser protocol + Botasaurus adapter)
- Diag runner: `scripts/diag.sh` (invoked via `make diag`)
- Tests: `tests/` (fixture-driven + mocked-session orchestration)

CI-safe tests do not hit LinkedIn. Live crawls are a manual/local capability only.
