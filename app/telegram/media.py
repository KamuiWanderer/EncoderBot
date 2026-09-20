"""
Telegram media detection and validation helpers.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional
from pyrogram.types import Message

from app.utils.logging import logger

VIDEO_EXTENSIONS = {".mp4", ".mkv", ".avi", ".mov", ".flv", ".wmv", ".webm", ".m4v", ".ts"}


@dataclass
class TelegramMediaInfo:
    file_id: str
    file_unique_id: str
    file_name: str
    file_size_bytes: int
    duration_seconds: int = 0
    width: int = 0
    height: int = 0
    mime_type: Optional[str] = None
    thumbnail_file_id: Optional[str] = None


class MediaExtractor:
    """Extracts media metadata from Telegram Message objects."""

    @classmethod
    def extract_video_media(cls, message: Message) -> Optional[TelegramMediaInfo]:
        """Validates and extracts video info from a message (video, document video, animation)."""
        if message.video:
            v = message.video
            name = v.file_name or f"video_{v.file_unique_id}.mp4"
            thumb_id = v.thumbs[0].file_id if v.thumbs else None
            return TelegramMediaInfo(
                file_id=v.file_id,
                file_unique_id=v.file_unique_id,
                file_name=name,
                file_size_bytes=v.file_size or 0,
                duration_seconds=v.duration or 0,
                width=v.width or 0,
                height=v.height or 0,
                mime_type=v.mime_type,
                thumbnail_file_id=thumb_id,
            )

        if message.document:
            d = message.document
            name = d.file_name or "video.mp4"
            ext = Path(name).suffix.lower()
            mime = (d.mime_type or "").lower()

            # Check if document is a video format
            if ext in VIDEO_EXTENSIONS or mime.startswith("video/"):
                thumb_id = d.thumbs[0].file_id if d.thumbs else None
                return TelegramMediaInfo(
                    file_id=d.file_id,
                    file_unique_id=d.file_unique_id,
                    file_name=name,
                    file_size_bytes=d.file_size or 0,
                    duration_seconds=0,
                    width=0,
                    height=0,
                    mime_type=d.mime_type,
                    thumbnail_file_id=thumb_id,
                )

        if message.animation:
            a = message.animation
            name = a.file_name or f"animation_{a.file_unique_id}.mp4"
            thumb_id = a.thumbs[0].file_id if a.thumbs else None
            return TelegramMediaInfo(
                file_id=a.file_id,
                file_unique_id=a.file_unique_id,
                file_name=name,
                file_size_bytes=a.file_size or 0,
                duration_seconds=a.duration or 0,
                width=a.width or 0,
                height=a.height or 0,
                mime_type=a.mime_type,
                thumbnail_file_id=thumb_id,
            )

        return None

    @classmethod
    def extract_photo_id(cls, message: Message) -> Optional[str]:
        """Extracts largest photo file ID if message contains a photo."""
        if message.photo:
            return message.photo.file_id
        return None
