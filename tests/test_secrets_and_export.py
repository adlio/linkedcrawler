from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from linkedcrawler.models import LinkedInPost
from linkedcrawler.auth import ensure_linkedin_login
from linkedcrawler.secrets import LinkedInCredentials, get_linkedin_credentials
from linkedcrawler.export import body_hash, post_filename, render_post_markdown, write_posts_to_directory


class CompletedProcessFactory:
    def __init__(self, values: dict[tuple[str, ...], str]):
        self.values = values
        self.calls: list[tuple[str, ...]] = []
        self.envs: list[dict[str, str] | None] = []

    def __call__(self, args: list[str], check: bool, capture_output: bool, text: bool, env=None):
        assert check is True
        assert capture_output is True
        assert text is True
        key = tuple(args)
        self.calls.append(key)
        self.envs.append(env)
        if key not in self.values:
            raise subprocess.CalledProcessError(returncode=1, cmd=args, stderr='missing')
        return subprocess.CompletedProcess(args=args, returncode=0, stdout=self.values[key], stderr='')


class FakeLoginSession:
    def __init__(self, logged_in: bool = False, *, modern_login: bool = False):
        self.logged_in = logged_in
        self.modern_login = modern_login
        self.visited_urls: list[str] = []
        self.typed: list[tuple[str, str]] = []
        self.typed_by_label: list[tuple[str, str]] = []
        self.clicked: list[str] = []
        self.clicked_text: list[str] = []

    def get(self, url: str) -> None:
        self.visited_urls.append(url)

    def page_html(self) -> str:
        if self.logged_in:
            return '<html><body><div data-urn="urn:li:activity:1"></div></body></html>'
        if self.modern_login:
            return '<html><body>Sign in Email or phone Password</body></html>'
        return '<html><body><input id="username" /><input id="password" /><button type="submit">Sign in</button></body></html>'

    def type(self, selector: str, text: str) -> None:
        self.typed.append((selector, text))

    def type_by_label(self, label: str, text: str) -> None:
        self.typed_by_label.append((label, text))

    def click(self, selector: str) -> None:
        self.clicked.append(selector)
        self.logged_in = True

    def click_text(self, text: str) -> None:
        self.clicked_text.append(text)
        self.logged_in = True


@pytest.fixture
def sample_post() -> LinkedInPost:
    return LinkedInPost(
        post_id='urn:li:activity:7426577558827216897',
        post_url='https://www.linkedin.com/posts/simonwardley_x-how-many-executives-are-looking-at-code-activity-7426577558827216897-5Y0G',
        post_date='2026-02-11T10:15:00.000Z',
        title='How many executives are looking at code level detail',
        author='Simon Wardley',
        is_repost=False,
        reposted_by='',
        text='X : How many executives are looking at code level detail at any sizable organization?',
        image_urls=['https://example.com/image.png'],
        has_video=True,
        video_id='video-123',
        video_poster_url='https://example.com/poster.jpg',
        video_cdn_urls=['https://cdn.example.com/video.m3u8'],
    )


def test_get_linkedin_credentials_reads_secret_tool(monkeypatch: pytest.MonkeyPatch) -> None:
    runner = CompletedProcessFactory(
        {
            ('secret-tool', 'lookup', 'service', 'linkedin-crawler', 'account', 'default', 'kind', 'username'): 'user@example.com\n',
            ('secret-tool', 'lookup', 'service', 'linkedin-crawler', 'account', 'default', 'kind', 'password'): 'hunter2\n',
        }
    )
    monkeypatch.setattr(subprocess, 'run', runner)

    credentials = get_linkedin_credentials()

    assert credentials == LinkedInCredentials(username='user@example.com', password='hunter2')
    assert len(runner.calls) == 2
    assert all(env is not None for env in runner.envs)


def test_get_linkedin_credentials_raises_clear_error_when_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    runner = CompletedProcessFactory({})
    monkeypatch.setattr(subprocess, 'run', runner)

    with pytest.raises(RuntimeError, match='secret-tool'):
        get_linkedin_credentials()


def test_ensure_linkedin_login_submits_credentials_when_login_form_present() -> None:
    session = FakeLoginSession(logged_in=False)

    ensure_linkedin_login(
        session,
        LinkedInCredentials(username='user@example.com', password='hunter2'),
        sleep=lambda _: None,
    )

    assert session.visited_urls == ['https://www.linkedin.com/login']
    assert session.typed == []
    assert session.typed_by_label == [('Email or phone', 'user@example.com'), ('Password', 'hunter2')]
    assert session.clicked == []
    assert session.clicked_text == ['Sign in']


def test_ensure_linkedin_login_uses_label_based_fallback_for_modern_login_form() -> None:
    session = FakeLoginSession(logged_in=False, modern_login=True)

    ensure_linkedin_login(
        session,
        LinkedInCredentials(username='user@example.com', password='hunter2'),
        sleep=lambda _: None,
    )

    assert session.visited_urls == ['https://www.linkedin.com/login']
    assert session.typed == []
    assert session.typed_by_label == [('Email or phone', 'user@example.com'), ('Password', 'hunter2')]
    assert session.clicked == []
    assert session.clicked_text == ['Sign in']


def test_ensure_linkedin_login_skips_when_already_logged_in() -> None:
    session = FakeLoginSession(logged_in=True)

    ensure_linkedin_login(
        session,
        LinkedInCredentials(username='user@example.com', password='hunter2'),
        sleep=lambda _: None,
    )

    assert session.visited_urls == []
    assert session.typed == []
    assert session.clicked == []


def test_render_post_markdown_formats_obsidian_note(sample_post: LinkedInPost) -> None:
    markdown = render_post_markdown(sample_post, fetched_date='2026-04-18')

    assert 'title: "How many executives are looking at code level detail"' in markdown
    assert 'source: "https://www.linkedin.com/posts/simonwardley_x-how-many-executives-are-looking-at-code-activity-7426577558827216897-5Y0G"' in markdown
    assert 'author: "Simon Wardley"' in markdown
    assert 'content_type: "linkedin-post"' in markdown
    assert 'published: 2026-02-11' in markdown
    assert 'fetched: 2026-04-18' in markdown
    assert 'body_hash: "3a924575d6"' in markdown
    assert 'tags:' in markdown
    assert '- "ai-thinkers"' in markdown
    assert '- "simon-wardley"' in markdown
    assert 'X : How many executives are looking at code level detail at any sizable organization?' in markdown
    assert '## Media' in markdown
    assert '- Image: https://example.com/image.png' in markdown
    assert '- Video poster: https://example.com/poster.jpg' in markdown
    assert '- Video CDN: https://cdn.example.com/video.m3u8' in markdown


def test_body_hash_is_stable_for_title_only_changes(sample_post: LinkedInPost) -> None:
    renamed = LinkedInPost(**sample_post.to_dict())
    renamed.title = 'A completely different title'

    assert body_hash(sample_post) == '3a924575d6'
    assert body_hash(renamed) == body_hash(sample_post)


def test_post_filename_uses_activity_urn_and_content_hash(sample_post: LinkedInPost) -> None:
    assert post_filename(sample_post) == '2026-02-11-activity-7426577558827216897-3a924575d6.md'


def test_post_filename_versions_when_content_changes(sample_post: LinkedInPost) -> None:
    revised = LinkedInPost(**sample_post.to_dict())
    revised.text = sample_post.text + ' Revised.'

    assert post_filename(revised) == '2026-02-11-activity-7426577558827216897-ce813bd50d.md'
    assert post_filename(revised) != post_filename(sample_post)


def test_write_posts_to_directory_is_idempotent_for_same_activity_and_content(sample_post: LinkedInPost, tmp_path: Path) -> None:
    first_written = write_posts_to_directory([sample_post], tmp_path, fetched_date='2026-04-18')
    retitled = LinkedInPost(**sample_post.to_dict())
    retitled.title = 'Changed title, same content'
    second_written = write_posts_to_directory([retitled], tmp_path, fetched_date='2026-04-19')

    expected_path = tmp_path / '2026-02-11-activity-7426577558827216897-3a924575d6.md'
    assert first_written == [expected_path]
    assert second_written == [expected_path]
    assert expected_path.exists()
    assert 'fetched: 2026-04-19' in expected_path.read_text()


def test_post_filename_omits_date_when_post_date_missing(sample_post: LinkedInPost) -> None:
    sample_post.post_date = ''
    assert post_filename(sample_post) == 'activity-7426577558827216897-3a924575d6.md'
