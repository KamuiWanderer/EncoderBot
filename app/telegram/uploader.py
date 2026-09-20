"""
Telegram media uploader with thumbnail attachment, HTML caption, streaming support, and progress updates.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Awaitable, Callable, Optional, Union
from pyrogram import Client
from pyrogram.enums import ParseMode
from pyrogram.types import Message

from app.utils.formatting import human_bytes, human_eta, progress_bar
from app.utils.logging import logger


class TelegramUploader:
    """Handles uploading encoded video files to destination chats with custom thumbnails."""

    def __init__(self, client: Client) -> None:
        self.client = client

    async def upload_video(
        self,
        chat_id: Union[int, str],
        video_path: str | Path,
        caption: str = "",
        thumbnail_path: Optional[str | Path] = None,
        duration: int = 0,
        width: int = 1280,
        height: int = 720,
        progress_callback: Optional[Callable[[int, int, float, str], Awaitable[None]]] = None,
    ) -> Message:
        """
        Uploads encoded video file to specified Telegram chat.
        Progress callback receives: (current_bytes, total_bytes, percentage, eta_str).
        """
        v_path = Path(video_path).resolve()
        if not v_path.exists():
            raise FileNotFoundError(f"Video file to upload does not exist: {v_path}")

        file_size = v_path.stat().st_size
        thumb = str(thumbnail_path) if thumbnail_path and Path(thumbnail_path).exists() else None

        last_update_time = 0.0
        start_time = time.time()

        async def _progress(current: int, total: int) -> None:
            nonlocal last_update_time
            now = time.time()
            if total > 0:
                pct = (current / total) * 100.0
                elapsed = now - start_time
                speed = current / elapsed if elapsed > 0 else 0
                eta_sec = (total - current) / speed if speed > 0 else 0
                eta_str = human_eta(eta_sec)

                if (now - last_update_time >= 3.0) or current >= total:
                    last_update_time = now
                    if progress_callback:
                        try:
                            await progress_callback(current, total, pct, eta_str)
                        except Exception as e:
                            logger.warning(f"Error in upload progress callback: {e}")

        logger.info(f"Uploading {v_path.name} ({human_bytes(file_size)}) to destination {chat_id}...")

        uploaded_msg = await self.client.send_video(
            chat_id=chat_id,
            video=str(v_path),
            caption=caption,
            parse_mode=ParseMode.HTML,
            duration=duration,
            width=width,
            height=height,
            thumb=thumb,
            supports_streaming=True,
            progress=_progress,
        )

        logger.info(f"Upload complete for {v_path.name}. Message ID: {uploaded_msg.id}")
        return uploaded_msg

    async def send_sticker(self, chat_id: Union[int, str], sticker_id: str) -> Optional[Message]:
        """Sends a configured sticker to the destination chat."""
        try:
            return await self.client.send_sticker(chat_id=chat_id, sticker=sticker_id)
        except Exception as e:
            logger.warning(f"Failed to send post-job sticker {sticker_id}: {e}")
            return None
