"""
High-speed parallel multi-part MTProto downloader for Pyrofork / Pyrogram.
Spawns multiple concurrent media sessions using 1MB chunk offsets
to bypass Telegram single-connection throttling and achieve maximum line-rate download speeds.
"""

from __future__ import annotations

import asyncio
import math
import os
import shutil
import time
from pathlib import Path
from typing import Awaitable, Callable, List, Optional, Union

from pyrogram import Client, raw, utils
from pyrogram.file_id import FileId, FileType, PHOTO_TYPES
from pyrogram.session import Auth, Session
from pyrogram.types import Message

from app.config import config
from app.utils.formatting import human_bytes, human_eta
from app.utils.logging import logger

CHUNK_SIZE = 1024 * 1024  # 1 MB optimal MTProto chunk
MAX_WORKERS = 8  # Parallel sessions per download


class FastParallelDownloader:
    """Parallel multi-part downloader using pooled MTProto media sessions."""

    def __init__(self, client: Client, max_workers: int = MAX_WORKERS) -> None:
        self.client = client
        self.max_workers = max_workers

    async def download_file(
        self,
        media_or_msg: Union[Message, str],
        target_path: Path,
        progress_callback: Optional[Callable[[int, int, float, str, str], Awaitable[None]]] = None,
        known_total_bytes: int = 0,
    ) -> Path:
        """
        Downloads media attached to a Telegram message or file_id in parallel chunks.
        """
        # 1. Resolve media item & FileId
        file_id_str, file_size, default_name = self._extract_media_info(media_or_msg)
        if known_total_bytes > 0 and (not file_size or file_size == 0):
            file_size = known_total_bytes

        if not file_size or file_size < 2 * CHUNK_SIZE:
            # For small files, standard client download is faster and has lower session setup overhead
            raise ValueError(f"File size ({file_size} bytes) too small for parallel download; fallback to standard.")

        file_id = FileId.decode(file_id_str)
        location = self._get_input_location(file_id)
        dc_id = file_id.dc_id

        # Determine target file path
        if target_path.is_dir():
            target_file = target_path / (default_name or f"download_{int(time.time())}.mp4")
        else:
            target_file = target_path

        target_file.parent.mkdir(parents=True, exist_ok=True)
        temp_file = target_file.with_suffix(target_file.suffix + ".parallel.temp")

        # 2. Pre-allocate disk space
        with open(temp_file, "wb") as f:
            if file_size > 0:
                f.seek(file_size - 1)
                f.write(b"\0")

        num_chunks = math.ceil(file_size / CHUNK_SIZE)
        worker_count = min(self.max_workers, num_chunks)

        logger.info(
            f"Starting high-speed parallel download: {target_file.name} "
            f"({human_bytes(file_size)}, {num_chunks} chunks of 1MB, {worker_count} parallel streams, DC {dc_id})"
        )

        # 3. Create parallel media session pool
        sessions: List[Session] = []
        try:
            for i in range(worker_count):
                auth_key = (
                    await Auth(self.client, dc_id, await self.client.storage.test_mode()).create()
                    if dc_id != await self.client.storage.dc_id()
                    else await self.client.storage.auth_key()
                )
                session = Session(
                    self.client,
                    dc_id,
                    auth_key,
                    await self.client.storage.test_mode(),
                    is_media=True,
                )
                await session.start()

                if dc_id != await self.client.storage.dc_id():
                    exported_auth = await self.client.invoke(raw.functions.auth.ExportAuthorization(dc_id=dc_id))
                    await session.invoke(
                        raw.functions.auth.ImportAuthorization(id=exported_auth.id, bytes=exported_auth.bytes)
                    )
                sessions.append(session)

            # 4. Fill download queue
            queue: asyncio.Queue[tuple[int, int, int]] = asyncio.Queue()
            for chunk_idx in range(num_chunks):
                offset_bytes = chunk_idx * CHUNK_SIZE
                limit_bytes = min(CHUNK_SIZE, file_size - offset_bytes)
                queue.put_nowait((chunk_idx, offset_bytes, limit_bytes))

            downloaded_bytes = 0
            file_lock = asyncio.Lock()
            start_time = time.time()
            last_update_time = 0.0

            file_handle = open(temp_file, "r+b")

            async def _worker(session_instance: Session):
                nonlocal downloaded_bytes, last_update_time
                while not queue.empty():
                    try:
                        chunk_idx, offset_bytes, limit_bytes = queue.get_nowait()
                    except asyncio.QueueEmpty:
                        break

                    retries = 3
                    while retries > 0:
                        try:
                            res = await session_instance.invoke(
                                raw.functions.upload.GetFile(
                                    location=location,
                                    offset=offset_bytes,
                                    limit=limit_bytes,
                                ),
                                sleep_threshold=30,
                            )
                            if isinstance(res, raw.types.upload.File):
                                async with file_lock:
                                    file_handle.seek(offset_bytes)
                                    file_handle.write(res.bytes)
                                downloaded_bytes += len(res.bytes)

                                # Progress notification
                                now = time.time()
                                if (now - last_update_time >= 1.5) or (downloaded_bytes >= file_size):
                                    last_update_time = now
                                    if progress_callback:
                                        elapsed = now - start_time
                                        speed_bps = downloaded_bytes / elapsed if elapsed > 0 else 0
                                        pct = (downloaded_bytes / file_size) * 100.0 if file_size > 0 else 0.0
                                        eta_sec = (file_size - downloaded_bytes) / speed_bps if (file_size > 0 and speed_bps > 0) else 0
                                        try:
                                            await progress_callback(
                                                downloaded_bytes,
                                                file_size,
                                                pct,
                                                f"{human_bytes(speed_bps)}/s",
                                                human_eta(eta_sec),
                                            )
                                        except Exception as cb_err:
                                            logger.warning(f"Download callback error: {cb_err}")

                                queue.task_done()
                                break
                            elif isinstance(res, raw.types.upload.FileCdnRedirect):
                                # Fallback to standard Pyrogram downloader for CDN files
                                raise ValueError("CDN redirect encountered, routing to standard engine.")
                            else:
                                raise RuntimeError(f"Unexpected MTProto GetFile response: {type(res)}")
                        except Exception as req_err:
                            retries -= 1
                            if retries == 0:
                                queue.task_done()
                                raise req_err
                            await asyncio.sleep(0.5)

            # 5. Run parallel workers
            worker_tasks = [asyncio.create_task(_worker(s)) for s in sessions]
            await asyncio.gather(*worker_tasks)
            file_handle.close()

            # 6. Finalize file
            if temp_file.exists():
                if target_file.exists():
                    target_file.unlink()
                shutil.move(str(temp_file), str(target_file))

            logger.info(
                f"Parallel download finished: {target_file.name} ({human_bytes(target_file.stat().st_size)}) "
                f"in {time.time() - start_time:.2f}s"
            )
            return target_file

        finally:
            # Clean up sessions and temporary files
            for s in sessions:
                try:
                    await s.stop()
                except Exception:
                    pass
            if temp_file.exists() and not target_file.exists():
                try:
                    temp_file.unlink()
                except Exception:
                    pass

    @staticmethod
    def _extract_media_info(media_or_msg: Union[Message, str]) -> tuple[str, int, str]:
        """Extracts file_id, file_size, and filename from Message or str."""
        if isinstance(media_or_msg, str):
            return media_or_msg, 0, ""

        if isinstance(media_or_msg, Message):
            available_media = (
                "video",
                "document",
                "audio",
                "animation",
                "photo",
                "voice",
                "video_note",
            )
            for kind in available_media:
                media = getattr(media_or_msg, kind, None)
                if media is not None:
                    if kind == "photo":
                        # Photos can have multiple sizes; pick largest
                        photo = media_or_msg.photo
                        return photo.file_id, photo.file_size, f"photo_{photo.file_unique_id}.jpg"
                    return (
                        getattr(media, "file_id", ""),
                        getattr(media, "file_size", 0),
                        getattr(media, "file_name", ""),
                    )

        raise ValueError("Could not extract downloadable media from provided object.")

    @staticmethod
    def _get_input_location(file_id: FileId) -> raw.base.InputFileLocation:
        """Constructs MTProto InputFileLocation from FileId."""
        file_type = file_id.file_type

        if file_type in PHOTO_TYPES:
            return raw.types.InputPhotoFileLocation(
                id=file_id.media_id,
                access_hash=file_id.access_hash,
                file_reference=file_id.file_reference,
                thumb_size=file_id.thumbnail_size,
            )
        elif file_type == FileType.CHAT_PHOTO:
            if file_id.chat_id > 0:
                peer = raw.types.InputPeerUser(
                    user_id=file_id.chat_id,
                    access_hash=file_id.chat_access_hash,
                )
            else:
                if file_id.chat_access_hash == 0:
                    peer = raw.types.InputPeerChat(chat_id=-file_id.chat_id)
                else:
                    peer = raw.types.InputPeerChannel(
                        channel_id=utils.get_channel_id(file_id.chat_id),
                        access_hash=file_id.chat_access_hash,
                    )
            return raw.types.InputPeerPhotoFileLocation(
                peer=peer,
                photo_id=file_id.media_id,
                big=file_id.thumbnail_source == 1,
            )
        else:
            return raw.types.InputDocumentFileLocation(
                id=file_id.media_id,
                access_hash=file_id.access_hash,
                file_reference=file_id.file_reference,
                thumb_size=file_id.thumbnail_size,
            )
