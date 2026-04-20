from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path

from .models import CrawlRequest
from .orchestration import run_linkedin_crawl
from .sync import sync_profile_to_directory
from .voyager_crawler import crawl_via_api


def _extractor_for(args):
    if args.via == 'api':
        return lambda url: crawl_via_api(
            url, max_pages=args.api_max_pages, delay_seconds=args.api_delay_seconds
        )
    return lambda url: run_linkedin_crawl(
        CrawlRequest(
            url=url,
            last_saved_item_key=args.last_saved_item_key,
            max_scroll_rounds=args.max_scroll_rounds,
            wait_attempts=args.wait_attempts,
            wait_delay_seconds=args.wait_delay_seconds,
        )
    ).posts


def run_sync(args) -> object:
    return sync_profile_to_directory(
        target_url=args.url,
        directory=args.output_dir,
        db_path=args.db_path,
        mode=args.mode,
        include_reposts=args.include_reposts,
        author_only=args.author_only,
        fetched_at=args.fetched_at,
        extract_posts=_extractor_for(args),
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
