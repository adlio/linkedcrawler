# LinkedIn Daily Sync Hardening Implementation Plan

> For Hermes: Use subagent-driven-development skill to implement this plan task-by-task.

Goal: Turn linkedcrawler into an idempotent, reliable, clean, semantically correct LinkedIn-to-Obsidian sync tool that supports both historical backfill and daily incremental updates.

Architecture: Split the pipeline into four explicit stages: browser acquisition, semantic extraction/normalization, durable sync state, and deterministic markdown export. Keep custom LinkedIn extraction as the source of truth. Use MarkItDown only as an optional downstream converter for linked/downloaded attachments, not for the main feed HTML.

Tech stack: Python, Botasaurus, BeautifulSoup/lxml, pytest, sqlite3, Obsidian markdown export.

---

## Key findings to address

1. Placeholder DOM nodes like `ember262` are being accepted as posts because extraction falls back from `data-urn` to arbitrary `id` values.
2. Reposts are mixed into the same output stream as authored posts without a clean semantic distinction.
3. Relative dates like `8h` and `4d` are emitted directly and are not stable enough for durable sync identity.
4. Markdown filenames are derived from titles/slugs, which is not idempotent and can drift when text extraction changes.
5. There is no durable crawl state, so daily sync and historical backfill are not resumable or strongly idempotent.
6. Tests pass today but do not cover the real failure modes seen in the saved LinkedIn page HTML.

---

## Target output model

### ActivityItem
- `activity_urn: str`
- `activity_url: str`
- `activity_type: Literal["authored", "repost"]`
- `actor_name: str`
- `reposted_by: str`
- `published_label: str`  # original raw label from LinkedIn, e.g. 8h / 4d / 2026-04-01
- `content_key: str`      # canonical export/persistence identity
- `body_text: str`
- `title: str`
- `image_urls: list[str]`
- `video_poster_url: str`
- `video_cdn_urls: list[str]`
- `canonical_content_url: str`
- `raw_html_hash: str`

### SyncState
- `target_profile_url: str`
- `newest_seen_activity_urn: str | None`
- `oldest_seen_activity_urn: str | None`
- `last_successful_run_at: str | None`
- `backfill_complete: bool`
- `last_exported_activity_urn: str | None`
- `extraction_version: str`

### Exported note frontmatter
- `linkedin_activity_urn`
- `linkedin_activity_type`
- `source`
- `author`
- `content_type: linkedin-post`
- `published` when resolvable
- `fetched`
- `body_hash`
- `tags`

### Note identity
- Filename/path should be canonical and stable.
- Recommended filename:
  - `YYYY-MM-DD-activity-<numeric_urn>.md` when a publish date can be resolved
  - otherwise `activity-<numeric_urn>.md`
- Never use text/title slug as the primary identity.

---

## Task 1: Add regression fixtures for the real LinkedIn DOM

Objective: Capture the real-world DOM failure mode in tests before changing extraction.

Files:
- Create: `tests/fixtures/linkedin_real_activity_page.html`
- Modify: `tests/test_extractors.py`

Step 1: Copy the saved real LinkedIn page HTML into a stable fixture.
- Source candidate already observed locally:
  - `error_logs/2026-04-18_21-45-06/page.html`

Step 2: Write failing regression tests asserting:
- placeholder `ember###` nodes are ignored
- blank posts are not emitted
- only valid `urn:li:activity:<digits>` items are returned
- reposts remain detectable

Step 3: Run specific tests and confirm failure.

Step 4: Commit.

---

## Task 2: Tighten extraction identity rules

Objective: Stop accepting placeholder DOM elements as posts.

Files:
- Modify: `src/linkedcrawler/extractors.py`
- Test: `tests/test_extractors.py`

Step 1: Add a helper like `extract_activity_urn(post: Tag) -> str | None`.
Rules:
- accept only `data-urn` values matching `^urn:li:activity:\d+$`
- do not fall back to arbitrary `id` values like `ember262`

Step 2: Add a semantic validity check helper like `looks_like_meaningful_activity(post: Tag) -> bool`.
Require at least one of:
- non-empty author
- non-empty body text
- permalink to activity URL
- timestamp label
- media presence

Step 3: Update `extract_post()` to return `None` for placeholders and semantically empty nodes.

Step 4: Run all extractor tests.

Step 5: Commit.

---

## Task 3: Separate authored posts from reposts semantically

Objective: Make export behavior semantically correct.

Files:
- Modify: `src/linkedcrawler/models.py`
- Modify: `src/linkedcrawler/extractors.py`
- Modify: `src/linkedcrawler/export.py`
- Test: `tests/test_extractors.py`
- Test: `tests/test_secrets_and_export.py`

Step 1: Extend the model to include an explicit `activity_type` field.
- values: `authored`, `repost`

Step 2: Set `activity_type` in extraction based on existing repost detection.
- `is_repost == False` => `authored`
- `is_repost == True` => `repost`

Step 3: Update export rendering so reposts are clearly labeled.
- Example markdown section:
  - `**Activity Type:** Repost`
  - `**Original Author:** Erik Schön`
  - `**Reposted By:** Simon Wardley`

Step 4: Add tests verifying authored vs repost markdown semantics.

Step 5: Commit.

---

## Task 4: Make note identity deterministic and idempotent

Objective: Ensure repeated runs update the same note instead of creating churn.

Files:
- Modify: `src/linkedcrawler/export.py`
- Test: `tests/test_secrets_and_export.py`

Step 1: Replace slug-first filename generation with canonical activity identity.
- derive numeric part from `urn:li:activity:<digits>`
- path format:
  - `YYYY-MM-DD-activity-<digits>.md` when date resolved
  - else `activity-<digits>.md`

Step 2: Add a `body_hash` helper.
- hash exported semantic body text + media refs, not raw HTML

Step 3: Ensure `write_posts_to_directory()` overwrites the same file path deterministically.

Step 4: Add tests for:
- repeated export of same activity -> same filename
- changed title text but same activity_urn -> same filename
- two posts with similar titles -> different files because URNs differ

Step 5: Commit.

---

## Task 5: Add durable sync state using SQLite

Objective: Support daily incremental sync and resumable historical backfill.

Files:
- Create: `src/linkedcrawler/state.py`
- Modify: `src/linkedcrawler/models.py`
- Test: `tests/test_state.py`

Step 1: Create a tiny SQLite-backed store using stdlib `sqlite3`.
Tables:
- `sync_targets`
- `seen_activities`
- `exported_notes`
- `crawl_runs`

Step 2: Add functions:
- `init_db(path)`
- `record_seen_activity(target_url, activity_urn, fetched_at, activity_type, source_url, body_hash)`
- `has_seen_activity(target_url, activity_urn)`
- `record_export(activity_urn, note_path, body_hash)`
- `load_sync_state(target_url)`
- `update_sync_state(...)`

Step 3: Add tests for idempotent recording and state reload.

Step 4: Commit.

---

## Task 6: Add incremental filtering and daily sync behavior

Objective: Only export unseen items on daily runs.

Files:
- Modify: `src/linkedcrawler/orchestration.py`
- Create: `src/linkedcrawler/sync.py`
- Test: `tests/test_sync.py`

Step 1: Create a sync function like:
- `sync_profile_to_directory(target_url, directory, db_path, mode="daily" | "backfill", include_reposts=False)`

Step 2: Daily mode behavior:
- crawl newest-first
- skip activities already in state DB
- export only unseen valid activities
- stop after enough consecutive already-seen valid activities

Step 3: Backfill mode behavior:
- continue exporting older unseen activities
- persist progress after each batch
- allow reruns without duplicates

Step 4: Add tests with fake extracted sequences proving:
- daily run adds only new posts
- rerun is a no-op
- backfill resumes cleanly after partial state

Step 5: Commit.

---

## Task 7: Improve crawl stopping and DOM stabilization

Objective: Make browser crawling more reliable against LinkedIn’s virtualized page behavior.

Files:
- Modify: `src/linkedcrawler/orchestration.py`
- Test: `tests/test_orchestration.py`

Step 1: Track validated activity count instead of raw matched node count.

Step 2: After each scroll:
- wait for page settle
- re-extract
- count only valid activity URNs
- treat placeholder-only growth as stale

Step 3: Add a stopping heuristic:
- stop after N consecutive rounds with no unseen valid activity URNs

Step 4: Add tests covering placeholder growth vs real new activity growth.

Step 5: Commit.

---

## Task 8: Add Obsidian-focused export polish

Objective: Make the notes clean and semantically correct.

Files:
- Modify: `src/linkedcrawler/export.py`
- Test: `tests/test_secrets_and_export.py`

Step 1: Improve frontmatter:
- include canonical LinkedIn identifiers
- include `linkedin_activity_type`
- include `body_hash`
- keep `published` optional when unresolved

Step 2: Improve markdown body structure:
- authored post: content only + media section
- repost: explicit metadata block indicating repost + original author/content

Step 3: Preserve clean readable paragraph breaks when possible.

Step 4: Add tests for authored and repost note output.

Step 5: Commit.

---

## Task 9: Evaluate MarkItDown integration for attachments only

Objective: Use MarkItDown where it helps without replacing semantic extraction.

Files:
- Create: `src/linkedcrawler/attachments.py`
- Create: `tests/test_attachments.py`
- Modify: `README.md`

Step 1: Add a minimal optional integration point for linked/downloaded attachments.
- Do not use MarkItDown on raw LinkedIn feed HTML.

Step 2: Add a helper like:
- `convert_attachment_with_markitdown(path) -> str`
- import lazily and fail clearly if not installed

Step 3: Document that MarkItDown is for downstream attached documents/pages only.

Step 4: Commit.

---

## Task 10: Add CLI workflow for backfill and daily sync

Objective: Make the tool runnable without custom scripts.

Files:
- Modify: `src/linkedcrawler/cli.py`
- Modify: `README.md`
- Test: `tests/test_cli.py`

Step 1: Add CLI flags:
- `--mode daily|backfill`
- `--db-path`
- `--output-dir`
- `--include-reposts`
- `--author-only`

Step 2: Default recommended behavior for Simon Wardley:
- `--mode daily`
- `--author-only`
- output dir set to Obsidian vault folder

Step 3: Add tests for CLI argument routing.

Step 4: Commit.

---

## Task 11: Run historical backfill and validate

Objective: Perform one clean backfill into Obsidian and verify it is rerunnable.

Files:
- No required code changes unless issues arise.

Step 1: Run a Simon Wardley historical backfill.

Step 2: Verify:
- no placeholder files
- authored vs repost semantics look correct
- rerunning backfill produces no duplicates
- daily rerun after backfill is a no-op unless new posts exist

Step 3: Record any remaining DOM-specific gaps and add regression fixtures/tests.

Step 4: Commit docs/bugfixes if needed.

---

## Recommended export policy

Default policy for this vault:
- `author-only=True`
- `include_reposts=False`

Reason:
- If the goal is “Simon Wardley’s posts”, reposts should not silently mix with authored posts.
- Reposts can be enabled later if you want a complete activity archive instead of authored posts only.

---

## MarkItDown recommendation

Use MarkItDown only for:
- downloaded PDFs/docs/slides/articles linked from a post
- optional secondary note generation

Do not use MarkItDown for:
- turning LinkedIn feed HTML into posts
- semantic extraction
- activity identity
- frontmatter generation

---

## Verification checklist

- repeated sync of unchanged content produces zero new files
- placeholder `ember###` nodes are ignored
- authored posts and reposts are distinguishable
- note identity is stable across reruns
- state DB preserves progress across runs
- historical backfill is resumable
- daily sync adds only unseen activity items
- all tests pass

---

## Suggested first implementation slice

If implementing in phases, do these first:
1. Task 1: real DOM regression fixture
2. Task 2: strict activity_urn validation
3. Task 4: canonical filename identity
4. Task 5: SQLite state
5. Task 6: daily sync/backfill behavior

This gets the crawler from “fragile demo” to “usable sync tool” fastest.
