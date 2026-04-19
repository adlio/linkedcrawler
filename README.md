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

## Installation

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -e '.[dev]'
```

## Run tests

```bash
pytest
# or
make test
```

## Run a local crawl

Legacy raw crawl JSON:

```bash
linkedcrawler 'https://www.linkedin.com/in/<profile>/recent-activity/all/'
```

Historical/backfill sync into an Obsidian directory:

```bash
linkedcrawler \
  'https://www.linkedin.com/in/simonwardley/recent-activity/all/' \
  --output-dir "$HOME/Notes/adlio/Resources/AIThinkers/SimonWardley/linkedin-posts" \
  --db-path "$HOME/Repos/linkedincrawler/data/simonwardley-sync.sqlite3" \
  --mode backfill \
  --fetched-at 2026-04-19
```

Daily incremental sync:

```bash
linkedcrawler \
  'https://www.linkedin.com/in/simonwardley/recent-activity/all/' \
  --output-dir "$HOME/Notes/adlio/Resources/AIThinkers/SimonWardley/linkedin-posts" \
  --db-path "$HOME/Repos/linkedincrawler/data/simonwardley-sync.sqlite3" \
  --mode daily \
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

## Current limitations

- No guaranteed production LinkedIn end-to-end test in CI
- Media bytes are not downloaded or persisted; this package extracts canonical metadata and discovered CDN/video references
- Botasaurus runtime support is intentionally thin so the deterministic extraction layer remains the long-term source of truth
