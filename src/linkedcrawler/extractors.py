from __future__ import annotations

import re
from collections.abc import Iterable

from bs4 import BeautifulSoup, Tag

from .models import ExtractionError, ExtractionReport, LinkedInPost

POST_SELECTORS = [
    '[data-urn*="urn:li:activity"]',
    '.feed-shared-update-v2',
    '.occludable-update',
]

TEXT_SELECTORS = [
    '.feed-shared-text',
    '.update-components-text',
    '.feed-shared-inline-show-more-text',
]

IMAGE_SELECTORS = [
    '.feed-shared-image img',
    '.update-components-image img',
    '.update-components-image__image',
]

VIDEO_CONTAINER_SELECTORS = [
    '.update-components-linkedin-video',
    '.feed-shared-linkedin-video',
]

IGNORE_IMAGE_PATTERNS = [
    re.compile(r'profile-displayphoto'),
    re.compile(r'company-logo'),
    re.compile(r'group-logo'),
    re.compile(r'shrink_(?:48|100)_'),
    re.compile(r'ghost-person'),
    re.compile(r'ghost-organization'),
]

ACTOR_SELECTORS = [
    '.update-components-actor__title span[aria-hidden="true"]',
    '.update-components-actor__name span[aria-hidden="true"]',
    '.feed-shared-actor__name span[aria-hidden="true"]',
    '.update-components-actor__title',
    '.update-components-actor__name',
    '.feed-shared-actor__name',
]

REPOST_HEADER_SELECTORS = [
    '.update-components-header__text-view',
    '.update-components-header__text',
    '.feed-shared-header__text',
]

PERMALINK_SELECTORS = [
    'a[href*="/feed/update/urn:li:activity:"]',
    '.update-components-actor__sub-description a[href*="activity"]',
    '.feed-shared-actor__sub-description a[href*="activity"]',
]

TIME_SELECTORS = [
    'time',
    '.update-components-actor__sub-description time',
    '.feed-shared-actor__sub-description time',
]

SUB_DESCRIPTION_SELECTORS = [
    '.update-components-actor__sub-description',
    '.feed-shared-actor__sub-description',
]


def soupify(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, 'lxml')


def select_with_fallbacks(root: Tag | BeautifulSoup, selectors: list[str]) -> list[Tag]:
    for selector in selectors:
        elements = root.select(selector)
        if elements:
            return elements
    return []


def _normalize_text(value: str) -> str:
    return ' '.join(value.split())


def _extract_first_text(root: Tag | BeautifulSoup, selectors: list[str]) -> str:
    elements = select_with_fallbacks(root, selectors)
    if not elements:
        return ''
    return _normalize_text(elements[0].get_text(' ', strip=True))


def _absolute_link(href: str, post_id: str) -> str:
    if href:
        return href if href.startswith('http') else f'https://www.linkedin.com{href}'
    activity_id = post_id.removeprefix('urn:li:activity:')
    return f'https://www.linkedin.com/feed/update/urn:li:activity:{activity_id}/'


def _extract_relative_date(root: Tag | BeautifulSoup) -> str:
    for element in select_with_fallbacks(root, SUB_DESCRIPTION_SELECTORS):
        text = _normalize_text(element.get_text(' ', strip=True))
        match = re.match(r'^(\d+[smhdw]|\d+mo)', text)
        if match:
            return match.group(1)
    return ''


def _title_from_text(text: str) -> str:
    first_line = text.splitlines()[0].strip() if text else ''
    if len(first_line) <= 100:
        return first_line
    trimmed = first_line[:100].rsplit(' ', 1)[0].strip()
    return f'{trimmed or first_line[:100]}…'


def _unique_content_image_urls(root: Tag | BeautifulSoup) -> list[str]:
    seen: set[str] = set()
    urls: list[str] = []
    for image in select_with_fallbacks(root, IMAGE_SELECTORS):
        src = image.get('src', '').strip()
        if not src or src.startswith('data:'):
            continue
        if any(pattern.search(src) for pattern in IGNORE_IMAGE_PATTERNS):
            continue
        if src not in seen:
            seen.add(src)
            urls.append(src)
    return urls


def _extract_video_metadata(root: Tag | BeautifulSoup) -> tuple[bool, str, str]:
    has_video = bool(select_with_fallbacks(root, VIDEO_CONTAINER_SELECTORS))
    video_poster_url = ''
    for video in root.select('video'):
        video_poster_url = video.get('poster', '').strip()
        if video_poster_url:
            break

    video_id = ''
    if video_poster_url:
        match = re.search(r'/([A-Za-z0-9_-]{15,})/videocover', video_poster_url)
        if match:
            video_id = match.group(1)
    return has_video, video_id, video_poster_url


def extract_post(post: Tag) -> LinkedInPost | None:
    post_id = (post.get('data-urn') or post.get('id') or '').strip()
    if not post_id:
        return None

    author = _extract_first_text(post, ACTOR_SELECTORS)
    header_text = _extract_first_text(post, REPOST_HEADER_SELECTORS)
    is_repost = bool(re.search(r'repost', header_text, re.IGNORECASE))
    reposted_by = re.sub(r'\s*reposted(\s+this)?\s*$', '', header_text, flags=re.IGNORECASE).strip() if is_repost else ''

    text = _extract_first_text(post, TEXT_SELECTORS)
    permalink_elements = select_with_fallbacks(post, PERMALINK_SELECTORS)
    href = permalink_elements[0].get('href', '').strip() if permalink_elements else ''
    post_url = _absolute_link(href, post_id)

    time_elements = select_with_fallbacks(post, TIME_SELECTORS)
    post_date = time_elements[0].get('datetime', '').strip() if time_elements else ''
    if not post_date:
        post_date = _extract_relative_date(post)

    has_video, video_id, video_poster_url = _extract_video_metadata(post)

    return LinkedInPost(
        post_id=post_id,
        post_url=post_url,
        post_date=post_date,
        title=_title_from_text(text),
        author=author,
        is_repost=is_repost,
        reposted_by=reposted_by,
        text=text,
        image_urls=_unique_content_image_urls(post),
        has_video=has_video,
        video_id=video_id,
        video_poster_url=video_poster_url,
    )


def collect_all_post_elements(root: Tag | BeautifulSoup) -> list[Tag]:
    seen: set[int] = set()
    results: list[Tag] = []
    for selector in POST_SELECTORS:
        for element in root.select(selector):
            marker = id(element)
            if marker in seen:
                continue
            seen.add(marker)
            results.append(element)
    return results


def extract_all_posts(html_or_soup: str | Tag | BeautifulSoup) -> ExtractionReport:
    root = soupify(html_or_soup) if isinstance(html_or_soup, str) else html_or_soup
    report = ExtractionReport()
    for index, post in enumerate(collect_all_post_elements(root)):
        item = extract_post(post)
        if item is None:
            report.errors.append(
                ExtractionError(
                    index=index,
                    selector=' | '.join(POST_SELECTORS),
                    message='Post element missing data-urn or id attribute',
                )
            )
            continue
        report.items.append(item)
    return report


def find_video_cdn_urls(video_id: str, resource_urls: Iterable[str]) -> list[str]:
    if not video_id:
        return []
    pattern = re.compile(rf'playlist/vid.*{re.escape(video_id)}')
    seen: set[str] = set()
    matches: list[str] = []
    for url in resource_urls:
        if pattern.search(url) and url not in seen:
            seen.add(url)
            matches.append(url)
    return matches


def matches_linkedin_activity(url: str) -> bool:
    return 'linkedin.com/in/' in url and '/recent-activity/' in url
