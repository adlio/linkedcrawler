from __future__ import annotations

import argparse
import json

from .models import CrawlRequest
from .orchestration import run_linkedin_crawl


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description='Run the LinkedIn recent-activity crawler.')
    parser.add_argument('url', help='LinkedIn recent activity URL to crawl')
    parser.add_argument('--last-saved-item-key', default=None)
    parser.add_argument('--max-scroll-rounds', type=int, default=3)
    parser.add_argument('--wait-attempts', type=int, default=10)
    parser.add_argument('--wait-delay-seconds', type=float, default=2.0)
    return parser


def main() -> int:
    args = build_parser().parse_args()
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
