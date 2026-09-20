"""
Telegram Bot client setup, dispatcher, and event loop lifecycle.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from pyrogram import Client, filters
from pyrogram.handlers import CallbackQueryHandler, MessageHandler
from pyrogram.types import CallbackQuery, Message

from app.config import config
from app.database.database import db
from app.handlers.batch import batch_cmd, handle_batch_callbacks
from app.handlers.caption import clear_caption_cmd, set_caption_cmd, view_caption_cmd
from app.handlers.destination import (
    clear_destination_cmd,
    set_destination_cmd,
    set_profile_cmd,
    set_quality_cmd,
    sticker_cmd,
)
from app.handlers.encode import (
    encode_cmd,
    handle_text_or_link_input,
    handle_video_input,
    handle_wizard_callbacks,
)
from app.handlers.filename import (
    clear_filename_cmd,
    set_filename_cmd,
    view_filename_cmd,
)
from app.handlers.mediainfo import handle_mediainfo_callbacks, mediainfo_cmd
from app.handlers.season import set_season_cmd, view_seasons_cmd
from app.handlers.settings import handle_settings_callbacks, settings_cmd, upscale_cmd
from app.handlers.start import handle_help_callbacks, help_handler, start_handler
from app.handlers.status import cancel_handler, handle_status_callbacks, status_handler
from app.handlers.thumbnails import (
    clear_thumbs_cmd,
    handle_thumbnail_callbacks,
    handle_thumbnail_messages,
    thumbs_cmd,
)
from app.handlers.upload_settings import (
    clear_header_cmd,
    handle_upload_settings_callbacks,
    handle_upload_settings_text,
    set_delay_cmd,
    set_header_cmd,
    set_staging_cmd,
    upload_settings_cmd,
)
from app.jobs.queue import JobQueueWorker
from app.jobs.recovery import JobRecoveryManager
from app.utils.logging import logger


class VideoEncoderBot:
    """Main Telegram Video Encoder Bot Application."""

    def __init__(self) -> None:
        session_name = str(config.storage.base_dir / "encoder_bot_session")
        self.client = Client(
            name=session_name,
            api_id=config.bot.api_id,
            api_hash=config.bot.api_hash,
            bot_token=config.bot.bot_token,
            workdir=str(config.storage.base_dir),
        )
        self.worker = JobQueueWorker(self.client)

    async def start(self) -> None:
        logger.info("Initializing SQLite database...")
        await db.init()

        logger.info("Reconciling jobs from previous runs...")
        await JobRecoveryManager.recover_jobs()

        logger.info("Registering bot message and callback handlers...")
        self._register_handlers()

        logger.info("Starting Telegram Bot Client...")
        await self.client.start()

        me = await self.client.get_me()
        logger.info(f"Bot connected successfully as @{me.username} (ID: {me.id})")

        logger.info("Starting Job Queue Worker...")
        await self.worker.start()

        # Send restarted bot notification to Owner(s)
        if config.bot.owner_ids:
            try:
                from datetime import datetime
                from pyrogram.enums import ParseMode
                now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                for owner_id in config.bot.owner_ids:
                    restart_msg = (
                        "🤖 **ENCODER FORGE BOT ONLINE**\n\n"
                        "🟢 **Status:** `Active & Ready`\n"
                        f"👤 **Bot:** @{me.username} (`{me.id}`)\n"
                        f"👑 **Owner ID:** `{owner_id}`\n"
                        "⚡ **Crypto Engine:** `TgCrypto Accelerated (C/Rust)`\n"
                        "📦 **Job Queue:** `Worker Active (Single Pass)`\n"
                        f"⏱ **Started At:** `{now_str}`\n"
                        "━━━━━━━━━━━━━━━━━━━━\n"
                        "🎬 _Ready to encode, batch convert & publish videos._"
                    )
                    try:
                        await self.client.send_message(
                            chat_id=owner_id,
                            text=restart_msg,
                            parse_mode=ParseMode.MARKDOWN,
                        )
                        logger.info(f"Sent startup notification to owner ID {owner_id}.")
                    except Exception as send_err:
                        logger.warning(f"Could not send startup alert to owner {owner_id}: {send_err}")
            except Exception as notify_err:
                logger.warning(f"Could not send startup alert to owner: {notify_err}")


    async def stop(self) -> None:
        logger.info("Stopping Job Queue Worker...")
        await self.worker.stop()

        logger.info("Stopping Telegram Bot Client...")
        await self.client.stop()
        logger.info("Bot stopped successfully.")

    def _register_handlers(self) -> None:
        # /start & /help
        self.client.add_handler(MessageHandler(start_handler, filters.command(["start"])))
        self.client.add_handler(MessageHandler(help_handler, filters.command(["help"])))

        # /encode & /batch & /mediainfo (/info, /probe, /details)
        self.client.add_handler(MessageHandler(encode_cmd, filters.command(["encode"])))
        self.client.add_handler(MessageHandler(batch_cmd, filters.command(["batch"])))
        self.client.add_handler(MessageHandler(mediainfo_cmd, filters.command(["mediainfo", "info", "probe", "details"])))

        # /status & /refresh & /cancel
        async def _status(c: Client, m: Message) -> None:
            await status_handler(c, m, self.worker)

        async def _cancel(c: Client, m: Message) -> None:
            await cancel_handler(c, m, self.worker)

        self.client.add_handler(MessageHandler(_status, filters.command(["status", "refresh"])))
        self.client.add_handler(MessageHandler(_cancel, filters.command(["cancel"])))

        # /settings & /reset & /upscale
        self.client.add_handler(MessageHandler(settings_cmd, filters.command(["settings", "reset"])))
        self.client.add_handler(MessageHandler(upscale_cmd, filters.command(["upscale"])))

        # Upload settings & header commands
        self.client.add_handler(MessageHandler(upload_settings_cmd, filters.command(["uploadsettings"])))
        self.client.add_handler(MessageHandler(set_header_cmd, filters.command(["setheader", "episode"])))
        self.client.add_handler(MessageHandler(clear_header_cmd, filters.command(["clearheader"])))
        self.client.add_handler(MessageHandler(set_staging_cmd, filters.command(["setstaging"])))
        self.client.add_handler(MessageHandler(set_delay_cmd, filters.command(["setdelay"])))

        # Filename commands
        self.client.add_handler(MessageHandler(set_filename_cmd, filters.command(["setfilename"])))
        self.client.add_handler(MessageHandler(view_filename_cmd, filters.command(["filename"])))
        self.client.add_handler(MessageHandler(clear_filename_cmd, filters.command(["clearfilename"])))

        # Caption commands
        self.client.add_handler(MessageHandler(set_caption_cmd, filters.command(["setcaption"])))
        self.client.add_handler(MessageHandler(view_caption_cmd, filters.command(["caption"])))
        self.client.add_handler(MessageHandler(clear_caption_cmd, filters.command(["clearcaption"])))

        # Thumbnail commands
        self.client.add_handler(MessageHandler(thumbs_cmd, filters.command(["thumbs", "thumb"])))
        self.client.add_handler(MessageHandler(clear_thumbs_cmd, filters.command(["clearthumbs"])))

        # Season commands
        self.client.add_handler(MessageHandler(set_season_cmd, filters.command(["setseason"])))
        self.client.add_handler(MessageHandler(view_seasons_cmd, filters.command(["seasons"])))

        # Destination, Sticker, Quality commands
        self.client.add_handler(MessageHandler(set_destination_cmd, filters.command(["setdestination"])))
        self.client.add_handler(MessageHandler(clear_destination_cmd, filters.command(["cleardestination"])))
        self.client.add_handler(MessageHandler(sticker_cmd, filters.command(["sticker"])))
        self.client.add_handler(MessageHandler(set_quality_cmd, filters.command(["setquality"])))
        self.client.add_handler(MessageHandler(set_profile_cmd, filters.command(["setprofile"])))

        # Direct media listener (videos, video documents, animations)
        self.client.add_handler(
            MessageHandler(handle_video_input, filters.video | filters.document | filters.animation)
        )

        # General text listener for wizard inputs, links, and staging channel configuration
        async def _text_dispatcher(c: Client, m: Message) -> None:
            if await handle_upload_settings_text(c, m):
                return
            if await handle_thumbnail_messages(c, m):
                return
            await handle_text_or_link_input(c, m)

        self.client.add_handler(
            MessageHandler(_text_dispatcher, (filters.text | filters.photo) & ~filters.command([]))
        )

        # Unified callback query dispatcher
        async def _callback_dispatcher(c: Client, q: CallbackQuery) -> None:
            data = q.data or ""
            if data.startswith("help:") or data == "nav:batch_prompt":
                await handle_help_callbacks(c, q)
                await q.answer()
                return

            if data.startswith("status:") or data == "nav:status" or data.startswith("job:cancel:"):
                await handle_status_callbacks(c, q, self.worker)
                return

            if data.startswith("upset:") or data == "nav:uploadsettings":
                await handle_upload_settings_callbacks(c, q)
                await q.answer()
                return

            if data.startswith("batch:"):
                await handle_batch_callbacks(c, q, self.worker)
                await q.answer()
                return

            if data.startswith("minfo:"):
                await handle_mediainfo_callbacks(c, q)
                await q.answer()
                return

            if data.startswith("th_"):
                await handle_thumbnail_callbacks(c, q)
                await q.answer()
                return

            if data.startswith("set_") or data.startswith("nav:settings"):
                await handle_settings_callbacks(c, q)
                await q.answer()
                return

            if data.startswith("wiz:") or data.startswith("nav:encode") or data == "wizard:cancel":
                await handle_wizard_callbacks(c, q, self.worker)
                await q.answer()
                return

        self.client.add_handler(CallbackQueryHandler(_callback_dispatcher))
