"""
Unit tests for TelegramLinkParser.
"""

from app.telegram.links import TelegramLinkParser


def test_public_channel_link():
    url = "https://t.me/KurulusOsmanOfficial/452"
    parsed = TelegramLinkParser.parse(url)
    assert parsed is not None
    assert parsed.chat_id == "@KurulusOsmanOfficial"
    assert parsed.message_id == 452
    assert parsed.is_private is False


def test_private_channel_link():
    url = "https://t.me/c/1839201948/1205"
    parsed = TelegramLinkParser.parse(url)
    assert parsed is not None
    assert parsed.chat_id == -1001839201948
    assert parsed.message_id == 1205
    assert parsed.is_private is True


def test_invalid_links():
    assert TelegramLinkParser.parse("https://google.com") is None
    assert TelegramLinkParser.parse("random text") is None
