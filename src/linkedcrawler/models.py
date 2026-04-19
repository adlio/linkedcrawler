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
