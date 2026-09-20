"""
Decoupled Thumbnail Manager subsystem with Link Mapping for Hyperlinked Previews.
Supports Single mode, Multiple episode range scanning, detailed previews, and JPEG normalization.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional
from PIL import Image

from app.config import config
from app.database.database import db
from app.database.models import ThumbnailConfig
from app.metadata.parser import MetadataParser
from app.utils.files import safe_delete
from app.utils.logging import logger


class ThumbnailManager:
    """Manages persistent thumbnail configurations, range scans, and episode lookups."""

    @staticmethod
    def normalize_thumbnail_image(source_path: str | Path, output_path: Optional[str | Path] = None) -> str:
        """
        Ensures thumbnail image is a valid JPEG with Telegram-compliant aspect ratio and dimensions.
        """
        src = Path(source_path).resolve()
        dst = Path(output_path).resolve() if output_path else src.with_suffix(".jpg")

        with Image.open(src) as img:
            if img.mode != "RGB":
                img = img.convert("RGB")
            img.thumbnail((320, 320), Image.Resampling.LANCZOS)
            img.save(dst, "JPEG", quality=90, optimize=True)

        return str(dst)

    @classmethod
    async def get_active_config(cls, user_id: int) -> Optional[ThumbnailConfig]:
        """Fetch the active thumbnail configuration for a user."""
        return await db.get_active_thumbnail_config(user_id)

    @classmethod
    async def set_single_thumbnail(
        cls,
        user_id: int,
        file_id: Optional[str] = None,
        file_path: Optional[str] = None,
    ) -> ThumbnailConfig:
        """Configure a single static thumbnail for all uploads."""
        cfg = ThumbnailConfig(
            id=None,
            user_id=user_id,
            mode="single",
            single_file_id=file_id,
            single_file_path=file_path,
            mapping_data={},
            mapping_links={},
            is_active=True,
        )
        saved = await db.save_thumbnail_config(cfg)
        logger.info(f"Saved single thumbnail configuration for user {user_id}")
        return saved

    @classmethod
    async def save_multiple_thumbnails(
        cls,
        user_id: int,
        channel: str,
        start_id: int,
        end_id: int,
        mapping: dict[int, str],
        mapping_links: Optional[dict[int, str]] = None,
    ) -> ThumbnailConfig:
        """Configure multiple episode thumbnails from scanned range."""
        mapping_str_keys = {str(k): v for k, v in mapping.items()}
        mapping_links_str = {str(k): v for k, v in (mapping_links or {}).items()}
        cfg = ThumbnailConfig(
            id=None,
            user_id=user_id,
            mode="multiple",
            channel_username_or_id=channel,
            start_message_id=start_id,
            end_message_id=end_id,
            mapping_data=mapping_str_keys,
            mapping_links=mapping_links_str,
            is_active=True,
        )
        saved = await db.save_thumbnail_config(cfg)
        logger.info(f"Saved multiple thumbnail configuration ({len(mapping)} episodes) for user {user_id}")
        return saved

    @classmethod
    async def clear_thumbnail_config(cls, user_id: int) -> None:
        """Clear/disable active thumbnail configuration."""
        await db.clear_thumbnail_config(user_id)
        logger.info(f"Cleared thumbnail configuration for user {user_id}")

    @classmethod
    async def get_thumbnail_for_episode(
        cls,
        user_id: int,
        episode: Optional[int],
    ) -> Optional[str]:
        """
        Determines the appropriate thumbnail (file_id or path) for a given episode.
        """
        cfg = await cls.get_active_config(user_id)
        if not cfg or not cfg.is_active:
            return None

        if cfg.mode == "single":
            return cfg.single_file_id or cfg.single_file_path

        if cfg.mode == "multiple" and episode is not None:
            ep_key = str(episode)
            ep_key_pad = f"{episode:02d}"
            ep_key_unpad = str(int(episode))

            thumb = cfg.mapping_data.get(ep_key) or cfg.mapping_data.get(ep_key_pad) or cfg.mapping_data.get(ep_key_unpad)
            if thumb:
                return thumb

        return None

    @classmethod
    async def get_thumbnail_link_for_episode(
        cls,
        user_id: int,
        episode: Optional[int],
    ) -> Optional[str]:
        """
        Retrieves the clickable Telegram message link for the episode's thumbnail photo.
        """
        cfg = await cls.get_active_config(user_id)
        if not cfg or not cfg.is_active or cfg.mode != "multiple" or episode is None:
            return None

        ep_key = str(episode)
        ep_key_pad = f"{episode:02d}"
        ep_key_unpad = str(int(episode))

        return cfg.mapping_links.get(ep_key) or cfg.mapping_links.get(ep_key_pad) or cfg.mapping_links.get(ep_key_unpad)

    @staticmethod
    def render_detailed_preview(
        channel: str,
        mapping: dict[int, str],
        max_episodes_to_show: int = 30,
    ) -> str:
        """
        Renders detailed verification preview.
        """
        if not mapping:
            return (
                "🖼️ <b>THUMBNAIL PREVIEW</b>\n\n"
                f"<b>Mode:</b> Multiple\n"
                f"<b>Source:</b> <code>{channel}</code>\n\n"
                "❌ <b>No valid photo thumbnails detected in specified range.</b>"
            )

        highest = max(mapping.keys()) if mapping else 0
        total_found = len(mapping)

        lines = [
            "🖼️ <b>THUMBNAIL PREVIEW</b>\n",
            "<b>Mode:</b> Multiple",
            f"<b>Source:</b> <code>{channel}</code>",
            f"<b>Total detected:</b> <code>{total_found}</code>\n",
            "<code>━━━━━━━━━━━━━━━━━━━━</code>",
        ]

        limit = min(highest, max_episodes_to_show)
        for ep in range(1, limit + 1):
            if ep in mapping:
                lines.append(f"Episode {ep:02d} → ✅ Found")
            else:
                lines.append(f"Episode {ep:02d} → ❌ Missing")

        if highest > max_episodes_to_show:
            remaining_found = sum(1 for ep in range(max_episodes_to_show + 1, highest + 1) if ep in mapping)
            lines.append(f"... and {highest - max_episodes_to_show} more episodes ({remaining_found} found)")

        lines.append("<code>━━━━━━━━━━━━━━━━━━━━</code>")
        return "\n".join(lines)
