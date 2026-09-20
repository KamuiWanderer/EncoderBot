"""
Encoding profiles for video and audio parameters.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class EncodingProfile:
    name: str
    display_name: str
    vcodec: str = "libx264"
    crf: int = 23
    preset: str = "medium"
    pix_fmt: str = "yuv420p"
    acodec: str = "aac"
    abitrate: str = "128k"
    extra_video_flags: list[str] = field(default_factory=lambda: ["-movflags", "+faststart"])
    description: str = ""


STANDARD_PROFILE = EncodingProfile(
    name="standard",
    display_name="🎬 Standard (Balanced)",
    vcodec="libx264",
    crf=23,
    preset="medium",
    pix_fmt="yuv420p",
    acodec="aac",
    abitrate="128k",
    extra_video_flags=["-movflags", "+faststart"],
    description="Optimal balance between quality, file size, and encoding speed.",
)

MOBILE_PROFILE = EncodingProfile(
    name="mobile",
    display_name="📱 Mobile (Fast & Compact)",
    vcodec="libx264",
    crf=26,
    preset="veryfast",
    pix_fmt="yuv420p",
    acodec="aac",
    abitrate="96k",
    extra_video_flags=["-movflags", "+faststart"],
    description="Faster encoding and smaller file sizes, ideal for mobile devices.",
)

HQ_PROFILE = EncodingProfile(
    name="hq",
    display_name="💎 High Quality (Crisp)",
    vcodec="libx264",
    crf=19,
    preset="slow",
    pix_fmt="yuv420p",
    acodec="aac",
    abitrate="192k",
    extra_video_flags=["-movflags", "+faststart"],
    description="Maximum visual clarity with high audio fidelity.",
)

PROFILES: dict[str, EncodingProfile] = {
    "standard": STANDARD_PROFILE,
    "mobile": MOBILE_PROFILE,
    "hq": HQ_PROFILE,
}


def get_profile(name: str) -> EncodingProfile:
    """Retrieve profile by key, fallback to standard."""
    return PROFILES.get(name.lower().strip(), STANDARD_PROFILE)
