# linkedcrawler

linkedcrawler is a small Python package that migrates the LinkedIn crawler logic out of the old Tampermonkey monorepo into a focused, testable package. The extractor logic is fixture-driven and deterministic; the runtime layer is a thin Botasaurus adapter for local/manual execution.

## What was migrated

- LinkedIn post selectors and extraction behavior from `tampermonkey/src/crawlers/linkedin/extractors.ts`
- Fixture knowledge from the original `linkedin-feed.html`
- Incremental crawl behavior from the old orchestration layer: wait for posts, deduplicate by `post_id`, stop at `last_saved_item_key`, and scroll until stale

## Architecture

- `src/linkedcrawler/extractors.py`
  - Pure HTML-to-structured-data parsing using BeautifulSoup
  - No browser or network side effects
  - Best place to update selectors when LinkedIn changes its DOM
- `src/linkedcrawler/orchestration.py`
  - Browser-session protocol used by the crawler runtime
  - Botasaurus adapter for real browser runs
  - `crawl_session(...)` contains crawl control flow and is fully unit tested with fake browser sessions
- `src/linkedcrawler/models.py`
  - Dataclasses for posts, extraction errors, crawl requests, and crawl results
- `tests/`
  - Fixture-driven extraction tests
  - Headless orchestration tests with mocked browser behavior

## Why this is testable in CI

Live LinkedIn automation is not reliable in CI because it depends on authentication state, anti-bot checks, dynamically changing DOM, and media/network timing. Because of that, this repository treats live Botasaurus execution as a manual/local capability and treats fixture-driven parsing plus mocked-session orchestration as the primary automated contract.

That means:

- CI-safe tests do not hit LinkedIn
- the extraction contract is locked to realistic fixture HTML
- the orchestration contract is validated with a fake session that simulates scrolling and delayed content

## Prerequisites

- **[uv](https://docs.astral.sh/uv/)** — installs in one line:
  ```bash
  curl -LsSf https://astral.sh/uv/install.sh | sh
  ```
  `uv` manages the Python interpreter, the `.venv`, and the locked dependency
  set (`uv.lock`). The pinned Python version lives in `.python-version`; `uv
  sync` will fetch it automatically if missing.
- **A local Chrome/Chromium**, reachable by Botasaurus. Live crawls open a
  real browser window (or an Xvfb virtual display on Linux headless hosts).
- **LinkedIn credentials stored via `secret-tool`** (Linux GNOME keyring) under
  service `linkedin-crawler`, account `default`, kinds `username` / `password`.

## Installation

```bash
make install       # or: uv sync --all-extras
```

This creates `.venv/` from `uv.lock` with the exact resolved versions. If
you're migrating from the old pip-based setup, `rm -rf .venv` first — uv's
venv layout differs from plain `python -m venv`.

## Run tests

```bash
make test          # or: uv run pytest
```

## Run a local crawl

Raw crawl JSON (no sync, no Obsidian output):

```bash
uv run linkedcrawler 'https://www.linkedin.com/in/<profile>/recent-activity/all/'
```

Historical/backfill sync into an Obsidian directory:

```bash
uv run linkedcrawler \
  'https://www.linkedin.com/in/simonwardley/recent-activity/all/' \
  --output-dir "$HOME/Notes/adlio/Resources/AIThinkers/SimonWardley/linkedin-posts" \
  --db-path "$HOME/Repos/linkedincrawler/data/simonwardley-sync.sqlite3" \
  --mode backfill \
  --profile-name 'Simon Wardley' \
  --tags ai-thinkers,simon-wardley \
  --fetched-at 2026-04-19
```

Daily incremental sync (stops once it hits the newest already-synced URN):

```bash
uv run linkedcrawler \
  'https://www.linkedin.com/in/simonwardley/recent-activity/all/' \
  --output-dir "$HOME/Notes/adlio/Resources/AIThinkers/SimonWardley/linkedin-posts" \
  --db-path "$HOME/Repos/linkedincrawler/data/simonwardley-sync.sqlite3" \
  --mode daily \
  --profile-name 'Simon Wardley' \
  --tags ai-thinkers,simon-wardley \
  --fetched-at $(date +%F)
```

Defaults for sync mode:
- `--author-only` is enabled by default
- `--include-reposts` is enabled by default
- this matches the current Simon Wardley policy: authored posts plus Simon reposts
- note identity is versioned by `activity URN + normalized content hash`, so content edits produce a new note version

Notes:

- A real run requires a locally usable Chrome/Chromium environment compatible with Botasaurus.
- LinkedIn may still require an authenticated session and may block or challenge automation.
- The package is intentionally not coupled to the old server/dashboard/task pipeline.
- The current live crawl still appears constrained by what LinkedIn exposes on the loaded activity page in headless mode; repeated backfills may be needed and the extractor may still need further DOM-specific hardening for full historical coverage.

## Diagnostics and inspection

The Makefile wraps the common feedback loop used when iterating on crawl reach, DOM selectors, or auth issues. All wrappers default to the newest matching directory when `DIR=` is omitted.

Run a diag crawl (HTML snapshots, events, and a one-line score appended to `output/diag/scoreboard.tsv`):

```bash
make diag                                  # Simon Wardley's profile
make diag URL=https://www.linkedin.com/in/<handle>/recent-activity/all/
make diag ROUNDS=10 DELAY=3                # tune scroll rounds / wait seconds
```

Inspect a diag run — per-round HTML size + unique `urn:li:activity:` counts, `events.jsonl`, `summary.json`, and the tail of `stdout.log`:

```bash
make inspect-diag
make inspect-diag DIR=output/diag/<timestamp>
```

Inspect the sync database — tables + row counts for `seen_activities`, `exported_notes`, `crawl_runs`:

```bash
make db-stats
make db-stats DB=data/simonwardley-sync.sqlite3
```

Inspect an error_logs dump — tail of `error.log` plus `page.html` heuristics (title, size, activity-URN count, auth/captcha markers):

```bash
make inspect-error
make inspect-error DIR=error_logs/<timestamp>
```

## Current limitations

- No guaranteed production LinkedIn end-to-end test in CI
- Media bytes are not downloaded or persisted; this package extracts canonical metadata and discovered CDN/video references
- Botasaurus runtime support is intentionally thin so the deterministic extraction layer remains the long-term source of truth
