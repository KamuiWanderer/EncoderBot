"""
Caption configuration command handlers.
"""

from __future__ import annotations

from pyrogram import Client, filters
from pyrogram.types import Message

from app.config import config
from app.managers.caption_manager import CaptionManager


async def set_caption_cmd(client: Client, message: Message) -> None:
    user_id = message.from_user.id if message.from_user else 0
    if not config.is_authorized(user_id):
        return

    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        await message.reply_text(
            "⚠️ **Usage:** `/setcaption <template>`\n\n"
            "**Example:**\n"
            "`/setcaption 🎬 <b>{title}</b>\\n📌 Season {season} Episode {episode}\\n🎞 {quality}`\n\n"
            "**Supported Placeholders:**\n"
            "`{title}`, `{season}`, `{episode}`, `{bolum}`, `{season_episode}`, `{quality}`, `{lang}`, `{source}`, `{year}`, `{sub}`, `{finale}`, `{part}`, `{width}`, `{height}`, `{codec}`, `{ext}`"
        )
        return

    template = parts[1].strip()
    saved = await CaptionManager.set_format(user_id, template)
    await message.reply_text(
        f"✅ **Caption template updated:**\n\n{saved.template}"
    )


async def view_caption_cmd(client: Client, message: Message) -> None:
    user_id = message.from_user.id if message.from_user else 0
    if not config.is_authorized(user_id):
        return

    saved = await CaptionManager.get_format(user_id)
    if saved:
        await message.reply_text(f"📝 **Current Caption Template:**\n\n{saved.template}")
    else:
        await message.reply_text(
            f"📝 **No custom caption configured.**\n\n"
            f"Default:\n{CaptionManager.get_default_template()}"
        )


async def clear_caption_cmd(client: Client, message: Message) -> None:
    user_id = message.from_user.id if message.from_user else 0
    if not config.is_authorized(user_id):
        return

    await CaptionManager.clear_format(user_id)
    await message.reply_text("🗑️ **Custom caption template cleared.**")
