"""
Destination channel / chat configuration manager.
"""

from __future__ import annotations

from typing import Optional
from app.database.database import db
from app.utils.logging import logger


class DestinationManager:
    """Manages persistent default destination channels/chats for encoded video uploads."""

    @staticmethod
    async def get_destination(user_id: int) -> Optional[str]:
        settings = await db.get_user_settings(user_id)
        return settings.default_destination

    @staticmethod
    async def set_destination(user_id: int, destination: str) -> None:
        settings = await db.get_user_settings(user_id)
        settings.default_destination = destination.strip()
        await db.update_user_settings(settings)
        logger.info(f"Updated default destination for user {user_id}: {destination}")

    @staticmethod
    async def clear_destination(user_id: int) -> None:
        settings = await db.get_user_settings(user_id)
        settings.default_destination = None
        await db.update_user_settings(settings)
        logger.info(f"Cleared default destination for user {user_id}")
