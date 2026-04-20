"""Voyager API-based crawl — bypasses the UI "Show more results" button.

LinkedIn's recent-activity page exposes a GraphQL endpoint at
`/voyager/api/graphql?queryId=voyagerFeedDashProfileUpdates.*` that returns up
to 20 activities per page, paginated by a token that the same endpoint returns
in its metadata. This module drives that endpoint end-to-end from an
authenticated Chrome session, bypassing the UI entirely (the DOM-based crawl
tops out at ~18 posts due to an internal reCAPTCHA-driven throttle on the
button's click handler).

The critical details:
- CSRF: LinkedIn requires a `csrf-token` header whose value is the
  `JSESSIONID` cookie, stripped of its surrounding quotes. Their own fetch
  interceptor adds this, but a direct fetch from the page must set it by hand.
- Pagination: the first response's `metadata.paginationToken` feeds the next
  request's `paginationToken=` query variable.
- Top-level return value: voyager uses a normalized response shape; see
  voyager_parser.py for how Updates and their referenced media entities are
  reassembled into LinkedInPost objects.
"""

from __future__ import annotations

import json
import time
import urllib.parse
from functools import lru_cache
from typing import Any, Callable

from .auth import ensure_linkedin_login
from .models import LinkedInPost
from .secrets import get_linkedin_credentials
from .voyager_parser import parse_voyager_response

# Query hash baked into LinkedIn's client bundle. Stable across sessions but
# rotates on bundle redeploys; override via `query_id=` if/when this stops
# working.
DEFAULT_QUERY_ID = 'voyagerFeedDashProfileUpdates.4af00b28d60ed0f1488018948daad822'


def _build_voyager_url(
    *,
    profile_urn: str,
    query_id: str,
    start: int,
    pagination_token: str,
) -> str:
    encoded_urn = urllib.parse.quote(profile_urn, safe='')
    fields = [f'count:20', f'start:{start}', f'profileUrn:{encoded_urn}']
    if pagination_token:
        fields.append(f'paginationToken:{pagination_token}')
    vars_inner = ','.join(fields)
    return f'https://www.linkedin.com/voyager/api/graphql?variables=({vars_inner})&queryId={query_id}'


def _extract_profile_urn(driver: Any) -> str:
    """Pull the target profile URN from the loaded page.

    Multiple profile URNs are referenced on any page (the viewer's own,
    suggestions, etc.); the target profile dominates by frequency, so choose
    the most-referenced URN.
    """
    return driver.run_js(
        """
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


def _fetch_voyager_page(driver: Any, url: str) -> dict[str, Any]:
    """Call fetch(url) from within the authenticated page context.

    Botasaurus wraps run_js in an IIFE and awaits returned Promises, so the
    script returns an async IIFE invocation whose Promise CDP awaits.
    """
    js_url = json.dumps(url)
    script = f"""
return (async () => {{
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
  return {{status: resp.status, body: text}};
}})();
"""
    return driver.run_js(script)


def paginate_voyager_feed(
    *,
    driver: Any,
    profile_urn: str,
    query_id: str = DEFAULT_QUERY_ID,
    max_pages: int = 200,
    delay_seconds: float = 1.5,
    on_page: Callable[[int, int, int], None] | None = None,
) -> list[LinkedInPost]:
    """Paginate through the voyager feed endpoint and return LinkedInPost objects.

    Stops when a page returns zero new posts or when `max_pages` is reached.
    `on_page(page_index, urns_in_page, cumulative_total)` is called after each
    successful page if provided.
    """
    all_posts: list[LinkedInPost] = []
    seen: set[str] = set()
    start = 0
    token = ''

    for page_i in range(max_pages):
        url = _build_voyager_url(
            profile_urn=profile_urn, query_id=query_id, start=start, pagination_token=token
        )
        result = _fetch_voyager_page(driver, url)
        status = result.get('status') if isinstance(result, dict) else None
        body = (result.get('body') if isinstance(result, dict) else '') or ''
        if status != 200:
            raise RuntimeError(f'voyager page {page_i} returned status={status}: {body[:200]!r}')
        posts, next_token = parse_voyager_response(body)
        new_posts = [p for p in posts if p.post_id not in seen]
        for p in new_posts:
            seen.add(p.post_id)
            all_posts.append(p)
        if on_page:
            on_page(page_i, len(posts), len(all_posts))
        if not posts:
            break
        if not next_token:
            break
        token = next_token
        start += 20
        time.sleep(delay_seconds)

    return all_posts


@lru_cache(maxsize=1)
def _decorated_runner() -> Callable[[dict[str, Any]], list[dict[str, Any]]]:
    from botasaurus.browser import browser

    from .orchestration import BotasaurusSessionAdapter

    @browser(
        headless=False,
        block_images=True,
        reuse_driver=False,
        profile='linkedin-crawler',
        enable_xvfb_virtual_display=True,
    )
    def _run(driver: Any, data: dict[str, Any]) -> list[dict[str, Any]]:
        adapter = BotasaurusSessionAdapter(driver)
        driver.get(data['url'])
        time.sleep(2)
        ensure_linkedin_login(adapter, get_linkedin_credentials())
        time.sleep(3)
        profile_urn = _extract_profile_urn(driver)
        if not profile_urn:
            raise RuntimeError('could not extract profileUrn from page')
        posts = paginate_voyager_feed(
            driver=driver,
            profile_urn=profile_urn,
            query_id=data.get('query_id', DEFAULT_QUERY_ID),
            max_pages=data.get('max_pages', 200),
            delay_seconds=data.get('delay_seconds', 1.5),
            on_page=lambda i, in_page, total: print(
                f'voyager page {i:3d}  in_page={in_page:2d}  total={total}', flush=True
            ),
        )
        return [p.to_dict() for p in posts]

    return _run


def crawl_via_api(
    url: str,
    *,
    max_pages: int = 200,
    delay_seconds: float = 1.5,
    query_id: str = DEFAULT_QUERY_ID,
) -> list[LinkedInPost]:
    """Public entry point: launches a browser, authenticates, paginates."""
    payload = _decorated_runner()(
        {
            'url': url,
            'max_pages': max_pages,
            'delay_seconds': delay_seconds,
            'query_id': query_id,
        }
    )
    return [LinkedInPost(**p) for p in payload]
