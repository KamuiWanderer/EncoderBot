"""
Unit tests for template placeholders, rendering, and filename sanitization.
"""

from app.metadata.parser import MetadataParser
from app.metadata.placeholders import TemplateContext, TemplateEngine
from app.utils.files import sanitize_filename


def test_filename_rendering_and_sanitization():
    meta = MetadataParser.parse("House of the Dragon S01E08 1080p.mkv")
    ctx = TemplateContext(
        metadata=meta,
        quality="720p",
        width=1280,
        height=720,
        codec="h264",
        ext="mp4",
        calculated_bolum=8,
    )

    template = "{title} S{season} E{episode} {quality}.{ext}"
    rendered = TemplateEngine.render_filename(template, ctx)
    assert rendered == "House Of The Dragon S01 E08 720p.mp4"


def test_invalid_filename_characters_sanitized():
    bad_filename = 'Invalid:Video*Name?With/Slashes\\And<Tags>|And"Quotes".mp4'
    clean = sanitize_filename(bad_filename)
    assert ":" not in clean
    assert "*" not in clean
    assert "?" not in clean
    assert "/" not in clean
    assert "\\" not in clean
    assert "<" not in clean
    assert ">" not in clean
    assert "|" not in clean
    assert '"' not in clean


def test_caption_rendering_with_html_tags():
    meta = MetadataParser.parse("Breaking Bad S05E14 1080p WEB-DL.mp4")
    ctx = TemplateContext(
        metadata=meta,
        quality="1080p",
        width=1920,
        height=1080,
        codec="h264",
        ext="mp4",
        calculated_bolum=60,
    )

    template = (
        "🎬 <b>{title}</b>\n\n"
        "<blockquote>📌 Season: {season}\n"
        "📌 Episode: {episode} (Bölüm {bolum})</blockquote>\n"
        "🎞 Quality: <code>{quality}</code>"
    )

    caption = TemplateEngine.render_caption(template, ctx)
    assert "<b>Breaking Bad</b>" in caption
    assert "<blockquote>" in caption
    assert "Season: 05" in caption
    assert "Episode: 14 (Bölüm 60)" in caption
    assert "<code>1080p</code>" in caption


def test_zero_padding_and_custom_placeholders():
    meta = MetadataParser.parse("Kurulus Osman S1E1 720p.mp4")
    ctx = TemplateContext(
        metadata=meta,
        quality="720p",
        calculated_bolum=1,
    )

    # Standard default 2-digit zero-padded
    assert TemplateEngine.render_caption("Season {season} Episode {episode} Bolum {bolum}", ctx) == "Season 01 Episode 01 Bolum 01"

    # Custom 3-digit zero-padded
    assert TemplateEngine.render_caption("{episode_3} / {episode:03d} / {bolum_3}", ctx) == "001 / 001 / 001"

    # Raw unpadded
    assert TemplateEngine.render_caption("{season_raw}x{episode_raw}", ctx) == "1x1"
    assert TemplateEngine.render_caption("S{season_1}E{episode_1}", ctx) == "S1E1"

