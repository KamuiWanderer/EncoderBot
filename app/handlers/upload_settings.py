"""
Upload settings control panel and direct command handlers.
"""

from __future__ import annotations

from pyrogram import Client, filters
from pyrogram.types import CallbackQuery, Message

from app.config import config
from app.managers.upload_config_manager import UploadConfigManager
from app.utils.logging import logger
from app.utils.ui import (
    build_keyboard,
    cancel_button,
    danger_button,
    primary_button,
    success_button,
)

_pending_input: dict[int, str] = {}


async def upload_settings_cmd(client: Client, message: Message) -> None:
    """Handle /uploadsettings command."""
    user_id = message.from_user.id if message.from_user else 0
    if not config.is_authorized(user_id):
        return

    text, markup = await UploadConfigManager.build_upload_settings_ui(user_id)
    await message.reply_text(text, reply_markup=markup)


async def set_header_cmd(client: Client, message: Message) -> None:
    """Handle /setheader command."""
    user_id = message.from_user.id if message.from_user else 0
    if not config.is_authorized(user_id):
        return

    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        await message.reply_text(
            "⚠️ **Usage:** `/setheader <template>`\n\n"
            "**Example:**\n"
            "`/setheader 🎬 <b>{title}</b>\\n📌 Season {season} Episode {episode} (Bölüm {bolum})\\n✨ <b>{finale}</b>`\n\n"
            "**Supported Placeholders:**\n"
            "`{title}`, `{season}`, `{episode}`, `{bolum}`, `{season_episode}`, `{finale}`, `{year}`, `{lang}`"
        )
        return

    template = parts[1].strip()
    await UploadConfigManager.set_episode_header_template(user_id, template)
    await message.reply_text(f"✅ **Episode Header template saved and enabled!**\n\n{template}")


async def clear_header_cmd(client: Client, message: Message) -> None:
    """Handle /clearheader command."""
    user_id = message.from_user.id if message.from_user else 0
    if not config.is_authorized(user_id):
        return

    st = await UploadConfigManager.get_settings(user_id)
    st.episode_header_template = None
    st.episode_header_enabled = False
    from app.database.database import db
    await db.update_user_settings(st)
    await message.reply_text("🗑️ **Episode Header template cleared and disabled.**")


async def set_staging_cmd(client: Client, message: Message) -> None:
    """Handle /setstaging command."""
    user_id = message.from_user.id if message.from_user else 0
    if not config.is_authorized(user_id):
        return

    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        await message.reply_text(
            "⚠️ **Usage:** `/setstaging <@channel_username or chat_id>`\n\n"
            "**Example:** `/setstaging -1001234567890` or `/setstaging @MyStagingChannel`\n\n"
            "During Private Staging mode, encoded qualities are temporarily uploaded to this group to keep your local disk empty, then delivered in perfect order to your main channel."
        )
        return

    ch = parts[1].strip()
    await UploadConfigManager.set_staging_channel(user_id, ch)
    await message.reply_text(f"✅ **Private Staging Channel set to:** `{ch}`")


async def set_delay_cmd(client: Client, message: Message) -> None:
    """Handle /setdelay command."""
    user_id = message.from_user.id if message.from_user else 0
    if not config.is_authorized(user_id):
        return

    parts = message.text.split()
    if len(parts) < 2:
        await message.reply_text("⚠️ **Usage:** `/setdelay <seconds>` (e.g. `/setdelay 1.0` or `/setdelay 2.0`)")
        return

    try:
        val = float(parts[1])
        await UploadConfigManager.set_send_delay(user_id, val)
        await message.reply_text(f"✅ **Post-upload delay set to:** `{val:.1f}s`")
    except ValueError:
        await message.reply_text("⚠️ Invalid number format. Please provide seconds like `1.0` or `2.0`.")


async def handle_upload_settings_callbacks(client: Client, query: CallbackQuery) -> None:
    user_id = query.from_user.id
    data = query.data

    if data == "upset:main" or data == "nav:uploadsettings":
        text, markup = await UploadConfigManager.build_upload_settings_ui(user_id)
        await query.message.edit_text(text, reply_markup=markup)

    elif data == "upset:timing":
        st = await UploadConfigManager.get_settings(user_id)
        new_timing = "immediate" if st.upload_timing == "staging" else "staging"
        await UploadConfigManager.set_upload_timing(user_id, new_timing)
        await query.answer(f"Upload Timing switched to: {new_timing.capitalize()}", show_alert=False)
        text, markup = await UploadConfigManager.build_upload_settings_ui(user_id)
        await query.message.edit_text(text, reply_markup=markup)

    elif data == "upset:header":
        enabled = await UploadConfigManager.toggle_episode_header(user_id)
        status_txt = "Enabled" if enabled else "Disabled"
        await query.answer(f"Episode Header: {status_txt}", show_alert=False)
        text, markup = await UploadConfigManager.build_upload_settings_ui(user_id)
        await query.message.edit_text(text, reply_markup=markup)

    elif data == "upset:sticker":
        text = (
            "🏷️ **Sticker Dispatch Settings**\n\n"
            "Choose when to dispatch the completion sticker:"
        )
        buttons = [
            [primary_button("📌 Send Per Episode (After each episode)", "upset:set_stick:per_episode")],
            [primary_button("📦 Send Per Job (Once at the end)", "upset:set_stick:per_job")],
            [danger_button("🚫 Disable Sticker Dispatch", "upset:set_stick:disabled")],
            [primary_button("↩️ Back", "upset:main")],
        ]
        await query.message.edit_text(text, reply_markup=build_keyboard(buttons))

    elif data.startswith("upset:set_stick:"):
        mode = data.split(":")[-1]
        await UploadConfigManager.set_sticker_mode(user_id, mode)
        await query.answer(f"Sticker mode: {mode.replace('_', ' ').title()}", show_alert=False)
        text, markup = await UploadConfigManager.build_upload_settings_ui(user_id)
        await query.message.edit_text(text, reply_markup=markup)

    elif data == "upset:dest":
        text = (
            "📤 **Destination Mode Settings**\n\n"
            "Choose how videos are routed to channels:"
        )
        buttons = [
            [primary_button("📢 Default Channel (Auto-send)", "upset:set_dest:default")],
            [primary_button("❓ Ask Every Time (Prompt in wizard)", "upset:set_dest:ask")],
            [primary_button("🪞 Multi-Channel (Mirror to Backup)", "upset:set_dest:multi")],
            [primary_button("↩️ Back", "upset:main")],
        ]
        await query.message.edit_text(text, reply_markup=build_keyboard(buttons))

    elif data.startswith("upset:set_dest:"):
        mode = data.split(":")[-1]
        await UploadConfigManager.set_destination_mode(user_id, mode)
        await query.answer(f"Destination mode: {mode.capitalize()}", show_alert=False)
        text, markup = await UploadConfigManager.build_upload_settings_ui(user_id)
        await query.message.edit_text(text, reply_markup=markup)

    elif data == "upset:delay":
        text = (
            "⏳ **Send Delay Configuration (Anti-Flood)**\n\n"
            "Select the delay duration between consecutive uploads:"
        )
        buttons = [
            [primary_button("⚡ 0.5s (Fast)", "upset:set_delay:0.5"), primary_button("⏱ 1.0s (Standard)", "upset:set_delay:1.0")],
            [primary_button("🛡 1.5s (Safe)", "upset:set_delay:1.5"), primary_button("🐢 2.0s (Slow)", "upset:set_delay:2.0")],
            [primary_button("🔒 3.0s (Maximum Anti-Flood)", "upset:set_delay:3.0")],
            [primary_button("↩️ Back", "upset:main")],
        ]
        await query.message.edit_text(text, reply_markup=build_keyboard(buttons))

    elif data.startswith("upset:set_delay:"):
        del_val = float(data.split(":")[-1])
        await UploadConfigManager.set_send_delay(user_id, del_val)
        await query.answer(f"Delay set to: {del_val:.1f}s", show_alert=False)
        text, markup = await UploadConfigManager.build_upload_settings_ui(user_id)
        await query.message.edit_text(text, reply_markup=markup)

    elif data == "upset:staging":
        _pending_input[user_id] = "staging_channel"
        await query.message.edit_text(
            "🔒 **Set Private Staging Channel**\n\n"
            "Please send the Chat ID (e.g. `-1001234567890`) or username (e.g. `@MyStagingChannel`) of your private staging group/channel.\n\n"
            "The bot must be an administrator in that channel.",
            reply_markup=build_keyboard([[cancel_button("upset:main")]]),
        )


async def handle_upload_settings_text(client: Client, message: Message) -> bool:
    """Handles text input when configuring staging channel."""
    user_id = message.from_user.id if message.from_user else 0
    if _pending_input.get(user_id) == "staging_channel":
        del _pending_input[user_id]
        ch = (message.text or "").strip()
        await UploadConfigManager.set_staging_channel(user_id, ch)
        await message.reply_text(f"✅ **Private Staging Channel configured:** `{ch}`")
        return True
    return False
