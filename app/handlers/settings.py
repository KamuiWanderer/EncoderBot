"""
Settings control panel handlers with vibrant colorful buttons.
Provides a button-driven interface for all user preferences including Upload Settings.
"""

from __future__ import annotations

from pyrogram import Client, filters
from pyrogram.types import CallbackQuery, Message

from app.config import config
from app.database.database import db
from app.encoder.presets import STANDARD_QUALITIES
from app.encoder.profiles import get_profile
from app.managers.caption_manager import CaptionManager
from app.managers.destination_manager import DestinationManager
from app.managers.filename_manager import FilenameManager
from app.managers.settings_manager import SettingsManager
from app.managers.thumbnail_manager import ThumbnailManager
from app.managers.upload_config_manager import UploadConfigManager
from app.utils.ui import (
    build_keyboard,
    cancel_button,
    danger_button,
    primary_button,
    success_button,
)


async def settings_cmd(client: Client, message: Message) -> None:
    """Handle /settings command."""
    user_id = message.from_user.id if message.from_user else 0
    if not config.is_authorized(user_id):
        return

    text, markup = await _render_main_settings_ui(user_id)
    await message.reply_text(text, reply_markup=markup)


async def _render_main_settings_ui(user_id: int) -> tuple[str, Any]:
    st = await SettingsManager.get_settings(user_id)
    fn = await FilenameManager.get_format(user_id)
    cap = await CaptionManager.get_format(user_id)
    th = await ThumbnailManager.get_active_config(user_id)
    prof = get_profile(st.default_profile)

    th_str = "None"
    if th:
        th_str = "Single" if th.mode == "single" else f"Multiple ({len(th.mapping_data)} eps)"

    timing_str = "Staging" if st.upload_timing == "staging" else "Immediate"
    upscale_str = "🟢 Force Upscale (Lanczos)" if st.allow_upscale else "🚫 Smart Cap (No Fake Pixels)"

    text = (
        "⚙️ **ENCODER SETTINGS CONTROL PANEL**\n\n"
        f"📁 **Filename Format:** `{fn.template if fn else 'Default'}`\n"
        f"📝 **Caption:** `{'Configured' if cap else 'None'}`\n"
        f"🖼️ **Thumbnails:** `{th_str}`\n"
        f"🎞️ **Default Qualities:** `{', '.join(st.default_qualities)}`\n"
        f"🎥 **Default Codec:** `{st.default_codec.upper()}`\n"
        f"⚙️ **Profile:** `{prof.display_name}`\n"
        f"📐 **Upscale Policy:** `{upscale_str}`\n"
        f"📤 **Upload Mode:** `{timing_str}` (Dest: `{st.default_destination or 'Current Chat'}`)\n"
        f"🏷️ **Sticker:** `{'Configured' if st.sticker_id else 'None'}`\n\n"
        "Select a section to customize:"
    )

    buttons = [
        [primary_button("🔵 📁 Filename", "set_menu:fn"), primary_button("🔵 📝 Caption", "set_menu:cap")],
        [primary_button("🔵 🖼️ Thumbnails", "set_menu:th"), primary_button("🔵 🎞️ Qualities", "set_menu:q")],
        [primary_button("🔵 ⚙️ Profile", "set_menu:prof"), primary_button("🔵 📐 Upscale Policy", "set_menu:upscale")],
        [primary_button("🔵 📤 Upload & Staging", "upset:main"), primary_button("🔵 🏷️ Sticker", "set_menu:stick")],
        [primary_button("🔵 📌 Season Mappings", "set_menu:season")],
        [danger_button("🔄 Reset All to Defaults", "set_menu:reset")],
        [cancel_button("set_menu:close")],
    ]

    return text, build_keyboard(buttons)


async def upscale_cmd(client: Client, message: Message) -> None:
    """Handle /upscale [on|off] command."""
    user_id = message.from_user.id if message.from_user else 0
    if not config.is_authorized(user_id):
        return

    parts = message.text.strip().split()
    if len(parts) > 1:
        arg = parts[1].lower()
        if arg in ("on", "true", "enable", "1"):
            await SettingsManager.set_allow_upscale(user_id, True)
            await message.reply_text("✅ **Upscale Policy:** `🟢 Force Upscale Enabled (Lanczos)`\n_Videos lower than target resolution will be upscaled._")
            return
        elif arg in ("off", "false", "disable", "0"):
            await SettingsManager.set_allow_upscale(user_id, False)
            await message.reply_text("✅ **Upscale Policy:** `🚫 Smart Cap Enabled (Default)`\n_Videos lower than target resolution will preserve their original crisp dimensions without bloated pixels._")
            return

    st = await SettingsManager.get_settings(user_id)
    cur_status = "🟢 Enabled (Force Upscale)" if st.allow_upscale else "🚫 Disabled (Smart Cap / No Bloat)"
    await message.reply_text(
        f"📐 **Upscale Policy:** `{cur_status}`\n\n"
        "**Options:**\n"
        "• `/upscale off` — **(Recommended)** Preserves source dimensions if lower than target (avoids fake blurry pixels & saves disk).\n"
        "• `/upscale on` — Forces FFmpeg to upscale using high-quality Lanczos scaling filter."
    )


async def handle_settings_callbacks(client: Client, query: CallbackQuery) -> None:
    user_id = query.from_user.id
    data = query.data

    if data == "nav:settings" or data == "set_menu:main":
        text, markup = await _render_main_settings_ui(user_id)
        await query.message.edit_text(text, reply_markup=markup)

    elif data == "set_menu:reset":
        await SettingsManager.reset_settings(user_id)
        text, markup = await _render_main_settings_ui(user_id)
        await query.message.edit_text("🔄 **All settings have been reset to defaults.**\n\n" + text, reply_markup=markup)

    elif data == "set_menu:close":
        await query.message.delete()

    elif data == "set_menu:upscale":
        st = await SettingsManager.get_settings(user_id)
        text = (
            "📐 **Low-Resolution Upscaling Policy**\n\n"
            "Choose how the encoder handles source videos smaller than the target resolution (e.g. 180p source $\\rightarrow$ 240p / 720p):\n\n"
            f"**Current State:** `{'🟢 Force Upscale (Lanczos)' if st.allow_upscale else '🚫 Smart Cap (No Fake Pixels)'}`\n\n"
            "• **🚫 Smart Cap (Recommended):** Keeps original resolution if lower than target. Prevents fake blurry pixel bloat and saves encoding time.\n"
            "• **🟢 Force Upscale:** Forces FFmpeg to resize upward using high-quality Lanczos + accurate rounding algorithm."
        )
        buttons = [
            [primary_button(f"{'🟢 🔘' if not st.allow_upscale else '⚪ ⚪'} 🚫 Smart Cap (Preserve Original)", "set_act:upscale:off")],
            [primary_button(f"{'🟢 🔘' if st.allow_upscale else '⚪ ⚪'} 🟢 Force Upscale (Lanczos)", "set_act:upscale:on")],
            [primary_button("🔵 ↩️ Back to Settings", "set_menu:main")],
        ]
        await query.message.edit_text(text, reply_markup=build_keyboard(buttons))

    elif data == "set_act:upscale:on":
        await SettingsManager.set_allow_upscale(user_id, True)
        await query.answer("Force Upscale enabled (Lanczos)", show_alert=False)
        text, markup = await _render_main_settings_ui(user_id)
        await query.message.edit_text(text, reply_markup=markup)

    elif data == "set_act:upscale:off":
        await SettingsManager.set_allow_upscale(user_id, False)
        await query.answer("Smart Cap enabled (No bloat)", show_alert=False)
        text, markup = await _render_main_settings_ui(user_id)
        await query.message.edit_text(text, reply_markup=markup)

    elif data == "set_menu:fn":
        fn = await FilenameManager.get_format(user_id)
        text = (
            "📁 **Filename Settings**\n\n"
            f"**Current Format:**\n`{fn.template if fn else FilenameManager.get_default_template()}`\n\n"
            "To set a new format, use `/setfilename <format>`\n"
            "To clear, click Clear below."
        )
        buttons = [
            [danger_button("🗑️ Clear Filename", "set_act:clear_fn")] if fn else [],
            [primary_button("🔵 ↩️ Back to Settings", "set_menu:main")],
        ]
        filtered = [b for b in buttons if b]
        await query.message.edit_text(text, reply_markup=build_keyboard(filtered))

    elif data == "set_act:clear_fn":
        await FilenameManager.clear_format(user_id)
        await query.answer("Filename cleared", show_alert=False)
        text, markup = await _render_main_settings_ui(user_id)
        await query.message.edit_text(text, reply_markup=markup)

    elif data == "set_menu:cap":
        cap = await CaptionManager.get_format(user_id)
        text = (
            "📝 **Caption Settings**\n\n"
            f"**Current Template:**\n{cap.template if cap else '_(None)_'}\n\n"
            "To set a new template, use `/setcaption <template>`\n"
            "To clear, click Clear below."
        )
        buttons = [
            [danger_button("🗑️ Clear Caption", "set_act:clear_cap")] if cap else [],
            [primary_button("🔵 ↩️ Back to Settings", "set_menu:main")],
        ]
        filtered = [b for b in buttons if b]
        await query.message.edit_text(text, reply_markup=build_keyboard(filtered))

    elif data == "set_act:clear_cap":
        await CaptionManager.clear_format(user_id)
        await query.answer("Caption cleared", show_alert=False)
        text, markup = await _render_main_settings_ui(user_id)
        await query.message.edit_text(text, reply_markup=markup)
