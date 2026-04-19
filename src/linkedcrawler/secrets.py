from __future__ import annotations

import subprocess
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class LinkedInCredentials:
    username: str
    password: str


def _lookup_secret(kind: str) -> str:
    try:
        result = subprocess.run(
            [
                'secret-tool',
                'lookup',
                'service',
                'linkedin-crawler',
                'account',
                'default',
                'kind',
                kind,
            ],
            check=True,
            capture_output=True,
            text=True,
        )
    except (FileNotFoundError, subprocess.CalledProcessError) as exc:
        raise RuntimeError(
            'Unable to read LinkedIn crawler secrets from secret-tool. '
            'Store them with secret-tool first.'
        ) from exc

    value = result.stdout.strip()
    if not value:
        raise RuntimeError(f'LinkedIn crawler secret for {kind!r} was empty.')
    return value


def get_linkedin_credentials() -> LinkedInCredentials:
    return LinkedInCredentials(
        username=_lookup_secret('username'),
        password=_lookup_secret('password'),
    )
