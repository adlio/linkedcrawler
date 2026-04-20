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
import re
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

# LinkedIn's client bundle names the feed query `voyagerFeedDashProfileUpdates.`
# followed by a hex hash. The hash rotates whenever the bundle redeploys, so
# this module prefers one observed live on the page over the constant above.
_QUERY_ID_RE = re.compile(r'queryId=(voyagerFeedDashProfileUpdates\.[A-Fa-f0-9]+)')
# Voyager embeds the profileUrn as a comma-separated variable inside the
# `variables=(...)` URL tuple. The value itself is percent-encoded, so match
# the encoded form rather than the cleaner unencoded URN.
_PROFILE_URN_IN_URL_RE = re.compile(r'profileUrn:(urn%3Ali%3Afsd_profile%3A[A-Za-z0-9_-]+)')


class VoyagerError(Exception):
    """Base class for voyager API failures.

    Subclasses carry enough context to make the failure mode obvious in logs
    (which page, which status code, a prefix of the body) so an operator can
    tell the difference between "re-auth needed", "bundle rotated", and
    "transient network flake" without poking at the raw response.
    """

    def __init__(
        self,
        message: str,
        *,
        page_index: int | None = None,
        status: int | None = None,
        body_head: str = '',
    ) -> None:
        super().__init__(message)
        self.page_index = page_index
        self.status = status
        self.body_head = body_head


class VoyagerAuthError(VoyagerError):
    """401/403 or a CSRF failure. Not retryable — the session must be refreshed."""


class VoyagerRateLimitError(VoyagerError):
    """429. The caller should back off aggressively."""


class VoyagerQueryIdError(VoyagerError):
    """The queryId rotated (404, or 200 with a non-JSON body). Rediscover it."""


class VoyagerTransientError(VoyagerError):
    """5xx, network blip, or a dict-shaped 200 we couldn't parse. Safe to retry."""


def classify_voyager_response(status: int | None, body: str) -> type[VoyagerError] | None:
    """Return the right exception class for this response, or None if it's OK.

    Kept pure and separate from the fetch loop so error handling can be tested
    without a browser.
    """
    body_lower = (body or '').lstrip().lower()
    if status == 200:
        # A 200 without a JSON body usually means LinkedIn served an HTML error
        # or authwall page — which is what happens when the queryId rotates.
        if not body_lower.startswith('{'):
            return VoyagerQueryIdError
        return None
    if status in (401, 407):
        return VoyagerAuthError
    if status == 403:
        return VoyagerAuthError
    if status == 404:
        # queryId typos / rotations surface as 404 from the voyager backend.
        return VoyagerQueryIdError
    if status == 429:
        return VoyagerRateLimitError
    if status is None or (500 <= status < 600):
        return VoyagerTransientError
    return VoyagerError


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


def extract_query_id_from_url(url: str) -> str | None:
    """Pull the feed queryId from a voyager URL, or None if it isn't one.

    Kept separate from the CDP plumbing so it's unit-testable: feed any URL in,
    get `voyagerFeedDashProfileUpdates.<hash>` out.
    """
    m = _QUERY_ID_RE.search(url or '')
    return m.group(1) if m else None


def extract_profile_urn_from_url(url: str) -> str | None:
    """Decode the profileUrn out of a voyager feed URL's variables tuple.

    LinkedIn URL-encodes the URN (`:` -> `%3A`) inside `variables=(...)`. We
    look for that exact shape so we don't accidentally match a profileUrn
    reference in some other endpoint's query string.
    """
    m = _PROFILE_URN_IN_URL_RE.search(url or '')
    return urllib.parse.unquote(m.group(1)) if m else None


def observe_voyager_feed_call(
    driver: Any,
    *,
    timeout_seconds: float = 10.0,
    scroll_interval_seconds: float = 1.5,
    sleep: Callable[[float], None] = time.sleep,
) -> tuple[str | None, str | None]:
    """Watch the tab for a voyager feed call; return (queryId, profileUrn).

    Registers a CDP ResponseReceived handler, scrolls incrementally to trip
    LinkedIn's IntersectionObserver (which is what normally fires the first
    feed pagination), and returns values from the first feed URL seen. Either
    value may be None if not present — caller falls back to defaults.

    Observing the live call gives us both the exact queryId LinkedIn's bundle
    is using right now AND the profileUrn it resolved for this page, which is
    more reliable than any DOM-scraping heuristic.
    """
    from botasaurus_driver.cdp import network as cdp_network  # noqa: F401

    observed: dict[str, str | None] = {'query_id': None, 'profile_urn': None}

    def _on_response(_request_id: Any, response: Any, _event: Any) -> None:
        url = getattr(response, 'url', '') or ''
        if observed['query_id'] is None:
            query_id = extract_query_id_from_url(url)
            if query_id:
                observed['query_id'] = query_id
                # profileUrn only exists on feed URLs, and only ever grabs from
                # the same URL that yielded the queryId.
                observed['profile_urn'] = extract_profile_urn_from_url(url)

    tab = driver._get_driver()._tab  # type: ignore[attr-defined]
    tab.after_response_received(_on_response)

    deadline = time.time() + timeout_seconds
    while time.time() < deadline and observed['query_id'] is None:
        try:
            driver.run_js('window.scrollBy(0, window.innerHeight);')
        except Exception:
            break
        sleep(scroll_interval_seconds)

    return observed['query_id'], observed['profile_urn']


def observe_voyager_query_id(
    driver: Any,
    *,
    timeout_seconds: float = 10.0,
    scroll_interval_seconds: float = 1.5,
    sleep: Callable[[float], None] = time.sleep,
) -> str | None:
    """Back-compat shim: return only the queryId from the first observed call."""
    query_id, _ = observe_voyager_feed_call(
        driver,
        timeout_seconds=timeout_seconds,
        scroll_interval_seconds=scroll_interval_seconds,
        sleep=sleep,
    )
    return query_id


def _extract_profile_urn(driver: Any) -> str:
    """Pull the target profile URN from the loaded page, preferring stable anchors.

    Multiple profile URNs are referenced on any page (the viewer's own,
    suggestions, etc.). Strategies in order of reliability:

      1. A `data-urn="urn:li:fsd_profile:..."` attribute on a profile-card /
         top-card element — explicitly tied to the page's subject.
      2. Any `data-urn` with the fsd_profile prefix — still structured data,
         not a string match.
      3. Frequency heuristic over the full page HTML — works for prolific
         profiles but can be wrong on sparse pages.

    The caller may also pass a URN observed from a live voyager feed call;
    that's preferred over everything here and should be tried first.
    """
    return driver.run_js(
        """
// Strategy 1: data-urn on an element whose class hints it's the top card
const candidates = Array.from(document.querySelectorAll('[data-urn^="urn:li:fsd_profile:"]'));
for (const el of candidates) {
  const cls = (el.className && typeof el.className === 'string') ? el.className : '';
  if (cls.includes('top-card') || cls.includes('pv-top') || cls.includes('profile-view')) {
    return el.getAttribute('data-urn');
  }
}
// Strategy 2: first structured data-urn of any kind
if (candidates.length > 0) {
  return candidates[0].getAttribute('data-urn');
}
// Strategy 3: frequency heuristic
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
    script returns an async IIFE invocation whose Promise CDP awaits. CSRF
    extraction tries the cookie first (both quoted and unquoted JSESSIONID
    forms LinkedIn has used historically), falling back to the page's csrf
    meta tag when present.
    """
    js_url = json.dumps(url)
    script = f"""
return (async () => {{
  let csrf = '';
  const cookie = document.cookie || '';
  const quoted = cookie.match(/JSESSIONID="([^"]+)"/);
  const bare = cookie.match(/JSESSIONID=([^;]+)/);
  if (quoted) csrf = quoted[1];
  else if (bare) csrf = bare[1].replace(/^"|"$/g, '');
  if (!csrf) {{
    const meta = document.querySelector('meta[name="csrfToken"]');
    if (meta) csrf = meta.getAttribute('content') || '';
  }}
  const resp = await fetch({js_url}, {{
    credentials: 'include',
    headers: {{
      accept: 'application/vnd.linkedin.normalized+json+2.1',
      'x-restli-protocol-version': '2.0.0',
      'csrf-token': csrf,
    }},
  }});
  const text = await resp.text();
  return {{status: resp.status, body: text, csrf_present: !!csrf}};
}})();
"""
    return driver.run_js(script)


def _fetch_with_retry(
    driver: Any,
    url: str,
    *,
    page_index: int,
    max_attempts: int = 3,
    base_delay_seconds: float = 1.0,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    """Execute _fetch_voyager_page with exponential backoff on transient errors.

    Transient = network/tab exception, 5xx, or malformed dict return. Auth,
    rate-limit, and queryId errors propagate immediately — retrying them would
    just delay the inevitable.
    """
    last_transient: VoyagerError | None = None
    for attempt in range(max_attempts):
        try:
            raw = _fetch_voyager_page(driver, url)
        except Exception as exc:
            last_transient = VoyagerTransientError(
                f'fetch threw {type(exc).__name__}: {exc}',
                page_index=page_index,
            )
            if attempt + 1 < max_attempts:
                sleep(base_delay_seconds * (3 ** attempt))
                continue
            raise last_transient from exc

        status = raw.get('status') if isinstance(raw, dict) else None
        body = (raw.get('body') if isinstance(raw, dict) else '') or ''
        err_cls = classify_voyager_response(status, body)
        if err_cls is None:
            return {'status': status, 'body': body}
        if err_cls is VoyagerTransientError and attempt + 1 < max_attempts:
            last_transient = err_cls(
                f'transient status={status}', page_index=page_index, status=status,
                body_head=body[:200],
            )
            sleep(base_delay_seconds * (3 ** attempt))
            continue
        raise err_cls(
            f'voyager page {page_index} returned status={status}',
            page_index=page_index,
            status=status,
            body_head=body[:200],
        )

    assert last_transient is not None  # unreachable once loop body ran
    raise last_transient


def paginate_voyager_feed(
    fetch_page: Callable[[str], dict[str, Any]],
    *,
    profile_urn: str,
    query_id: str = DEFAULT_QUERY_ID,
    max_pages: int = 200,
    delay_seconds: float = 1.5,
    sleep: Callable[[float], None] = time.sleep,
    on_page: Callable[[int, int, int], None] | None = None,
    stop_after_urn: str | None = None,
    resume_from: tuple[int, str] | None = None,
    on_checkpoint: Callable[[int, str], None] | None = None,
) -> list[LinkedInPost]:
    """Paginate through the voyager feed endpoint and return LinkedInPost objects.

    `fetch_page(url)` must return `{'status': int, 'body': str}` or raise a
    VoyagerError subclass. The crawler injects a retry-wrapped driver call;
    tests pass a pure dict-returning stub and skip the network entirely.

    Stops when:
      - a page returns zero new posts,
      - the paginationToken doesn't advance,
      - `max_pages` is reached, or
      - `stop_after_urn` is set and that URN appears in a page (daily-sync
        short-circuit: we've caught up with previously-synced history).
    """
    all_posts: list[LinkedInPost] = []
    seen: set[str] = set()
    if resume_from is not None:
        start, token = resume_from
    else:
        start, token = 0, ''

    for page_i in range(max_pages):
        url = _build_voyager_url(
            profile_urn=profile_urn, query_id=query_id, start=start, pagination_token=token
        )
        result = fetch_page(url)
        body = result.get('body') or ''
        try:
            posts, next_token = parse_voyager_response(body)
        except Exception as exc:
            # Parser failing on a 200 response means the body shape surprised
            # us — usually a queryId rotation served a payload we don't grok.
            raise VoyagerQueryIdError(
                f'voyager page {page_i} body failed to parse: {type(exc).__name__}: {exc}',
                page_index=page_i,
                status=result.get('status'),
                body_head=body[:200],
            ) from exc
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
        if stop_after_urn is not None and any(p.post_id == stop_after_urn for p in posts):
            break
        # Persist the position we're about to advance to. If the process dies
        # during the sleep or the next fetch, a resume will pick up here.
        if on_checkpoint:
            on_checkpoint(start + 20, next_token)
        token = next_token
        start += 20
        sleep(delay_seconds)

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
        # Try to discover a fresh queryId + authoritative profileUrn by letting
        # LinkedIn issue its own first feed call. If the bundle never emits one
        # (happens when the initial 20 posts render server-side), fall back.
        requested_query_id = data.get('query_id') or DEFAULT_QUERY_ID
        observed_query_id: str | None = None
        observed_profile_urn: str | None = None
        if data.get('discover_query_id', True):
            observed_query_id, observed_profile_urn = observe_voyager_feed_call(
                driver, timeout_seconds=8.0
            )

        if observed_query_id:
            print(f'queryId: {observed_query_id} (observed live)', flush=True)
            query_id = observed_query_id
        else:
            print(f'queryId: {requested_query_id} (fallback — no live call seen)', flush=True)
            query_id = requested_query_id

        if observed_profile_urn:
            profile_urn = observed_profile_urn
            print(f'profileUrn: {profile_urn} (from live feed call)', flush=True)
        else:
            profile_urn = _extract_profile_urn(driver)
            if not profile_urn:
                raise RuntimeError('could not extract profileUrn from page')
            print(f'profileUrn: {profile_urn} (from DOM heuristic)', flush=True)

        # paginate_voyager_feed doesn't know the current page index when it
        # calls its fetcher, so we track the counter here and increment
        # post-call. Keeps the retry layer's error messages informative
        # without exposing the counter in the public API.
        page_counter = {'i': 0}

        def _fetch_page(url: str) -> dict[str, Any]:
            try:
                return _fetch_with_retry(driver, url, page_index=page_counter['i'])
            finally:
                page_counter['i'] += 1

        resume_from = data.get('resume_from')  # tuple[int, str] | None
        on_checkpoint = data.get('on_checkpoint')  # Callable[[int, str], None] | None

        posts = paginate_voyager_feed(
            _fetch_page,
            profile_urn=profile_urn,
            query_id=query_id,
            max_pages=data.get('max_pages', 200),
            delay_seconds=data.get('delay_seconds', 1.5),
            stop_after_urn=data.get('stop_after_urn'),
            resume_from=resume_from,
            on_checkpoint=on_checkpoint,
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
    query_id: str | None = None,
    discover_query_id: bool = True,
    stop_after_urn: str | None = None,
    resume_from: tuple[int, str] | None = None,
    on_checkpoint: Callable[[int, str], None] | None = None,
) -> list[LinkedInPost]:
    """Public entry point: launches a browser, authenticates, paginates.

    `discover_query_id=True` (the default) asks the runner to observe one live
    feed call after login and use its queryId. Set `query_id=` explicitly to
    bypass discovery and pin a specific hash.

    `stop_after_urn` causes pagination to halt as soon as the given activity
    URN appears in a response — used by daily sync to avoid refetching history
    we've already exported.

    `resume_from=(start, token)` + `on_checkpoint` let an interrupted run pick
    up where it left off. The sqlite-backed versions of these are wired in by
    the CLI when `--resume` is passed.
    """
    payload = _decorated_runner()(
        {
            'url': url,
            'max_pages': max_pages,
            'delay_seconds': delay_seconds,
            'query_id': query_id,
            'discover_query_id': discover_query_id,
            'stop_after_urn': stop_after_urn,
            'resume_from': resume_from,
            'on_checkpoint': on_checkpoint,
        }
    )
    return [LinkedInPost(**p) for p in payload]
