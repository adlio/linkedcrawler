"""Unit tests for the voyager API crawler.

The browser runner itself (`crawl_via_api`) is integration-tested via live
runs; here we cover the pieces that matter for correctness without a browser:
URL construction, response classification, the retry wrapper, and the
pagination loop driven by an injected fetch stub.
"""

from __future__ import annotations

import json

import pytest

from linkedcrawler.voyager_crawler import (
    DEFAULT_QUERY_ID,
    VoyagerAuthError,
    VoyagerError,
    VoyagerQueryIdError,
    VoyagerRateLimitError,
    VoyagerTransientError,
    _build_voyager_url,
    _extract_profile_urn,
    _fetch_with_retry,
    classify_voyager_response,
    extract_profile_urn_from_url,
    extract_query_id_from_url,
    paginate_voyager_feed,
)


# ---------------------------------------------------------------------------
# _build_voyager_url
# ---------------------------------------------------------------------------

def test_build_url_omits_pagination_token_when_empty() -> None:
    url = _build_voyager_url(
        profile_urn='urn:li:fsd_profile:ABC',
        query_id='voyagerFeedDashProfileUpdates.abc123',
        start=0,
        pagination_token='',
    )
    assert 'count:20' in url
    assert 'start:0' in url
    assert 'profileUrn:urn%3Ali%3Afsd_profile%3AABC' in url
    assert 'paginationToken' not in url
    assert 'queryId=voyagerFeedDashProfileUpdates.abc123' in url


def test_build_url_includes_pagination_token_when_provided() -> None:
    url = _build_voyager_url(
        profile_urn='urn:li:fsd_profile:ABC',
        query_id='voyagerFeedDashProfileUpdates.abc123',
        start=40,
        pagination_token='dXJuOmxpOmFjdGl2aXR5',
    )
    assert 'start:40' in url
    assert 'paginationToken:dXJuOmxpOmFjdGl2aXR5' in url


# ---------------------------------------------------------------------------
# classify_voyager_response
# ---------------------------------------------------------------------------

def test_classify_200_json_body_is_ok() -> None:
    assert classify_voyager_response(200, '{"data": {}}') is None


def test_classify_200_html_body_suggests_query_id_rotation() -> None:
    # LinkedIn sometimes serves an HTML error/authwall page with a 200 when
    # the queryId no longer resolves. That's the dominant cause of this
    # shape, so we surface it as VoyagerQueryIdError.
    assert classify_voyager_response(200, '<html>nope</html>') is VoyagerQueryIdError


def test_classify_401_is_auth() -> None:
    assert classify_voyager_response(401, '') is VoyagerAuthError


def test_classify_403_csrf_body_is_auth() -> None:
    assert classify_voyager_response(403, 'CSRF check failed.') is VoyagerAuthError


def test_classify_404_is_query_id_error() -> None:
    assert classify_voyager_response(404, 'not found') is VoyagerQueryIdError


def test_classify_429_is_rate_limit() -> None:
    assert classify_voyager_response(429, 'Too Many Requests') is VoyagerRateLimitError


def test_classify_5xx_is_transient() -> None:
    assert classify_voyager_response(500, '') is VoyagerTransientError
    assert classify_voyager_response(502, '') is VoyagerTransientError
    assert classify_voyager_response(504, '') is VoyagerTransientError


def test_classify_no_status_is_transient() -> None:
    assert classify_voyager_response(None, '') is VoyagerTransientError


# ---------------------------------------------------------------------------
# paginate_voyager_feed (with injected fetch stub)
# ---------------------------------------------------------------------------

def _feed_body(activity_ids: list[int], next_token: str | None = None) -> str:
    """Build a minimally-shaped voyager response body for N activity URNs."""
    element_refs = [
        f'urn:li:fsd_update:(urn:li:activity:{aid},MEMBER_SHARES,DEBUG_REASON,DEFAULT,false)'
        for aid in activity_ids
    ]
    included = [
        {
            '$type': 'com.linkedin.voyager.dash.feed.Update',
            'entityUrn': ref,
            'actor': {'name': {'text': 'Simon Wardley'}},
            'header': None,
            'commentary': {'text': {'text': f'post {aid}'}},
        }
        for aid, ref in zip(activity_ids, element_refs)
    ]
    return json.dumps({
        'data': {'data': {'feedDashProfileUpdatesByMemberShareFeed': {
            '*elements': element_refs,
            'metadata': {'paginationToken': next_token},
        }}},
        'included': included,
    })


def _stub_fetch(pages: list[dict]):
    """Sequential fetch stub: each call returns the next page dict, in order."""
    iterator = iter(pages)

    def fetch(_url: str) -> dict:
        return next(iterator)

    return fetch


def test_paginate_stops_when_elements_are_empty() -> None:
    fetch = _stub_fetch([
        {'status': 200, 'body': _feed_body([], next_token=None)},
    ])
    posts = paginate_voyager_feed(
        fetch,
        profile_urn='urn:li:fsd_profile:X',
        sleep=lambda _: None,
    )
    assert posts == []


def test_paginate_chains_pages_using_tokens() -> None:
    fetch = _stub_fetch([
        {'status': 200, 'body': _feed_body([1, 2, 3], next_token='tok-a')},
        {'status': 200, 'body': _feed_body([4, 5], next_token='tok-b')},
        {'status': 200, 'body': _feed_body([], next_token=None)},
    ])
    posts = paginate_voyager_feed(
        fetch,
        profile_urn='urn:li:fsd_profile:X',
        sleep=lambda _: None,
    )
    assert [p.post_id for p in posts] == [
        'urn:li:activity:1',
        'urn:li:activity:2',
        'urn:li:activity:3',
        'urn:li:activity:4',
        'urn:li:activity:5',
    ]


def test_paginate_stops_when_token_not_advanced() -> None:
    # If the server keeps handing back an empty paginationToken we must bail,
    # otherwise we'd loop forever refetching start=0.
    fetch = _stub_fetch([
        {'status': 200, 'body': _feed_body([1, 2], next_token=None)},
    ])
    posts = paginate_voyager_feed(
        fetch,
        profile_urn='urn:li:fsd_profile:X',
        sleep=lambda _: None,
    )
    assert len(posts) == 2


def test_paginate_honours_max_pages_ceiling() -> None:
    # Responder would happily keep serving; we should stop at max_pages anyway.
    def fetch(_url: str) -> dict:
        return {'status': 200, 'body': _feed_body([999], next_token='never-empty')}

    posts = paginate_voyager_feed(
        fetch,
        profile_urn='urn:li:fsd_profile:X',
        max_pages=2,
        sleep=lambda _: None,
    )
    # First page emits urn:li:activity:999, second page sees it again and adds nothing.
    assert [p.post_id for p in posts] == ['urn:li:activity:999']


def test_paginate_sleeps_between_successful_pages() -> None:
    sleeps: list[float] = []
    fetch = _stub_fetch([
        {'status': 200, 'body': _feed_body([1], next_token='tok')},
        {'status': 200, 'body': _feed_body([], next_token=None)},
    ])
    paginate_voyager_feed(
        fetch,
        profile_urn='urn:li:fsd_profile:X',
        delay_seconds=2.25,
        sleep=sleeps.append,
    )
    assert sleeps == [2.25]  # one sleep between the two pages, none after the terminal empty page


def test_paginate_wraps_parse_failure_as_query_id_error() -> None:
    # 200 with a body that parse_voyager_response blows up on — same
    # signature as a rotated queryId serving a surprise payload. The retry
    # layer won't catch this (status is 200), so paginate needs to translate.
    fetch = _stub_fetch([
        {'status': 200, 'body': 'this is not json at all'},
    ])
    with pytest.raises(VoyagerQueryIdError):
        paginate_voyager_feed(
            fetch,
            profile_urn='urn:li:fsd_profile:X',
            sleep=lambda _: None,
        )


# ---------------------------------------------------------------------------
# _fetch_with_retry
# ---------------------------------------------------------------------------

class _StubDriver:
    """Records run_js invocations and returns responses from a queue.

    If an entry in the queue is an Exception subclass, it is raised instead of
    returned — used to simulate network / tab failures.
    """

    def __init__(self, responses: list) -> None:
        self._responses = list(responses)
        self.calls = 0

    def run_js(self, _script: str):
        self.calls += 1
        item = self._responses.pop(0)
        if isinstance(item, BaseException) or (isinstance(item, type) and issubclass(item, BaseException)):
            raise item if isinstance(item, BaseException) else item()
        return item


def test_fetch_with_retry_returns_success_immediately() -> None:
    driver = _StubDriver([{'status': 200, 'body': '{}'}])
    result = _fetch_with_retry(
        driver, 'https://example/url', page_index=0, sleep=lambda _: None
    )
    assert result == {'status': 200, 'body': '{}'}
    assert driver.calls == 1


def test_fetch_with_retry_retries_on_5xx_then_succeeds() -> None:
    driver = _StubDriver([
        {'status': 502, 'body': 'bad gateway'},
        {'status': 503, 'body': 'unavailable'},
        {'status': 200, 'body': '{"ok": true}'},
    ])
    sleeps: list[float] = []
    result = _fetch_with_retry(
        driver, 'https://example/url', page_index=0,
        max_attempts=3, base_delay_seconds=0.5, sleep=sleeps.append,
    )
    assert result['status'] == 200
    assert driver.calls == 3
    # exponential: 0.5, 1.5 (next would be 4.5 but we succeeded first).
    assert sleeps == [0.5, 1.5]


def test_fetch_with_retry_gives_up_after_max_attempts_on_5xx() -> None:
    driver = _StubDriver([
        {'status': 502, 'body': ''},
        {'status': 502, 'body': ''},
        {'status': 502, 'body': ''},
    ])
    with pytest.raises(VoyagerTransientError):
        _fetch_with_retry(
            driver, 'https://example/url', page_index=7,
            max_attempts=3, base_delay_seconds=0.1, sleep=lambda _: None,
        )
    assert driver.calls == 3


def test_fetch_with_retry_raises_auth_immediately_without_retrying() -> None:
    driver = _StubDriver([{'status': 403, 'body': 'CSRF check failed.'}])
    with pytest.raises(VoyagerAuthError):
        _fetch_with_retry(
            driver, 'https://example/url', page_index=0, sleep=lambda _: None
        )
    assert driver.calls == 1  # did NOT retry


def test_fetch_with_retry_raises_rate_limit_immediately() -> None:
    driver = _StubDriver([{'status': 429, 'body': ''}])
    with pytest.raises(VoyagerRateLimitError):
        _fetch_with_retry(
            driver, 'https://example/url', page_index=0, sleep=lambda _: None
        )
    assert driver.calls == 1


def test_fetch_with_retry_catches_runtime_exception_as_transient() -> None:
    driver = _StubDriver([
        ConnectionError('tab crashed'),
        {'status': 200, 'body': '{}'},
    ])
    result = _fetch_with_retry(
        driver, 'https://example/url', page_index=0,
        max_attempts=3, base_delay_seconds=0.01, sleep=lambda _: None,
    )
    assert result['status'] == 200
    assert driver.calls == 2


def test_fetch_with_retry_preserves_page_index_in_raised_error() -> None:
    driver = _StubDriver([{'status': 403, 'body': ''}])
    with pytest.raises(VoyagerAuthError) as excinfo:
        _fetch_with_retry(
            driver, 'https://example/url', page_index=42, sleep=lambda _: None
        )
    assert excinfo.value.page_index == 42
    assert excinfo.value.status == 403


# ---------------------------------------------------------------------------
# _extract_profile_urn
# ---------------------------------------------------------------------------

class _ProfileUrnStubDriver:
    def __init__(self, return_value):
        self.return_value = return_value

    def run_js(self, _script: str):
        return self.return_value


def test_extract_profile_urn_returns_driver_js_result() -> None:
    driver = _ProfileUrnStubDriver('urn:li:fsd_profile:ABC123')
    assert _extract_profile_urn(driver) == 'urn:li:fsd_profile:ABC123'


def test_extract_profile_urn_returns_none_when_driver_finds_nothing() -> None:
    driver = _ProfileUrnStubDriver(None)
    assert _extract_profile_urn(driver) is None


# ---------------------------------------------------------------------------
# extract_query_id_from_url
# ---------------------------------------------------------------------------

def test_extract_query_id_from_feed_url() -> None:
    url = (
        'https://www.linkedin.com/voyager/api/graphql?variables=(count:20,start:20)'
        '&queryId=voyagerFeedDashProfileUpdates.4af00b28d60ed0f1488018948daad822'
    )
    assert (
        extract_query_id_from_url(url)
        == 'voyagerFeedDashProfileUpdates.4af00b28d60ed0f1488018948daad822'
    )


def test_extract_query_id_ignores_non_feed_voyager_urls() -> None:
    # Messaging and identity queryIds have the same shape but different prefix;
    # we only care about the profile-updates feed.
    messaging_url = (
        'https://www.linkedin.com/voyager/api/graphql?'
        'queryId=messengerConversations.9501074288a12f3ae9e3c7ea243bccbf'
    )
    assert extract_query_id_from_url(messaging_url) is None


def test_extract_query_id_handles_empty_and_malformed_urls() -> None:
    assert extract_query_id_from_url('') is None
    assert extract_query_id_from_url('not a url') is None
    assert extract_query_id_from_url('https://www.linkedin.com/feed/') is None


def test_extract_profile_urn_from_feed_url() -> None:
    url = (
        'https://www.linkedin.com/voyager/api/graphql?variables=(count:20,start:20,'
        'profileUrn:urn%3Ali%3Afsd_profile%3AACoAAAAMdmABJzOgMdp87Sult7wsvr-uY-ZW3l4,'
        'paginationToken:abc)&queryId=voyagerFeedDashProfileUpdates.hash'
    )
    assert (
        extract_profile_urn_from_url(url)
        == 'urn:li:fsd_profile:ACoAAAAMdmABJzOgMdp87Sult7wsvr-uY-ZW3l4'
    )


def test_extract_profile_urn_returns_none_when_not_present() -> None:
    url_without = 'https://www.linkedin.com/voyager/api/graphql?queryId=messengerConversations.abc'
    assert extract_profile_urn_from_url(url_without) is None
    assert extract_profile_urn_from_url('') is None


def test_default_query_id_has_expected_prefix() -> None:
    # Sanity check that the constant stays aligned with what the extractor
    # looks for — if someone updates DEFAULT_QUERY_ID to a different shape
    # the observe/fallback plumbing would silently stop agreeing.
    assert DEFAULT_QUERY_ID.startswith('voyagerFeedDashProfileUpdates.')


# ---------------------------------------------------------------------------
# Error metadata plumbing
# ---------------------------------------------------------------------------

def test_voyager_error_carries_context() -> None:
    err = VoyagerError('boom', page_index=3, status=500, body_head='{"err":"y"}')
    assert err.page_index == 3
    assert err.status == 500
    assert err.body_head == '{"err":"y"}'


def test_voyager_error_subclasses_are_distinct_for_catch_sites() -> None:
    # Consumers should be able to catch specific subclasses without catching
    # siblings.
    assert not issubclass(VoyagerAuthError, VoyagerRateLimitError)
    assert not issubclass(VoyagerRateLimitError, VoyagerAuthError)
    assert issubclass(VoyagerAuthError, VoyagerError)
    assert issubclass(VoyagerTransientError, VoyagerError)
