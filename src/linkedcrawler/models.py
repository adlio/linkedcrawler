from __future__ import annotations

from dataclasses import asdict, dataclass, field


@dataclass(slots=True)
class LinkedInPost:
    post_id: str
    post_url: str
    post_date: str
    title: str
    author: str
    is_repost: bool
    reposted_by: str
    text: str
    image_urls: list[str] = field(default_factory=list)
    has_video: bool = False
    video_id: str = ""
    video_poster_url: str = ""
    video_cdn_urls: list[str] = field(default_factory=list)
    # Link previews (articleComponent) — external article the post references.
    article_url: str = ""
    article_title: str = ""
    # Attached documents (documentComponent) — typically a PDF on LinkedIn's CDN.
    document_url: str = ""
    document_title: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(slots=True)
class ExtractionError:
    index: int
    selector: str
    message: str

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(slots=True)
class ExtractionReport:
    items: list[LinkedInPost] = field(default_factory=list)
    errors: list[ExtractionError] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "items": [item.to_dict() for item in self.items],
            "errors": [error.to_dict() for error in self.errors],
        }


@dataclass(slots=True)
class CrawlRequest:
    url: str
    last_saved_item_key: str | None = None
    max_scroll_rounds: int = 3
    wait_attempts: int = 10
    wait_delay_seconds: float = 2.0

@dataclass(slots=True)
class CrawlResult:
    request: CrawlRequest
    posts: list[LinkedInPost] = field(default_factory=list)
    extraction_errors: list[ExtractionError] = field(default_factory=list)
    rounds_scrolled: int = 0
    reached_last_saved_item: bool = False
    newest_item_key: str | None = None

    def to_dict(self) -> dict:
        return {
            "request": asdict(self.request),
            "posts": [post.to_dict() for post in self.posts],
            "extraction_errors": [error.to_dict() for error in self.extraction_errors],
            "rounds_scrolled": self.rounds_scrolled,
            "reached_last_saved_item": self.reached_last_saved_item,
            "newest_item_key": self.newest_item_key,
        }


@dataclass(slots=True)
class SyncState:
    target_profile_url: str
    newest_seen_activity_urn: str | None = None
    oldest_seen_activity_urn: str | None = None
    last_successful_run_at: str | None = None
    backfill_complete: bool = False
    last_exported_activity_urn: str | None = None
    extraction_version: str = '1'


@dataclass(slots=True)
class SyncResult:
    exported_activity_urns: list[str] = field(default_factory=list)
    skipped_seen_activity_urns: list[str] = field(default_factory=list)
    filtered_out_activity_urns: list[str] = field(default_factory=list)
    stopped_on_seen_streak: bool = False

    def to_dict(self) -> dict:
        return asdict(self)
