from __future__ import annotations

import sqlite3
import time
from pathlib import Path

from .models import SyncState


def _connect(db_path: Path | str) -> sqlite3.Connection:
    path = str(db_path)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(db_path: Path | str) -> None:
    with _connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS sync_targets (
                target_profile_url TEXT PRIMARY KEY,
                newest_seen_activity_urn TEXT,
                oldest_seen_activity_urn TEXT,
                last_successful_run_at TEXT,
                backfill_complete INTEGER NOT NULL DEFAULT 0,
                last_exported_activity_urn TEXT,
                extraction_version TEXT NOT NULL DEFAULT '1'
            );

            CREATE TABLE IF NOT EXISTS seen_activities (
                target_profile_url TEXT NOT NULL,
                activity_urn TEXT NOT NULL,
                fetched_at TEXT NOT NULL,
                activity_type TEXT NOT NULL,
                source_url TEXT NOT NULL,
                body_hash TEXT NOT NULL,
                PRIMARY KEY (target_profile_url, activity_urn)
            );

            CREATE TABLE IF NOT EXISTS exported_notes (
                activity_urn TEXT NOT NULL,
                note_path TEXT NOT NULL,
                body_hash TEXT NOT NULL,
                PRIMARY KEY (activity_urn, note_path, body_hash)
            );

            CREATE TABLE IF NOT EXISTS crawl_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                target_profile_url TEXT NOT NULL,
                mode TEXT NOT NULL,
                run_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS crawl_checkpoints (
                target_profile_url TEXT PRIMARY KEY,
                last_start INTEGER NOT NULL,
                last_token TEXT NOT NULL,
                updated_at REAL NOT NULL
            );
            """
        )


def load_sync_state(db_path: Path | str, target_url: str) -> SyncState:
    init_db(db_path)
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM sync_targets WHERE target_profile_url = ?",
            (target_url,),
        ).fetchone()
    if row is None:
        return SyncState(target_profile_url=target_url)
    return SyncState(
        target_profile_url=row['target_profile_url'],
        newest_seen_activity_urn=row['newest_seen_activity_urn'],
        oldest_seen_activity_urn=row['oldest_seen_activity_urn'],
        last_successful_run_at=row['last_successful_run_at'],
        backfill_complete=bool(row['backfill_complete']),
        last_exported_activity_urn=row['last_exported_activity_urn'],
        extraction_version=row['extraction_version'],
    )


def update_sync_state(
    db_path: Path | str,
    *,
    target_url: str,
    newest_seen_activity_urn: str | None,
    oldest_seen_activity_urn: str | None,
    last_successful_run_at: str | None,
    backfill_complete: bool,
    last_exported_activity_urn: str | None,
    extraction_version: str,
) -> None:
    init_db(db_path)
    with _connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO sync_targets (
                target_profile_url, newest_seen_activity_urn, oldest_seen_activity_urn,
                last_successful_run_at, backfill_complete, last_exported_activity_urn, extraction_version
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(target_profile_url) DO UPDATE SET
                newest_seen_activity_urn=excluded.newest_seen_activity_urn,
                oldest_seen_activity_urn=excluded.oldest_seen_activity_urn,
                last_successful_run_at=excluded.last_successful_run_at,
                backfill_complete=excluded.backfill_complete,
                last_exported_activity_urn=excluded.last_exported_activity_urn,
                extraction_version=excluded.extraction_version
            """,
            (
                target_url,
                newest_seen_activity_urn,
                oldest_seen_activity_urn,
                last_successful_run_at,
                int(backfill_complete),
                last_exported_activity_urn,
                extraction_version,
            ),
        )


def has_seen_activity(db_path: Path | str, target_url: str, activity_urn: str) -> bool:
    init_db(db_path)
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT 1 FROM seen_activities WHERE target_profile_url = ? AND activity_urn = ?",
            (target_url, activity_urn),
        ).fetchone()
    return row is not None


def record_seen_activity(
    db_path: Path | str,
    target_url: str,
    activity_urn: str,
    *,
    fetched_at: str,
    activity_type: str,
    source_url: str,
    body_hash: str,
) -> None:
    init_db(db_path)
    with _connect(db_path) as conn:
        conn.execute(
            """
            INSERT OR IGNORE INTO seen_activities (
                target_profile_url, activity_urn, fetched_at, activity_type, source_url, body_hash
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (target_url, activity_urn, fetched_at, activity_type, source_url, body_hash),
        )

    state = load_sync_state(db_path, target_url)
    urns = [u for u in [state.newest_seen_activity_urn, state.oldest_seen_activity_urn, activity_urn] if u]
    newest = max(urns, key=_activity_sort_key)
    oldest = min(urns, key=_activity_sort_key)
    update_sync_state(
        db_path,
        target_url=target_url,
        newest_seen_activity_urn=newest,
        oldest_seen_activity_urn=oldest,
        last_successful_run_at=state.last_successful_run_at,
        backfill_complete=state.backfill_complete,
        last_exported_activity_urn=state.last_exported_activity_urn,
        extraction_version=state.extraction_version,
    )


def record_export(db_path: Path | str, *, activity_urn: str, note_path: str, body_hash: str) -> None:
    init_db(db_path)
    with _connect(db_path) as conn:
        conn.execute(
            "INSERT OR IGNORE INTO exported_notes (activity_urn, note_path, body_hash) VALUES (?, ?, ?)",
            (activity_urn, note_path, body_hash),
        )


def _activity_sort_key(activity_urn: str) -> int:
    return int(activity_urn.removeprefix('urn:li:activity:'))


def save_crawl_checkpoint(
    db_path: Path | str,
    target_url: str,
    *,
    start: int,
    token: str,
    now: float | None = None,
) -> None:
    """Persist pagination position so an interrupted crawl can resume."""
    init_db(db_path)
    updated_at = time.time() if now is None else now
    with _connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO crawl_checkpoints (target_profile_url, last_start, last_token, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(target_profile_url) DO UPDATE SET
                last_start=excluded.last_start,
                last_token=excluded.last_token,
                updated_at=excluded.updated_at
            """,
            (target_url, start, token, updated_at),
        )


def load_crawl_checkpoint(
    db_path: Path | str,
    target_url: str,
    *,
    max_age_seconds: float = 3600.0,
    now: float | None = None,
) -> tuple[int, str] | None:
    """Return (start, token) for a checkpoint newer than `max_age_seconds`.

    Checkpoints older than the threshold are treated as stale — LinkedIn's
    paginationTokens can expire, and mixing a stale cursor with a fresh
    bundle's queryId is a fast path to "CSRF check failed" / empty responses.
    """
    init_db(db_path)
    current = time.time() if now is None else now
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT last_start, last_token, updated_at FROM crawl_checkpoints WHERE target_profile_url = ?",
            (target_url,),
        ).fetchone()
    if row is None:
        return None
    if current - float(row['updated_at']) > max_age_seconds:
        return None
    return int(row['last_start']), str(row['last_token'])


def clear_crawl_checkpoint(db_path: Path | str, target_url: str) -> None:
    init_db(db_path)
    with _connect(db_path) as conn:
        conn.execute(
            "DELETE FROM crawl_checkpoints WHERE target_profile_url = ?",
            (target_url,),
        )
