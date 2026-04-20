#!/usr/bin/env python3
"""Probe LinkedIn's voyager feed pagination API.

Bypasses the UI "Show more results" button by calling the voyager GraphQL
endpoint directly from within the authenticated page context. Reports how many
activity URNs are reachable by chasing paginationToken until the API stops
returning new items.

Usage:
    .venv/bin/python scripts/api_probe.py <profile-activity-url> [--max-pages N]

Output goes to output/api_probe/<timestamp>/:
    - probe.log       (stderr tee)
    - urns.txt        (newline-separated activity URNs collected)
    - pages/NNN.json  (per-page: status, body_len, urn count, token)
    - summary.json    (final tally + timings)
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
import traceback
import urllib.parse
from datetime import datetime
from pathlib import Path
from typing import Any

# Ensure src/ is importable when running from repo root.
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'src'))

from botasaurus.browser import browser  # noqa: E402
from linkedcrawler.auth import ensure_linkedin_login  # noqa: E402
from linkedcrawler.orchestration import BotasaurusSessionAdapter  # noqa: E402
from linkedcrawler.secrets import get_linkedin_credentials  # noqa: E402
from linkedcrawler.voyager_parser import parse_voyager_response  # noqa: E402

VOYAGER_FEED_RE = re.compile(
    r'/voyager/api/graphql\?.*feedDashProfileUpdatesByMemberShareFeed|'
    r'queryId=voyagerFeedDashProfileUpdates'
)
FEED_MARKER = 'feedDashProfileUpdatesByMemberShareFeed'


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('url', help='profile recent-activity URL (e.g. .../in/<handle>/recent-activity/all/)')
    p.add_argument('--max-pages', type=int, default=50, help='hard cap on API calls (default 50 = ~1000 posts)')
    p.add_argument('--delay-seconds', type=float, default=1.5, help='sleep between API calls (default 1.5s)')
    p.add_argument('--save-bodies', action='store_true', help='also write each response body to pages/NNN.body.json')
    return p.parse_args()


def fresh_output_dir() -> Path:
    stamp = datetime.now().strftime('%Y-%m-%d_%H-%M-%S')
    out = ROOT / 'output' / 'api_probe' / stamp
    (out / 'pages').mkdir(parents=True, exist_ok=True)
    return out


def wait_for_feed_call(adapter: BotasaurusSessionAdapter, timeout: float = 30.0) -> dict[str, Any] | None:
    """Block until the voyager feed endpoint has been observed on the CDP tap."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        buf = getattr(adapter, '_fetch_tap_buffer', [])
        for entry in buf:
            if FEED_MARKER in (entry.get('body_head') or ''):
                return entry
            # Fall back to URL pattern — some bodies not yet captured.
            if 'voyager/api/graphql' in entry.get('url', '') and 'feedDashProfileUpdates' in entry.get('url', ''):
                return entry
        time.sleep(0.25)
    return None


def parse_url_template(url: str) -> dict[str, str]:
    """Extract mutable query params from a captured voyager feed URL."""
    # The URL looks like:
    #   /voyager/api/graphql?variables=(count:20,start:20,profileUrn:urn%3Ali%3A...,paginationToken:XXX)&queryId=...
    variables_match = re.search(r'variables=\(([^)]*)\)', url)
    query_match = re.search(r'queryId=([^&]+)', url)
    if not variables_match or not query_match:
        raise ValueError(f'unexpected voyager URL shape: {url[:200]!r}')
    vars_str = variables_match.group(1)
    parts = {}
    for kv in vars_str.split(','):
        k, _, v = kv.partition(':')
        parts[k] = v
    return {
        'query_id': query_match.group(1),
        'profile_urn': parts.get('profileUrn', ''),
        'pagination_token': parts.get('paginationToken', ''),
        'count': parts.get('count', '20'),
        'start': parts.get('start', '0'),
    }


def build_voyager_url(profile_urn: str, query_id: str, start: int, pagination_token: str) -> str:
    fields = [f'count:20', f'start:{start}', f'profileUrn:{profile_urn}']
    if pagination_token:
        fields.append(f'paginationToken:{pagination_token}')
    vars_inner = ','.join(fields)
    return f'https://www.linkedin.com/voyager/api/graphql?variables=({vars_inner})&queryId={query_id}'


def extract_urns_and_next_token(body_text: str) -> tuple[list[str], str | None]:
    """Parse a voyager feed response, returning (activity_urns, next_pagination_token)."""
    data = json.loads(body_text)
    feed = data['data']['data']['feedDashProfileUpdatesByMemberShareFeed']
    element_refs = feed.get('*elements', []) or []
    urns: list[str] = []
    for ref in element_refs:
        m = re.search(r'urn:li:activity:\d+', ref)
        if m:
            urns.append(m.group(0))
    token = (feed.get('metadata') or {}).get('paginationToken')
    return urns, token


def fetch_voyager_via_page(adapter: BotasaurusSessionAdapter, url: str) -> dict[str, Any]:
    """Call fetch(url) from within the authenticated page context, return status + body text.

    Botasaurus's run_js uses CDP Runtime.evaluate with await_promise=True, so
    returning a Promise works — but the script itself must be sync. Wrap the
    fetch in an async IIFE whose invocation yields the Promise CDP awaits.
    """
    js_url = json.dumps(url)
    script = f"""
return (async () => {{
  // LinkedIn's fetch interceptor normally adds `csrf-token`. Our direct call
  // bypasses the interceptor, so set it manually from the JSESSIONID cookie
  // (LinkedIn stores it as a quoted string; strip the quotes).
  const csrfMatch = document.cookie.match(/JSESSIONID=\\"?([^;\\"]+)\\"?/);
  const csrf = csrfMatch ? csrfMatch[1] : '';
  const resp = await fetch({js_url}, {{
    credentials: 'include',
    headers: {{
      accept: 'application/vnd.linkedin.normalized+json+2.1',
      'x-restli-protocol-version': '2.0.0',
      'csrf-token': csrf,
    }},
  }});
  const text = await resp.text();
  return {{status: resp.status, body_len: text.length, body: text, csrf_present: !!csrf}};
}})();
"""
    return adapter.driver.run_js(script)


def main() -> int:
    args = parse_args()
    out_dir = fresh_output_dir()

    @browser(
        headless=False,
        block_images=True,
        reuse_driver=False,
        profile='linkedin-crawler',
        enable_xvfb_virtual_display=True,
    )
    def _run(driver: Any, _data: Any) -> dict[str, Any]:
        adapter = BotasaurusSessionAdapter(driver)
        # Install CDP fetch tap so we can observe the first voyager feed call.
        import os
        os.environ['LINKEDCRAWLER_DEBUG_DIR'] = str(out_dir)
        os.environ['LINKEDCRAWLER_FETCH_TAP'] = '1'
        adapter._install_fetch_tap_if_needed()

        driver.get(args.url)
        time.sleep(2)
        ensure_linkedin_login(adapter, get_linkedin_credentials())
        time.sleep(3)
        current = driver.run_js('return location.href;')
        print(f'current URL after login: {current}', flush=True)

        # Try to observe a real voyager feed call first — gives us the exact
        # queryId and a fresh paginationToken for this profile's current state.
        first_call = None
        for attempt in range(6):
            driver.run_js('window.scrollBy(0, window.innerHeight);')
            time.sleep(1.5)
            first_call = wait_for_feed_call(adapter, timeout=1.0)
            if first_call:
                print(f'feed call observed on scroll attempt {attempt + 1}', flush=True)
                break

        if first_call:
            template = parse_url_template(first_call['url'])
        else:
            print('no feed call observed — falling back to synthetic URL (start=0, no token)', flush=True)
            profile_urn = driver.run_js(
                """
// The page references multiple profile URNs (the viewer's own + the target's
// + others). The target profile appears far more often than any other, so
// pick the one with the most occurrences.
const html = document.documentElement.outerHTML;
const matches = html.match(/urn:li:fsd_profile:[A-Za-z0-9_-]+/g) || [];
const counts = {};
for (const u of matches) counts[u] = (counts[u] || 0) + 1;
let best = null, bestN = 0;
for (const [u, n] of Object.entries(counts)) {
  if (n > bestN) { best = u; bestN = n; }
}
return best;
"""
            )
            if not profile_urn:
                raise RuntimeError('could not extract profileUrn from page')
            template = {
                'query_id': 'voyagerFeedDashProfileUpdates.4af00b28d60ed0f1488018948daad822',
                'profile_urn': urllib.parse.quote(profile_urn, safe=''),
                'pagination_token': '',
                'count': '20',
                'start': '0',
            }
            print(f'synthetic template profile_urn={profile_urn}', flush=True)

        print(f'queryId={template["query_id"][:50]}...', flush=True)
        print(f'initial start={template["start"]} token={(template["pagination_token"] or "(empty)")[:40]}', flush=True)

        start = int(template['start'])
        token = template['pagination_token']
        all_urns: list[str] = []
        all_posts: list[dict[str, Any]] = []
        seen: set[str] = set()
        pages_payload: list[dict[str, Any]] = []

        for page_i in range(args.max_pages):
            # Empty token is only expected on the very first (start=0) call.
            if not token and page_i > 0:
                print(f'page {page_i}: pagination token missing after fetch — stopping', flush=True)
                break
            url = build_voyager_url(template['profile_urn'], template['query_id'], start, token)
            t0 = time.time()
            try:
                result = fetch_voyager_via_page(adapter, url)
            except Exception as exc:
                print(f'page {page_i}: fetch raised {type(exc).__name__}: {exc}', flush=True)
                pages_payload.append({'page': page_i, 'start': start, 'error': str(exc)})
                break
            status = result.get('status') if isinstance(result, dict) else None
            body = (result.get('body') if isinstance(result, dict) else '') or ''
            elapsed = time.time() - t0
            try:
                posts_in_page, next_token = parse_voyager_response(body)
            except Exception as exc:
                print(f'page {page_i}: parse error: {exc}; body head: {body[:300]!r}', flush=True)
                pages_payload.append({'page': page_i, 'start': start, 'status': status, 'body_len': len(body), 'parse_error': str(exc)})
                break
            urns = [p.post_id for p in posts_in_page]
            new = [u for u in urns if u not in seen]
            new_posts = [p for p in posts_in_page if p.post_id in new]
            seen.update(new)
            all_urns.extend(new)
            all_posts.extend(p.to_dict() for p in new_posts)
            print(
                f'page {page_i:2d}  start={start:4d}  status={status}  urns_in_page={len(urns)}  '
                f'new={len(new)}  total={len(all_urns)}  elapsed={elapsed:.2f}s',
                flush=True,
            )
            (out_dir / 'pages' / f'{page_i:03d}.json').write_text(
                json.dumps({
                    'page': page_i,
                    'start': start,
                    'token': token,
                    'status': status,
                    'body_len': len(body),
                    'urns': urns,
                    'new_urns': new,
                    'next_token': next_token,
                    'elapsed_s': round(elapsed, 3),
                }, indent=2)
            )
            if args.save_bodies:
                (out_dir / 'pages' / f'{page_i:03d}.body.json').write_text(body)
            if not urns:
                print(f'page {page_i}: empty elements — stopping', flush=True)
                break
            if next_token == token:
                print(f'page {page_i}: token did not advance — stopping', flush=True)
                break
            token = next_token
            start += 20
            time.sleep(args.delay_seconds)

        (out_dir / 'urns.txt').write_text('\n'.join(all_urns) + '\n' if all_urns else '')
        (out_dir / 'posts.jsonl').write_text(
            ''.join(json.dumps(p, ensure_ascii=False) + '\n' for p in all_posts)
        )
        authored = sum(1 for p in all_posts if not p['is_repost'])
        reposts = sum(1 for p in all_posts if p['is_repost'])
        (out_dir / 'summary.json').write_text(json.dumps({
            'url': args.url,
            'total_urns': len(all_urns),
            'total_posts_parsed': len(all_posts),
            'authored': authored,
            'reposts': reposts,
            'pages_fetched': len(pages_payload),
            'template': {k: (v[:60] if isinstance(v, str) else v) for k, v in template.items()},
        }, indent=2))
        return {'total': len(all_urns), 'authored': authored, 'reposts': reposts, 'out': str(out_dir)}

    try:
        result = _run()
    except Exception:
        traceback.print_exc()
        return 1
    print(f'\nresult: {result}')
    print(f'dir:    {out_dir}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
