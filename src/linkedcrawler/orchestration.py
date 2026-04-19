from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from functools import lru_cache
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
        self.driver.run_js("window.scrollTo(0, document.body.scrollHeight);")

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
) -> CrawlResult:
    if not matches_linkedin_activity(request.url):
        raise ValueError('URL does not look like a LinkedIn recent activity page')

    session.get(request.url)

    html = session.page_html()
    report = extract_all_posts(html)
    attempts = 0
    while not report.items and attempts < request.wait_attempts:
        attempts += 1
        sleep(request.wait_delay_seconds)
        html = session.page_html()
        report = extract_all_posts(html)

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

    return result


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
        ensure_linkedin_login(session, get_linkedin_credentials(), sleep=time.sleep)
        return crawl_session(session, request, sleep=time.sleep).to_dict()

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
