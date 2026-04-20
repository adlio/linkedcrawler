from __future__ import annotations

import json
import sys
from pathlib import Path

from linkedcrawler import cli
from linkedcrawler.models import CrawlRequest, CrawlResult
from linkedcrawler.sync import SyncResult


class ArgvContext:
    def __init__(self, *args: str):
        self.args = ['linkedcrawler', *args]
        self.original = list(sys.argv)

    def __enter__(self):
        sys.argv[:] = self.args

    def __exit__(self, exc_type, exc, tb):
        sys.argv[:] = self.original


def test_main_uses_sync_mode_when_output_directory_is_provided(tmp_path: Path, monkeypatch, capsys) -> None:
    called: dict[str, object] = {}

    def fake_sync_profile_to_directory(**kwargs):
        called.update(kwargs)
        return SyncResult(
            exported_activity_urns=['urn:li:activity:1'],
            skipped_seen_activity_urns=[],
            filtered_out_activity_urns=[],
            stopped_on_seen_streak=False,
        )

    monkeypatch.setattr(cli, 'sync_profile_to_directory', fake_sync_profile_to_directory)

    with ArgvContext(
        'https://www.linkedin.com/in/simonwardley/recent-activity/all/',
        '--output-dir',
        str(tmp_path / 'out'),
        '--db-path',
        str(tmp_path / 'state.sqlite3'),
        '--mode',
        'backfill',
        '--fetched-at',
        '2026-04-19',
        '--no-include-reposts',
        '--all-activities',
    ):
        assert cli.main() == 0

    assert called == {
        'target_url': 'https://www.linkedin.com/in/simonwardley/recent-activity/all/',
        'directory': tmp_path / 'out',
        'db_path': tmp_path / 'state.sqlite3',
        'mode': 'backfill',
        'include_reposts': False,
        'author_only': False,
        'fetched_at': '2026-04-19',
        'extract_posts': called['extract_posts'],
    }
    payload = json.loads(capsys.readouterr().out)
    assert payload['exported_activity_urns'] == ['urn:li:activity:1']


def test_main_preserves_legacy_crawl_json_output_when_sync_flags_absent(monkeypatch, capsys) -> None:
    def fake_run_linkedin_crawl(request: CrawlRequest) -> CrawlResult:
        assert request == CrawlRequest(
            url='https://www.linkedin.com/in/simonwardley/recent-activity/all/',
            last_saved_item_key='urn:li:activity:1',
            max_scroll_rounds=5,
            wait_attempts=7,
            wait_delay_seconds=0.5,
        )
        return CrawlResult(request=request, posts=[], rounds_scrolled=2, newest_item_key='urn:li:activity:10')

    monkeypatch.setattr(cli, 'run_linkedin_crawl', fake_run_linkedin_crawl)

    with ArgvContext(
        'https://www.linkedin.com/in/simonwardley/recent-activity/all/',
        '--via',
        'html',
        '--last-saved-item-key',
        'urn:li:activity:1',
        '--max-scroll-rounds',
        '5',
        '--wait-attempts',
        '7',
        '--wait-delay-seconds',
        '0.5',
    ):
        assert cli.main() == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload['rounds_scrolled'] == 2
    assert payload['newest_item_key'] == 'urn:li:activity:10'
