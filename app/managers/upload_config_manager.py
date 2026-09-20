"""
Upload and publishing configuration manager.
Manages timing (Immediate vs Staging), Episode Headers, Staging Channel, Sticker Dispatch, and Send Delays.
"""

from __future__ import annotations

import re
from typing import Optional
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.database.database import db
from app.database.models import UserSettings
from app.metadata.parser import ParsedMetadata
from app.utils.logging import logger
from app.utils.ui import (
    build_keyboard,
    cancel_button,
    danger_button,
    primary_button,
    success_button,
)


class UploadConfigManager:
    """Coordinates upload preferences, episode headers, and publishing strategies."""

    DEFAULT_EPISODE_HEADER = (
        "🎬 <b>{title}</b>\n\n"
        "📌 <b>Season:</b> {season} | <b>Episode:</b> {episode} (Bölüm {bolum})\n"
        "✨ <b>{finale}</b>"
    )

    @classmethod
    async def get_settings(cls, user_id: int) -> UserSettings:
        return await db.get_user_settings(user_id)

    @classmethod
    async def set_upload_timing(cls, user_id: int, timing: str) -> None:
        st = await db.get_user_settings(user_id)
        st.upload_timing = timing  # 'immediate' or 'staging'
        await db.update_user_settings(st)
        logger.info(f"Updated upload timing for user {user_id}: {timing}")

    @classmethod
    async def set_staging_channel(cls, user_id: int, channel: Optional[str]) -> None:
        st = await db.get_user_settings(user_id)
        st.staging_channel = channel.strip() if channel else None
        await db.update_user_settings(st)
        logger.info(f"Updated staging channel for user {user_id}: {channel}")

    @classmethod
    async def toggle_episode_header(cls, user_id: int) -> bool:
        st = await db.get_user_settings(user_id)
        st.episode_header_enabled = not st.episode_header_enabled
        await db.update_user_settings(st)
        return st.episode_header_enabled

    @classmethod
    async def set_episode_header_template(cls, user_id: int, template: str) -> None:
        st = await db.get_user_settings(user_id)
        st.episode_header_template = template.strip()
        st.episode_header_enabled = True
        await db.update_user_settings(st)
        logger.info(f"Updated episode header template for user {user_id}")

    @classmethod
    async def set_sticker_mode(cls, user_id: int, mode: str) -> None:
        st = await db.get_user_settings(user_id)
        st.sticker_mode = mode  # 'per_episode', 'per_job', 'disabled'
        await db.update_user_settings(st)

    @classmethod
    async def set_destination_mode(cls, user_id: int, mode: str) -> None:
        st = await db.get_user_settings(user_id)
        st.destination_mode = mode  # 'default', 'ask', 'multi'
        await db.update_user_settings(st)

    @classmethod
    async def set_send_delay(cls, user_id: int, delay: float) -> None:
        st = await db.get_user_settings(user_id)
        st.send_delay = max(0.2, min(10.0, delay))
        await db.update_user_settings(st)

    @classmethod
    def render_episode_header(
        cls,
        template: Optional[str],
        metadata: ParsedMetadata,
        calculated_bolum: Optional[int] = None,
        calculated_finale: Optional[str] = None,
    ) -> str:
        """Renders the HTML episode header message."""
        tmpl = template or cls.DEFAULT_EPISODE_HEADER
        s_val = metadata.formatted_season
        e_val = metadata.formatted_episode
        b_num = calculated_bolum if calculated_bolum is not None else metadata.bolum
        b_val = f"{b_num:02d}" if b_num is not None else e_val
        fin_val = calculated_finale if calculated_finale is not None else metadata.finale

        output = tmpl
        if fin_val:
            output = re.sub(r"\{finale\}", fin_val, output, flags=re.IGNORECASE)
        else:
            output = re.sub(r"\s*\|\s*\{finale\}", "", output, flags=re.IGNORECASE)
            output = re.sub(r"\{finale\}\s*\|\s*", "", output, flags=re.IGNORECASE)
            output = re.sub(r"✨\s*<b>\{finale\}</b>\n?", "", output, flags=re.IGNORECASE)
            output = re.sub(r"\{finale\}", "", output, flags=re.IGNORECASE)

        replacements = {
            "{title}": metadata.title,
            "{season}": s_val,
            "{episode}": e_val,
            "{bolum}": b_val,
            "{season_episode}": f"S{s_val} E{e_val}",
            "{year}": str(metadata.year) if metadata.year else "",
            "{lang}": metadata.lang or "",
        }

        for placeholder, value in replacements.items():
            pattern = re.compile(re.escape(placeholder), re.IGNORECASE)
            output = pattern.sub(value, output)

        return output.strip()

    # ---------------- UI BUILDER ----------------

    @classmethod
    async def build_upload_settings_ui(cls, user_id: int) -> tuple[str, InlineKeyboardMarkup]:
        st = await cls.get_settings(user_id)

        timing_str = "Private Staging (Ordered Delivery)" if st.upload_timing == "staging" else "Immediate (Save Disk)"
        header_str = "Enabled" if st.episode_header_enabled else "Disabled"
        sticker_str = st.sticker_mode.replace("_", " ").title()
        dest_str = st.destination_mode.capitalize()
        staging_str = st.staging_channel or "Not Set (Using Current Chat)"
        delay_str = f"{st.send_delay:.1f}s"

        text = (
            "⚙️ **UPLOAD & PUBLISHING CONFIGURATION**\n\n"
            "**Current Configuration:**\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"• **Upload Timing:** `{timing_str}`\n"
            f"• **Episode Header:** `{header_str}`\n"
            f"• **Sticker Dispatch:** `{sticker_str}`\n"
            f"• **Destination Mode:** `{dest_str}` (`{st.default_destination or 'Current Chat'}`)\n"
            f"• **Staging Channel:** `{staging_str}`\n"
            f"• **Send Delay:** `{delay_str}` (Anti-Flood)\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "Click a button below to configure:"
        )

        buttons = [
            [primary_button(f"⏱ Timing: {timing_str[:15]}...", "upset:timing"), primary_button(f"📌 Header: {header_str}", "upset:header")],
            [primary_button(f"🏷️ Sticker: {sticker_str}", "upset:sticker"), primary_button(f"📤 Dest: {dest_str}", "upset:dest")],
            [primary_button(f"⏳ Delay: {delay_str}", "upset:delay"), primary_button("🔒 Staging Group", "upset:staging")],
            [primary_button("↩️ Back to Settings", "nav:settings"), cancel_button("set_menu:close")],
        ]

        return text, build_keyboard(buttons)
