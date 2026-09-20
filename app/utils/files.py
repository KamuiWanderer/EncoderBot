"""
File management, temporary paths, and safe filename sanitization.
"""

from __future__ import annotations

import os
import re
import shutil
from pathlib import Path
from typing import Optional


def sanitize_filename(filename: str, max_length: int = 220) -> str:
    """
    Sanitize filename by removing invalid filesystem characters across OSes
    (Windows, Linux, macOS, Android/Termux).
    """
    if not filename:
        return "video.mp4"

    # Replace invalid characters: < > : " / \ | ? * and control chars
    sanitized = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", filename)
    # Replace multiple spaces/dots
    sanitized = re.sub(r"\s+", " ", sanitized).strip()
    sanitized = sanitized.strip(". ")

    if not sanitized:
        return "video.mp4"

    # Ensure max length while preserving extension
    if len(sanitized) > max_length:
        parts = sanitized.rsplit(".", 1)
        if len(parts) == 2:
            base, ext = parts
            sanitized = f"{base[:max_length - len(ext) - 1]}.{ext}"
        else:
            sanitized = sanitized[:max_length]

    return sanitized


def safe_delete(file_path: str | Path | None) -> bool:
    """Safely delete a file if it exists without throwing exceptions."""
    if not file_path:
        return False
    try:
        p = Path(file_path)
        if p.is_file() or p.is_symlink():
            p.unlink(missing_ok=True)
            return True
        elif p.is_dir():
            shutil.rmtree(p, ignore_errors=True)
            return True
    except Exception:
        return False
    return False


def get_file_size(file_path: str | Path | None) -> int:
    """Return size in bytes or 0 if file does not exist."""
    if not file_path:
        return 0
    try:
        p = Path(file_path)
        if p.is_file():
            return p.stat().st_size
    except Exception:
        pass
    return 0
