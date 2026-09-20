"""
Filename configuration manager.
Handles active filename templates and commands (/setfilename, /filename, /clearfilename).
"""

from __future__ import annotations

from typing import Optional
from app.database.database import db
from app.database.models import FilenameFormat
from app.metadata.placeholders import TemplateEngine
from app.utils.logging import logger


class FilenameManager:
    """Manages persistent filename formatting templates."""

    @staticmethod
    async def get_format(user_id: int) -> Optional[FilenameFormat]:
        return await db.get_active_filename_format(user_id)

    @staticmethod
    async def set_format(user_id: int, template: str) -> FilenameFormat:
        saved = await db.set_filename_format(user_id, template.strip())
        logger.info(f"Updated filename format for user {user_id}: {template}")
        return saved

    @staticmethod
    async def clear_format(user_id: int) -> None:
        await db.clear_filename_format(user_id)
        logger.info(f"Cleared filename format for user {user_id}")

    @staticmethod
    def get_default_template() -> str:
        return TemplateEngine.DEFAULT_FILENAME_TEMPLATE
