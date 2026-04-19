from __future__ import annotations

from pathlib import Path

from linkedcrawler.extractors import extract_all_posts, extract_post, find_video_cdn_urls, matches_linkedin_activity, soupify

FIXTURE_PATH = Path(__file__).parent / 'fixtures' / 'linkedin_feed.html'
FIXTURE_HTML = FIXTURE_PATH.read_text()
SOUP = soupify(FIXTURE_HTML)
REAL_FIXTURE_PATH = Path(__file__).parent / 'fixtures' / 'linkedin_real_activity_page.html'
REAL_FIXTURE_HTML = REAL_FIXTURE_PATH.read_text()


def test_extract_post_from_data_urn() -> None:
    post = SOUP.select_one('[data-urn="urn:li:activity:7100000000000000001"]')
    result = extract_post(post)
    assert result is not None
    assert result.post_id == 'urn:li:activity:7100000000000000001'
    assert result.author == 'Simon Wardley'
    assert result.post_date == '2025-12-15T10:30:00.000Z'


def test_extract_repost_metadata() -> None:
    post = SOUP.select_one('[data-urn="urn:li:activity:7100000000000000002"]')
    result = extract_post(post)
    assert result is not None
    assert result.is_repost is True
    assert result.reposted_by == 'Simon Wardley'
    assert result.author == 'Another Author'


def test_filters_profile_and_logo_images() -> None:
    post = SOUP.select_one('[data-urn="urn:li:activity:7100000000000000001"]')
    result = extract_post(post)
    assert result is not None
    assert result.image_urls == [
        'https://media.licdn.com/dms/image/v2/D4E10AQGP8abc123/content-photo-shrink_800/content-photo-shrink_800/0/1702900000000?e=2147483647&v=beta&t=abc123'
    ]


def test_extracts_video_metadata_for_legacy_and_new_dom() -> None:
    legacy = extract_post(SOUP.select_one('[data-urn="urn:li:activity:7100000000000000005"]'))
    modern = extract_post(SOUP.select_one('[data-urn="urn:li:activity:7100000000000000006"]'))
    assert legacy is not None and modern is not None
    assert legacy.has_video is True
    assert legacy.video_id == 'D4E05AQFakeVideoId123'
    assert modern.has_video is True
    assert modern.video_id == 'D4E05AQEsOzrNYKp1RQ'
    assert modern.post_date == '5d'


def test_extract_all_posts_reports_malformed_entry() -> None:
    report = extract_all_posts(FIXTURE_HTML)
    assert [item.post_id for item in report.items] == [
        'urn:li:activity:7100000000000000001',
        'urn:li:activity:7100000000000000002',
        'urn:li:activity:7100000000000000003',
        'urn:li:activity:7100000000000000005',
        'urn:li:activity:7100000000000000006',
    ]
    assert len(report.errors) == 1
    assert 'missing data-urn or id' in report.errors[0].message


def test_find_video_cdn_urls_deduplicates_matches() -> None:
    urls = find_video_cdn_urls(
        'D4E05AQEsOzrNYKp1RQ',
        [
            'https://dms.licdn.com/playlist/vid/v2/D4E05AQEsOzrNYKp1RQ/mp4_720p/video.mp4',
            'https://dms.licdn.com/playlist/vid/v2/D4E05AQEsOzrNYKp1RQ/mp4_720p/video.mp4',
            'https://example.com/no-match',
        ],
    )
    assert urls == ['https://dms.licdn.com/playlist/vid/v2/D4E05AQEsOzrNYKp1RQ/mp4_720p/video.mp4']


def test_extract_all_posts_ignores_ember_placeholders_in_real_fixture() -> None:
    report = extract_all_posts(REAL_FIXTURE_HTML)

    assert [item.post_id for item in report.items] == [
        'urn:li:activity:7451359540291338241',
        'urn:li:activity:7451344738600943616',
        'urn:li:activity:7450896438235942912',
        'urn:li:activity:7450896189106958336',
        'urn:li:activity:7450534929064628225',
    ]
    assert not report.errors


def test_extract_all_posts_only_emits_real_activity_urns_from_real_fixture() -> None:
    report = extract_all_posts(REAL_FIXTURE_HTML)

    assert report.items
    assert all(item.post_id.startswith('urn:li:activity:') for item in report.items)
    assert all(item.post_id.removeprefix('urn:li:activity:').isdigit() for item in report.items)
    assert all(item.text.strip() for item in report.items)


def test_extract_all_posts_keeps_reposts_detectable_in_real_fixture() -> None:
    report = extract_all_posts(REAL_FIXTURE_HTML)

    reposts = {item.post_id: item for item in report.items if item.is_repost}
    assert set(reposts) == {
        'urn:li:activity:7451344738600943616',
        'urn:li:activity:7450896438235942912',
        'urn:li:activity:7450896189106958336',
        'urn:li:activity:7450534929064628225',
    }
    assert reposts['urn:li:activity:7451344738600943616'].reposted_by == 'Simon Wardley'


def test_matches_linkedin_activity_url() -> None:
    assert matches_linkedin_activity('https://www.linkedin.com/in/simonwardley/recent-activity/all/')
    assert not matches_linkedin_activity('https://www.linkedin.com/feed/')
