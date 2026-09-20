"""
Disk space validation and temporary requirement estimation.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from app.config import config
from app.utils.formatting import human_bytes


def get_available_disk_space_bytes(path: str | Path | None = None) -> int:
    """Return free disk space in bytes for the specified path (or default storage dir)."""
    check_path = Path(path) if path else config.storage.base_dir
    check_path.mkdir(parents=True, exist_ok=True)
    usage = shutil.disk_usage(check_path)
    return usage.free


def get_available_disk_space_gb(path: str | Path | None = None) -> float:
    """Return free disk space in gigabytes."""
    return get_available_disk_space_bytes(path) / (1024 ** 3)


def estimate_required_storage_bytes(source_size_bytes: int, quality_count: int = 1) -> int:
    """
    Estimate storage requirement.
    Since we encode sequentially and upload-then-delete each quality:
    Peak usage = source_size + largest_output_size (approx 1.2 * source_size).
    Add 500MB safety margin.
    """
    safety_margin = 500 * 1024 * 1024  # 500 MB
    estimated_peak = int(source_size_bytes * 2.2) + safety_margin
    return estimated_peak


def check_disk_space_sufficient(source_size_bytes: int, quality_count: int = 1) -> tuple[bool, str]:
    """
    Check if available disk space is sufficient for the job.
    Returns (is_sufficient, details_message).
    """
    available_bytes = get_available_disk_space_bytes()
    min_free_bytes = int(config.encoder.min_free_disk_gb * 1024 ** 3)
    estimated_required = estimate_required_storage_bytes(source_size_bytes, quality_count)

    if available_bytes < min_free_bytes or available_bytes < estimated_required:
        msg = (
            f"⚠️ **Insufficient Storage Space**\n\n"
            f"Available: `{human_bytes(available_bytes)}`\n"
            f"Estimated Required: `{human_bytes(estimated_required)}`\n"
            f"Safety Threshold: `{config.encoder.min_free_disk_gb:.1f} GB`\n\n"
            f"Please free up disk space before starting this encode."
        )
        return False, msg

    return True, f"Storage OK ({human_bytes(available_bytes)} free)"
