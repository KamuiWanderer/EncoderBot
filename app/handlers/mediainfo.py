"""
Media info inspector handler (/mediainfo, /info, /probe, /details).
Inspects replied media, links, or direct uploads and outputs comprehensive metadata,
quality resolutions (144p, 240p, 360p, 480p, 720p, 1080p, etc.), audio/video specs, and series info.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional
from pyrogram import Client, filters
from pyrogram.enums import ParseMode
from pyrogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from app.config import config
from app.encoder.presets import STANDARD_QUALITIES, get_quality_height
from app.managers.encode_manager import wizard_manager
from app.managers.filename_manager import FilenameManager
from app.managers.caption_manager import CaptionManager
from app.managers.thumbnail_manager import ThumbnailManager
from app.managers.settings_manager import SettingsManager
from app.metadata.parser import MetadataParser
from app.metadata.season import SeasonManager
from app.telegram.links import TelegramLinkParser
from app.telegram.media import MediaExtractor, TelegramMediaInfo
from app.utils.formatting import human_bytes, human_duration
from app.utils.ui import build_keyboard, cancel_button, primary_button, success_button


def get_resolution_label(width: int, height: int, fallback_quality: Optional[str] = None) -> tuple[str, str]:
    """
    Returns (quality_tag, display_badge) e.g. ("720p", "720p HD (1280x720) [16:9]")
    """
    if height > 0 and width > 0:
        aspect = f"{round(width / height, 2)}:1"
        if round(width / height, 2) in (1.77, 1.78):
            aspect = "16:9"
        elif round(width / height, 2) == 1.33:
            aspect = "4:3"

        if height >= 2160:
            tag = "2160p"
            badge = f"2160p 4K UHD ({width}×{height}) [{aspect}]"
        elif height >= 1440:
            tag = "1440p"
            badge = f"1440p 2K QHD ({width}×{height}) [{aspect}]"
        elif height >= 1080:
            tag = "1080p"
            badge = f"1080p Full HD ({width}×{height}) [{aspect}]"
        elif height >= 720:
            tag = "720p"
            badge = f"720p HD ({width}×{height}) [{aspect}]"
        elif height >= 480:
            tag = "480p"
            badge = f"480p SD ({width}×{height}) [{aspect}]"
        elif height >= 360:
            tag = "360p"
            badge = f"360p ({width}×{height}) [{aspect}]"
        elif height >= 240:
            tag = "240p"
            badge = f"240p ({width}×{height}) [{aspect}]"
        else:
            tag = f"{height}p"
            badge = f"{height}p Low-Res ({width}×{height}) [{aspect}]"
        return tag, badge

    if fallback_quality:
        return fallback_quality, f"{fallback_quality.upper()} (Extracted from filename)"

    return "Unknown", "Unknown Resolution"


async def mediainfo_cmd(client: Client, message: Message) -> None:
    """Handle /mediainfo, /info, /probe, /details commands."""
    user_id = message.from_user.id if message.from_user else 0
    if not config.is_authorized(user_id):
        return

    target_msg = message.reply_to_message or message
    media_info: Optional[TelegramMediaInfo] = None
    source_link: Optional[str] = None

    # 1. Check if replied message has media
    if message.reply_to_message:
        media_info = MediaExtractor.extract_video_media(message.reply_to_message)

    # 2. Check if command message itself has media
    if not media_info:
        media_info = MediaExtractor.extract_video_media(message)

    # 3. Check if a link was passed in arguments
    if not media_info:
        parts = message.text.strip().split(maxsplit=1)
        if len(parts) > 1:
            candidate_link = parts[1].strip()
            parsed = TelegramLinkParser.parse(candidate_link)
            if parsed:
                source_link = candidate_link
                try:
                    fetched = await client.get_messages(chat_id=parsed.chat_id, message_ids=parsed.message_id)
                    if fetched:
                        target_msg = fetched
                        media_info = MediaExtractor.extract_video_media(fetched)
                except Exception as e:
                    await message.reply_text(f"❌ **Failed to fetch media from link:**\n`{e}`")
                    return

    if not media_info:
        await message.reply_text(
            "🔍 **MEDIA & METADATA INSPECTOR**\n\n"
            "⚠️ **No media detected!**\n\n"
            "**How to use:**\n"
            "1. **Reply** to any video, document, or audio file with `/info` (or `/mediainfo`)\n"
            "2. **Pass a link:** `/info https://t.me/c/123456/100`\n"
            "3. **Send directly:** Attach a video and write `/info` in the caption."
        )
        return

    # Extract parsed metadata from filename + caption
    caption_text = target_msg.caption or target_msg.text or ""
    raw_text = f"{media_info.file_name} {caption_text}".strip()
    parsed_meta = MetadataParser.parse(raw_text)

    # Calculate season arithmetic & finale
    global_bolum = await SeasonManager.get_bolum_for_episode(
        user_id=user_id,
        season=parsed_meta.season,
        episode=parsed_meta.episode,
    )
    season_finale = await SeasonManager.check_season_finale(
        user_id=user_id,
        season=parsed_meta.season,
        global_bolum=global_bolum,
    )

    # Resolve quality & display badge
    q_tag, q_badge = get_resolution_label(media_info.width, media_info.height, parsed_meta.quality)

    # Bitrate estimation
    bitrate_str = "N/A"
    if media_info.duration_seconds > 0 and media_info.file_size_bytes > 0:
        total_kbps = int((media_info.file_size_bytes * 8) / (media_info.duration_seconds * 1000))
        bitrate_str = f"~{total_kbps} kbps"

    # Upscaling status
    user_st = await SettingsManager.get_settings(user_id)
    cur_height = media_info.height if media_info.height > 0 else get_quality_height(q_tag)

    recommended_qualities = []
    for q in ["240p", "360p", "480p", "720p", "1080p"]:
        qh = get_quality_height(q)
        if qh <= cur_height:
            recommended_qualities.append(f"🟢 {q}")
        else:
            if user_st.allow_upscale:
                recommended_qualities.append(f"🟡 {q} (Upscaled)")
            else:
                recommended_qualities.append(f"⚪ {q} (Capped)")

    season_display = f"{parsed_meta.season:02d}" if parsed_meta.season is not None else "01 (Default)"
    episode_display = f"{parsed_meta.episode:02d}" if parsed_meta.episode is not None else (f"{global_bolum:02d}" if global_bolum else "01 (Default)")
    bolum_display = f"{global_bolum:02d}" if global_bolum is not None else (f"{parsed_meta.bolum:02d}" if parsed_meta.bolum else episode_display)

    lines = [
        "🔍 **MEDIA & METADATA INSPECTION**\n",
        f"🎬 **Title:** `{parsed_meta.title}`",
        f"📁 **File Name:** `{media_info.file_name}`",
        f"📦 **File Size:** `{human_bytes(media_info.file_size_bytes)}`",
        f"⏱ **Duration:** `{human_duration(media_info.duration_seconds)}`\n",
        "🎞️ **Resolution & Quality:**",
        f"• **Quality:** `🟢 {q_badge}`",
        f"• **Dimensions:** `{media_info.width} × {media_info.height}`" if media_info.height > 0 else f"• **Quality Tag:** `{q_tag}`",
        f"• **Est. Bitrate:** `{bitrate_str}`\n",
        "📺 **Series Metadata (Parsed):**",
        f"• **Season:** `{season_display}`",
        f"• **Episode:** `{episode_display}` (Season-relative)",
        f"• **Bölüm:** `{bolum_display}` (Global series episode)",
        f"• **Language:** `{parsed_meta.lang or 'None specified'}`",
        f"• **Subtitles:** `{parsed_meta.sub or 'None specified'}`",
        f"• **Source Group:** `{parsed_meta.source or 'None specified'}`",
        f"• **Finale:** `{'🎉 ' + season_finale if season_finale else 'No'}`\n",
        "⚙️ **Available Encode Targets:**",
        f"• {', '.join(recommended_qualities)}",
        f"• **Upscale Policy:** `{'🟢 Force Upscale' if user_st.allow_upscale else '🚫 Smart Cap (Preserve Original)'}`",
    ]

    card_text = "\n".join(lines)

    # Action buttons
    buttons = [
        [
            success_button("🚀 Start Encode Wizard", f"minfo:start:{target_msg.chat.id}:{target_msg.id}"),
            primary_button("🔵 ⚙️ Settings", "nav:settings"),
        ],
        [cancel_button("minfo:close")],
    ]

    await message.reply_text(
        text=card_text,
        reply_markup=build_keyboard(buttons),
        parse_mode=ParseMode.MARKDOWN,
    )


async def handle_mediainfo_callbacks(client: Client, query: CallbackQuery) -> None:
    """Handle interactive buttons on the /mediainfo card."""
    user_id = query.from_user.id
    data = query.data

    if data == "minfo:close":
        await query.message.delete()
        return

    if data.startswith("minfo:start:"):
        parts = data.split(":")
        chat_id = int(parts[2])
        msg_id = int(parts[3])

        try:
            target_msg = await client.get_messages(chat_id=chat_id, message_ids=msg_id)
            if not target_msg:
                await query.answer("Could not fetch target media message.", show_alert=True)
                return

            media_info = MediaExtractor.extract_video_media(target_msg)
            if not media_info:
                await query.answer("No valid video file attached to message.", show_alert=True)
                return

            # Start wizard session
            sess = wizard_manager.start_session(
                user_id=user_id,
                chat_id=query.message.chat.id,
                source_title=media_info.file_name,
                source_type="telegram_file",
                source_file_id=media_info.file_id,
                source_size_bytes=media_info.file_size_bytes,
            )

            # Auto-load saved defaults
            saved_fn = await FilenameManager.get_format(user_id)
            if saved_fn:
                sess.filename_template = saved_fn.template

            saved_cap = await CaptionManager.get_format(user_id)
            if saved_cap:
                sess.caption_template = saved_cap.template

            saved_thumb = await ThumbnailManager.get_active_config(user_id)
            if saved_thumb:
                sess.thumbnail_mode = saved_thumb.mode

            st = await SettingsManager.get_settings(user_id)
            sess.selected_qualities = list(st.default_qualities)
            sess.encoding_profile = st.default_profile
            sess.destination_chat = st.default_destination

            text, markup = wizard_manager.build_filename_step_ui(
                current_saved_template=saved_fn.template if saved_fn else None
            )

            await query.message.edit_text(text, reply_markup=markup)
        except Exception as e:
            await query.answer(f"Error launching wizard: {e}", show_alert=True)
