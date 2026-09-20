"""
Configuration module for Telegram Multi-Quality Video Encoder Bot.
Loads environment variables and provides structured settings.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from dotenv import load_dotenv

# Load .env file from the current directory or parent directory
load_dotenv()


@dataclass
class BotConfig:
    """Core Telegram Bot Credentials and Identification."""
    api_id: int = field(default_factory=lambda: int(os.getenv("API_ID", "0")))
    api_hash: str = field(default_factory=lambda: os.getenv("API_HASH", ""))
    bot_token: str = field(default_factory=lambda: os.getenv("BOT_TOKEN", ""))
    owner_ids: list[int] = field(default_factory=lambda: [
        int(x.strip()) for x in os.getenv("OWNER_IDS", "").split(",") if x.strip().isdigit()
    ])


@dataclass
class StorageConfig:
    """Local storage and directory paths."""
    base_dir: Path = field(default_factory=lambda: Path(os.getenv("STORAGE_DIR", "./storage")).resolve())

    @property
    def downloads_dir(self) -> Path:
        p = self.base_dir / "downloads"
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def outputs_dir(self) -> Path:
        p = self.base_dir / "outputs"
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def thumbnails_dir(self) -> Path:
        p = self.base_dir / "thumbnails"
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def temp_dir(self) -> Path:
        p = self.base_dir / "temp"
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def database_path(self) -> Path:
        custom_path = os.getenv("DATABASE_PATH")
        if custom_path:
            p = Path(custom_path).resolve()
            p.parent.mkdir(parents=True, exist_ok=True)
            return p
        return self.base_dir / "encoder_bot.db"


@dataclass
class EncoderConfig:
    """FFmpeg and encoding pipeline parameters."""
    ffmpeg_binary: str = field(default_factory=lambda: os.getenv("FFMPEG_BINARY", "ffmpeg"))
    ffprobe_binary: str = field(default_factory=lambda: os.getenv("FFPROBE_BINARY", "ffprobe"))
    max_concurrent_encodes: int = field(default_factory=lambda: int(os.getenv("MAX_CONCURRENT_ENCODES", "1")))
    default_preset: str = field(default_factory=lambda: os.getenv("DEFAULT_PRESET", "medium"))
    min_free_disk_gb: float = field(default_factory=lambda: float(os.getenv("MIN_FREE_DISK_GB", "2.0")))
    no_upscale_default: bool = True


@dataclass
class AppConfig:
    """Unified application configuration."""
    bot: BotConfig = field(default_factory=BotConfig)
    storage: StorageConfig = field(default_factory=StorageConfig)
    encoder: EncoderConfig = field(default_factory=EncoderConfig)

    def is_authorized(self, user_id: int) -> bool:
        """Check if a user is an authorized owner/admin."""
        if not self.bot.owner_ids:
            # If no owner IDs set, allow all (or can be configured for open mode)
            return True
        return user_id in self.bot.owner_ids


# Global singleton instance
config = AppConfig()
