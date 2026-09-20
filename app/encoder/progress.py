"""
FFmpeg machine-readable progress parser.
Parses `-progress pipe:1` key-value lines and computes stats.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

from app.utils.formatting import human_duration, human_eta, progress_bar


@dataclass
class EncodeProgress:
    frame: int = 0
    fps: float = 0.0
    bitrate_kbps: float = 0.0
    total_size_bytes: int = 0
    out_time_seconds: float = 0.0
    speed: float = 0.0  # e.g. 1.35 means 1.35x real time
    percentage: float = 0.0
    eta_seconds: Optional[float] = None
    is_done: bool = False

    def formatted_status(self, quality_name: str) -> str:
        """Render standard status block for Telegram."""
        bar = progress_bar(self.percentage)
        speed_str = f"{self.speed:.2f}x" if self.speed > 0 else "0.0x"
        time_str = human_duration(self.out_time_seconds)
        eta_str = human_eta(self.eta_seconds)

        return (
            f"🎬 **Encoding {quality_name}**\n\n"
            f"`{bar}`\n\n"
            f"⏱ **Time:** `{time_str}`\n"
            f"⚡ **Speed:** `{speed_str}`\n"
            f"⏳ **ETA:** `{eta_str}`"
        )


class ProgressParser:
    """Stateful parser for FFmpeg progress stream."""

    def __init__(self, total_duration_seconds: float = 0.0, total_frames: int = 0) -> None:
        self.total_duration_seconds = max(0.0, total_duration_seconds)
        self.total_frames = max(0, total_frames)
        self.current = EncodeProgress()

    def update_from_line(self, line: str) -> Optional[EncodeProgress]:
        """
        Parse a single key=value line from FFmpeg progress output.
        Returns the updated progress object if a block ended with 'progress=continue' or 'progress=end'.
        """
        line = line.strip()
        if not line or "=" not in line:
            return None

        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip()

        if key == "frame":
            if val.isdigit():
                self.current.frame = int(val)
        elif key == "fps":
            try:
                self.current.fps = float(val)
            except ValueError:
                pass
        elif key == "total_size":
            if val.isdigit():
                self.current.total_size_bytes = int(val)
        elif key == "out_time_us":
            if val.isdigit():
                self.current.out_time_seconds = int(val) / 1_000_000.0
        elif key == "out_time_ms":
            if val.isdigit():
                self.current.out_time_seconds = int(val) / 1_000.0
        elif key == "speed":
            if val.endswith("x"):
                val = val[:-1]
            try:
                self.current.speed = float(val)
            except ValueError:
                pass
        elif key == "progress":
            if val == "end":
                self.current.is_done = True
                self.current.percentage = 100.0
                self.current.eta_seconds = 0.0
                return self.current
            elif val == "continue":
                # Compute percentage
                if self.total_duration_seconds > 0:
                    pct = (self.current.out_time_seconds / self.total_duration_seconds) * 100.0
                    self.current.percentage = min(99.9, max(0.0, pct))
                elif self.total_frames > 0:
                    pct = (self.current.frame / self.total_frames) * 100.0
                    self.current.percentage = min(99.9, max(0.0, pct))

                # Compute ETA
                if self.total_duration_seconds > self.current.out_time_seconds and self.current.speed > 0:
                    remaining_time = self.total_duration_seconds - self.current.out_time_seconds
                    self.current.eta_seconds = remaining_time / self.current.speed
                else:
                    self.current.eta_seconds = None

                return self.current

        return None
