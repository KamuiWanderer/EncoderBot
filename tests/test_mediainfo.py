"""
Unit tests for media info and resolution labeling.
"""

from app.handlers.mediainfo import get_resolution_label


def test_resolution_label_1080p():
    tag, badge = get_resolution_label(1920, 1080)
    assert tag == "1080p"
    assert "1080p Full HD (1920×1080) [16:9]" in badge


def test_resolution_label_720p():
    tag, badge = get_resolution_label(1280, 720)
    assert tag == "720p"
    assert "720p HD (1280×720) [16:9]" in badge


def test_resolution_label_180p_low():
    tag, badge = get_resolution_label(320, 180)
    assert tag == "180p"
    assert "180p Low-Res (320×180)" in badge


def test_resolution_label_fallback():
    tag, badge = get_resolution_label(0, 0, "480p")
    assert tag == "480p"
    assert "480P" in badge
