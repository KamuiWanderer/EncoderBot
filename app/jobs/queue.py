"""
Async sequential job queue worker with Private Staging & Ordered Delivery support.
Enforces:
1. One source download only.
2. Sequential encoding (240p -> 360p -> 480p -> 720p -> 1080p).
3. Immediate upload to staging/destination & local disk purge per quality.
4. Delete source only after entire episode succeeds.
5. Ordered batch delivery (Episode Header -> Qualities -> Sticker).
6. FloodWait throttled delivery with configurable send_delay.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Optional
from pyrogram import Client
from pyrogram.enums import ParseMode

from app.config import config
from app.database.database import db
from app.database.models import EncodeJob
from app.encoder.ffmpeg import FFmpegEncoder
from app.encoder.presets import calculate_scale, get_quality_height, normalize_quality_name, sort_qualities_ascending
from app.encoder.probe import MediaProbeInfo, probe_media
from app.encoder.progress import EncodeProgress
from app.jobs.job import JobStatus
from app.managers.thumbnail_manager import ThumbnailManager
from app.managers.upload_config_manager import UploadConfigManager
from app.metadata.parser import MetadataParser, ParsedMetadata
from app.metadata.placeholders import TemplateContext, TemplateEngine
from app.metadata.season import SeasonManager
from app.telegram.downloader import TelegramDownloader
from app.telegram.links import TelegramLinkParser
from app.telegram.media import MediaExtractor
from app.telegram.uploader import TelegramUploader
from app.utils.disk import check_disk_space_sufficient
from app.utils.files import safe_delete
from app.utils.formatting import human_bytes, human_duration, human_eta, progress_bar
from app.utils.logging import logger


class JobQueueWorker:
    """Processes encoding jobs sequentially and manages staging delivery."""

    def __init__(self, client: Client) -> None:
        self.client = client
        self.downloader = TelegramDownloader(client)
        self.uploader = TelegramUploader(client)
        self.encoder = FFmpegEncoder()

        self._queue: asyncio.Queue[str] = asyncio.Queue()
        self._worker_task: Optional[asyncio.Task] = None
        self._current_job: Optional[EncodeJob] = None
        self._is_running: bool = False

    @property
    def current_job(self) -> Optional[EncodeJob]:
        return self._current_job

    async def start(self) -> None:
        if self._is_running:
            return
        self._is_running = True
        self._worker_task = asyncio.create_task(self._run_loop())
        logger.info("JobQueueWorker started.")

    async def stop(self) -> None:
        self._is_running = False
        if self._current_job:
            self.encoder.cancel()
        if self._worker_task:
            self._worker_task.cancel()
            try:
                await self._worker_task
            except asyncio.CancelledError:
                pass
        logger.info("JobQueueWorker stopped.")

    async def enqueue_job(self, job: EncodeJob) -> None:
        await db.save_job(job)
        await self._queue.put(job.job_id)
        logger.info(f"Job {job.job_id} enqueued. Queue size: {self._queue.qsize()}")

    def cancel_active_job(self, job_id: str) -> bool:
        if self._current_job and self._current_job.job_id == job_id:
            logger.info(f"Cancelling active job {job_id}...")
            self.encoder.cancel()
            return True
        return False

    async def _run_loop(self) -> None:
        while self._is_running:
            try:
                queued_jobs = await db.get_queued_jobs()
                if queued_jobs:
                    job = queued_jobs[0]
                else:
                    job_id = await self._queue.get()
                    job = await db.get_job(job_id)
                    if not job or job.status != JobStatus.QUEUED:
                        continue

                self._current_job = job
                await self._process_job(job)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in JobQueueWorker loop: {e}", exc_info=True)
                await asyncio.sleep(3.0)
            finally:
                self._current_job = None
                await asyncio.sleep(1.0)

    async def _process_job(self, job: EncodeJob) -> None:
        logger.info(f"Processing job {job.job_id} ({job.source_title}) [Batch {job.batch_index}/{job.batch_total}]...")
        local_source_path: Optional[Path] = None

        try:
            # 1. Disk Space Check
            is_ok, disk_msg = check_disk_space_sufficient(job.source_size_bytes, len(job.selected_qualities))
            if not is_ok:
                job.status = JobStatus.FAILED
                job.error_message = disk_msg
                await db.save_job(job)
                await self._notify_user(job, f"❌ **Job Failed**\n\n{disk_msg}")
                return

            # 2. Download source EXACTLY ONCE
            job.status = JobStatus.DOWNLOADING
            await db.save_job(job)
            await self._update_status_msg(job, "📥 **Downloading source video (1×)...**\n\n`Connecting to Telegram...`")

            local_source_path = await self._download_source(job)
            if not local_source_path or not local_source_path.exists():
                raise RuntimeError("Source video download failed or file is empty.")

            job.source_path = str(local_source_path)
            job.source_size_bytes = local_source_path.stat().st_size
            await db.save_job(job)

            # 3. Probe source media
            probe_info = await probe_media(local_source_path)
            job.source_duration_seconds = probe_info.duration_seconds
            await db.save_job(job)

            # 4. Resolve metadata, season arithmetic & finale
            parsed_meta = MetadataParser.parse(job.source_title)
            global_bolum = await SeasonManager.get_bolum_for_episode(
                user_id=job.user_id,
                season=parsed_meta.season,
                episode=parsed_meta.episode,
            )
            season_finale = await SeasonManager.check_season_finale(
                user_id=job.user_id,
                season=parsed_meta.season,
                global_bolum=global_bolum,
            )

            # Resolve thumbnail for this episode
            thumb_target = await ThumbnailManager.get_thumbnail_for_episode(
                user_id=job.user_id,
                episode=parsed_meta.episode or global_bolum,
            )

            local_thumb_file: Optional[Path] = None
            if thumb_target:
                if not Path(thumb_target).exists():
                    try:
                        downloaded_thumb = await self.client.download_media(thumb_target)
                        if downloaded_thumb:
                            norm_jpg = ThumbnailManager.normalize_thumbnail_image(downloaded_thumb)
                            local_thumb_file = Path(norm_jpg)
                    except Exception as thumb_err:
                        logger.warning(f"Could not prepare thumbnail: {thumb_err}")
                else:
                    norm_jpg = ThumbnailManager.normalize_thumbnail_image(thumb_target)
                    local_thumb_file = Path(norm_jpg)

            user_settings = await db.get_user_settings(job.user_id)
            allow_upscale = user_settings.allow_upscale

            # Staging vs Immediate target
            use_staging = (job.upload_timing == "staging") and bool(job.staging_channel)
            target_upload_chat = job.staging_channel if use_staging else (job.destination_chat_id or job.chat_id)

            # 5. Send Episode Header if in Immediate mode
            if not use_staging and job.episode_header_enabled:
                header_text = UploadConfigManager.render_episode_header(
                    template=job.episode_header_template,
                    metadata=parsed_meta,
                    calculated_bolum=global_bolum,
                    calculated_finale=season_finale,
                )
                if header_text:
                    try:
                        await self.client.send_message(
                            chat_id=target_upload_chat,
                            text=header_text,
                            parse_mode=ParseMode.HTML,
                        )
                        await asyncio.sleep(job.send_delay)
                    except Exception as hdr_err:
                        logger.warning(f"Error sending episode header: {hdr_err}")

            # 6. Sequential Encoding Pipeline
            total_qualities = len(job.selected_qualities)
            for idx, quality in enumerate(job.selected_qualities, start=1):
                norm_q = normalize_quality_name(quality)

                if norm_q in job.completed_qualities:
                    continue

                if self.encoder.is_cancelled:
                    job.status = JobStatus.CANCELLED
                    await db.save_job(job)
                    await self._notify_user(job, "⛔ **Job Cancelled by user.**")
                    return

                job.status = JobStatus.ENCODING
                job.current_quality = norm_q
                await db.save_job(job)

                out_filename = TemplateEngine.render_filename(
                    template=job.filename_template,
                    ctx=TemplateContext(
                        metadata=parsed_meta,
                        quality=norm_q,
                        width=probe_info.width,
                        height=probe_info.height,
                        codec="h264",
                        calculated_bolum=global_bolum,
                        calculated_finale=season_finale,
                    ),
                )
                output_path = config.storage.outputs_dir / f"{job.job_id}_{norm_q}_{out_filename}"

                async def _on_encode_progress(prog: EncodeProgress) -> None:
                    job.progress_percent = prog.percentage
                    job.progress_speed = f"{prog.speed:.2f}x"
                    job.progress_eta = human_eta(prog.eta_seconds)
                    status_text = (
                        f"🎬 **Encoding [{job.batch_index}/{job.batch_total}] Quality {idx}/{total_qualities} ({norm_q})**\n\n"
                        f"📁 `{out_filename}`\n"
                        f"`{progress_bar(prog.percentage)}`\n\n"
                        f"⏱ **Time:** `{human_duration(prog.out_time_seconds)}`\n"
                        f"⚡ **Speed:** `{job.progress_speed}`\n"
                        f"⏳ **ETA:** `{job.progress_eta}`"
                    )
                    await self._update_status_msg(job, status_text)

                encode_res = await self.encoder.encode(
                    input_path=local_source_path,
                    output_path=output_path,
                    quality=norm_q,
                    profile_name=job.encoding_profile,
                    probe_info=probe_info,
                    progress_callback=_on_encode_progress,
                    allow_upscale=allow_upscale,
                )

                if encode_res.was_cancelled:
                    job.status = JobStatus.CANCELLED
                    await db.save_job(job)
                    await self._notify_user(job, "⛔ **Job cancelled by user.**")
                    return

                if not encode_res.success:
                    logger.error(f"Encoding failed for quality {norm_q}: {encode_res.error_message}")
                    job.failed_qualities.append(norm_q)
                    await db.save_job(job)
                    continue

                # 7. Upload Encoded Quality (Immediate or Staging)
                job.status = JobStatus.UPLOADING if not use_staging else "staging"
                await db.save_job(job)

                scale_res = calculate_scale(probe_info.width, probe_info.height, norm_q, allow_upscale=allow_upscale)
                caption_text = TemplateEngine.render_caption(
                    template=job.caption_template,
                    ctx=TemplateContext(
                        metadata=parsed_meta,
                        quality=norm_q,
                        width=scale_res.width,
                        height=scale_res.height,
                        codec="h264",
                        calculated_bolum=global_bolum,
                        calculated_finale=season_finale,
                    ),
                )

                async def _on_upload_progress(curr: int, tot: int, pct: float, eta_s: str) -> None:
                    job.progress_percent = pct
                    dest_label = "Staging Buffer" if use_staging else "Destination"
                    status_text = (
                        f"📤 **Uploading to {dest_label} ({norm_q}) [{idx}/{total_qualities}]**\n\n"
                        f"📁 `{out_filename}`\n"
                        f"`{progress_bar(pct)}`\n\n"
                        f"📊 **Uploaded:** `{human_bytes(curr)} / {human_bytes(tot)}`\n"
                        f"⏳ **ETA:** `{eta_s}`"
                    )
                    await self._update_status_msg(job, status_text)

                uploaded_msg = await self.uploader.upload_video(
                    chat_id=target_upload_chat,
                    video_path=encode_res.output_path,
                    caption=caption_text,
                    thumbnail_path=str(local_thumb_file) if local_thumb_file else None,
                    duration=int(encode_res.duration_seconds),
                    width=scale_res.width,
                    height=scale_res.height,
                    progress_callback=_on_upload_progress,
                )

                # Record staging message ID if in staging mode
                if use_staging and uploaded_msg:
                    job.staged_outputs[norm_q] = uploaded_msg.id

                # Delete local output file immediately
                safe_delete(encode_res.output_path)
                job.completed_qualities.append(norm_q)
                await db.save_job(job)
                await asyncio.sleep(job.send_delay)

            # 8. Post-Episode actions
            if local_thumb_file:
                safe_delete(local_thumb_file)

            # If immediate mode and per_episode sticker enabled
            if not use_staging and job.sticker_mode == "per_episode" and job.sticker_id:
                await self.uploader.send_sticker(target_upload_chat, job.sticker_id)
                await asyncio.sleep(job.send_delay)

            # Mark current job completed
            job.status = JobStatus.COMPLETED
            job.progress_percent = 100.0
            await db.save_job(job)

            # 9. If Staging Mode & Last Episode of Batch -> Trigger Ordered Delivery!
            if use_staging:
                if not job.batch_id or (job.batch_index == job.batch_total):
                    await self._deliver_staged_batch(job)

            summary_text = (
                f"✅ **Episode Completed:** `{job.source_title}`\n"
                f"🎞 Qualities: {', '.join(job.completed_qualities)}\n"
                f"📤 Destination: `{job.destination_chat_id or 'Current Chat'}`"
            )
            await self._notify_user(job, summary_text)

        except Exception as e:
            logger.error(f"Job {job.job_id} failed: {e}", exc_info=True)
            job.status = JobStatus.FAILED
            job.error_message = str(e)
            await db.save_job(job)
            await self._notify_user(job, f"❌ **Job Failed:** `{e}`")

        finally:
            if local_source_path and local_source_path.exists():
                safe_delete(local_source_path)
                logger.info(f"Deleted local source video {local_source_path.name}")

    async def _deliver_staged_batch(self, trigger_job: EncodeJob) -> None:
        """
        Delivers staged episodes from Private Staging channel to the public Destination channel
        in the exact, perfect ordered sequence:
        Header -> Qualities (ascending) -> Sticker -> Next Episode...
        """
        dest_chat = trigger_job.destination_chat_id or trigger_job.chat_id
        staging_chat = trigger_job.staging_channel
        if not staging_chat or not dest_chat:
            return

        logger.info(f"Starting Ordered Delivery from staging {staging_chat} to destination {dest_chat}...")

        # Fetch all jobs in this batch (or just this single job)
        if trigger_job.batch_id:
            batch_jobs = await db.get_batch_jobs(trigger_job.batch_id)
        else:
            batch_jobs = [trigger_job]

        for ep_job in batch_jobs:
            if ep_job.status != JobStatus.COMPLETED or not ep_job.staged_outputs:
                continue

            parsed_meta = MetadataParser.parse(ep_job.source_title)
            global_bolum = await SeasonManager.get_bolum_for_episode(
                user_id=ep_job.user_id,
                season=parsed_meta.season,
                episode=parsed_meta.episode,
            )
            season_finale = await SeasonManager.check_season_finale(
                user_id=ep_job.user_id,
                season=parsed_meta.season,
                global_bolum=global_bolum,
            )

            # 1. Send Episode Header banner
            if ep_job.episode_header_enabled:
                header_text = UploadConfigManager.render_episode_header(
                    template=ep_job.episode_header_template,
                    metadata=parsed_meta,
                    calculated_bolum=global_bolum,
                    calculated_finale=season_finale,
                )
                if header_text:
                    try:
                        await self.client.send_message(
                            chat_id=dest_chat,
                            text=header_text,
                            parse_mode=ParseMode.HTML,
                        )
                        await asyncio.sleep(ep_job.send_delay)
                    except Exception as e:
                        logger.warning(f"Error sending delivery header: {e}")

            # 2. Forward/Copy staged videos in ascending quality order
            sorted_qualities = sort_qualities_ascending(list(ep_job.staged_outputs.keys()))
            for q in sorted_qualities:
                msg_id = ep_job.staged_outputs.get(q)
                if not msg_id:
                    continue
                try:
                    await self.client.copy_message(
                        chat_id=dest_chat,
                        from_chat_id=staging_chat,
                        message_id=msg_id,
                    )
                    await asyncio.sleep(ep_job.send_delay)
                except Exception as copy_err:
                    logger.warning(f"Error copying quality {q} from staging: {copy_err}")

            # 3. Post-Episode sticker
            if ep_job.sticker_mode == "per_episode" and ep_job.sticker_id:
                await self.uploader.send_sticker(dest_chat, ep_job.sticker_id)
                await asyncio.sleep(ep_job.send_delay)

        # 4. Final Batch sticker (if per_job)
        if trigger_job.sticker_mode == "per_job" and trigger_job.sticker_id:
            await self.uploader.send_sticker(dest_chat, trigger_job.sticker_id)

        logger.info("Ordered Delivery to destination completed successfully.")

    async def _download_source(self, job: EncodeJob) -> Path:
        async def _on_dl_progress(curr: int, tot: int, pct: float, speed_s: str, eta_s: str) -> None:
            job.progress_percent = pct
            job.progress_speed = speed_s
            job.progress_eta = eta_s
            tot_str = human_bytes(tot) if tot > 0 else "Unknown"
            status_text = (
                f"📥 **Downloading Source Video [{job.batch_index}/{job.batch_total}] (1×)**\n\n"
                f"🎬 `{job.source_title}`\n"
                f"`{progress_bar(pct)}`\n\n"
                f"📊 **Downloaded:** `{human_bytes(curr)} / {tot_str}`\n"
                f"⚡ **Speed:** `{speed_s}`\n"
                f"⏳ **ETA:** `{eta_s}`"
            )
            await self._update_status_msg(job, status_text)

        if job.source_type == "telegram_link" and job.source_link:
            parsed = TelegramLinkParser.parse(job.source_link)
            if not parsed:
                raise ValueError(f"Invalid Telegram message link: {job.source_link}")

            msg = await self.client.get_messages(chat_id=parsed.chat_id, message_ids=parsed.message_id)
            if not msg:
                raise ValueError(f"Could not fetch Telegram message at link: {job.source_link}")

            media_info = MediaExtractor.extract_video_media(msg)
            if not media_info:
                raise ValueError("The linked message does not contain a supported video file.")

            return await self.downloader.download_media(
                msg,
                progress_callback=_on_dl_progress,
                known_total_bytes=job.source_size_bytes or media_info.file_size_bytes,
            )

        elif job.source_file_id:
            target_path = config.storage.downloads_dir / f"{job.job_id}_source_{job.source_title}"
            return await self.downloader.download_media(
                message=job.source_file_id,
                destination_path=target_path,
                progress_callback=_on_dl_progress,
                known_total_bytes=job.source_size_bytes,
            )

        raise ValueError("Job is missing both source_link and source_file_id.")

    async def _update_status_msg(self, job: EncodeJob, text: str) -> None:
        if not job.status_message_id:
            return
        try:
            buttons = [
                [
                    primary_button("🔄 Refresh", f"status:refresh:{job.job_id}"),
                    danger_button("⛔ Cancel Job", f"job:cancel:{job.job_id}"),
                ]
            ]
            await self.client.edit_message_text(
                chat_id=job.chat_id,
                message_id=job.status_message_id,
                text=text,
                reply_markup=build_keyboard(buttons),
                parse_mode=ParseMode.MARKDOWN,
            )
        except Exception:
            pass

    async def _notify_user(self, job: EncodeJob, text: str) -> None:
        try:
            if job.status_message_id:
                await self.client.edit_message_text(
                    chat_id=job.chat_id,
                    message_id=job.status_message_id,
                    text=text,
                    parse_mode=ParseMode.MARKDOWN,
                )
            else:
                await self.client.send_message(
                    chat_id=job.chat_id,
                    text=text,
                    parse_mode=ParseMode.MARKDOWN,
                )
        except Exception as e:
            logger.warning(f"Could not send notification for job {job.job_id}: {e}")
