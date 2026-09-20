"""
Encode Job entity and lifecycle state definitions.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Optional

from app.database.models import EncodeJob
from app.encoder.presets import sort_qualities_ascending
from app.metadata.parser import ParsedMetadata


class JobStatus:
    QUEUED = "queued"
    DOWNLOADING = "downloading"
    ENCODING = "encoding"
    UPLOADING = "uploading"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


def create_job(
    user_id: int,
    chat_id: int,
    source_title: str,
    source_type: str = "telegram_file",
    source_file_id: Optional[str] = None,
    source_link: Optional[str] = None,
    source_size_bytes: int = 0,
    metadata: Optional[dict[str, Any]] = None,
    selected_qualities: Optional[list[str]] = None,
    filename_template: Optional[str] = None,
    caption_template: Optional[str] = None,
    thumbnail_config_id: Optional[int] = None,
    thumbnail_mode: str = "none",
    destination_chat_id: Optional[str] = None,
    sticker_id: Optional[str] = None,
    encoding_profile: str = "standard",
) -> EncodeJob:
    """Factory helper to construct a new EncodeJob."""
    job_id = f"job_{uuid.uuid4().hex[:10]}"
    sorted_qualities = sort_qualities_ascending(selected_qualities or ["720p"])

    return EncodeJob(
        job_id=job_id,
        user_id=user_id,
        chat_id=chat_id,
        status=JobStatus.QUEUED,
        source_type=source_type,
        source_file_id=source_file_id,
        source_link=source_link,
        source_title=source_title,
        source_size_bytes=source_size_bytes,
        metadata=metadata or {},
        selected_qualities=sorted_qualities,
        current_quality=None,
        completed_qualities=[],
        failed_qualities=[],
        filename_template=filename_template,
        caption_template=caption_template,
        thumbnail_config_id=thumbnail_config_id,
        thumbnail_mode=thumbnail_mode,
        destination_chat_id=destination_chat_id,
        sticker_id=sticker_id,
        encoding_profile=encoding_profile,
        created_at=time.time(),
        updated_at=time.time(),
    )
