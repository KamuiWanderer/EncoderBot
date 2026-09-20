"""
Interactive, modular, button-driven /start and /help command handlers.
Provides detailed category walkthroughs and placeholder references.
"""

from __future__ import annotations

from typing import Any
from pyrogram import Client, filters
from pyrogram.enums import ParseMode
from pyrogram.types import CallbackQuery, Message

from app.config import config
from app.utils.ui import (
    build_keyboard,
    cancel_button,
    danger_button,
    primary_button,
    success_button,
)


async def start_handler(client: Client, message: Message) -> None:
    """Handle /start command."""
    user_id = message.from_user.id if message.from_user else 0
    if not config.is_authorized(user_id):
        await message.reply_text("⛔ **Access Denied:** You are not authorized to use this encoder bot.")
        return

    welcome_text = (
        "🎬 **Welcome to Telegram Multi-Quality Video Encoder Bot!**\n\n"
        "I download source videos **exactly once**, sequentially encode them into multiple resolutions "
        "(`240p`, `360p`, `480p`, `720p`, `1080p`), and upload them with custom thumbnails, captions, "
        "and Episode Headers.\n\n"
        "📌 **Quick Actions:**\n"
        "• Send or forward a video, or type `/encode`\n"
        "• Encode multiple episodes with `/batch`\n"
        "• Configure settings with `/settings`\n"
        "• Tap **📖 Help Guide** below for full documentation."
    )

    keyboard = build_keyboard([
        [success_button("🚀 Start Encoding", "nav:encode_prompt"), primary_button("📦 Batch Encode", "nav:batch_prompt")],
        [primary_button("⚙️ Encoder Settings", "nav:settings"), primary_button("📤 Upload Config", "upset:main")],
        [primary_button("🖼️ Thumbnails", "th_menu:view"), primary_button("📊 Check Status", "nav:status")],
        [primary_button("📖 Help Guide & Manual", "help:main")],
    ])

    await message.reply_text(welcome_text, reply_markup=keyboard)


# ============================================================
# MODULAR HELP CENTER
# ============================================================

def get_main_help_ui() -> tuple[str, Any]:
    text = (
        "📖 **TELEGRAM VIDEO ENCODER — HELP CENTER**\n\n"
        "Select a category below to view detailed instructions, commands, and examples:\n\n"
        "• 🎬 **Encoding & Batching** — Single & multi-video workflows\n"
        "• 📁 **Filename & Caption** — Templates & customization\n"
        "• 🖼️ **Thumbnail Manager** — Single covers & multi-episode ranges\n"
        "• 📌 **Season & Bölüm Mapping** — Global episode arithmetic & Finale\n"
        "• 📤 **Upload & Staging** — Ordered delivery & Episode Headers\n"
        "• 🧩 **Placeholders Reference** — Full tag cheat sheet"
    )

    buttons = [
        [primary_button("🎬 Encoding & Batching", "help:encode"), primary_button("📁 Filename & Caption", "help:templates")],
        [primary_button("🖼️ Thumbnail Manager", "help:thumbs"), primary_button("📌 Season & Bölüm", "help:season")],
        [primary_button("📤 Upload & Staging", "help:upload"), primary_button("🧩 Placeholders Cheat Sheet", "help:tags")],
        [primary_button("⚙️ General Settings", "help:settings"), cancel_button("help:close")],
    ]

    return text, build_keyboard(buttons)


async def help_handler(client: Client, message: Message) -> None:
    """Handle /help command."""
    user_id = message.from_user.id if message.from_user else 0
    if not config.is_authorized(user_id):
        return

    text, markup = get_main_help_ui()
    await message.reply_text(text, reply_markup=markup)


async def handle_help_callbacks(client: Client, query: CallbackQuery) -> None:
    """Handle help menu navigation callbacks."""
    data = query.data

    if data == "help:main":
        text, markup = get_main_help_ui()
        await query.message.edit_text(text, reply_markup=markup)

    elif data == "help:close":
        await query.message.delete()

    elif data == "nav:batch_prompt":
        await query.message.edit_text(
            "📦 **Batch Encoding Guide**\n\n"
            "To start a batch encode:\n"
            "1. Forward 2 or more video files into this chat or group, OR\n"
            "2. Send `/batch <start_message_link> <end_message_link>`\n\n"
            "The bot will analyze the files and reply with a **Hyperlinked Batch Review Card**!",
            reply_markup=build_keyboard([[primary_button("↩️ Back to Menu", "help:main")]]),
        )

    elif data == "help:encode":
        text = (
            "🎬 **ENCODING & BATCHING COMMANDS**\n\n"
            "• `/encode` — Launch single-video interactive wizard.\n"
            "  ↳ _Accepts direct videos, forwarded videos, or Telegram links._\n\n"
            "• `/batch` — Launch multi-video batch encoder.\n"
            "  ↳ _Usage:_ `/batch https://t.me/c/123/100 https://t.me/c/123/105`\n\n"
            "• `/status` — View real-time active job progress bar, encoding speed, time elapsed, ETA, and queue.\n\n"
            "• `/cancel` — Instantly abort the active encoding job and cleanup temporary files safely.\n\n"
            "⚡ **How It Works:**\n"
            "Source is downloaded **only once**. Qualities encode sequentially (`240p` ➔ `1080p`) with zero extra CPU heat."
        )
        await query.message.edit_text(
            text,
            reply_markup=build_keyboard([[primary_button("↩️ Back to Help Center", "help:main")]]),
        )

    elif data == "help:templates":
        text = (
            "📁 **FILENAME & CAPTION COMMANDS**\n\n"
            "**Filename Commands:**\n"
            "• `/setfilename <format>` — Set custom naming format.\n"
            "  ↳ _Example:_ `/setfilename {title} S{season} E{episode} {quality}.mp4`\n"
            "• `/filename` — View active filename format.\n"
            "• `/clearfilename` — Reset to default format.\n\n"
            "**Caption Commands (HTML Supported):**\n"
            "• `/setcaption <template>` — Set custom caption.\n"
            "  ↳ _Example:_\n"
            "    `/setcaption 🎬 <b>{title}</b>\\n📌 Season {season} Episode {episode}\\n🎞 {quality}`\n"
            "• `/caption` — View active caption template.\n"
            "• `/clearcaption` — Clear caption."
        )
        await query.message.edit_text(
            text,
            reply_markup=build_keyboard([[primary_button("↩️ Back to Help Center", "help:main")]]),
        )

    elif data == "help:thumbs":
        text = (
            "🖼️ **THUMBNAIL MANAGER**\n\n"
            "• `/thumbs` or `/thumb` — Open Thumbnail Manager.\n"
            "• `/clearthumbs` — Clear active thumbnail configuration.\n\n"
            "**Modes:**\n"
            "1. **Single Mode:** Send a photo to apply to all video outputs.\n"
            "2. **Multiple Mode (Range Scan):**\n"
            "   ↳ Send **START** message link\n"
            "   ↳ Send **END** message link\n"
            "   ↳ Bot scans channel, maps each photo to an episode number, and displays a detailed preview (`Episode 01 → ✅ Found`, etc.)."
        )
        await query.message.edit_text(
            text,
            reply_markup=build_keyboard([[primary_button("↩️ Back to Help Center", "help:main")]]),
        )

    elif data == "help:season":
        text = (
            "📌 **SEASON & GLOBAL BÖLÜM MAPPING**\n\n"
            "• `/setseason <season> <global_bolum>` — Map season to global episode.\n"
            "  ↳ _Example:_ `/setseason 2 11`\n"
            "    • `S02 E01` ➔ **Bölüm 11**\n"
            "    • `S02 E02` ➔ **Bölüm 12**\n"
            "    • `S02 E10` ➔ **Bölüm 20**\n\n"
            "• `/seasons` — View all active season mappings.\n\n"
            "✨ **Automatic Season Finale:**\n"
            "If Season 2 starts at Bölüm 11, the bot automatically detects Episode 10 of Season 1 as the **Season Finale**."
        )
        await query.message.edit_text(
            text,
            reply_markup=build_keyboard([[primary_button("↩️ Back to Help Center", "help:main")]]),
        )

    elif data == "help:upload":
        text = (
            "📤 **UPLOAD & STAGING PUBLISHING**\n\n"
            "• `/uploadsettings` — Open Upload Configuration Panel.\n"
            "• `/setheader <template>` — Set Episode Header banner message.\n"
            "  ↳ _Example:_ `/setheader 🎬 <b>{title}</b>\\n📌 Season {season} Episode {episode}`\n"
            "• `/clearheader` — Disable Episode Header.\n"
            "• `/setstaging <channel>` — Set private staging group for Ordered Delivery.\n"
            "• `/setdelay <seconds>` — Set anti-flood post delay (`0.5s` to `3.0s`).\n"
            "• `/setdestination <@channel>` — Set default public destination.\n"
            "• `/sticker` — Reply to any sticker to send after each episode."
        )
        await query.message.edit_text(
            text,
            reply_markup=build_keyboard([[primary_button("↩️ Back to Help Center", "help:main")]]),
        )

    elif data == "help:tags":
        text = (
            "🧩 **TEMPLATE PLACEHOLDERS CHEAT SHEET**\n\n"
            "• `{title}` — Video / show title\n"
            "• `{season}` — 2-digit season number (`01`, `02`)\n"
            "• `{episode}` — 2-digit episode number (`01`, `15`)\n"
            "• `{bolum}` — Global Bölüm number\n"
            "• `{season_episode}` — Formatted `S01 E01`\n"
            "• `{quality}` — Resolution (`720p`, `1080p`)\n"
            "• `{lang}` — Audio language (`Dual Audio`, `Urdu`)\n"
            "• `{source}` — Release source (`WEB-DL`, `BluRay`)\n"
            "• `{year}` — Year (`2024`)\n"
            "• `{sub}` — Subtitle info (`EngSub`, `ESub`)\n"
            "• `{finale}` — `Season Finale` or empty\n"
            "• `{part}` — Part number (`Part 01`)\n"
            "• `{width}` / `{height}` — Pixel dimensions\n"
            "• `{codec}` — Video codec (`h264`)\n"
            "• `{ext}` — Extension (`mp4`)"
        )
        await query.message.edit_text(
            text,
            reply_markup=build_keyboard([[primary_button("↩️ Back to Help Center", "help:main")]]),
        )

    elif data == "help:settings":
        text = (
            "⚙️ **SETTINGS & PREFERENCES**\n\n"
            "• `/settings` — Main settings panel.\n"
            "• `/setquality <q1> <q2> ...` — Set default qualities (e.g. `/setquality 480p 720p 1080p`).\n"
            "• `/setprofile <profile>` — Set encoding profile (`standard`, `mobile`, `hq`).\n"
            "• `/reset` — Reset all user preferences to default values."
        )
        await query.message.edit_text(
            text,
            reply_markup=build_keyboard([[primary_button("↩️ Back to Help Center", "help:main")]]),
        )
