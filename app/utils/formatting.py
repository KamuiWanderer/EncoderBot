"""
Formatting utilities for file sizes, duration, progress bars, and speeds.
"""

from __future__ import annotations

import math


def human_bytes(size: int | float | None) -> str:
    """Convert bytes to human-readable string (e.g. 1.25 GB, 350 MB)."""
    if size is None or size <= 0:
        return "0 B"
    units = ["B", "KB", "MB", "GB", "TB", "PB"]
    i = int(math.floor(math.log(size, 1024)))
    p = math.pow(1024, i)
    s = round(size / p, 2)
    return f"{s} {units[i]}"


def human_duration(seconds: int | float | None) -> str:
    """Format seconds into HH:MM:SS or MM:SS."""
    if seconds is None or seconds < 0:
        return "00:00"
    seconds = int(seconds)
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    secs = seconds % 60
    if hours > 0:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def human_eta(seconds: int | float | None) -> str:
    """Format ETA seconds nicely (e.g. 14m, 2h 15m, 45s)."""
    if seconds is None or seconds <= 0 or math.isinf(seconds) or math.isnan(seconds):
        return "N/A"
    seconds = int(seconds)
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    secs = seconds % 60

    if hours > 0:
        return f"{hours}h {minutes}m"
    if minutes > 0:
        return f"{minutes}m {secs}s"
    return f"{secs}s"


def progress_bar(percentage: float, length: int = 12) -> str:
    """
    Generate visual progress bar (e.g. ████████░░░░ 66.7%).
    """
    percentage = max(0.0, min(100.0, percentage))
    filled_length = int(round(length * percentage / 100))
    bar = "█" * filled_length + "░" * (length - filled_length)
    return f"{bar} {percentage:.1f}%"
