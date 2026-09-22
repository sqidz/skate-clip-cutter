#!/usr/bin/env python3
"""Public version + GitHub identity. Stdlib only.

Bump APP_VERSION when tagging a Release. Set GITHUB_REPO to owner/name
once the public repo exists (Check for updates uses it).
"""

from __future__ import annotations

APP_VERSION = "0.1.0"
GITHUB_REPO = "sqidz/skate-clip-cutter"


def releases_url() -> str:
    return f"https://github.com/{GITHUB_REPO}/releases"


def latest_release_api_url() -> str:
    return f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
