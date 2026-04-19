from __future__ import annotations

import os
import pathlib
import subprocess
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class LinkedInCredentials:
    username: str
    password: str


def _secret_tool_env() -> dict[str, str]:
    env = os.environ.copy()
    if not env.get('XDG_RUNTIME_DIR'):
        runtime_dir = pathlib.Path(f'/run/user/{os.getuid()}')
        if runtime_dir.exists():
            env['XDG_RUNTIME_DIR'] = str(runtime_dir)

    if not env.get('DBUS_SESSION_BUS_ADDRESS') and env.get('XDG_RUNTIME_DIR'):
        bus_path = pathlib.Path(env['XDG_RUNTIME_DIR']) / 'bus'
        if bus_path.exists():
            env['DBUS_SESSION_BUS_ADDRESS'] = f'unix:path={bus_path}'

    return env


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
            env=_secret_tool_env(),
        )
    except (FileNotFoundError, subprocess.CalledProcessError) as exc:
        raise RuntimeError(
            'Unable to read LinkedIn crawler secrets from secret-tool. '
            'Store them with secret-tool first and ensure a session D-Bus is available.'
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
