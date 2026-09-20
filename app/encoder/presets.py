"""
Quality presets, height mappings, and aspect-ratio preserving scaling calculations.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional


QUALITY_HEIGHT_MAP = {
    "144p": 144,
    "240p": 240,
    "360p": 360,
    "480p": 480,
    "720p": 720,
    "1080p": 1080,
    "1440p": 1440,
    "2K": 1440,
    "2160p": 2160,
    "4K": 2160,
}

# Standard V1 quality options
STANDARD_QUALITIES = ["240p", "360p", "480p", "720p", "1080p"]
EXTENDED_QUALITIES = ["144p", "240p", "360p", "480p", "720p", "1080p", "1440p", "2160p"]


def normalize_quality_name(quality: str) -> str:
    """Normalize quality string (e.g. 2k -> 1440p, 4k -> 2160p, 720 -> 720p)."""
    q = quality.strip().lower()
    if q in ("2k", "1440", "1440p"):
        return "1440p"
    if q in ("4k", "2160", "2160p", "uhd"):
        return "2160p"
    if q in ("fhd", "1080", "1080p"):
        return "1080p"
    if q in ("hd", "720", "720p"):
        return "720p"
    if q in ("sd", "480", "480p"):
        return "480p"
    if q in ("360", "360p"):
        return "360p"
    if q in ("240", "240p"):
        return "240p"
    if q in ("144", "144p"):
        return "144p"
    
    # Generic regex check for numbers like 720 or 1080
    match = re.search(r"(\d{3,4})", q)
    if match:
        val = int(match.group(1))
        return f"{val}p"
    return quality


def get_quality_height(quality: str) -> int:
    """Get target numeric height for a quality name."""
    norm = normalize_quality_name(quality)
    if norm in QUALITY_HEIGHT_MAP:
        return QUALITY_HEIGHT_MAP[norm]
    match = re.search(r"(\d+)", norm)
    if match:
        return int(match.group(1))
    return 720


def sort_qualities_ascending(qualities: list[str]) -> list[str]:
    """Sort list of qualities in ascending numerical order (e.g. 240p -> 360p -> 480p -> 720p -> 1080p)."""
    return sorted(qualities, key=get_quality_height)


@dataclass
class ScaleResolution:
    width: int
    height: int
    ffmpeg_filter: str
    is_upscaled: bool


def calculate_scale(
    orig_width: int,
    orig_height: int,
    target_quality: str,
    allow_upscale: bool = False,
) -> ScaleResolution:
    """
    Calculate target dimensions preserving aspect ratio with even (divisible by 2) width & height.
    Ensures safe FFmpeg scaling filter.
    """
    target_height = get_quality_height(target_quality)

    if orig_height <= 0 or orig_width <= 0:
        # Fallback if unknown
        return ScaleResolution(
            width=-2,
            height=target_height,
            ffmpeg_filter=f"scale=-2:{target_height}",
            is_upscaled=False,
        )

    is_upscaled = target_height > orig_height

    if is_upscaled and not allow_upscale:
        # Policy: Do not upscale, keep original dimensions
        calc_h = orig_height - (orig_height % 2)
        calc_w = orig_width - (orig_width % 2)
        return ScaleResolution(
            width=calc_w,
            height=calc_h,
            ffmpeg_filter=f"scale={calc_w}:{calc_h}:flags=bicubic",
            is_upscaled=False,
        )

    # Compute proportional width
    calc_width = int(round((orig_width / orig_height) * target_height))
    # Ensure divisible by 2 for H.264 / YUV420p
    calc_width = calc_width if calc_width % 2 == 0 else calc_width + 1
    calc_height = target_height if target_height % 2 == 0 else target_height + 1

    # High-quality Lanczos filter with accurate rounding for upscale, bicubic for downscale
    scale_flags = "flags=lanczos+accurate_rnd" if is_upscaled else "flags=bicubic"

    return ScaleResolution(
        width=calc_width,
        height=calc_height,
        ffmpeg_filter=f"scale={calc_width}:{calc_height}:{scale_flags}",
        is_upscaled=is_upscaled,
    )

