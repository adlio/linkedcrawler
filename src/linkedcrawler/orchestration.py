from __future__ import annotations

import json
import os
import time
from collections.abc import Callable, Sequence
from functools import lru_cache
from pathlib import Path
from typing import Any, Protocol, cast

from .auth import ensure_linkedin_login
from .extractors import POST_SELECTORS, extract_all_posts, find_video_cdn_urls, matches_linkedin_activity
from .models import CrawlRequest, CrawlResult, ExtractionError, LinkedInPost
from .secrets import get_linkedin_credentials


class BrowserSession(Protocol):
    def get(self, url: str) -> None: ...
    def page_html(self) -> str: ...
    def scroll_to_bottom(self) -> None: ...
    def resource_urls(self) -> Sequence[str]: ...
    def type(self, selector: str, text: str) -> None: ...
    def click(self, selector: str) -> None: ...


class BotasaurusSessionAdapter:
    def __init__(self, driver: Any):
        self.driver = driver

    def get(self, url: str) -> None:
        self.driver.get(url)

    def page_html(self) -> str:
        html = self.driver.page_html
        return html() if callable(html) else cast(str, html)

    def scroll_to_bottom(self) -> None:
        # LinkedIn's activity page uses scaffold-finite-scroll--finite, which pages
        # via an explicit "Show more results" button rather than infinite scroll.
        # The button sits in the middle of the document (followed by "People you
        # may know" etc.), so scrollTo(body.scrollHeight) overshoots it. Scroll
        # directly to the button's position instead.
        self._install_fetch_tap_if_needed()
        time.sleep(0.3)
        outcome = self._attempt_load_more_click()
        _append_debug_log('scroll_to_bottom', outcome)
        time.sleep(2.5 if outcome.get('clicked') else 0.3)
        for _ in range(6):
            self.driver.run_js("window.scrollBy(0, Math.max(600, window.innerHeight));")
            time.sleep(0.3)
        self.driver.run_js("window.scrollTo(0, document.body.scrollHeight);")
        self._drain_fetch_tap()

    def _install_fetch_tap_if_needed(self) -> None:
        if not os.environ.get('LINKEDCRAWLER_DEBUG_DIR'):
            return
        if not os.environ.get('LINKEDCRAWLER_FETCH_TAP'):
            return
        if getattr(self, '_fetch_tap_installed', False):
            return
        self._fetch_tap_installed = True
        self._fetch_tap_buffer: list[dict[str, Any]] = []

        def on_response(request_id: Any, response: Any, _event: Any) -> None:
            url = getattr(response, 'url', '') or ''
            if 'linkedin.com' not in url:
                return
            if any(marker in url for marker in ('/7MUFHs', '/sensorCollect', '/li/track', '/tscp/')):
                return
            self._fetch_tap_buffer.append(
                {
                    'url': url,
                    'status': getattr(response, 'status', None),
                    'mime': getattr(response, 'mime_type', None),
                    'ts': int(time.time() * 1000),
                    'request_id': request_id,
                }
            )

        tab = self.driver._get_driver()._tab  # type: ignore[attr-defined]
        tab.after_response_received(on_response)

    def _drain_fetch_tap(self) -> None:
        target = os.environ.get('LINKEDCRAWLER_DEBUG_DIR')
        if not target or not getattr(self, '_fetch_tap_installed', False):
            return
        buffer = getattr(self, '_fetch_tap_buffer', None)
        if not buffer:
            return
        entries = buffer[:]
        buffer.clear()

        # Best-effort: pull response bodies for voyager/feed URLs so we can see
        # the pagination cursor / hasMore signal.
        from botasaurus_driver.cdp import network as cdp_network

        tab = self.driver._get_driver()._tab  # type: ignore[attr-defined]
        for entry in entries:
            if 'voyager' not in entry.get('url', ''):
                entry.pop('request_id', None)
                continue
            try:
                body, is_b64 = tab.send(cdp_network.get_response_body(entry['request_id']))
                if is_b64:
                    entry['body_b64_head'] = body[:8000]
                else:
                    # Full voyager feed response — keep enough to map the schema.
                    entry['body_head'] = body[:40000]
                    entry['body_len'] = len(body)
            except Exception as exc:
                entry['body_err'] = f'{type(exc).__name__}: {exc}'
            entry.pop('request_id', None)

        path = Path(target) / 'fetches.jsonl'
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open('a') as fh:
            for entry in entries:
                fh.write(json.dumps(entry, default=str) + '\n')

    def _attempt_load_more_click(self) -> dict[str, Any]:
        report: dict[str, Any] = {'strategies': []}

        def record(name: str, ok: bool, detail: str = '') -> None:
            report['strategies'].append({'name': name, 'ok': ok, 'detail': detail})

        # Strategy 1: real mouse click via CDP Input.dispatchMouseEvent. Produces a
        # trusted click — necessary because LinkedIn's Ember handlers ignore the
        # synthetic click that `(el) => el.click()` dispatches. Botasaurus wraps
        # run_js in its own IIFE, so scripts must use top-level `return`.
        coords = self.driver.run_js(
            """
const btn = document.querySelector('.pv-recent-activity-detail__core-rail .scaffold-finite-scroll__load-button');
if (!btn) return null;
// Scroll the window so the button sits in viewport centre. scrollIntoView
// alone is unreliable when an ancestor has overflow:auto or the page was
// previously parked at scrollHeight past the button.
const rectPre = btn.getBoundingClientRect();
const docTop = rectPre.top + window.scrollY;
window.scrollTo(0, Math.max(0, docTop - window.innerHeight / 2 + rectPre.height / 2));
const r = btn.getBoundingClientRect();
return {
  x: Math.round(r.left + r.width / 2),
  y: Math.round(r.top + r.height / 2),
  visible: r.width > 0 && r.height > 0 && r.top >= 0 && r.top < window.innerHeight,
};
"""
        )
        if isinstance(coords, dict) and coords.get('visible'):
            try:
                self.driver.click_at_point(coords['x'], coords['y'])
                record('click_at_point', True, f"x={coords['x']} y={coords['y']}")
                report['clicked'] = True
                return report
            except Exception as exc:
                record('click_at_point', False, f'{type(exc).__name__}: {exc}')
        else:
            record('click_at_point', False, f'no visible button: {coords!r}')

        # Strategy 2: synthetic click via Botasaurus (fallback).
        try:
            self.driver.click('.pv-recent-activity-detail__core-rail .scaffold-finite-scroll__load-button')
            record('driver.click', True)
            report['clicked'] = True
            return report
        except Exception as exc:
            record('driver.click', False, f'{type(exc).__name__}: {exc}')

        report['clicked'] = False
        return report

    def type(self, selector: str, text: str) -> None:
        self.driver.type(selector, text)

    def type_by_label(self, label: str, text: str) -> None:
        self.driver.type_by_label(label, text)

    def click(self, selector: str) -> None:
        self.driver.click(selector)

    def run_js(self, script: str) -> object:
        return self.driver.run_js(script)

    def click_text(self, text: str) -> None:
        self.driver.click_element_containing_text(text)

    def resource_urls(self) -> Sequence[str]:
        requests = getattr(self.driver, 'requests', None)
        if requests is None:
            return []
        if callable(requests):
            requests = requests()
        if not isinstance(requests, (list, tuple)):
            return []
        urls: list[str] = []
        for request in requests:
            url = getattr(request, 'url', None)
            if isinstance(url, str):
                urls.append(url)
        return urls


def crawl_session(
    session: BrowserSession,
    request: CrawlRequest,
    *,
    sleep: Callable[[float], None] = time.sleep,
    debug_dir: Path | None = None,
) -> CrawlResult:
    if not matches_linkedin_activity(request.url):
        raise ValueError('URL does not look like a LinkedIn recent activity page')

    recorder = _DebugRecorder(debug_dir)

    session.get(request.url)

    html = session.page_html()
    recorder.dump('initial', html)
    report = extract_all_posts(html)
    attempts = 0
    while not report.items and attempts < request.wait_attempts:
        attempts += 1
        sleep(request.wait_delay_seconds)
        html = session.page_html()
        report = extract_all_posts(html)
    recorder.dump('after-wait', html, meta={'wait_attempts_used': attempts})

    if not report.items:
        selectors = ', '.join(POST_SELECTORS)
        raise RuntimeError(f'No LinkedIn posts found. Selectors tried: {selectors}')

    result = CrawlResult(request=request, extraction_errors=list(report.errors))
    processed_ids: set[str] = set()
    stale_rounds = 0

    while stale_rounds < request.max_scroll_rounds and not result.reached_last_saved_item:
        html = session.page_html()
        report = extract_all_posts(html)
        result.extraction_errors = _merge_errors(result.extraction_errors, report.errors)

        new_posts_this_round = 0
        for post in report.items:
            if post.post_id in processed_ids:
                continue
            processed_ids.add(post.post_id)
            new_posts_this_round += 1

            if post.post_id == request.last_saved_item_key:
                result.reached_last_saved_item = True
                break

            if result.newest_item_key is None:
                result.newest_item_key = post.post_id

            result.posts.append(_attach_video_urls(post, session.resource_urls()))

        recorder.dump(
            f'round-{result.rounds_scrolled:02d}',
            html,
            meta={
                'new_posts_this_round': new_posts_this_round,
                'posts_total': len(result.posts),
                'stale_rounds': stale_rounds,
                'extracted_in_dom': len(report.items),
            },
        )

        if result.reached_last_saved_item:
            break

        if new_posts_this_round == 0:
            stale_rounds += 1
        else:
            stale_rounds = 0

        result.rounds_scrolled += 1
        if stale_rounds >= request.max_scroll_rounds:
            break
        session.scroll_to_bottom()
        sleep(request.wait_delay_seconds)

    recorder.write_summary(result)
    return result


def _append_debug_log(event: str, payload: dict[str, Any]) -> None:
    target = os.environ.get('LINKEDCRAWLER_DEBUG_DIR')
    if not target:
        return
    path = Path(target) / 'events.jsonl'
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a') as fh:
        fh.write(json.dumps({'event': event, **payload}) + '\n')


class _DebugRecorder:
    def __init__(self, base: Path | None) -> None:
        self.base = Path(base) if base else None
        self.counter = 0
        if self.base is not None:
            self.base.mkdir(parents=True, exist_ok=True)

    def dump(self, label: str, html: str, *, meta: dict[str, Any] | None = None) -> None:
        if self.base is None:
            return
        name = f'{self.counter:02d}-{label}'
        self.counter += 1
        (self.base / f'{name}.html').write_text(html)
        if meta is not None:
            (self.base / f'{name}.json').write_text(json.dumps(meta, indent=2))

    def write_summary(self, result: CrawlResult) -> None:
        if self.base is None:
            return
        (self.base / 'summary.json').write_text(
            json.dumps(
                {
                    'posts': len(result.posts),
                    'rounds_scrolled': result.rounds_scrolled,
                    'reached_last_saved_item': result.reached_last_saved_item,
                    'newest_item_key': result.newest_item_key,
                    'extraction_errors': len(result.extraction_errors),
                },
                indent=2,
            )
        )


def _attach_video_urls(post: LinkedInPost, resource_urls: Sequence[str]) -> LinkedInPost:
    if not post.has_video:
        return post
    cloned = LinkedInPost(**post.to_dict())
    cloned.video_cdn_urls = find_video_cdn_urls(cloned.video_id, resource_urls)
    return cloned


def _merge_errors(existing: list, new_errors: list) -> list:
    merged = {(error.index, error.message): error for error in existing}
    for error in new_errors:
        merged[(error.index, error.message)] = error
    return list(merged.values())


@lru_cache(maxsize=1)
def _decorated_runner() -> Callable[[CrawlRequest], dict[str, Any]]:
    from botasaurus.browser import browser

    @browser(
        headless=False,
        block_images=True,
        reuse_driver=False,
        profile='linkedin-crawler',
        enable_xvfb_virtual_display=True,
    )
    def _run(driver: Any, request: CrawlRequest) -> dict[str, Any]:
        session = BotasaurusSessionAdapter(driver)
        debug_env = os.environ.get('LINKEDCRAWLER_DEBUG_DIR')
        debug_dir = Path(debug_env) if debug_env else None
        ensure_linkedin_login(
            session,
            get_linkedin_credentials(),
            sleep=time.sleep,
            debug_dir=debug_dir,
        )
        return crawl_session(
            session,
            request,
            sleep=time.sleep,
            debug_dir=debug_dir,
        ).to_dict()

    return _run


def run_linkedin_crawl(request: CrawlRequest) -> CrawlResult:
    payload = _decorated_runner()(request)
    posts = [LinkedInPost(**post) for post in payload['posts']]
    extraction_errors = [ExtractionError(**error) for error in payload['extraction_errors']]
    result = CrawlResult(
        request=CrawlRequest(**payload['request']),
        posts=posts,
        rounds_scrolled=payload['rounds_scrolled'],
        reached_last_saved_item=payload['reached_last_saved_item'],
        newest_item_key=payload['newest_item_key'],
    )
    result.extraction_errors = extraction_errors
    return result
