from __future__ import annotations

import argparse
import json
import re
from datetime import date
from pathlib import Path

from .models import CrawlRequest
from .orchestration import run_linkedin_crawl
from .state import load_crawl_checkpoint, load_sync_state, save_crawl_checkpoint
from .sync import sync_profile_to_directory
from .voyager_crawler import crawl_via_api


_PROFILE_SLUG_RE = re.compile(r'/in/([^/?#]+)')


def _slug_from_profile_url(url: str) -> str:
    """Pull the vanity handle out of a LinkedIn profile URL.

    Used as a last-resort default when no explicit --profile-name / --tags is
    given: better to emit `simonwardley` in a tag and have the user notice than
    to silently hardcode a single profile.
    """
    m = _PROFILE_SLUG_RE.search(url or '')
    return m.group(1) if m else ''


def _resolve_profile_name(args) -> str:
    if args.profile_name:
        return args.profile_name
    slug = _slug_from_profile_url(args.url)
    # Title-case the slug as a tolerable display-name fallback. The user will
    # usually want to pass --profile-name "Real Display Name" instead.
    return slug.replace('-', ' ').replace('_', ' ').title() if slug else ''


def _resolve_tags(args) -> list[str]:
    if args.tags is not None:
        return [t.strip() for t in args.tags.split(',') if t.strip()]
    slug = _slug_from_profile_url(args.url)
    return [slug] if slug else []


def _extractor_for(args, *, stop_after_urn: str | None = None):
    if args.via == 'api':
        resume_from: tuple[int, str] | None = None
        on_checkpoint = None
        if args.resume and args.db_path:
            resume_from = load_crawl_checkpoint(args.db_path, args.url)

            def _persist(start: int, token: str) -> None:
                save_crawl_checkpoint(args.db_path, args.url, start=start, token=token)

            on_checkpoint = _persist
        return lambda url: crawl_via_api(
            url,
            max_pages=args.api_max_pages,
            delay_seconds=args.api_delay_seconds,
            stop_after_urn=stop_after_urn,
            resume_from=resume_from,
            on_checkpoint=on_checkpoint,
        )
    return lambda url: run_linkedin_crawl(
        CrawlRequest(
            url=url,
            # Prefer an explicit --last-saved-item-key, falling back to the
            # newest URN we already have in sqlite when running in daily mode.
            last_saved_item_key=args.last_saved_item_key or stop_after_urn,
            max_scroll_rounds=args.max_scroll_rounds,
            wait_attempts=args.wait_attempts,
            wait_delay_seconds=args.wait_delay_seconds,
        )
    ).posts


def run_sync(args) -> object:
    profile_name = _resolve_profile_name(args)
    tags = _resolve_tags(args)
    if not profile_name:
        raise SystemExit(
            '--profile-name is required when syncing (URL did not match '
            'https://www.linkedin.com/in/<handle>/...)'
        )

    # Daily sync: let the crawler stop as soon as it sees the newest URN we
    # already have on disk. Shaves the fetch time from ~2 minutes down to
    # however long it takes to catch up, and avoids pressuring LinkedIn for
    # pages we'd throw away at the sqlite dedupe layer anyway.
    stop_after_urn: str | None = None
    if args.mode == 'daily':
        stop_after_urn = load_sync_state(args.db_path, args.url).newest_seen_activity_urn

    return sync_profile_to_directory(
        target_url=args.url,
        directory=args.output_dir,
        db_path=args.db_path,
        mode=args.mode,
        profile_name=profile_name,
        tags=tags,
        include_reposts=args.include_reposts,
        author_only=args.author_only,
        fetched_at=args.fetched_at,
        extract_posts=_extractor_for(args, stop_after_urn=stop_after_urn),
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description='Run the LinkedIn recent-activity crawler.')
    parser.add_argument('url', help='LinkedIn recent activity URL to crawl')
    parser.add_argument('--last-saved-item-key', default=None)
    parser.add_argument('--max-scroll-rounds', type=int, default=3)
    parser.add_argument('--wait-attempts', type=int, default=10)
    parser.add_argument('--wait-delay-seconds', type=float, default=2.0)
    parser.add_argument('--output-dir', type=Path, default=None)
    parser.add_argument('--db-path', type=Path, default=None)
    parser.add_argument('--mode', choices=['daily', 'backfill'], default='daily')
    parser.add_argument('--fetched-at', default=str(date.today()))
    parser.add_argument(
        '--via',
        choices=['html', 'api'],
        default='api',
        help='post-fetching strategy (default: api = voyager GraphQL bypass)',
    )
    parser.add_argument('--api-max-pages', type=int, default=200)
    parser.add_argument('--api-delay-seconds', type=float, default=1.5)
    parser.add_argument(
        '--resume',
        action='store_true',
        default=False,
        help='resume from the saved crawl checkpoint (<1h old) instead of starting over',
    )
    parser.add_argument(
        '--profile-name',
        default=None,
        help='display name for frontmatter author fallback and author_only filter. '
        'Defaults to a title-cased URL slug.',
    )
    parser.add_argument(
        '--tags',
        default=None,
        help='comma-separated frontmatter tags. Defaults to the URL slug as a single tag.',
    )
    parser.add_argument('--include-reposts', dest='include_reposts', action='store_true', default=True)
    parser.add_argument('--no-include-reposts', dest='include_reposts', action='store_false')
    parser.add_argument('--author-only', dest='author_only', action='store_true', default=True)
    parser.add_argument('--all-activities', dest='author_only', action='store_false')
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.output_dir is not None:
        if args.db_path is None:
            raise SystemExit('--db-path is required when --output-dir is provided')
        result = run_sync(args)
        print(json.dumps(result.to_dict(), indent=2))
        return 0

    if args.via == 'api':
        posts = crawl_via_api(
            args.url,
            max_pages=args.api_max_pages,
            delay_seconds=args.api_delay_seconds,
        )
        print(json.dumps({'posts': [p.to_dict() for p in posts]}, indent=2))
        return 0

    request = CrawlRequest(
        url=args.url,
        last_saved_item_key=args.last_saved_item_key,
        max_scroll_rounds=args.max_scroll_rounds,
        wait_attempts=args.wait_attempts,
        wait_delay_seconds=args.wait_delay_seconds,
    )
    result = run_linkedin_crawl(request)
    print(json.dumps(result.to_dict(), indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
