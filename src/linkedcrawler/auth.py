from __future__ import annotations

import json
import time
from collections.abc import Callable
from pathlib import Path
from typing import Protocol

from .secrets import LinkedInCredentials


class LoginSession(Protocol):
    def get(self, url: str) -> None: ...
    def page_html(self) -> str: ...
    def type(self, selector: str, text: str) -> None: ...
    def click(self, selector: str) -> None: ...
    def type_by_label(self, label: str, text: str) -> None: ...
    def click_text(self, text: str) -> None: ...
    def run_js(self, script: str) -> object: ...


def _looks_logged_in(html: str) -> bool:
    markers = (
        'data-urn="urn:li:activity:',
        "data-urn='urn:li:activity:",
        '/feed/update/urn:li:activity:',
        'global-nav__me',
    )
    return any(marker in html for marker in markers)


def _looks_like_login_form(html: str) -> bool:
    return (
        ('id="username"' in html and 'id="password"' in html)
        or ('Email or phone' in html and 'Password' in html and 'Sign in' in html)
    )


def ensure_linkedin_login(
    session: LoginSession,
    credentials: LinkedInCredentials,
    *,
    sleep: Callable[[float], None] = time.sleep,
    debug_dir: Path | None = None,
) -> None:
    def dump(label: str, body: str) -> None:
        if debug_dir is None:
            return
        debug_dir.mkdir(parents=True, exist_ok=True)
        (debug_dir / f'auth-{label}.html').write_text(body)

    html = session.page_html()
    dump('entry', html)
    if _looks_logged_in(html):
        dump('entry-logged-in', html)
        return

    session.get('https://www.linkedin.com/login')
    sleep(5)
    html = session.page_html()
    dump('login-page', html)
    if not _looks_like_login_form(html):
        dump('login-form-missing', html)
        return

    if hasattr(session, 'run_js'):
        user = json.dumps(credentials.username)
        password = json.dumps(credentials.password)
        session.run_js(
            f"""
function setVal(id, value) {{
  const el = document.getElementById(id);
  if (!el) return false;
  const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
  setter.call(el, value);
  el.dispatchEvent(new Event('input', {{ bubbles: true }}));
  el.dispatchEvent(new Event('change', {{ bubbles: true }}));
  return true;
}}
setVal(':r0:', {user});
setVal(':r1:', {password});
const btn = Array.from(document.querySelectorAll('button')).find((b) => (b.innerText || '').trim() === 'Sign in');
if (btn) btn.click();
"""
        )
    elif hasattr(session, 'type_by_label'):
        session.type_by_label('Email or phone', credentials.username)
        session.type_by_label('Password', credentials.password)
        if hasattr(session, 'click_text'):
            session.click_text('Sign in')
        else:
            session.click('button[type="submit"]')
    else:
        session.type('#username', credentials.username)
        session.type('#password', credentials.password)
        session.click('button[type="submit"]')
    sleep(2)
    dump('post-submit', session.page_html())
