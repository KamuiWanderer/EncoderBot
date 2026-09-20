"""
Season and global Bölüm mapping command handlers.
"""

from __future__ import annotations

from pyrogram import Client, filters
from pyrogram.types import Message

from app.config import config
from app.metadata.season import SeasonManager


async def set_season_cmd(client: Client, message: Message) -> None:
    user_id = message.from_user.id if message.from_user else 0
    if not config.is_authorized(user_id):
        return

    parts = message.text.split()
    if len(parts) < 3 or not parts[1].isdigit() or not parts[2].isdigit():
        await message.reply_text(
            "⚠️ **Usage:** `/setseason <season_number> <global_bolum_start>`\n\n"
            "**Example:**\n`/setseason 2 11`\n\n"
            "This maps Season 2 Episode 1 → Bölüm 11, S02E02 → Bölüm 12, etc."
        )
        return

    season = int(parts[1])
    offset = int(parts[2])
    mapping = await SeasonManager.set_mapping(user_id, season, offset)

    await message.reply_text(
        f"✅ **Season Mapping Configured!**\n\n"
        f"• **Season {season} Episode 01** ➔ **Bölüm {offset:02d}**\n"
        f"• **Season {season} Episode 02** ➔ **Bölüm {offset + 1:02d}**\n"
        f"• **Season {season} Episode 10** ➔ **Bölüm {offset + 9:02d}**"
    )


async def view_seasons_cmd(client: Client, message: Message) -> None:
    user_id = message.from_user.id if message.from_user else 0
    if not config.is_authorized(user_id):
        return

    mappings = await SeasonManager.get_all_mappings(user_id)
    if not mappings:
        await message.reply_text(
            "📌 **No Season Mappings Configured.**\n\n"
            "By default, Season 1 uses Episode numbers as Bölüm numbers.\n"
            "To map Season 2 to start at Bölüm 11, type:\n`/setseason 2 11`"
        )
        return

    lines = ["📌 **Configured Season ➔ Bölüm Mappings:**\n"]
    for m in mappings:
        lines.append(f"• **Season {m.season}** starts at **Bölüm {m.global_bolum_offset:02d}**")
    await message.reply_text("\n".join(lines))
