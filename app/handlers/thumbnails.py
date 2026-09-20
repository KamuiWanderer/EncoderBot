"""
Thumbnail management command and callback handlers with message link recording.
Supports Single mode and Multiple episode range scanning from Telegram channels.
"""

from __future__ import annotations

from pathlib import Path
from pyrogram import Client, filters
from pyrogram.types import CallbackQuery, Message

from app.config import config
from app.managers.thumbnail_manager import ThumbnailManager
from app.metadata.parser import MetadataParser
from app.telegram.links import TelegramLinkParser
from app.utils.logging import logger
from app.utils.ui import (
    build_keyboard,
    cancel_button,
    danger_button,
    primary_button,
    success_button,
)

_pending_scans: dict[int, dict] = {}


async def thumbs_cmd(client: Client, message: Message) -> None:
    """Handle /thumbs or /thumb command."""
    user_id = message.from_user.id if message.from_user else 0
    if not config.is_authorized(user_id):
        return

    active_cfg = await ThumbnailManager.get_active_config(user_id)
    status_str = "No active configuration."
    if active_cfg:
        if active_cfg.mode == "single":
            status_str = "Single thumbnail active."
        else:
            status_str = f"Multiple thumbnails active ({len(active_cfg.mapping_data)} episodes mapped)."

    text = (
        "🖼️ **Thumbnail Configuration Manager**\n\n"
        f"**Status:** {status_str}\n\n"
        "Choose an option below:"
    )

    buttons = [
        [primary_button("🖼️ Single Thumbnail", "th_menu:single")],
        [primary_button("📚 Multiple Thumbnails (Range)", "th_menu:multiple")],
        [primary_button("👁 View Current Config", "th_menu:view")] if active_cfg else [],
        [danger_button("🗑️ Clear Thumbnail", "th_menu:clear")] if active_cfg else [],
        [cancel_button("th_menu:close")],
    ]
    filtered = [b for b in buttons if b]
    await message.reply_text(text, reply_markup=build_keyboard(filtered))


async def clear_thumbs_cmd(client: Client, message: Message) -> None:
    user_id = message.from_user.id if message.from_user else 0
    if not config.is_authorized(user_id):
        return

    await ThumbnailManager.clear_thumbnail_config(user_id)
    await message.reply_text("🗑️ **Thumbnail configuration cleared.**")


async def handle_thumbnail_callbacks(client: Client, query: CallbackQuery) -> None:
    """Handle thumbnail menu inline buttons."""
    user_id = query.from_user.id
    data = query.data

    if data == "th_menu:single":
        _pending_scans[user_id] = {"step": "await_single"}
        await query.message.edit_text(
            "🖼️ **Single Thumbnail Mode**\n\n"
            "Please send or forward the photo you want to use as the thumbnail for all encoded videos.",
            reply_markup=build_keyboard([[cancel_button("th_menu:cancel")]]),
        )

    elif data == "th_menu:multiple":
        _pending_scans[user_id] = {"step": "await_start_link"}
        await query.message.edit_text(
            "📚 **Multiple Thumbnails Range Setup**\n\n"
            "Please send the **START Telegram message link** containing the first episode thumbnail.\n\n"
            "**Example:** `https://t.me/channel_name/100` or `https://t.me/c/1234567890/100`",
            reply_markup=build_keyboard([[cancel_button("th_menu:cancel")]]),
        )

    elif data == "th_menu:view":
        cfg = await ThumbnailManager.get_active_config(user_id)
        if not cfg:
            await query.message.edit_text("ℹ️ No thumbnail configuration currently active.")
            return

        if cfg.mode == "single":
            await query.message.edit_text("🖼️ **Active Configuration:** Single Thumbnail.")
        else:
            mapping_int = {int(k): v for k, v in cfg.mapping_data.items() if k.isdigit()}
            preview_text = ThumbnailManager.render_detailed_preview(
                channel=cfg.channel_username_or_id or "Channel",
                mapping=mapping_int,
            )
            await query.message.edit_text(preview_text, reply_markup=build_keyboard([[danger_button("🗑️ Clear", "th_menu:clear")]]))

    elif data == "th_menu:clear":
        await ThumbnailManager.clear_thumbnail_config(user_id)
        await query.message.edit_text("🗑️ **Thumbnail configuration cleared successfully.**")

    elif data == "th_menu:cancel" or data == "th_menu:close":
        if user_id in _pending_scans:
            del _pending_scans[user_id]
        if data == "th_menu:close":
            await query.message.delete()
        else:
            await query.message.edit_text("❌ Thumbnail setup cancelled.")

    elif data == "th_scan:save":
        scan_data = _pending_scans.get(user_id)
        if scan_data and "mapping" in scan_data:
            await ThumbnailManager.save_multiple_thumbnails(
                user_id=user_id,
                channel=scan_data["channel"],
                start_id=scan_data["start_id"],
                end_id=scan_data["end_id"],
                mapping=scan_data["mapping"],
                mapping_links=scan_data.get("mapping_links"),
            )
            del _pending_scans[user_id]
            await query.message.edit_text("✅ **Multiple thumbnail configuration saved successfully!**")
        else:
            await query.message.edit_text("⚠️ No pending scan data found to save.")


async def handle_thumbnail_messages(client: Client, message: Message) -> bool:
    """Handles photos or links sent while configuring thumbnails."""
    user_id = message.from_user.id if message.from_user else 0
    state = _pending_scans.get(user_id)
    if not state:
        return False

    step = state.get("step")

    if step == "await_single":
        if message.photo:
            file_id = message.photo.file_id
            await ThumbnailManager.set_single_thumbnail(user_id=user_id, file_id=file_id)
            del _pending_scans[user_id]
            await message.reply_text("✅ **Single thumbnail saved successfully!** It will be applied to all your encoded videos.")
            return True
        else:
            await message.reply_text("⚠️ Please send a valid photo.")
            return True

    elif step == "await_start_link":
        text = message.text or ""
        parsed = TelegramLinkParser.parse(text)
        if not parsed:
            await message.reply_text("⚠️ Invalid Telegram link. Please send a valid message link (e.g. `https://t.me/channel/100`).")
            return True

        state["start_link"] = text
        state["chat_id"] = parsed.chat_id
        state["start_id"] = parsed.message_id
        state["step"] = "await_end_link"

        await message.reply_text(
            f"✅ **Start message link recorded (ID: {parsed.message_id})**\n\n"
            "Now send the **END Telegram message link** to define the range."
        )
        return True

    elif step == "await_end_link":
        text = message.text or ""
        parsed = TelegramLinkParser.parse(text)
        if not parsed:
            await message.reply_text("⚠️ Invalid Telegram link. Please send a valid message link (e.g. `https://t.me/channel/124`).")
            return True

        start_id = state["start_id"]
        end_id = parsed.message_id
        chat_id = state["chat_id"]

        if start_id > end_id:
            start_id, end_id = end_id, start_id

        wait_msg = await message.reply_text(f"🔍 **Scanning messages from ID {start_id} to {end_id}...**")

        mapping: dict[int, str] = {}
        mapping_links: dict[int, str] = {}
        curr_ep = 1

        try:
            msg_ids = list(range(start_id, end_id + 1))
            messages = await client.get_messages(chat_id=chat_id, message_ids=msg_ids)
            if not isinstance(messages, list):
                messages = [messages]

            for msg in messages:
                if not msg:
                    continue
                photo_id = None
                if msg.photo:
                    photo_id = msg.photo.file_id
                elif msg.document and (msg.document.mime_type or "").startswith("image/"):
                    photo_id = msg.document.file_id

                if photo_id:
                    caption = msg.caption or msg.text or ""
                    meta = MetadataParser.parse(caption)
                    ep_num = meta.episode or curr_ep
                    mapping[ep_num] = photo_id

                    # Build link to message
                    if str(chat_id).startswith("-100"):
                        raw_id = str(chat_id)[4:]
                        link_url = f"https://t.me/c/{raw_id}/{msg.id}"
                    elif str(chat_id).startswith("@"):
                        link_url = f"https://t.me/{str(chat_id)[1:]}/{msg.id}"
                    else:
                        link_url = f"https://t.me/c/{chat_id}/{msg.id}"
                    mapping_links[ep_num] = link_url

                    curr_ep += 1

            state["mapping"] = mapping
            state["mapping_links"] = mapping_links
            state["start_id"] = start_id
            state["end_id"] = end_id
            state["channel"] = str(chat_id)

            preview_text = ThumbnailManager.render_detailed_preview(
                channel=str(chat_id),
                mapping=mapping,
            )

            buttons = [
                [success_button("✅ Save Configuration", "th_scan:save")],
                [danger_button("❌ Cancel", "th_menu:cancel")],
            ]

            await wait_msg.edit_text(preview_text, reply_markup=build_keyboard(buttons))
            return True

        except Exception as e:
            logger.error(f"Error scanning thumbnail messages: {e}")
            await wait_msg.edit_text(f"❌ **Error scanning channel range:** `{e}`")
            del _pending_scans[user_id]
            return True

    return False
