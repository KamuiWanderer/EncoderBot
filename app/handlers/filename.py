"""
Filename configuration command handlers.
"""

from __future__ import annotations

from pyrogram import Client, filters
from pyrogram.types import Message

from app.config import config
from app.managers.filename_manager import FilenameManager


async def set_filename_cmd(client: Client, message: Message) -> None:
    user_id = message.from_user.id if message.from_user else 0
    if not config.is_authorized(user_id):
        return

    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        await message.reply_text(
            "⚠️ **Usage:** `/setfilename <template>`\n\n"
            "**Example:**\n`/setfilename {title} S{season} E{episode} {quality}.mp4`\n\n"
            "**Supported Placeholders:**\n"
            "`{title}`, `{season}`, `{episode}`, `{bolum}`, `{season_episode}`, `{quality}`, `{lang}`, `{source}`, `{year}`, `{sub}`, `{finale}`, `{part}`, `{width}`, `{height}`, `{codec}`, `{ext}`"
        )
        return

    template = parts[1].strip()
    saved = await FilenameManager.set_format(user_id, template)
    await message.reply_text(
        f"✅ **Filename template updated:**\n\n`{saved.template}`"
    )


async def view_filename_cmd(client: Client, message: Message) -> None:
    user_id = message.from_user.id if message.from_user else 0
    if not config.is_authorized(user_id):
        return

    saved = await FilenameManager.get_format(user_id)
    if saved:
        await message.reply_text(f"📁 **Current Filename Template:**\n\n`{saved.template}`")
    else:
        await message.reply_text(
            f"📁 **No custom template configured.**\n\n"
            f"Default:\n`{FilenameManager.get_default_template()}`"
        )


async def clear_filename_cmd(client: Client, message: Message) -> None:
    user_id = message.from_user.id if message.from_user else 0
    if not config.is_authorized(user_id):
        return

    await FilenameManager.clear_format(user_id)
    await message.reply_text("🗑️ **Custom filename template cleared.** Using default format.")
