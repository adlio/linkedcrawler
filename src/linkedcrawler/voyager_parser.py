"""Parse LinkedIn voyager feed GraphQL responses into LinkedInPost objects.

The `/voyager/api/graphql?queryId=voyagerFeedDashProfileUpdates.<hash>` endpoint
returns a JSON document with two relevant sections:

    {
      "data": {
        "data": {
          "feedDashProfileUpdatesByMemberShareFeed": {
            "metadata": {"paginationToken": "..."},
            "*elements": ["urn:li:fsd_update:(urn:li:activity:NNN,...)", ...]
          }
        }
      },
      "included": [
        {"$type": "com.linkedin.voyager.dash.feed.Update", ...},
        {"$type": "com.linkedin.voyager.dash.identity.profile.Profile", ...},
        ...
      ]
    }

`included` is a flat pool of all entities referenced anywhere in the response
(voyager's normalization scheme). For each top-level activity URN listed in
`*elements`, a matching Update entity appears in `included`. Reposts also
include the inner (original) Update, but we identify the outer update by
cross-referencing `*elements` so we don't double-count.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any

from .models import LinkedInPost

_ACTIVITY_URN_RE = re.compile(r'urn:li:activity:(\d+)')
_DIGITAL_ASSET_ID_RE = re.compile(r'urn:li:digitalmediaAsset:([A-Za-z0-9_-]+)')
_REPOSTED_BY_RE = re.compile(r'(.+?) reposted this', re.IGNORECASE)

UPDATE_TYPE = 'com.linkedin.voyager.dash.feed.Update'
FEED_KEY = 'feedDashProfileUpdatesByMemberShareFeed'


def parse_voyager_response(body: str | dict[str, Any]) -> tuple[list[LinkedInPost], str | None]:
    """Parse a single voyager feed response.

    Returns (posts, next_pagination_token). `next_pagination_token` is None when
    the response carries no more pages.
    """
    data = json.loads(body) if isinstance(body, str) else body
    feed_block = ((data.get('data') or {}).get('data') or {}).get(FEED_KEY, {}) or {}

    outer_refs: list[str] = feed_block.get('*elements') or []
    outer_urns: list[str] = []
    for ref in outer_refs:
        m = _ACTIVITY_URN_RE.search(ref)
        if m:
            outer_urns.append(m.group(0))
    outer_set = set(outer_urns)

    included = data.get('included') or []
    entities_by_urn: dict[str, dict[str, Any]] = {}
    for entity in included:
        eurn = entity.get('entityUrn')
        if isinstance(eurn, str):
            entities_by_urn[eurn] = entity

    updates_by_urn: dict[str, dict[str, Any]] = {}
    for entity in included:
        if entity.get('$type') != UPDATE_TYPE:
            continue
        urn = _extract_activity_urn(entity.get('entityUrn', ''))
        if not urn:
            continue
        updates_by_urn[urn] = entity

    posts: list[LinkedInPost] = []
    for urn in outer_urns:
        update = updates_by_urn.get(urn)
        if not update:
            continue
        posts.append(_update_to_post(urn, update, entities_by_urn))

    token = ((feed_block.get('metadata') or {}).get('paginationToken')) or None
    return posts, token


def _extract_activity_urn(text: str) -> str | None:
    m = _ACTIVITY_URN_RE.search(text)
    return m.group(0) if m else None


def activity_urn_to_iso_date(activity_urn: str) -> str:
    """Derive the ISO date (YYYY-MM-DD, UTC) from an activity URN.

    LinkedIn activity URNs are snowflake-style: the numeric id's upper bits
    encode ms since the Unix epoch. `id >> 22` gives millisecond-precision
    creation time, accurate to within ~25ms of the authoritative timestamp
    embedded in voyager's paginationTokens.
    """
    m = _ACTIVITY_URN_RE.search(activity_urn)
    if not m:
        return ''
    ms = int(m.group(1)) >> 22
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime('%Y-%m-%d')


def _update_to_post(
    activity_urn: str,
    update: dict[str, Any],
    entities_by_urn: dict[str, dict[str, Any]],
) -> LinkedInPost:
    actor = update.get('actor') or {}
    actor_name = _text_of((actor.get('name') or {}))
    header_text = _text_of(((update.get('header') or {}).get('text') or {}))
    commentary = (update.get('commentary') or {}).get('text') or {}
    body = _text_of(commentary)

    is_repost = bool(header_text) and 'reposted this' in header_text.lower()
    if is_repost:
        m = _REPOSTED_BY_RE.match(header_text)
        reposted_by = m.group(1).strip() if m else 'Simon Wardley'
    else:
        reposted_by = ''

    content = update.get('content') or {}
    image_urls = _image_urls_from_component(content.get('imageComponent'))
    video_info = _video_info_from_component(content.get('linkedInVideoComponent'), entities_by_urn)

    return LinkedInPost(
        post_id=activity_urn,
        post_url=f'https://www.linkedin.com/feed/update/{activity_urn}/',
        post_date=activity_urn_to_iso_date(activity_urn),
        title=_derive_title(body),
        author=actor_name,
        is_repost=is_repost,
        reposted_by=reposted_by,
        text=body,
        image_urls=image_urls,
        has_video=video_info is not None,
        video_id=video_info['id'] if video_info else '',
        video_poster_url=video_info['poster'] if video_info else '',
        video_cdn_urls=video_info['streams'] if video_info else [],
    )


def _image_urls_from_component(image_component: dict[str, Any] | None) -> list[str]:
    if not image_component:
        return []
    urls: list[str] = []
    for image in image_component.get('images') or []:
        for attr in image.get('attributes') or []:
            vector = ((attr.get('detailData') or {}).get('vectorImage'))
            url = _vector_image_to_url(vector)
            if url:
                urls.append(url)
    return urls


def _vector_image_to_url(vector_image: dict[str, Any] | None) -> str:
    """Build a CDN URL by combining rootUrl with the largest artifact's path."""
    if not vector_image:
        return ''
    root = vector_image.get('rootUrl') or ''
    artifacts = vector_image.get('artifacts') or []
    if not root or not artifacts:
        return ''
    largest = max(artifacts, key=lambda a: a.get('width', 0) or 0)
    path = largest.get('fileIdentifyingUrlPathSegment') or ''
    if not path:
        return ''
    return f'{root}{path}'


def _video_info_from_component(
    video_component: dict[str, Any] | None,
    entities_by_urn: dict[str, dict[str, Any]],
) -> dict[str, Any] | None:
    if not video_component:
        return None
    vpm_urn = video_component.get('*videoPlayMetadata')
    if not vpm_urn:
        return None
    vpm = entities_by_urn.get(vpm_urn)
    if not vpm:
        return None

    video_id = ''
    m = _DIGITAL_ASSET_ID_RE.search(vpm_urn)
    if m:
        video_id = m.group(1)

    poster = _vector_image_to_url(vpm.get('thumbnail'))

    streams: list[str] = []
    for stream in vpm.get('progressiveStreams') or []:
        for location in stream.get('streamingLocations') or []:
            url = location.get('url')
            if url:
                streams.append(url)

    return {'id': video_id, 'poster': poster, 'streams': streams}


def _text_of(block: dict[str, Any]) -> str:
    """Extract plain text from a voyager AttributedText-ish dict."""
    if not block:
        return ''
    # `text` is the simplest representation; voyager usually also provides
    # attributesV2 for rich formatting, but we don't need that here.
    value = block.get('text')
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return value.get('text') or ''
    return ''


def _derive_title(body: str) -> str:
    """Mirror the HTML extractor's "title" convention: first 100 chars, trimmed."""
    if not body:
        return ''
    compact = body.replace('\n', ' ').strip()
    if len(compact) <= 100:
        return compact
    return compact[:99].rstrip() + '\u2026'
