from __future__ import annotations

from linkedcrawler.models import SyncState
from linkedcrawler.state import (
    has_seen_activity,
    init_db,
    load_sync_state,
    record_export,
    record_seen_activity,
    update_sync_state,
)


def test_load_sync_state_returns_defaults_for_new_target(tmp_path) -> None:
    db_path = tmp_path / 'state.sqlite3'

    init_db(db_path)
    state = load_sync_state(db_path, 'https://www.linkedin.com/in/simonwardley/recent-activity/all/')

    assert state == SyncState(
        target_profile_url='https://www.linkedin.com/in/simonwardley/recent-activity/all/',
        newest_seen_activity_urn=None,
        oldest_seen_activity_urn=None,
        last_successful_run_at=None,
        backfill_complete=False,
        last_exported_activity_urn=None,
        extraction_version='1',
    )


def test_record_seen_activity_is_idempotent_and_persists_sync_bounds(tmp_path) -> None:
    db_path = tmp_path / 'state.sqlite3'
    target_url = 'https://www.linkedin.com/in/simonwardley/recent-activity/all/'

    init_db(db_path)
    record_seen_activity(
        db_path,
        target_url,
        'urn:li:activity:200',
        fetched_at='2026-04-18T22:30:00Z',
        activity_type='authored',
        source_url='https://example.com/200',
        body_hash='hash-200',
    )
    record_seen_activity(
        db_path,
        target_url,
        'urn:li:activity:100',
        fetched_at='2026-04-18T22:35:00Z',
        activity_type='repost',
        source_url='https://example.com/100',
        body_hash='hash-100',
    )
    record_seen_activity(
        db_path,
        target_url,
        'urn:li:activity:200',
        fetched_at='2026-04-18T22:40:00Z',
        activity_type='authored',
        source_url='https://example.com/200',
        body_hash='hash-200',
    )

    assert has_seen_activity(db_path, target_url, 'urn:li:activity:200') is True
    assert has_seen_activity(db_path, target_url, 'urn:li:activity:999') is False

    state = load_sync_state(db_path, target_url)
    assert state.newest_seen_activity_urn == 'urn:li:activity:200'
    assert state.oldest_seen_activity_urn == 'urn:li:activity:100'


def test_update_sync_state_round_trips_values_and_record_export_is_idempotent(tmp_path) -> None:
    db_path = tmp_path / 'state.sqlite3'
    target_url = 'https://www.linkedin.com/in/simonwardley/recent-activity/all/'

    init_db(db_path)
    update_sync_state(
        db_path,
        target_url=target_url,
        newest_seen_activity_urn='urn:li:activity:300',
        oldest_seen_activity_urn='urn:li:activity:100',
        last_successful_run_at='2026-04-18T23:00:00Z',
        backfill_complete=True,
        last_exported_activity_urn='urn:li:activity:100',
        extraction_version='2',
    )
    record_export(
        db_path,
        activity_urn='urn:li:activity:300',
        note_path='exports/2026-04-18-activity-300-abc.md',
        body_hash='abc',
    )
    record_export(
        db_path,
        activity_urn='urn:li:activity:300',
        note_path='exports/2026-04-18-activity-300-abc.md',
        body_hash='abc',
    )

    state = load_sync_state(db_path, target_url)
    assert state == SyncState(
        target_profile_url=target_url,
        newest_seen_activity_urn='urn:li:activity:300',
        oldest_seen_activity_urn='urn:li:activity:100',
        last_successful_run_at='2026-04-18T23:00:00Z',
        backfill_complete=True,
        last_exported_activity_urn='urn:li:activity:100',
        extraction_version='2',
    )

    # record_export should not fail or create duplicate rows on repeated inserts.
    record_export(
        db_path,
        activity_urn='urn:li:activity:300',
        note_path='exports/2026-04-18-activity-300-abc.md',
        body_hash='abc',
    )
