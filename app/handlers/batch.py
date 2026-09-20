"""
Batch Encoding & Hyperlinked Review Handlers.
Supports multi-video drops in private chats and Admin groups with clickable verification links.
"""

from __future__ import annotations

from typing import Any, Optional
from pyrogram import Client, filters
from pyrogram.enums import ParseMode
from pyrogram.types import CallbackQuery, Message

from app.config import config
from app.database.database import db
from app.encoder.presets import STANDARD_QUALITIES, sort_qualities_ascending
from app.jobs.job import create_job
from app.jobs.queue import JobQueueWorker
from app.managers.batch_manager import BatchSession, BatchVideoItem, batch_manager
from app.managers.caption_manager import CaptionManager
from app.managers.filename_manager import FilenameManager
from app.managers.thumbnail_manager import ThumbnailManager
from app.managers.upload_config_manager import UploadConfigManager
from app.metadata.parser import MetadataParser
from app.metadata.season import SeasonManager
from app.telegram.links import TelegramLinkParser
from app.telegram.media import MediaExtractor
from app.utils.logging import logger
from app.utils.ui import (
    build_keyboard,
    cancel_button,
    danger_button,
    primary_button,
    success_button,
)


async def batch_cmd(client: Client, message: Message) -> None:
    """Handle /batch command."""
    user_id = message.from_user.id if message.from_user else 0
    if not config.is_authorized(user_id):
        return

    text = (
        "📦 **BATCH VIDEO ENCODER WIZARD**\n\n"
        "To encode a batch of multiple episodes at once:\n\n"
        "1. **Option A (Forward / Drop):** Forward or upload multiple video files directly into this chat or group.\n"
        "2. **Option B (Link Range):** Send `/batch <start_link> <end_link>` to scan a channel range.\n\n"
        "**Example Link Range:**\n"
        "`/batch https://t.me/channel/100 https://t.me/channel/105`"
    )

    parts = message.text.split()
    if len(parts) >= 3:
        start_link = parts[1].strip()
        end_link = parts[2].strip()
        await _process_batch_link_range(client, message, start_link, end_link)
        return

    await message.reply_text(text)


async def _process_batch_link_range(client: Client, message: Message, start_link: str, end_link: str) -> None:
    user_id = message.from_user.id if message.from_user else 0
    p_start = TelegramLinkParser.parse(start_link)
    p_end = TelegramLinkParser.parse(end_link)

    if not p_start or not p_end:
        await message.reply_text("⚠️ Invalid Telegram message link format.")
        return

    start_id = p_start.message_id
    end_id = p_end.message_id
    chat_id = p_start.chat_id

    if start_id > end_id:
        start_id, end_id = end_id, start_id

    wait_msg = await message.reply_text(f"🔍 **Scanning channel videos from ID {start_id} to {end_id}...**")

    st = await db.get_user_settings(user_id)
    fn_saved = await db.get_active_filename_format(user_id)
    cap_saved = await db.get_active_caption_format(user_id)

    sess = batch_manager.create_session(user_id=user_id, chat_id=message.chat.id)
    sess.selected_qualities = list(st.default_qualities)
    sess.encoding_profile = st.default_profile
    sess.destination_chat = st.default_destination
    sess.staging_channel = st.staging_channel
    sess.upload_timing = st.upload_timing
    sess.episode_header_enabled = st.episode_header_enabled
    sess.episode_header_template = st.episode_header_template
    sess.sticker_id = st.sticker_id
    sess.sticker_mode = st.sticker_mode
    sess.send_delay = st.send_delay
    sess.filename_template = fn_saved.template if fn_saved else None
    sess.caption_template = cap_saved.template if cap_saved else None

    msg_ids = list(range(start_id, end_id + 1))
    messages = await client.get_messages(chat_id=chat_id, message_ids=msg_ids)
    if not isinstance(messages, list):
        messages = [messages]

    item_idx = 1
    for msg in messages:
        if not msg:
            continue
        media_info = MediaExtractor.extract_video_media(msg)
        if not media_info:
            continue

        raw_title = msg.caption or media_info.file_name
        meta = MetadataParser.parse(raw_title)

        global_bolum = await SeasonManager.get_bolum_for_episode(user_id, meta.season, meta.episode)
        finale_str = await SeasonManager.check_season_finale(user_id, meta.season, global_bolum)

        ep_num = meta.episode or global_bolum
        thumb_target = await ThumbnailManager.get_thumbnail_for_episode(user_id, ep_num)
        thumb_link = await ThumbnailManager.get_thumbnail_link_for_episode(user_id, ep_num)

        # Build message source link
        if str(chat_id).startswith("-100"):
            raw_id = str(chat_id)[4:]
            src_url = f"https://t.me/c/{raw_id}/{msg.id}"
        elif str(chat_id).startswith("@"):
            src_url = f"https://t.me/{str(chat_id)[1:]}/{msg.id}"
        else:
            src_url = f"https://t.me/c/{chat_id}/{msg.id}"

        item = BatchVideoItem(
            index=item_idx,
            source_type="telegram_link",
            source_link=src_url,
            source_title=media_info.file_name,
            source_size_bytes=media_info.file_size_bytes,
            parsed_metadata=meta,
            global_bolum=global_bolum,
            season_finale=finale_str,
            thumbnail_target=thumb_target,
            thumbnail_link=thumb_link,
        )
        sess.items.append(item)
        item_idx += 1

    if not sess.items:
        await wait_msg.edit_text("❌ No supported video files found in the specified range.")
        batch_manager.clear_session(user_id)
        return

    # Render hyperlinked review card
    text, markup = await batch_manager.build_review_card(sess)
    await wait_msg.edit_text(text, reply_markup=markup, parse_mode=ParseMode.HTML)
    sess.status_message_id = wait_msg.id


async def handle_batch_callbacks(client: Client, query: CallbackQuery, worker: JobQueueWorker) -> None:
    user_id = query.from_user.id
    data = query.data

    sess = batch_manager.get_user_session(user_id)
    if not sess:
        await query.answer("Batch session expired.", show_alert=True)
        return

    if data.startswith("batch:start:"):
        # Queue all jobs in the batch
        count = len(sess.items)
        for item in sess.items:
            job = create_job(
                user_id=user_id,
                chat_id=sess.chat_id,
                source_title=item.source_title,
                source_type=item.source_type,
                source_file_id=item.source_file_id,
                source_link=item.source_link,
                source_size_bytes=item.source_size_bytes,
                metadata=item.parsed_metadata.__dict__,
                selected_qualities=sess.selected_qualities,
                filename_template=sess.filename_template,
                caption_template=sess.caption_template,
                thumbnail_mode="multiple" if item.thumbnail_target else "none",
                destination_chat_id=sess.destination_chat,
                sticker_id=sess.sticker_id,
                encoding_profile=sess.encoding_profile,
            )
            # Attach batch and upload configuration
            job.batch_id = sess.batch_id
            job.batch_index = item.index
            job.batch_total = count
            job.upload_timing = sess.upload_timing
            job.staging_channel = sess.staging_channel
            job.episode_header_enabled = sess.episode_header_enabled
            job.episode_header_template = sess.episode_header_template
            job.sticker_mode = sess.sticker_mode
            job.send_delay = sess.send_delay
            job.thumbnail_link = item.thumbnail_link

            await worker.enqueue_job(job)

        batch_manager.clear_session(user_id)
        await query.message.edit_text(
            f"🚀 **Batch Queued Successfully!**\n\n"
            f"📦 **Total Episodes:** `{count}`\n"
            f"🎞 **Qualities per Episode:** `{', '.join(sess.selected_qualities)}`\n"
            f"⏱ **Upload Timing:** `{sess.upload_timing.capitalize()}`\n"
            f"📤 **Destination:** `{sess.destination_chat or 'Current Chat'}`\n\n"
            "Worker is now sequentially downloading and encoding each episode in order."
        )

    elif data.startswith("batch:qualities:"):
        text = "🎞 **Select Qualities for this Batch:**"
        rows: list[list[Any]] = []
        row: list[Any] = []
        for q in STANDARD_QUALITIES:
            chk = "☑" if q in sess.selected_qualities else "☐"
            row.append(primary_button(f"{chk} {q}", f"batch:toggle_q:{sess.batch_id}:{q}"))
            if len(row) == 2:
                rows.append(row)
                row = []
        if row:
            rows.append(row)
        rows.append([primary_button("✨ Select All", f"batch:q_all:{sess.batch_id}"), success_button("✅ Done", f"batch:refresh:{sess.batch_id}")])
        await query.message.edit_text(text, reply_markup=build_keyboard(rows))

    elif data.startswith("batch:toggle_q:"):
        parts = data.split(":")
        q = parts[-1]
        if q in sess.selected_qualities:
            if len(sess.selected_qualities) > 1:
                sess.selected_qualities.remove(q)
            else:
                await query.answer("At least 1 quality must be selected!", show_alert=True)
                return
        else:
            sess.selected_qualities.append(q)
        sess.selected_qualities = sort_qualities_ascending(sess.selected_qualities)

        # Redraw quality selector
        text = "🎞 **Select Qualities for this Batch:**"
        rows: list[list[Any]] = []
        row: list[Any] = []
        for q_item in STANDARD_QUALITIES:
            chk = "☑" if q_item in sess.selected_qualities else "☐"
            row.append(primary_button(f"{chk} {q_item}", f"batch:toggle_q:{sess.batch_id}:{q_item}"))
            if len(row) == 2:
                rows.append(row)
                row = []
        if row:
            rows.append(row)
        rows.append([primary_button("✨ Select All", f"batch:q_all:{sess.batch_id}"), success_button("✅ Done", f"batch:refresh:{sess.batch_id}")])
        await query.message.edit_text(text, reply_markup=build_keyboard(rows))

    elif data.startswith("batch:q_all:"):
        sess.selected_qualities = list(STANDARD_QUALITIES)
        text, markup = await batch_manager.build_review_card(sess)
        await query.message.edit_text(text, reply_markup=markup, parse_mode=ParseMode.HTML)

    elif data.startswith("batch:refresh:"):
        text, markup = await batch_manager.build_review_card(sess)
        await query.message.edit_text(text, reply_markup=markup, parse_mode=ParseMode.HTML)

    elif data.startswith("batch:toggle_view:"):
        sess.is_detailed_view = not sess.is_detailed_view
        text, markup = await batch_manager.build_review_card(sess)
        await query.message.edit_text(text, reply_markup=markup, parse_mode=ParseMode.HTML)

    elif data.startswith("batch:cancel:"):
        batch_manager.clear_session(user_id)
        await query.message.edit_text("❌ **Batch encode cancelled.**")
