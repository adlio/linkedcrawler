from __future__ import annotations

from pathlib import Path

import pytest

from linkedcrawler.models import CrawlRequest
from linkedcrawler.orchestration import crawl_session

FIXTURE_HTML = (Path(__file__).parent / 'fixtures' / 'linkedin_feed.html').read_text()


class FakeBrowserSession:
    def __init__(self, pages: list[str], resource_urls: list[str] | None = None):
        self.pages = pages
        self.resource_url_values = resource_urls or []
        self.current_index = 0
        self.visited_urls: list[str] = []
        self.scroll_calls = 0

    def get(self, url: str) -> None:
        self.visited_urls.append(url)

    def page_html(self) -> str:
        return self.pages[self.current_index]

    def scroll_to_bottom(self) -> None:
        self.scroll_calls += 1
        if self.current_index < len(self.pages) - 1:
            self.current_index += 1

    def resource_urls(self) -> list[str]:
        return self.resource_url_values


def test_crawl_session_collects_posts_and_stops_after_stale_rounds() -> None:
    session = FakeBrowserSession(
        pages=[FIXTURE_HTML, FIXTURE_HTML],
        resource_urls=[
            'https://dms.licdn.com/playlist/vid/v2/D4E05AQFakeVideoId123/mp4_720p/video.mp4',
            'https://dms.licdn.com/playlist/vid/v2/D4E05AQEsOzrNYKp1RQ/mp4_720p/video.mp4',
        ],
    )
    result = crawl_session(
        session,
        CrawlRequest(
            url='https://www.linkedin.com/in/simonwardley/recent-activity/all/',
            max_scroll_rounds=2,
            wait_attempts=1,
            wait_delay_seconds=0,
        ),
        sleep=lambda _: None,
    )
    assert session.visited_urls == ['https://www.linkedin.com/in/simonwardley/recent-activity/all/']
    assert len(result.posts) == 5
    assert result.newest_item_key == 'urn:li:activity:7100000000000000001'
    assert result.rounds_scrolled == 3
    assert session.scroll_calls == 2
    video_posts = [post for post in result.posts if post.has_video]
    assert video_posts[0].video_cdn_urls == [
        'https://dms.licdn.com/playlist/vid/v2/D4E05AQFakeVideoId123/mp4_720p/video.mp4'
    ]
    assert video_posts[1].video_cdn_urls == [
        'https://dms.licdn.com/playlist/vid/v2/D4E05AQEsOzrNYKp1RQ/mp4_720p/video.mp4'
    ]


def test_crawl_session_stops_at_last_saved_key() -> None:
    session = FakeBrowserSession(pages=[FIXTURE_HTML])
    result = crawl_session(
        session,
        CrawlRequest(
            url='https://www.linkedin.com/in/simonwardley/recent-activity/all/',
            last_saved_item_key='urn:li:activity:7100000000000000003',
            max_scroll_rounds=2,
            wait_attempts=1,
            wait_delay_seconds=0,
        ),
        sleep=lambda _: None,
    )
    assert [post.post_id for post in result.posts] == [
        'urn:li:activity:7100000000000000001',
        'urn:li:activity:7100000000000000002',
    ]
    assert result.reached_last_saved_item is True


def test_crawl_session_waits_for_posts_to_appear() -> None:
    session = FakeBrowserSession(pages=['<html></html>', FIXTURE_HTML])
    sleeps: list[float] = []

    def remember_sleep(seconds: float) -> None:
        sleeps.append(seconds)
        session.scroll_to_bottom()

    result = crawl_session(
        session,
        CrawlRequest(
            url='https://www.linkedin.com/in/simonwardley/recent-activity/all/',
            max_scroll_rounds=1,
            wait_attempts=2,
            wait_delay_seconds=0.25,
        ),
        sleep=remember_sleep,
    )
    assert sleeps[0] == 0.25
    assert len(result.posts) == 5


def test_crawl_session_rejects_non_activity_urls() -> None:
    session = FakeBrowserSession(pages=[FIXTURE_HTML])
    with pytest.raises(ValueError):
        crawl_session(session, CrawlRequest(url='https://www.linkedin.com/feed/'), sleep=lambda _: None)


def test_crawl_session_raises_when_no_posts_found() -> None:
    session = FakeBrowserSession(pages=['<html></html>'])
    with pytest.raises(RuntimeError):
        crawl_session(
            session,
            CrawlRequest(
                url='https://www.linkedin.com/in/simonwardley/recent-activity/all/',
                wait_attempts=1,
                wait_delay_seconds=0,
            ),
            sleep=lambda _: None,
        )
