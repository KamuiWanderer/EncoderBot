"""
Async FFmpeg execution engine with cancellation support and progress streaming.
"""

from __future__ import annotations

import asyncio
import os
import signal
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional

from app.config import config
from app.encoder.presets import calculate_scale, normalize_quality_name
from app.encoder.probe import MediaProbeInfo, probe_media
from app.encoder.profiles import EncodingProfile, get_profile
from app.encoder.progress import EncodeProgress, ProgressParser
from app.utils.logging import logger


@dataclass
class EncodeResult:
    success: bool
    quality: str
    output_path: Optional[str] = None
    file_size_bytes: int = 0
    duration_seconds: float = 0.0
    error_message: Optional[str] = None
    was_cancelled: bool = False


class FFmpegEncoder:
    """Async FFmpeg encoder managing subprocess lifecycle and progress callbacks."""

    def __init__(self) -> None:
        self.current_process: Optional[asyncio.subprocess.Process] = None
        self.is_cancelled: bool = False

    def cancel(self) -> None:
        """Signals active encode to abort immediately."""
        self.is_cancelled = True
        if self.current_process and self.current_process.returncode is None:
            try:
                if sys.platform == "win32":
                    self.current_process.terminate()
                else:
                    self.current_process.send_signal(signal.SIGINT)
                logger.info("Sent termination signal to active FFmpeg process.")
            except Exception as e:
                logger.warning(f"Error terminating FFmpeg process: {e}")

    async def encode(
        self,
        input_path: str | Path,
        output_path: str | Path,
        quality: str,
        profile_name: str = "standard",
        probe_info: Optional[MediaProbeInfo] = None,
        progress_callback: Optional[Callable[[EncodeProgress], Awaitable[None]]] = None,
        allow_upscale: bool = False,
    ) -> EncodeResult:
        """
        Encodes input video to target quality and profile asynchronously.
        Streams progress updates via progress_callback.
        """
        self.is_cancelled = False
        in_p = Path(input_path).resolve()
        out_p = Path(output_path).resolve()
        out_p.parent.mkdir(parents=True, exist_ok=True)

        if not in_p.exists():
            return EncodeResult(
                success=False,
                quality=quality,
                error_message=f"Input file not found: {in_p}",
            )

        # Probe if not supplied
        if probe_info is None:
            try:
                probe_info = await probe_media(in_p)
            except Exception as e:
                return EncodeResult(
                    success=False,
                    quality=quality,
                    error_message=f"Failed to probe input media: {e}",
                )

        profile = get_profile(profile_name)
        norm_quality = normalize_quality_name(quality)
        scale_res = calculate_scale(
            orig_width=probe_info.width,
            orig_height=probe_info.height,
            target_quality=norm_quality,
            allow_upscale=allow_upscale,
        )

        ffmpeg_bin = config.encoder.ffmpeg_binary

        # Build FFmpeg command arguments
        cmd = [
            ffmpeg_bin,
            "-y",  # Overwrite output without asking
            "-v", "error",  # Keep stderr clean for real errors
            "-progress", "pipe:1",  # Machine-readable progress on stdout
            "-nostats",
            "-i", str(in_p),
            "-vf", scale_res.ffmpeg_filter,
            "-c:v", profile.vcodec,
            "-crf", str(profile.crf),
            "-preset", profile.preset,
            "-pix_fmt", profile.pix_fmt,
            "-c:a", profile.acodec,
            "-b:a", profile.abitrate,
        ]

        # Extra flags (e.g. -movflags +faststart)
        cmd.extend(profile.extra_video_flags)
        cmd.append(str(out_p))

        logger.info(f"Starting FFmpeg encode: {norm_quality} -> {out_p.name} with filter '{scale_res.ffmpeg_filter}'")

        parser = ProgressParser(
            total_duration_seconds=probe_info.duration_seconds,
            total_frames=probe_info.total_frames,
        )

        try:
            self.current_process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            # Read stdout for progress updates
            last_callback_time = 0.0
            stderr_chunks: list[str] = []

            async def read_stderr() -> None:
                if self.current_process and self.current_process.stderr:
                    while True:
                        line = await self.current_process.stderr.readline()
                        if not line:
                            break
                        decoded = line.decode(errors="replace").strip()
                        if decoded:
                            stderr_chunks.append(decoded)

            stderr_task = asyncio.create_task(read_stderr())

            if self.current_process.stdout:
                while True:
                    line_bytes = await self.current_process.stdout.readline()
                    if not line_bytes:
                        break

                    line_str = line_bytes.decode(errors="replace")
                    progress = parser.update_from_line(line_str)

                    if progress and progress_callback:
                        now = asyncio.get_event_loop().time()
                        # Throttle callbacks to at most once every 1.5 seconds or on finish
                        if (now - last_callback_time >= 1.5) or progress.is_done:
                            last_callback_time = now
                            try:
                                await progress_callback(progress)
                            except Exception as cb_err:
                                logger.warning(f"Error in encode progress callback: {cb_err}")

            await self.current_process.wait()
            await stderr_task

            if self.is_cancelled:
                # Cleanup partially encoded file
                if out_p.exists():
                    out_p.unlink(missing_ok=True)
                return EncodeResult(
                    success=False,
                    quality=norm_quality,
                    was_cancelled=True,
                    error_message="Encoding was cancelled by user.",
                )

            if self.current_process.returncode != 0:
                err_text = "\n".join(stderr_chunks[-10:])
                logger.error(f"FFmpeg exited with code {self.current_process.returncode}: {err_text}")
                if out_p.exists():
                    out_p.unlink(missing_ok=True)
                return EncodeResult(
                    success=False,
                    quality=norm_quality,
                    error_message=f"FFmpeg error (code {self.current_process.returncode}): {err_text}",
                )

            # Verify output file
            if not out_p.exists() or out_p.stat().st_size == 0:
                return EncodeResult(
                    success=False,
                    quality=norm_quality,
                    error_message="Encoded output file is missing or 0 bytes.",
                )

            file_size = out_p.stat().st_size
            logger.info(f"Successfully encoded {norm_quality}: {out_p.name} ({file_size} bytes)")

            return EncodeResult(
                success=True,
                quality=norm_quality,
                output_path=str(out_p),
                file_size_bytes=file_size,
                duration_seconds=probe_info.duration_seconds,
            )

        except Exception as e:
            logger.error(f"Unexpected error during FFmpeg execution: {e}")
            if out_p.exists():
                out_p.unlink(missing_ok=True)
            return EncodeResult(
                success=False,
                quality=norm_quality,
                error_message=str(e),
                was_cancelled=self.is_cancelled,
            )
        finally:
            self.current_process = None
