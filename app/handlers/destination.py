"""
Destination, Sticker, and Quality direct command handlers.
"""

from __future__ import annotations

from pyrogram import Client, filters
from pyrogram.types import Message

from app.config import config
from app.encoder.presets import normalize_quality_name
from app.encoder.profiles import PROFILES, get_profile
from app.managers.destination_manager import DestinationManager
from app.managers.settings_manager import SettingsManager
from app.managers.sticker_manager import StickerManager


async def set_destination_cmd(client: Client, message: Message) -> None:
    user_id = message.from_user.id if message.from_user else 0
    if not config.is_authorized(user_id):
        return

    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        await message.reply_text(
            "⚠️ **Usage:** `/setdestination <@channel_username or chat_id>`\n\n"
            "**Example:** `/setdestination @MyChannel`"
        )
        return

    dest = parts[1].strip()
    await DestinationManager.set_destination(user_id, dest)
    await message.reply_text(f"✅ **Default upload destination set to:** `{dest}`")


async def clear_destination_cmd(client: Client, message: Message) -> None:
    user_id = message.from_user.id if message.from_user else 0
    if not config.is_authorized(user_id):
        return

    await DestinationManager.clear_destination(user_id)
    await message.reply_text("🗑️ **Default destination cleared.** Encoded videos will upload to current chat.")


async def sticker_cmd(client: Client, message: Message) -> None:
    user_id = message.from_user.id if message.from_user else 0
    if not config.is_authorized(user_id):
        return

    # If replying to a sticker
    if message.reply_to_message and message.reply_to_message.sticker:
        sticker_id = message.reply_to_message.sticker.file_id
        await StickerManager.set_sticker(user_id, sticker_id)
        await message.reply_text("✅ **Post-encode completion sticker saved!**")
        return

    parts = message.text.split(maxsplit=1)
    if len(parts) > 1 and parts[1].strip() == "clear":
        await StickerManager.clear_sticker(user_id)
        await message.reply_text("🗑️ **Sticker configuration cleared.**")
        return

    await message.reply_text(
        "🏷️ **Sticker Configuration**\n\n"
        "Reply to any sticker with `/sticker` to configure it as the post-job completion sticker.\n"
        "To remove, type `/sticker clear`."
    )


async def set_quality_cmd(client: Client, message: Message) -> None:
    user_id = message.from_user.id if message.from_user else 0
    if not config.is_authorized(user_id):
        return

    parts = message.text.split()[1:]
    if not parts:
        await message.reply_text(
            "⚠️ **Usage:** `/setquality <quality1> <quality2> ...`\n\n"
            "**Example:** `/setquality 480p 720p 1080p`"
        )
        return

    norm_qualities = [normalize_quality_name(q) for q in parts]
    await SettingsManager.set_default_qualities(user_id, norm_qualities)
    await message.reply_text(f"✅ **Default qualities set to:** {', '.join(norm_qualities)}")


async def set_profile_cmd(client: Client, message: Message) -> None:
    user_id = message.from_user.id if message.from_user else 0
    if not config.is_authorized(user_id):
        return

    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        avail = ", ".join(PROFILES.keys())
        await message.reply_text(
            f"⚠️ **Usage:** `/setprofile <profile_name>`\n\n"
            f"**Available profiles:** `{avail}` (e.g. `/setprofile standard`, `/setprofile mobile`, `/setprofile hq`)"
        )
        return

    prof_key = parts[1].strip().lower()
    prof = get_profile(prof_key)
    await SettingsManager.set_default_profile(user_id, prof.name)
    await message.reply_text(f"✅ **Default profile set to:** {prof.display_name}")
