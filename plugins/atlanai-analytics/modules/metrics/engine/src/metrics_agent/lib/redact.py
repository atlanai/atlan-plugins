# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Atlan Pte. Ltd.
"""Credentials never printed (guardrail 19).

ONE DEFINITION OF "A CREDENTIAL". `run.yml` refuses a settings key that names one
(`run_config._refuse_credentials`), and the stage harness keeps the VALUE of every environment
variable whose name says it is one out of what a stage says. Both read the patterns here.

Why the harness needs it at all: a stage reports a failure by its message, and a driver's
message can quote what it was given. libpq repeats a malformed connection string word for
word; an HTTP library can repeat a URL with a password in it. The stage cannot know what a
third-party message carries, so what it prints passes through `text()` first: the value of
every credential variable in the environment, and the password inside any `scheme://user:pw@`
URL, become `<redacted>`.
"""
from __future__ import annotations

import os
import re
from typing import Mapping

PLACEHOLDER = "<redacted>"

# A key (a settings key, an environment variable name) that names a credential.
CREDENTIAL_KEY = re.compile(r"(^|_)(dsn|password|passwd|pwd|secret|token|api_?key|"
                            r"private_?key|credentials?)($|_)", re.IGNORECASE)

# A value that carries a password inline: `postgres://user:pw@host/db`.  # trufflehog:ignore
CREDENTIAL_URL = re.compile(r"[A-Za-z][A-Za-z0-9+.\-]*://[^/\s:@]+:[^/\s@]+@")
_URL_PASSWORD = re.compile(r"([A-Za-z][A-Za-z0-9+.\-]*://[^/\s:@]+:)([^/\s@]+)(@)")

# Shorter values are not redacted: a three-letter value would turn ordinary words into noise,
# and no real credential is that short.
MIN_SECRET_CHARS = 8


def key_words(key: str) -> str:
    """`clientSecret`, `client-secret` and `client.secret` all read as `client_secret`."""
    k = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", str(key))
    return re.sub(r"[^A-Za-z0-9]+", "_", k).lower()


def is_credential_name(name: str) -> bool:
    return bool(CREDENTIAL_KEY.search(key_words(name)))


def secret_values(env: Mapping[str, str] | None = None) -> list[str]:
    """The values of every credential-named variable, longest first.

    A DSN that is the path of a local database file is not a credential (DuckDB's is a file),
    so a value naming an existing path is left alone."""
    env = os.environ if env is None else env
    out = set()
    for name, value in env.items():
        v = str(value or "").strip()
        if len(v) < MIN_SECRET_CHARS or not is_credential_name(name):
            continue
        try:
            if os.path.exists(v):
                continue
        except (OSError, ValueError):
            pass
        out.add(v)
        out.update(_inner_secrets(v))
    return sorted(out, key=len, reverse=True)


def _inner_secrets(value: str) -> set[str]:
    """The password INSIDE a connection string (`k=v;k=v`, libpq's `k=v k=v`, or a URL),
    because a driver's message may quote that part alone."""
    out = set()
    words = [w for w in re.split(r"[\s;]+", value) if w]
    for i, w in enumerate(words):
        if "=" in w:
            k, _, v = w.partition("=")
            if is_credential_name(k) and len(v.strip("'\"")) >= MIN_SECRET_CHARS:
                out.add(v.strip("'\""))
        elif is_credential_name(w) and i + 1 < len(words) \
                and len(words[i + 1]) >= MIN_SECRET_CHARS:
            out.add(words[i + 1].strip("'\""))
    for m in _URL_PASSWORD.finditer(value):
        if len(m.group(2)) >= MIN_SECRET_CHARS:
            out.add(m.group(2))
    return out


def text(message, env: Mapping[str, str] | None = None) -> str:
    """`message` with every credential value in the environment, and every inline URL
    password, replaced by `<redacted>`."""
    out = str(message)
    for v in secret_values(env):
        out = out.replace(v, PLACEHOLDER)
    return _URL_PASSWORD.sub(lambda m: m.group(1) + PLACEHOLDER + m.group(3), out)
