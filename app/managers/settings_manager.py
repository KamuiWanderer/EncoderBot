"""
Centralized settings manager coordinating all user preferences.
"""

from __future__ import annotations

from typing import Optional
from app.database.database import db
from app.database.models import UserSettings
from app.encoder.presets import STANDARD_QUALITIES
from app.utils.logging import logger


class SettingsManager:
    """Provides high-level helpers for getting and setting user options."""

    @staticmethod
    async def get_settings(user_id: int) -> UserSettings:
        return await db.get_user_settings(user_id)

    @staticmethod
    async def set_default_qualities(user_id: int, qualities: list[str]) -> None:
        settings = await db.get_user_settings(user_id)
        settings.default_qualities = qualities or STANDARD_QUALITIES
        await db.update_user_settings(settings)
        logger.info(f"Updated default qualities for user {user_id}: {qualities}")

    @staticmethod
    async def set_default_codec(user_id: int, codec: str) -> None:
        settings = await db.get_user_settings(user_id)
        settings.default_codec = codec
        await db.update_user_settings(settings)
        logger.info(f"Updated default codec for user {user_id}: {codec}")

    @staticmethod
    async def set_default_profile(user_id: int, profile: str) -> None:
        settings = await db.get_user_settings(user_id)
        settings.default_profile = profile
        await db.update_user_settings(settings)
        logger.info(f"Updated default profile for user {user_id}: {profile}")

    @staticmethod
    async def set_allow_upscale(user_id: int, allow: bool) -> None:
        settings = await db.get_user_settings(user_id)
        settings.allow_upscale = allow
        await db.update_user_settings(settings)
        logger.info(f"Updated allow_upscale for user {user_id}: {allow}")

    @staticmethod
    async def reset_settings(user_id: int) -> UserSettings:
        default = UserSettings(user_id=user_id)
        await db.update_user_settings(default)
        await db.clear_filename_format(user_id)
        await db.clear_caption_format(user_id)
        await db.clear_thumbnail_config(user_id)
        logger.info(f"Reset all settings to defaults for user {user_id}")
        return default

