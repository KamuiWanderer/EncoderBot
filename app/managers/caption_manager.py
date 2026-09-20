"""
Caption configuration manager.
Handles active caption templates and commands (/setcaption, /caption, /clearcaption).
"""

from __future__ import annotations

from typing import Optional
from app.database.database import db
from app.database.models import CaptionFormat
from app.metadata.placeholders import TemplateEngine
from app.utils.logging import logger


class CaptionManager:
    """Manages persistent caption formatting templates."""

    @staticmethod
    async def get_format(user_id: int) -> Optional[CaptionFormat]:
        return await db.get_active_caption_format(user_id)

    @staticmethod
    async def set_format(user_id: int, template: str) -> CaptionFormat:
        saved = await db.set_caption_format(user_id, template.strip())
        logger.info(f"Updated caption format for user {user_id}: {template}")
        return saved

    @staticmethod
    async def clear_format(user_id: int) -> None:
        await db.clear_caption_format(user_id)
        logger.info(f"Cleared caption format for user {user_id}")

    @staticmethod
    def get_default_template() -> str:
        return TemplateEngine.DEFAULT_CAPTION_TEMPLATE
