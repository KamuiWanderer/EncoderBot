"""
Telegram single-pass media downloader.
Downloads the source video file exactly once to local storage.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Awaitable, Callable, Optional
from pyrogram import Client
from pyrogram.types import Message

from app.config import config
from app.telegram.parallel_downloader import FastParallelDownloader
from app.utils.formatting import human_bytes, human_eta, progress_bar
from app.utils.logging import logger


class TelegramDownloader:
    """Manages downloading files from Telegram with progress updates and rate limit throttling."""

    def __init__(self, client: Client) -> None:
        self.client = client
        self.parallel_downloader = FastParallelDownloader(client)

    async def download_media(
        self,
        message: Message | str,
        destination_path: Optional[str | Path] = None,
        progress_callback: Optional[Callable[[int, int, float, str, str], Awaitable[None]]] = None,
        known_total_bytes: int = 0,
    ) -> Path:
        """
        Downloads media attached to a Telegram message or file_id to local storage.
        Uses high-speed parallel MTProto streams when possible, falling back to standard download.
        """
        downloads_dir = config.storage.downloads_dir
        downloads_dir.mkdir(parents=True, exist_ok=True)
        target_dest = Path(destination_path) if destination_path else downloads_dir

        # Try high-speed parallel downloader first
        try:
            return await self.parallel_downloader.download_file(
                media_or_msg=message,
                target_path=target_dest,
                progress_callback=progress_callback,
                known_total_bytes=known_total_bytes,
            )
        except Exception as e:
            logger.warning(
                f"Fast parallel download unavailable or skipped ({e}), falling back to standard sequential download..."
            )

        last_update_time = 0.0
        start_time = time.time()

        async def _progress(current: int, total: int) -> None:
            nonlocal last_update_time
            now = time.time()
            effective_total = total if total > 0 else known_total_bytes
            pct = (current / effective_total) * 100.0 if effective_total > 0 else 0.0
            elapsed = now - start_time
            speed_bps = current / elapsed if elapsed > 0 else 0
            speed_str = f"{human_bytes(speed_bps)}/s"
            eta_sec = (effective_total - current) / speed_bps if (effective_total > 0 and speed_bps > 0) else 0
            eta_str = human_eta(eta_sec) if effective_total > 0 else "Calculating..."

            if (now - last_update_time >= 1.5) or (effective_total > 0 and current >= effective_total):
                last_update_time = now
                if progress_callback:
                    try:
                        await progress_callback(current, effective_total, pct, speed_str, eta_str)
                    except Exception as err:
                        logger.warning(f"Error in download progress callback: {err}")

        if isinstance(message, Message):
            chat_identifier = message.chat.id if message.chat else "unknown"
            logger.info(f"Initiating single-source download for message {message.id} in chat {chat_identifier}...")
        else:
            logger.info(f"Initiating single-source download for media file_id: {str(message)[:24]}...")

        target_file = str(target_dest)

        downloaded_path = await self.client.download_media(
            message=message,
            file_name=target_file,
            progress=_progress,
        )

        if not downloaded_path:
            raise RuntimeError("Telegram media download returned empty path.")

        res_path = Path(downloaded_path).resolve()
        logger.info(f"Source download complete: {res_path.name} ({human_bytes(res_path.stat().st_size)})")
        return res_path

