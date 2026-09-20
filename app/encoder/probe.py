"""
FFprobe media stream inspector.
Extracts video, audio, and subtitle stream details asynchronously.
"""

from __future__ import annotations

import asyncio
import json
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from app.config import config
from app.utils.logging import logger


@dataclass
class AudioStreamInfo:
    index: int
    codec_name: str
    channels: int
    sample_rate: int
    bit_rate: Optional[int] = None
    language: Optional[str] = None
    title: Optional[str] = None


@dataclass
class SubtitleStreamInfo:
    index: int
    codec_name: str
    language: Optional[str] = None
    title: Optional[str] = None


@dataclass
class MediaProbeInfo:
    file_path: str
    duration_seconds: float = 0.0
    file_size_bytes: int = 0
    format_name: str = ""
    bit_rate: int = 0
    # Video details
    has_video: bool = False
    video_codec: str = ""
    width: int = 0
    height: int = 0
    aspect_ratio: str = "16:9"
    fps: float = 0.0
    total_frames: int = 0
    pix_fmt: str = ""
    # Audio & Subtitle streams
    audio_streams: list[AudioStreamInfo] = field(default_factory=list)
    subtitle_streams: list[SubtitleStreamInfo] = field(default_factory=list)

    @property
    def display_resolution(self) -> str:
        if self.height <= 0:
            return "Unknown"
        return f"{self.width}x{self.height} ({self.height}p)"


async def probe_media(file_path: str | Path) -> MediaProbeInfo:
    """
    Inspects media file with ffprobe and returns structured MediaProbeInfo.
    """
    path_str = str(Path(file_path).resolve())
    ffprobe_bin = config.encoder.ffprobe_binary

    cmd = [
        ffprobe_bin,
        "-v", "quiet",
        "-print_format", "json",
        "-show_format",
        "-show_streams",
        path_str,
    ]

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()

        if proc.returncode != 0:
            err_msg = stderr.decode(errors="replace")
            logger.error(f"FFprobe failed with returncode {proc.returncode}: {err_msg}")
            raise RuntimeError(f"FFprobe failed: {err_msg}")

        data = json.loads(stdout.decode(errors="replace"))
        format_info = data.get("format", {})
        streams = data.get("streams", [])

        probe = MediaProbeInfo(
            file_path=path_str,
            duration_seconds=float(format_info.get("duration", 0.0)),
            file_size_bytes=int(format_info.get("size", 0)),
            format_name=format_info.get("format_name", ""),
            bit_rate=int(format_info.get("bit_rate", 0)),
        )

        for stream in streams:
            codec_type = stream.get("codec_type")
            tags = stream.get("tags", {})
            lang = tags.get("language") or tags.get("LANG")
            title = tags.get("title") or tags.get("TITLE")

            if codec_type == "video" and not probe.has_video:
                probe.has_video = True
                probe.video_codec = stream.get("codec_name", "")
                probe.width = int(stream.get("width", 0))
                probe.height = int(stream.get("height", 0))
                probe.pix_fmt = stream.get("pix_fmt", "")

                # FPS calculation
                r_frame_rate = stream.get("r_frame_rate", "0/1")
                if "/" in r_frame_rate:
                    num, den = r_frame_rate.split("/")
                    probe.fps = round(float(num) / float(den), 2) if float(den) > 0 else 0.0

                probe.aspect_ratio = stream.get("display_aspect_ratio", "")
                if not probe.aspect_ratio and probe.height > 0:
                    probe.aspect_ratio = f"{round(probe.width / probe.height, 2)}:1"

                # Frames
                nb_frames = stream.get("nb_frames")
                if nb_frames and nb_frames.isdigit():
                    probe.total_frames = int(nb_frames)
                elif probe.duration_seconds > 0 and probe.fps > 0:
                    probe.total_frames = int(probe.duration_seconds * probe.fps)

            elif codec_type == "audio":
                audio_stream = AudioStreamInfo(
                    index=int(stream.get("index", len(probe.audio_streams))),
                    codec_name=stream.get("codec_name", ""),
                    channels=int(stream.get("channels", 2)),
                    sample_rate=int(stream.get("sample_rate", 44100)),
                    bit_rate=int(stream.get("bit_rate", 0)) if stream.get("bit_rate") else None,
                    language=lang,
                    title=title,
                )
                probe.audio_streams.append(audio_stream)

            elif codec_type == "subtitle":
                sub_stream = SubtitleStreamInfo(
                    index=int(stream.get("index", len(probe.subtitle_streams))),
                    codec_name=stream.get("codec_name", ""),
                    language=lang,
                    title=title,
                )
                probe.subtitle_streams.append(sub_stream)

        logger.info(
            f"Probed media: {Path(path_str).name} -> {probe.display_resolution}, "
            f"duration={probe.duration_seconds:.1f}s, vcodec={probe.video_codec}, "
            f"audio_tracks={len(probe.audio_streams)}"
        )
        return probe

    except Exception as e:
        logger.error(f"Error executing ffprobe on {path_str}: {e}")
        raise
