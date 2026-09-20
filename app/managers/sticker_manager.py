"""
Sticker post-encode configuration manager.
"""

from __future__ import annotations

from typing import Optional
from app.database.database import db
from app.utils.logging import logger


class StickerManager:
    """Manages persistent sticker configurations to send after completed episodes/jobs."""

    @staticmethod
    async def get_sticker(user_id: int) -> Optional[str]:
        settings = await db.get_user_settings(user_id)
        return settings.sticker_id

    @staticmethod
    async def set_sticker(user_id: int, sticker_id: str) -> None:
        settings = await db.get_user_settings(user_id)
        settings.sticker_id = sticker_id.strip()
        await db.update_user_settings(settings)
        logger.info(f"Updated post-encode sticker for user {user_id}")

    @staticmethod
    async def clear_sticker(user_id: int) -> None:
        settings = await db.get_user_settings(user_id)
        settings.sticker_id = None
        await db.update_user_settings(settings)
        logger.info(f"Cleared sticker configuration for user {user_id}")
