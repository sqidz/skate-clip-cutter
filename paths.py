#!/usr/bin/env python3
"""Stdlib path helpers shared by the UI and the worker."""

from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DEFAULT_MODEL_NAME = "yolo11n.pt"


def is_portable_layout() -> bool:
    return (ROOT / "python" / "python.exe").is_file()


def bundled_python() -> Path | None:
    exe = ROOT / "python" / "python.exe"
    return exe if exe.is_file() else None


def dev_venv_python() -> Path | None:
    exe = ROOT / ".venv" / "Scripts" / "python.exe"
    return exe if exe.is_file() else None


def bundled_model_path() -> Path | None:
    for cand in (
        ROOT / "models" / DEFAULT_MODEL_NAME,
        ROOT / DEFAULT_MODEL_NAME,
    ):
        if cand.is_file():
            return cand
    return None


def find_bundled_ffmpeg_tool(name: str) -> Path | None:
    exe = f"{name}.exe" if os.name == "nt" else name
    for cand in (
        ROOT / "vendor" / "ffmpeg" / exe,
        ROOT / "vendor" / "ffmpeg" / "bin" / exe,
    ):
        if cand.is_file():
            return cand
    return None


def default_landing_dir() -> Path:
    videos = Path.home() / "Videos"
    if videos.is_dir():
        return videos
    docs = Path.home() / "Documents"
    if docs.is_dir():
        return docs
    return ROOT / "out"
