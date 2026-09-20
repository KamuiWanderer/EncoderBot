"""
Unit tests for presets, scaling, and quality normalization.
"""

from app.encoder.presets import (
    calculate_scale,
    get_quality_height,
    normalize_quality_name,
    sort_qualities_ascending,
)


def test_quality_normalization():
    assert normalize_quality_name("2k") == "1440p"
    assert normalize_quality_name("4K") == "2160p"
    assert normalize_quality_name("1080") == "1080p"
    assert normalize_quality_name("720p") == "720p"
    assert normalize_quality_name("360") == "360p"


def test_ascending_quality_sorting():
    raw_list = ["1080p", "240p", "720p", "360p", "480p"]
    sorted_list = sort_qualities_ascending(raw_list)
    assert sorted_list == ["240p", "360p", "480p", "720p", "1080p"]


def test_scale_calculation_1080p_to_720p():
    scale = calculate_scale(orig_width=1920, orig_height=1080, target_quality="720p")
    assert scale.height == 720
    assert scale.width == 1280
    assert scale.width % 2 == 0
    assert scale.height % 2 == 0
    assert scale.is_upscaled is False
    assert "scale=1280:720" in scale.ffmpeg_filter


def test_scale_calculation_prevent_upscale():
    # Source is 480p, requested 1080p without upscale allowed
    scale = calculate_scale(orig_width=854, orig_height=480, target_quality="1080p", allow_upscale=False)
    # Stays at original height
    assert scale.height == 480
    assert scale.width == 854
    assert scale.is_upscaled is False
    assert "flags=bicubic" in scale.ffmpeg_filter


def test_scale_calculation_force_upscale():
    # Source is 180p, requested 240p with upscale allowed
    scale = calculate_scale(orig_width=320, orig_height=180, target_quality="240p", allow_upscale=True)
    assert scale.height == 240
    assert scale.is_upscaled is True
    assert "flags=lanczos+accurate_rnd" in scale.ffmpeg_filter

