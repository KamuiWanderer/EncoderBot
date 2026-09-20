"""
Telegram message link parser.
Supports public channel links (t.me/channel/123) and private channel links (t.me/c/1234567890/123).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional, Union


@dataclass
class ParsedTelegramLink:
    raw_url: str
    chat_id: Union[int, str]
    message_id: int
    is_private: bool = False


class TelegramLinkParser:
    """Parses various formats of Telegram message links."""

    # Public: https://t.me/username/123 or t.me/username/123
    PUBLIC_PATTERN = re.compile(
        r"(?:https?://)?(?:www\.)?(?:t\.me|telegram\.me)/(?P<username>[a-zA-Z0-9_]{4,})/(?P<msg_id>\d+)",
        re.IGNORECASE,
    )

    # Private: https://t.me/c/1234567890/123 or t.me/c/1234567890/123
    PRIVATE_PATTERN = re.compile(
        r"(?:https?://)?(?:www\.)?(?:t\.me|telegram\.me)/c/(?P<channel_id>\d+)/(?P<msg_id>\d+)",
        re.IGNORECASE,
    )

    @classmethod
    def parse(cls, url: str) -> Optional[ParsedTelegramLink]:
        """Extracts chat identifier and message ID from Telegram message URL."""
        if not url:
            return None

        clean_url = url.strip()

        # Check private pattern first
        priv_match = cls.PRIVATE_PATTERN.search(clean_url)
        if priv_match:
            raw_channel_id = int(priv_match.group("channel_id"))
            msg_id = int(priv_match.group("msg_id"))
            # Format as MTProto supergroup ID (-100...)
            formatted_chat_id = int(f"-100{raw_channel_id}") if not str(raw_channel_id).startswith("-100") else raw_channel_id
            return ParsedTelegramLink(
                raw_url=clean_url,
                chat_id=formatted_chat_id,
                message_id=msg_id,
                is_private=True,
            )

        # Check public pattern
        pub_match = cls.PUBLIC_PATTERN.search(clean_url)
        if pub_match:
            username = pub_match.group("username")
            # Exclude special subpaths like /c/, /s/, /joinchat/
            if username.lower() not in ("c", "s", "joinchat", "addstickers"):
                msg_id = int(pub_match.group("msg_id"))
                return ParsedTelegramLink(
                    raw_url=clean_url,
                    chat_id=f"@{username}" if not username.startswith("@") else username,
                    message_id=msg_id,
                    is_private=False,
                )

        return None
