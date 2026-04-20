"""Unit tests for the voyager JSON feed parser."""
from __future__ import annotations

from linkedcrawler.voyager_parser import activity_urn_to_iso_date, parse_voyager_response


def _make_update(activity_id: int, *, author: str, text: str, header_text: str | None = None, flavor: str = 'DEFAULT') -> dict:
    return {
        '$type': 'com.linkedin.voyager.dash.feed.Update',
        'entityUrn': f'urn:li:fsd_update:(urn:li:activity:{activity_id},MEMBER_SHARES,DEBUG_REASON,{flavor},false)',
        'actor': {
            'name': {'text': author},
        },
        'header': {'text': {'text': header_text}} if header_text is not None else None,
        'commentary': {'text': {'text': text}},
    }


def _wrap_feed(updates: list[dict], *, token: str | None = 'next-token-abc') -> dict:
    urn_refs = [f'urn:li:fsd_update:(urn:li:activity:{_id(u)},MEMBER_SHARES,DEBUG_REASON,DEFAULT,false)' for u in updates]
    return {
        'data': {
            'data': {
                'feedDashProfileUpdatesByMemberShareFeed': {
                    'metadata': {'paginationToken': token},
                    '*elements': urn_refs,
                },
            },
        },
        'included': updates,
    }


def _id(update: dict) -> int:
    import re
    m = re.search(r'urn:li:activity:(\d+)', update['entityUrn'])
    assert m
    return int(m.group(1))


def test_parses_authored_post() -> None:
    update = _make_update(7451000000000000001, author='Simon Wardley', text='Simon here. Mapping is a tool, not a religion.')
    posts, token = parse_voyager_response(_wrap_feed([update]))
    assert token == 'next-token-abc'
    assert len(posts) == 1
    post = posts[0]
    assert post.post_id == 'urn:li:activity:7451000000000000001'
    assert post.post_url == 'https://www.linkedin.com/feed/update/urn:li:activity:7451000000000000001/'
    assert post.author == 'Simon Wardley'
    assert post.is_repost is False
    assert post.reposted_by == ''
    assert post.text.startswith('Simon here.')


def test_parses_repost_with_separate_author_and_reposter() -> None:
    update = _make_update(
        7451000000000000002,
        author='Mark C.',
        text='What it felt like when my plugin hit GitHub trending.',
        header_text='Simon Wardley reposted this',
    )
    posts, _ = parse_voyager_response(_wrap_feed([update]))
    post = posts[0]
    assert post.is_repost is True
    assert post.author == 'Mark C.'
    assert post.reposted_by == 'Simon Wardley'


def test_derives_title_trims_to_100_chars_with_ellipsis() -> None:
    long_text = 'X ' * 200
    update = _make_update(7451000000000000003, author='Simon Wardley', text=long_text)
    posts, _ = parse_voyager_response(_wrap_feed([update]))
    title = posts[0].title
    assert len(title) <= 100
    assert title.endswith('\u2026')


def test_title_normalises_newlines_to_spaces() -> None:
    update = _make_update(7451000000000000004, author='Simon Wardley', text='First line\n\nSecond line')
    posts, _ = parse_voyager_response(_wrap_feed([update]))
    assert posts[0].title == 'First line  Second line'


def test_outer_elements_determine_post_order_and_count() -> None:
    # Simulate a response with extra included entities that ARE NOT in *elements
    # (e.g. inner reshared updates). Those should be filtered out.
    outer_a = _make_update(7451000000000000010, author='Simon Wardley', text='Outer A')
    outer_b = _make_update(7451000000000000011, author='Simon Wardley', text='Outer B')
    inner_reshared = _make_update(
        7350000000000000000, author='Someone Else', text='Original content', flavor='RESHARED'
    )
    feed = _wrap_feed([outer_a, outer_b])  # only outer_a / outer_b referenced
    feed['included'].append(inner_reshared)
    posts, _ = parse_voyager_response(feed)
    assert [p.post_id for p in posts] == [
        'urn:li:activity:7451000000000000010',
        'urn:li:activity:7451000000000000011',
    ]


def test_missing_pagination_token_returns_none() -> None:
    update = _make_update(7451000000000000020, author='Simon Wardley', text='Last page')
    posts, token = parse_voyager_response(_wrap_feed([update], token=None))
    assert len(posts) == 1
    assert token is None


def test_accepts_string_body() -> None:
    import json as _json
    update = _make_update(7451000000000000030, author='Simon Wardley', text='String input test')
    raw = _json.dumps(_wrap_feed([update]))
    posts, _ = parse_voyager_response(raw)
    assert posts[0].text == 'String input test'


def _update_with_images(activity_id: int, vector_images: list[dict]) -> dict:
    base = _make_update(activity_id, author='Simon Wardley', text='image post')
    base['content'] = {
        'imageComponent': {
            'images': [
                {'attributes': [{'detailData': {'vectorImage': vi}}]}
                for vi in vector_images
            ],
        },
    }
    return base


def test_extracts_image_urls_picking_largest_artifact() -> None:
    vector = {
        'rootUrl': 'https://media.licdn.com/dms/image/v2/D4E22AQEdWiSwtOYoMQ/feedshare-shrink_',
        'artifacts': [
            {'width': 160, 'fileIdentifyingUrlPathSegment': '160/small.jpg?token=s'},
            {'width': 1280, 'fileIdentifyingUrlPathSegment': '1280/large.jpg?token=L'},
            {'width': 800, 'fileIdentifyingUrlPathSegment': '800/medium.jpg?token=M'},
        ],
    }
    update = _update_with_images(7451000000000000040, [vector])
    posts, _ = parse_voyager_response(_wrap_feed([update]))
    assert posts[0].image_urls == [
        'https://media.licdn.com/dms/image/v2/D4E22AQEdWiSwtOYoMQ/feedshare-shrink_1280/large.jpg?token=L',
    ]


def test_extracts_multiple_image_urls_in_order() -> None:
    def vec(asset_id: str, width: int, path: str) -> dict:
        return {
            'rootUrl': f'https://media.licdn.com/dms/image/v2/{asset_id}/feedshare-shrink_',
            'artifacts': [{'width': width, 'fileIdentifyingUrlPathSegment': f'{width}/{path}?t=x'}],
        }
    update = _update_with_images(7451000000000000041, [vec('A', 800, 'a.jpg'), vec('B', 800, 'b.jpg')])
    posts, _ = parse_voyager_response(_wrap_feed([update]))
    assert len(posts[0].image_urls) == 2
    assert '/A/' in posts[0].image_urls[0]
    assert '/B/' in posts[0].image_urls[1]


def test_skips_image_when_root_or_artifact_missing() -> None:
    no_root = {'rootUrl': None, 'artifacts': [{'width': 800, 'fileIdentifyingUrlPathSegment': 'x.jpg'}]}
    no_artifacts = {'rootUrl': 'https://example/', 'artifacts': []}
    update = _update_with_images(7451000000000000042, [no_root, no_artifacts])
    posts, _ = parse_voyager_response(_wrap_feed([update]))
    assert posts[0].image_urls == []


def test_extracts_video_metadata_via_reference() -> None:
    vpm_urn = 'urn:li:digitalmediaAsset:D4E05AQEsOzrNYKp1RQ'
    vpm_entity = {
        '$type': 'com.linkedin.videocontent.VideoPlayMetadata',
        'entityUrn': vpm_urn,
        'thumbnail': {
            'rootUrl': 'https://media.licdn.com/dms/image/v2/D4E05AQEsOzrNYKp1RQ/videocover-',
            'artifacts': [
                {'width': 360, 'fileIdentifyingUrlPathSegment': 'low/thumb-low.jpg?t=l'},
                {'width': 720, 'fileIdentifyingUrlPathSegment': 'high/thumb-high.jpg?t=h'},
            ],
        },
        'progressiveStreams': [
            {'streamingLocations': [{'url': 'https://dms.licdn.com/video/720p.mp4?t=x'}]},
            {'streamingLocations': [{'url': 'https://dms.licdn.com/video/640p.mp4?t=y'}]},
        ],
    }
    update = _make_update(7451000000000000050, author='Simon Wardley', text='video post')
    update['content'] = {'linkedInVideoComponent': {'*videoPlayMetadata': vpm_urn}}
    feed = _wrap_feed([update])
    feed['included'].append(vpm_entity)
    posts, _ = parse_voyager_response(feed)
    assert posts[0].has_video is True
    assert posts[0].video_id == 'D4E05AQEsOzrNYKp1RQ'
    assert posts[0].video_poster_url == (
        'https://media.licdn.com/dms/image/v2/D4E05AQEsOzrNYKp1RQ/videocover-high/thumb-high.jpg?t=h'
    )
    assert posts[0].video_cdn_urls == [
        'https://dms.licdn.com/video/720p.mp4?t=x',
        'https://dms.licdn.com/video/640p.mp4?t=y',
    ]


def test_activity_urn_to_iso_date_decodes_snowflake_timestamp() -> None:
    # Cross-checked against the authoritative timestamps embedded in voyager
    # paginationTokens: these URNs decode to dates visibly from Simon Wardley's
    # activity pages.
    assert activity_urn_to_iso_date('urn:li:activity:7451677807086088192') == '2026-04-19'
    assert activity_urn_to_iso_date('urn:li:activity:6325707860131004416') == '2017-10-16'
    assert activity_urn_to_iso_date('urn:li:activity:5806742157415313408') == '2013-11-14'


def test_activity_urn_to_iso_date_returns_empty_for_non_urn() -> None:
    assert activity_urn_to_iso_date('') == ''
    assert activity_urn_to_iso_date('not a urn') == ''


def test_post_date_populated_from_activity_urn() -> None:
    update = _make_update(7451677807086088192, author='Simon Wardley', text='post')
    posts, _ = parse_voyager_response(_wrap_feed([update]))
    assert posts[0].post_date == '2026-04-19'


def test_video_component_without_metadata_entity_is_ignored() -> None:
    update = _make_update(7451000000000000051, author='Simon Wardley', text='broken video')
    update['content'] = {'linkedInVideoComponent': {'*videoPlayMetadata': 'urn:li:digitalmediaAsset:MISSING'}}
    posts, _ = parse_voyager_response(_wrap_feed([update]))
    assert posts[0].has_video is False
    assert posts[0].video_id == ''
    assert posts[0].video_cdn_urls == []
