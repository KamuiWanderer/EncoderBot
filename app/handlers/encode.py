"""
Interactive /encode wizard and media reception handlers.
Coordinates:
1. Video / forward / link input
2. Filename configuration step
3. Caption configuration step
4. Thumbnail configuration step
5. Quality multi-select step
6. Profile step
7. Destination step
8. Complete Final Preview
9. START dispatch into single-download sequential queue
"""

from __future__ import annotations

from typing import Optional
from pyrogram import Client, filters
from pyrogram.types import CallbackQuery, Message

from app.config import config
from app.database.database import db
from app.encoder.presets import STANDARD_QUALITIES, sort_qualities_ascending
from app.jobs.job import create_job
from app.jobs.queue import JobQueueWorker
from app.managers.caption_manager import CaptionManager
from app.managers.destination_manager import DestinationManager
from app.managers.encode_manager import WizardSession, wizard_manager
from app.managers.filename_manager import FilenameManager
from app.managers.thumbnail_manager import ThumbnailManager
from app.metadata.parser import MetadataParser
from app.metadata.season import SeasonManager
from app.telegram.links import TelegramLinkParser
from app.telegram.media import MediaExtractor
from app.utils.logging import logger
from app.utils.ui import build_keyboard, cancel_button, primary_button, success_button


async def encode_cmd(client: Client, message: Message) -> None:
    """Handle /encode command."""
    user_id = message.from_user.id if message.from_user else 0
    if not config.is_authorized(user_id):
        return

    # Check if user sent command in reply to a video
    if message.reply_to_message:
        media_info = MediaExtractor.extract_video_media(message.reply_to_message)
        if media_info:
            await _start_wizard_with_media(client, message, media_info, message.reply_to_message)
            return

    # Check if command has a link argument: /encode https://t.me/...
    parts = message.text.split(maxsplit=1)
    if len(parts) > 1:
        link_str = parts[1].strip()
        parsed = TelegramLinkParser.parse(link_str)
        if parsed:
            await _start_wizard_with_link(client, message, link_str, parsed)
            return

    # Otherwise prompt user to send video or link
    wizard_manager.start_session(
        user_id=user_id,
        chat_id=message.chat.id,
        source_title="Awaiting Video",
    )
    sess = wizard_manager.get_session(user_id)
    if sess:
        sess.waiting_for_input = "video_source"

    text = (
        "🎬 **ENCODE WIZARD**\n\n"
        "Please send, forward, or paste a link to the video you want to encode.\n\n"
        "• Direct video upload / video document\n"
        "• Forwarded video from another channel\n"
        "• Telegram message link (e.g. `https://t.me/channel/123`)"
    )
    await message.reply_text(text, reply_markup=build_keyboard([[cancel_button("wiz:cancel")]]))


async def handle_video_input(client: Client, message: Message) -> None:
    """Handles direct videos or forwarded video messages."""
    user_id = message.from_user.id if message.from_user else 0
    if not config.is_authorized(user_id):
        return

    media_info = MediaExtractor.extract_video_media(message)
    if not media_info:
        return

    await _start_wizard_with_media(client, message, media_info, message)


async def handle_text_or_link_input(client: Client, message: Message) -> bool:
    """Handles text messages that may be Telegram links or wizard text inputs."""
    user_id = message.from_user.id if message.from_user else 0
    if not config.is_authorized(user_id):
        return False

    text = message.text or ""
    sess = wizard_manager.get_session(user_id)

    # 1. Check if user is entering a custom text inside wizard
    if sess and sess.waiting_for_input:
        input_type = sess.waiting_for_input
        sess.waiting_for_input = None

        if input_type == "filename_text":
            sess.filename_template = text.strip()
            # Proceed to caption step
            await _render_caption_step(client, message.chat.id, user_id, sess.message_id)
            return True

        elif input_type == "caption_text":
            sess.caption_template = text.strip()
            # Proceed to thumbnail step
            await _render_thumbnail_step(client, message.chat.id, user_id, sess.message_id)
            return True

        elif input_type == "dest_text":
            sess.destination_chat = text.strip()
            # Proceed to preview step
            await _render_preview_step(client, message.chat.id, user_id, sess.message_id)
            return True

        elif input_type == "video_source":
            parsed = TelegramLinkParser.parse(text)
            if parsed:
                await _start_wizard_with_link(client, message, text, parsed)
                return True
            else:
                await message.reply_text("⚠️ Invalid Telegram video link. Please send a valid message link or upload a video.")
                return True

    # 2. Standalone Telegram link received outside explicit wizard prompt
    parsed_link = TelegramLinkParser.parse(text)
    if parsed_link:
        await _start_wizard_with_link(client, message, text, parsed_link)
        return True

    return False


async def _start_wizard_with_media(client: Client, message: Message, media_info: Any, src_msg: Message) -> None:
    user_id = message.from_user.id if message.from_user else 0
    sess = wizard_manager.start_session(
        user_id=user_id,
        chat_id=message.chat.id,
        source_title=media_info.file_name,
        source_type="telegram_file",
        source_file_id=media_info.file_id,
        source_size_bytes=media_info.file_size_bytes,
    )

    # Set default values from user preferences
    settings = await db.get_user_settings(user_id)
    saved_fn = await db.get_active_filename_format(user_id)
    saved_cap = await db.get_active_caption_format(user_id)
    saved_th = await db.get_active_thumbnail_config(user_id)

    sess.selected_qualities = list(settings.default_qualities)
    sess.encoding_profile = settings.default_profile
    sess.destination_chat = settings.default_destination
    sess.filename_template = saved_fn.template if saved_fn else None
    sess.caption_template = saved_cap.template if saved_cap else None
    sess.thumbnail_mode = "none" if not saved_th else saved_th.mode

    # Send first step: Filename
    text, markup = wizard_manager.build_filename_step_ui(saved_fn.template if saved_fn else None)
    sent_msg = await message.reply_text(text, reply_markup=markup)
    sess.message_id = sent_msg.id


async def _start_wizard_with_link(client: Client, message: Message, link_str: str, parsed: Any) -> None:
    user_id = message.from_user.id if message.from_user else 0
    wait_msg = await message.reply_text("🔍 **Inspecting linked Telegram message...**")

    try:
        target_msg = await client.get_messages(chat_id=parsed.chat_id, message_ids=parsed.message_id)
        if not target_msg:
            await wait_msg.edit_text("❌ Could not access message at the provided link. Ensure bot has access.")
            return

        media_info = MediaExtractor.extract_video_media(target_msg)
        if not media_info:
            await wait_msg.edit_text("❌ The linked message does not contain a supported video file.")
            return

        sess = wizard_manager.start_session(
            user_id=user_id,
            chat_id=message.chat.id,
            source_title=media_info.file_name,
            source_type="telegram_link",
            source_link=link_str,
            source_size_bytes=media_info.file_size_bytes,
        )

        settings = await db.get_user_settings(user_id)
        saved_fn = await db.get_active_filename_format(user_id)
        saved_cap = await db.get_active_caption_format(user_id)
        saved_th = await db.get_active_thumbnail_config(user_id)

        sess.selected_qualities = list(settings.default_qualities)
        sess.encoding_profile = settings.default_profile
        sess.destination_chat = settings.default_destination
        sess.filename_template = saved_fn.template if saved_fn else None
        sess.caption_template = saved_cap.template if saved_cap else None
        sess.thumbnail_mode = "none" if not saved_th else saved_th.mode

        text, markup = wizard_manager.build_filename_step_ui(saved_fn.template if saved_fn else None)
        await wait_msg.edit_text(text, reply_markup=markup)
        sess.message_id = wait_msg.id

    except Exception as e:
        logger.error(f"Error inspecting linked video: {e}")
        await wait_msg.edit_text(f"❌ **Error accessing link:** `{e}`")


# ---------------- WIZARD STEP RENDERERS ----------------

async def _render_caption_step(client: Client, chat_id: int, user_id: int, message_id: Optional[int]) -> None:
    saved_cap = await db.get_active_caption_format(user_id)
    text, markup = wizard_manager.build_caption_step_ui(saved_cap.template if saved_cap else None)
    if message_id:
        try:
            await client.edit_message_text(chat_id=chat_id, message_id=message_id, text=text, reply_markup=markup)
            return
        except Exception:
            pass
    await client.send_message(chat_id=chat_id, text=text, reply_markup=markup)


async def _render_thumbnail_step(client: Client, chat_id: int, user_id: int, message_id: Optional[int]) -> None:
    saved_th = await db.get_active_thumbnail_config(user_id)
    has_thumb = saved_th is not None and saved_th.is_active
    info_str = "None"
    if saved_th:
        info_str = "Single thumbnail" if saved_th.mode == "single" else f"Multiple ({len(saved_th.mapping_data)} episodes mapped)"
    text, markup = wizard_manager.build_thumbnail_step_ui(has_thumb, info_str)
    if message_id:
        try:
            await client.edit_message_text(chat_id=chat_id, message_id=message_id, text=text, reply_markup=markup)
            return
        except Exception:
            pass
    await client.send_message(chat_id=chat_id, text=text, reply_markup=markup)


async def _render_quality_step(client: Client, chat_id: int, user_id: int, message_id: Optional[int]) -> None:
    sess = wizard_manager.get_session(user_id)
    selected = sess.selected_qualities if sess else ["720p"]
    text, markup = wizard_manager.build_quality_step_ui(selected)
    if message_id:
        try:
            await client.edit_message_text(chat_id=chat_id, message_id=message_id, text=text, reply_markup=markup)
            return
        except Exception:
            pass
    await client.send_message(chat_id=chat_id, text=text, reply_markup=markup)


async def _render_profile_step(client: Client, chat_id: int, user_id: int, message_id: Optional[int]) -> None:
    sess = wizard_manager.get_session(user_id)
    cur_prof = sess.encoding_profile if sess else "standard"
    text, markup = wizard_manager.build_profile_step_ui(cur_prof)
    if message_id:
        try:
            await client.edit_message_text(chat_id=chat_id, message_id=message_id, text=text, reply_markup=markup)
            return
        except Exception:
            pass
    await client.send_message(chat_id=chat_id, text=text, reply_markup=markup)


async def _render_destination_step(client: Client, chat_id: int, user_id: int, message_id: Optional[int]) -> None:
    st = await db.get_user_settings(user_id)
    text, markup = wizard_manager.build_destination_step_ui(st.default_destination)
    if message_id:
        try:
            await client.edit_message_text(chat_id=chat_id, message_id=message_id, text=text, reply_markup=markup)
            return
        except Exception:
            pass
    await client.send_message(chat_id=chat_id, text=text, reply_markup=markup)


async def _render_preview_step(client: Client, chat_id: int, user_id: int, message_id: Optional[int]) -> None:
    sess = wizard_manager.get_session(user_id)
    if not sess:
        return

    global_bolum = await SeasonManager.get_bolum_for_episode(
        user_id=user_id,
        season=sess.parsed_metadata.season,
        episode=sess.parsed_metadata.episode,
    )

    text, markup = wizard_manager.build_preview_step_ui(sess, global_bolum)
    if message_id:
        try:
            await client.edit_message_text(chat_id=chat_id, message_id=message_id, text=text, reply_markup=markup)
            return
        except Exception:
            pass
    await client.send_message(chat_id=chat_id, text=text, reply_markup=markup)


# ---------------- WIZARD CALLBACK ROUTER ----------------

async def handle_wizard_callbacks(client: Client, query: CallbackQuery, worker: JobQueueWorker) -> None:
    user_id = query.from_user.id
    data = query.data
    sess = wizard_manager.get_session(user_id)

    if data == "nav:encode_prompt":
        await query.message.edit_text(
            "🎬 **Send or forward a video or paste a Telegram link to begin encoding.**",
            reply_markup=build_keyboard([[cancel_button("wiz:cancel")]]),
        )
        return

    if data == "wiz:cancel":
        wizard_manager.clear_session(user_id)
        await query.message.edit_text("❌ **Encoding wizard cancelled.**")
        return

    if not sess:
        await query.answer("Session expired. Please send video or use /encode again.", show_alert=True)
        return

    # Filename callbacks
    if data == "wiz:fn:use_current":
        saved_fn = await db.get_active_filename_format(user_id)
        sess.filename_template = saved_fn.template if saved_fn else None
        await _render_caption_step(client, query.message.chat.id, user_id, query.message.id)

    elif data == "wiz:fn:keep_default":
        sess.filename_template = None
        await _render_caption_step(client, query.message.chat.id, user_id, query.message.id)

    elif data == "wiz:fn:add_new":
        sess.waiting_for_input = "filename_text"
        await query.message.edit_text(
            "📁 **Send your custom filename format:**\n\n"
            "**Example:** `{title} S{season} E{episode} {quality}.mp4`\n\n"
            "**Placeholders:** `{title}`, `{season}`, `{episode}`, `{bolum}`, `{season_episode}`, `{quality}`, `{lang}`, `{source}`, `{year}`, `{sub}`, `{finale}`, `{part}`",
            reply_markup=build_keyboard([[cancel_button("wiz:cancel")]]),
        )

    # Caption callbacks
    elif data == "wiz:cap:use_current":
        saved_cap = await db.get_active_caption_format(user_id)
        sess.caption_template = saved_cap.template if saved_cap else None
        await _render_thumbnail_step(client, query.message.chat.id, user_id, query.message.id)

    elif data == "wiz:cap:no_caption":
        sess.caption_template = ""
        await _render_thumbnail_step(client, query.message.chat.id, user_id, query.message.id)

    elif data == "wiz:cap:add_new":
        sess.waiting_for_input = "caption_text"
        await query.message.edit_text(
            "📝 **Send your custom caption template (HTML supported):**\n\n"
            "**Example:**\n`🎬 <b>{title}</b>\\n📌 Season {season} Episode {episode}\\n🎞 {quality}`",
            reply_markup=build_keyboard([[cancel_button("wiz:cancel")]]),
        )

    # Thumbnail callbacks
    elif data == "wiz:th:use_current":
        saved_th = await db.get_active_thumbnail_config(user_id)
        sess.thumbnail_mode = saved_th.mode if saved_th else "none"
        await _render_quality_step(client, query.message.chat.id, user_id, query.message.id)

    elif data == "wiz:th:no_thumb":
        sess.thumbnail_mode = "none"
        await _render_quality_step(client, query.message.chat.id, user_id, query.message.id)

    elif data == "wiz:th:add_new":
        await query.message.edit_text(
            "🖼️ Launching Thumbnail Manager...\nUse `/thumbs` to configure your thumbnails, then return here.",
            reply_markup=build_keyboard([[success_button("➡️ Proceed to Qualities", "wiz:th:no_thumb")]]),
        )

    # Quality multi-select callbacks
    elif data.startswith("wiz:q:toggle:"):
        q = data.split(":")[-1]
        if q in sess.selected_qualities:
            if len(sess.selected_qualities) > 1:
                sess.selected_qualities.remove(q)
            else:
                await query.answer("At least one quality must be selected!", show_alert=True)
                return
        else:
            sess.selected_qualities.append(q)
        sess.selected_qualities = sort_qualities_ascending(sess.selected_qualities)
        await _render_quality_step(client, query.message.chat.id, user_id, query.message.id)

    elif data == "wiz:q:all":
        sess.selected_qualities = list(STANDARD_QUALITIES)
        await _render_quality_step(client, query.message.chat.id, user_id, query.message.id)

    elif data == "wiz:q:invert":
        sess.selected_qualities = [q for q in STANDARD_QUALITIES if q not in sess.selected_qualities]
        if not sess.selected_qualities:
            sess.selected_qualities = ["720p"]
        await _render_quality_step(client, query.message.chat.id, user_id, query.message.id)

    elif data == "wiz:q:continue":
        await _render_profile_step(client, query.message.chat.id, user_id, query.message.id)

    # Profile callbacks
    elif data.startswith("wiz:prof:"):
        p = data.split(":")[-1]
        if p in ("standard", "mobile", "hq"):
            sess.encoding_profile = p
            await _render_profile_step(client, query.message.chat.id, user_id, query.message.id)
        elif p == "continue":
            await _render_destination_step(client, query.message.chat.id, user_id, query.message.id)

    # Destination callbacks
    elif data == "wiz:dest:current":
        sess.destination_chat = None
        await _render_preview_step(client, query.message.chat.id, user_id, query.message.id)

    elif data == "wiz:dest:saved":
        st = await db.get_user_settings(user_id)
        sess.destination_chat = st.default_destination
        await _render_preview_step(client, query.message.chat.id, user_id, query.message.id)

    elif data == "wiz:dest:custom":
        sess.waiting_for_input = "dest_text"
        await query.message.edit_text(
            "📤 **Send the username or chat ID of destination channel:**\n\n**Example:** `@MyChannel` or `-1001234567890`",
            reply_markup=build_keyboard([[cancel_button("wiz:cancel")]]),
        )

    # Edit shortcuts
    elif data == "wiz:edit:qualities":
        await _render_quality_step(client, query.message.chat.id, user_id, query.message.id)

    elif data == "wiz:edit:profile":
        await _render_profile_step(client, query.message.chat.id, user_id, query.message.id)

    # 🚀 START JOB
    elif data == "wiz:start_job":
        st = await db.get_user_settings(user_id)
        job = create_job(
            user_id=user_id,
            chat_id=query.message.chat.id,
            source_title=sess.source_title,
            source_type=sess.source_type,
            source_file_id=sess.source_file_id,
            source_link=sess.source_link,
            source_size_bytes=sess.source_size_bytes,
            metadata=sess.parsed_metadata.__dict__,
            selected_qualities=sess.selected_qualities,
            filename_template=sess.filename_template,
            caption_template=sess.caption_template,
            thumbnail_mode=sess.thumbnail_mode,
            destination_chat_id=sess.destination_chat,
            sticker_id=st.sticker_id,
            encoding_profile=sess.encoding_profile,
        )
        job.status_message_id = query.message.id

        wizard_manager.clear_session(user_id)
        await worker.enqueue_job(job)

        await query.message.edit_text(
            f"🚀 **Encoding Job Queued!**\n\n"
            f"🎬 **Source:** `{job.source_title}`\n"
            f"🎞 **Qualities:** {', '.join(job.selected_qualities)}\n"
            f"📌 **Status:** Queued (Waiting for worker...)"
        )
