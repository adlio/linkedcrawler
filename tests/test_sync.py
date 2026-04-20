from __future__ import annotations

from pathlib import Path

from linkedcrawler.models import LinkedInPost
from linkedcrawler.sync import SyncResult, sync_profile_to_directory


TARGET_URL = 'https://www.linkedin.com/in/simonwardley/recent-activity/all/'
PROFILE_NAME = 'Simon Wardley'
TAGS = ['ai-thinkers', 'simon-wardley']


def _sync(**kwargs):
    """Shortcut: inject profile_name/tags so each test doesn't have to repeat them."""
    return sync_profile_to_directory(
        profile_name=kwargs.pop('profile_name', PROFILE_NAME),
        tags=kwargs.pop('tags', TAGS),
        **kwargs,
    )


def make_post(
    activity_id: int,
    *,
    author: str = 'Simon Wardley',
    is_repost: bool = False,
    reposted_by: str = '',
    text: str | None = None,
) -> LinkedInPost:
    return LinkedInPost(
        post_id=f'urn:li:activity:{activity_id}',
        post_url=f'https://www.linkedin.com/posts/example-{activity_id}',
        post_date='2026-04-18T10:15:00.000Z',
        title=f'post-{activity_id}',
        author=author,
        is_repost=is_repost,
        reposted_by=reposted_by,
        text=text or f'body-{activity_id}',
        image_urls=[],
        has_video=False,
        video_id='',
        video_poster_url='',
        video_cdn_urls=[],
    )


def test_daily_sync_exports_only_new_items_and_stops_after_seen_streak(tmp_path: Path) -> None:
    output_dir = tmp_path / 'out'
    db_path = tmp_path / 'state.sqlite3'
    extracted = [
        make_post(500),
        make_post(499, is_repost=True, reposted_by='Simon Wardley'),
        make_post(498),
        make_post(497),
        make_post(496),
    ]

    _sync(
        target_url=TARGET_URL,
        directory=output_dir,
        db_path=db_path,
        mode='backfill',
        fetched_at='2026-04-17',
        extract_posts=lambda _: [extracted[2], extracted[3]],
    )

    result = _sync(
        target_url=TARGET_URL,
        directory=output_dir,
        db_path=db_path,
        mode='daily',
        fetched_at='2026-04-18',
        extract_posts=lambda _: extracted,
        seen_streak_limit=2,
    )

    assert result == SyncResult(
        exported_activity_urns=['urn:li:activity:500', 'urn:li:activity:499'],
        skipped_seen_activity_urns=['urn:li:activity:498', 'urn:li:activity:497'],
        filtered_out_activity_urns=[],
        stopped_on_seen_streak=True,
    )
    written = sorted(path.name for path in output_dir.glob('*.md'))
    assert written == [
        '2026-04-18-activity-497-d4a6a031ba.md',
        '2026-04-18-activity-498-d76c96dac4.md',
        '2026-04-18-activity-499-eb3e796a2a.md',
        '2026-04-18-activity-500-8ac9399b23.md',
    ]
    assert '2026-04-18-activity-496-934a83aa74.md' not in written


def test_daily_sync_rerun_is_noop_when_nothing_changed(tmp_path: Path) -> None:
    output_dir = tmp_path / 'out'
    db_path = tmp_path / 'state.sqlite3'
    extracted = [make_post(500), make_post(499, is_repost=True, reposted_by='Simon Wardley')]

    first = _sync(
        target_url=TARGET_URL,
        directory=output_dir,
        db_path=db_path,
        mode='daily',
        fetched_at='2026-04-18',
        extract_posts=lambda _: extracted,
        seen_streak_limit=2,
    )
    second = _sync(
        target_url=TARGET_URL,
        directory=output_dir,
        db_path=db_path,
        mode='daily',
        fetched_at='2026-04-19',
        extract_posts=lambda _: extracted,
        seen_streak_limit=2,
    )

    assert first.exported_activity_urns == ['urn:li:activity:500', 'urn:li:activity:499']
    assert second == SyncResult(
        exported_activity_urns=[],
        skipped_seen_activity_urns=['urn:li:activity:500', 'urn:li:activity:499'],
        filtered_out_activity_urns=[],
        stopped_on_seen_streak=True,
    )
    assert sorted(path.name for path in output_dir.glob('*.md')) == [
        '2026-04-18-activity-499-eb3e796a2a.md',
        '2026-04-18-activity-500-8ac9399b23.md',
    ]


def test_backfill_sync_resumes_without_duplicates_and_honors_filters(tmp_path: Path) -> None:
    output_dir = tmp_path / 'out'
    db_path = tmp_path / 'state.sqlite3'
    extracted = [
        make_post(600),
        make_post(599, is_repost=True, reposted_by='Simon Wardley'),
        make_post(598, author='Another Author'),
        make_post(597, is_repost=True, reposted_by='Another Person'),
    ]

    first = _sync(
        target_url=TARGET_URL,
        directory=output_dir,
        db_path=db_path,
        mode='backfill',
        fetched_at='2026-04-18',
        extract_posts=lambda _: extracted[:1],
    )
    second = _sync(
        target_url=TARGET_URL,
        directory=output_dir,
        db_path=db_path,
        mode='backfill',
        fetched_at='2026-04-19',
        extract_posts=lambda _: extracted,
        author_only=True,
        include_reposts=True,
    )

    assert first.exported_activity_urns == ['urn:li:activity:600']
    assert second == SyncResult(
        exported_activity_urns=['urn:li:activity:599'],
        skipped_seen_activity_urns=['urn:li:activity:600'],
        filtered_out_activity_urns=['urn:li:activity:598', 'urn:li:activity:597'],
        stopped_on_seen_streak=False,
    )
    assert sorted(path.name for path in output_dir.glob('*.md')) == [
        '2026-04-18-activity-599-990b0da602.md',
        '2026-04-18-activity-600-53e1e6f829.md',
    ]

    third = _sync(
        target_url=TARGET_URL,
        directory=output_dir,
        db_path=db_path,
        mode='backfill',
        fetched_at='2026-04-20',
        extract_posts=lambda _: extracted,
        author_only=True,
        include_reposts=True,
    )
    assert third == SyncResult(
        exported_activity_urns=[],
        skipped_seen_activity_urns=['urn:li:activity:600', 'urn:li:activity:599'],
        filtered_out_activity_urns=['urn:li:activity:598', 'urn:li:activity:597'],
        stopped_on_seen_streak=False,
    )
