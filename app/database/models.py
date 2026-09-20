"""
Database models and dataclasses for configuration, templates, mappings, jobs, and upload settings.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class UserSettings:
    user_id: int
    default_qualities: list[str] = field(default_factory=lambda: ["240p", "360p", "480p", "720p", "1080p"])
    default_codec: str = "h264"
    default_profile: str = "standard"
    default_destination: Optional[str] = None
    sticker_id: Optional[str] = None
    # Advanced Upload Settings
    upload_timing: str = "staging"  # 'immediate' or 'staging'
    staging_channel: Optional[str] = None  # Chat ID or @channel for private buffer
    episode_header_enabled: bool = True
    episode_header_template: Optional[str] = None
    sticker_mode: str = "per_episode"  # 'per_episode', 'per_job', 'disabled'
    destination_mode: str = "default"  # 'default', 'ask', 'multi'
    backup_destination: Optional[str] = None
    send_delay: float = 1.0  # seconds between posts to avoid flood
    allow_upscale: bool = False  # False: Smart Cap / Preserve Original, True: Force Upscale (Lanczos)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)


@dataclass
class FilenameFormat:
    id: Optional[int]
    user_id: int
    template: str
    is_active: bool = True
    created_at: float = field(default_factory=time.time)


@dataclass
class CaptionFormat:
    id: Optional[int]
    user_id: int
    template: str
    is_active: bool = True
    created_at: float = field(default_factory=time.time)


@dataclass
class SeasonMapping:
    id: Optional[int]
    user_id: int
    season: int
    global_bolum_offset: int  # e.g. season 2 starts at episode 11
    updated_at: float = field(default_factory=time.time)


@dataclass
class ThumbnailConfig:
    id: Optional[int]
    user_id: int
    mode: str  # 'single' or 'multiple'
    single_file_id: Optional[str] = None
    single_file_path: Optional[str] = None
    channel_username_or_id: Optional[str] = None
    start_message_id: Optional[int] = None
    end_message_id: Optional[int] = None
    mapping_data: dict[str, str] = field(default_factory=dict)  # episode_str -> file_id_or_path
    mapping_links: dict[str, str] = field(default_factory=dict)  # episode_str -> message_link
    is_active: bool = True
    updated_at: float = field(default_factory=time.time)


@dataclass
class JobOutput:
    id: Optional[int]
    job_id: str
    quality: str
    file_path: Optional[str] = None
    file_size_bytes: int = 0
    duration_seconds: float = 0.0
    status: str = "pending"  # pending, encoding, encoded, staging, uploading, completed, failed
    telegram_message_id: Optional[int] = None
    staging_message_id: Optional[int] = None
    error_message: Optional[str] = None
    started_at: Optional[float] = None
    completed_at: Optional[float] = None


@dataclass
class EncodeJob:
    job_id: str
    user_id: int
    chat_id: int
    status: str = "queued"  # queued, downloading, encoding, staging, uploading, completed, failed, cancelled
    source_type: str = "telegram_file"  # telegram_file or telegram_link
    source_file_id: Optional[str] = None
    source_link: Optional[str] = None
    source_path: Optional[str] = None
    source_title: str = "video.mp4"
    source_size_bytes: int = 0
    source_duration_seconds: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)
    selected_qualities: list[str] = field(default_factory=lambda: ["720p"])
    current_quality: Optional[str] = None
    completed_qualities: list[str] = field(default_factory=list)
    failed_qualities: list[str] = field(default_factory=list)
    filename_template: Optional[str] = None
    caption_template: Optional[str] = None
    thumbnail_config_id: Optional[int] = None
    thumbnail_mode: str = "none"  # none, single, multiple
    thumbnail_link: Optional[str] = None
    destination_chat_id: Optional[str] = None
    staging_channel: Optional[str] = None
    upload_timing: str = "staging"  # 'immediate' or 'staging'
    episode_header_enabled: bool = True
    episode_header_template: Optional[str] = None
    sticker_id: Optional[str] = None
    sticker_mode: str = "per_episode"
    send_delay: float = 1.0
    encoding_profile: str = "standard"
    # Batch tracking
    batch_id: Optional[str] = None
    batch_index: int = 1
    batch_total: int = 1
    staged_outputs: dict[str, int] = field(default_factory=dict)  # quality -> staging_message_id
    progress_percent: float = 0.0
    progress_speed: str = "0x"
    progress_eta: str = "N/A"
    error_message: Optional[str] = None
    status_message_id: Optional[int] = None
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
