from __future__ import annotations

import hashlib
from pathlib import Path

from .models import LinkedInPost


def _published_date(post: LinkedInPost) -> str | None:
    return post.post_date[:10] if len(post.post_date) >= 10 and post.post_date[4:5] == '-' else None


def _numeric_activity_id(post: LinkedInPost) -> str:
    prefix = 'urn:li:activity:'
    if not post.post_id.startswith(prefix):
        raise ValueError(f'Unsupported LinkedIn activity id: {post.post_id!r}')
    activity_id = post.post_id.removeprefix(prefix)
    if not activity_id.isdigit():
        raise ValueError(f'LinkedIn activity id must be numeric: {post.post_id!r}')
    return activity_id


def body_hash(post: LinkedInPost) -> str:
    normalized_parts = [
        post.post_id.strip(),
        post.post_url.strip(),
        post.author.strip(),
        str(post.is_repost),
        post.reposted_by.strip(),
        post.text.strip(),
        '\n'.join(post.image_urls),
        str(post.has_video),
        post.video_id.strip(),
        post.video_poster_url.strip(),
        '\n'.join(post.video_cdn_urls),
        post.article_url.strip(),
        post.article_title.strip(),
        post.document_url.strip(),
        post.document_title.strip(),
    ]
    digest = hashlib.sha256('\n'.join(normalized_parts).encode('utf-8')).hexdigest()
    return digest[:10]


def post_filename(post: LinkedInPost) -> str:
    activity_id = _numeric_activity_id(post)
    published = _published_date(post)
    version = body_hash(post)
    stem = f'activity-{activity_id}-{version}'
    return f'{published}-{stem}.md' if published else f'{stem}.md'


def _description(post: LinkedInPost, *, profile_name: str) -> str:
    source = post.text.strip() or post.title.strip() or f'LinkedIn post by {profile_name}'
    trimmed = ' '.join(source.split())
    if len(trimmed) <= 180:
        return trimmed
    shortened = trimmed[:177].rsplit(' ', 1)[0].strip()
    return f'{shortened}...'


def render_post_markdown(
    post: LinkedInPost,
    *,
    fetched_date: str,
    profile_name: str,
    tags: list[str],
) -> str:
    lines = [
        '---',
        f'title: "{(post.title or post.text[:100] or post.post_id).replace("\"", "\\\"")}"',
        f'source: "{post.post_url}"',
        f'author: "{post.author or profile_name}"',
        'content_type: "linkedin-post"',
    ]
    published = _published_date(post)
    if published:
        lines.append(f'published: {published}')
    lines.extend(
        [
            f'fetched: {fetched_date}',
            f'body_hash: "{body_hash(post)}"',
            f'description: "{_description(post, profile_name=profile_name).replace("\"", "\\\"")}"',
        ]
    )
    if tags:
        lines.append('tags:')
        for tag in tags:
            lines.append(f'  - "{tag}"')
    lines.extend(
        [
            '---',
            '',
            post.text.strip() or post.title.strip() or post.post_url,
        ]
    )

    media_lines: list[str] = []
    if post.article_url or post.article_title:
        label = post.article_title or post.article_url
        media_lines.append(f'- Article: [{label}]({post.article_url})' if post.article_url else f'- Article: {label}')
    if post.document_url or post.document_title:
        label = post.document_title or post.document_url
        media_lines.append(f'- Document: [{label}]({post.document_url})' if post.document_url else f'- Document: {label}')
    for url in post.image_urls:
        media_lines.append(f'- Image: {url}')
    if post.video_poster_url:
        media_lines.append(f'- Video poster: {post.video_poster_url}')
    for url in post.video_cdn_urls:
        media_lines.append(f'- Video CDN: {url}')
    if media_lines:
        lines.extend(['', '## Media', *media_lines])

    return '\n'.join(lines).rstrip() + '\n'


def write_posts_to_directory(
    posts: list[LinkedInPost],
    directory: Path,
    *,
    fetched_date: str,
    profile_name: str,
    tags: list[str],
) -> list[Path]:
    directory.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for post in posts:
        path = directory / post_filename(post)
        path.write_text(
            render_post_markdown(
                post,
                fetched_date=fetched_date,
                profile_name=profile_name,
                tags=tags,
            )
        )
        written.append(path)
    return written
