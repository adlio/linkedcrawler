from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Callable

from .export import body_hash, write_posts_to_directory
from .models import CrawlRequest, LinkedInPost, SyncResult
from .orchestration import run_linkedin_crawl
from .state import has_seen_activity, init_db, load_sync_state, record_export, record_seen_activity, update_sync_state

EXTRACTION_VERSION = '1'


def _should_include_post(
    post: LinkedInPost,
    *,
    profile_name: str,
    author_only: bool,
    include_reposts: bool,
) -> bool:
    if author_only:
        if post.is_repost:
            return include_reposts and post.reposted_by == profile_name
        return post.author == profile_name
    if not include_reposts and post.is_repost:
        return False
    return True


def sync_profile_to_directory(
    *,
    target_url: str,
    directory: Path,
    db_path: Path,
    mode: str,
    profile_name: str,
    tags: list[str],
    fetched_at: str = '1970-01-01',
    extract_posts: Callable[[str], list[LinkedInPost]] | None = None,
    seen_streak_limit: int = 2,
    include_reposts: bool = True,
    author_only: bool = True,
) -> SyncResult:
    init_db(db_path)
    result = SyncResult()
    exported_posts: list[LinkedInPost] = []
    seen_streak = 0
    extractor = extract_posts or (
        lambda url: run_linkedin_crawl(CrawlRequest(url=url)).posts
    )

    for post in extractor(target_url):
        if not _should_include_post(
            post,
            profile_name=profile_name,
            author_only=author_only,
            include_reposts=include_reposts,
        ):
            result.filtered_out_activity_urns.append(post.post_id)
            continue

        already_seen = has_seen_activity(db_path, target_url, post.post_id)
        if already_seen:
            result.skipped_seen_activity_urns.append(post.post_id)
            if mode == 'daily':
                seen_streak += 1
                if seen_streak >= seen_streak_limit:
                    result.stopped_on_seen_streak = True
                    break
            continue

        seen_streak = 0
        exported_posts.append(post)
        result.exported_activity_urns.append(post.post_id)
        record_seen_activity(
            db_path,
            target_url,
            post.post_id,
            fetched_at=fetched_at,
            activity_type='repost' if post.is_repost else 'authored',
            source_url=post.post_url,
            body_hash=body_hash(post),
        )

    written_paths = write_posts_to_directory(
        exported_posts,
        directory,
        fetched_date=fetched_at,
        profile_name=profile_name,
        tags=tags,
    )
    for post, path in zip(exported_posts, written_paths, strict=False):
        record_export(db_path, activity_urn=post.post_id, note_path=str(path), body_hash=body_hash(post))

    state = load_sync_state(db_path, target_url)
    update_sync_state(
        db_path,
        target_url=target_url,
        newest_seen_activity_urn=state.newest_seen_activity_urn,
        oldest_seen_activity_urn=state.oldest_seen_activity_urn,
        last_successful_run_at=fetched_at,
        backfill_complete=state.backfill_complete or mode == 'backfill',
        last_exported_activity_urn=result.exported_activity_urns[-1] if result.exported_activity_urns else state.last_exported_activity_urn,
        extraction_version=EXTRACTION_VERSION,
    )
    return result
