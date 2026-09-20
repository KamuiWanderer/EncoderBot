"""
Batch Encoding & Hyperlinked Review Card Manager.
Supports multi-video drops in private chats and Admin groups with clickable verification links.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Optional
from pyrogram.types import InlineKeyboardMarkup

from app.database.database import db
from app.database.models import EncodeJob
from app.encoder.presets import STANDARD_QUALITIES, sort_qualities_ascending
from app.jobs.job import create_job
from app.managers.thumbnail_manager import ThumbnailManager
from app.managers.upload_config_manager import UploadConfigManager
from app.metadata.parser import MetadataParser, ParsedMetadata
from app.metadata.placeholders import TemplateContext, TemplateEngine
from app.metadata.season import SeasonManager
from app.telegram.links import TelegramLinkParser
from app.utils.formatting import human_bytes
from app.utils.ui import (
    build_keyboard,
    cancel_button,
    danger_button,
    primary_button,
    success_button,
)


@dataclass
class BatchVideoItem:
    index: int
    source_type: str  # 'telegram_file' or 'telegram_link'
    source_file_id: Optional[str] = None
    source_link: Optional[str] = None
    source_title: str = "video.mp4"
    source_size_bytes: int = 0
    parsed_metadata: ParsedMetadata = field(default_factory=lambda: ParsedMetadata(raw_input=""))
    global_bolum: Optional[int] = None
    season_finale: Optional[str] = None
    thumbnail_target: Optional[str] = None
    thumbnail_link: Optional[str] = None


@dataclass
class BatchSession:
    batch_id: str
    user_id: int
    chat_id: int
    items: list[BatchVideoItem] = field(default_factory=list)
    selected_qualities: list[str] = field(default_factory=lambda: ["480p", "720p", "1080p"])
    encoding_profile: str = "standard"
    filename_template: Optional[str] = None
    caption_template: Optional[str] = None
    destination_chat: Optional[str] = None
    staging_channel: Optional[str] = None
    upload_timing: str = "staging"
    episode_header_enabled: bool = True
    episode_header_template: Optional[str] = None
    sticker_id: Optional[str] = None
    sticker_mode: str = "per_episode"
    send_delay: float = 1.0
    status_message_id: Optional[int] = None
    is_detailed_view: bool = False
    created_at: float = field(default_factory=time.time)


class BatchManager:
    """Manages multi-video batch sessions and builds hyperlinked review cards."""

    def __init__(self) -> None:
        self.sessions: dict[str, BatchSession] = {}
        self.user_active_batch: dict[int, str] = {}

    def get_session(self, batch_id: str) -> Optional[BatchSession]:
        return self.sessions.get(batch_id)

    def get_user_session(self, user_id: int) -> Optional[BatchSession]:
        bid = self.user_active_batch.get(user_id)
        return self.sessions.get(bid) if bid else None

    def create_session(self, user_id: int, chat_id: int) -> BatchSession:
        batch_id = f"batch_{uuid.uuid4().hex[:8]}"
        sess = BatchSession(batch_id=batch_id, user_id=user_id, chat_id=chat_id)
        self.sessions[batch_id] = sess
        self.user_active_batch[user_id] = batch_id
        return sess

    def clear_session(self, user_id: int) -> None:
        bid = self.user_active_batch.pop(user_id, None)
        if bid and bid in self.sessions:
            del self.sessions[bid]

    # ---------------- BATCH REVIEW CARD RENDERER ----------------

    @classmethod
    async def build_review_card(cls, sess: BatchSession) -> tuple[str, InlineKeyboardMarkup]:
        """
        Builds the hyperlinked review card with direct clickable links for Episode, Thumbnail, and Quality outputs.
        """
        count = len(sess.items)
        qualities_str = ", ".join(sort_qualities_ascending(sess.selected_qualities))
        total_encodes = count * len(sess.selected_qualities)

        timing_name = "Private Staging (Ordered Delivery)" if sess.upload_timing == "staging" else "Immediate (Save Disk)"
        dest_name = sess.destination_chat or "Current Chat"

        lines = [
            f"📦 <b>BATCH ENCODE REVIEW ({count} Episode{'s' if count > 1 else ''})</b>",
            "<code>━━━━━━━━━━━━━━━━━━━━━━━━━━━━</code>",
            f"🎞 <b>Selected Qualities:</b> <code>{qualities_str}</code>",
            f"📊 <b>Total Encodes:</b> <code>{total_encodes} tasks</code>\n",
        ]

        # Iterate through each episode item
        for item in sess.items:
            meta = item.parsed_metadata
            ep_title = meta.title
            s_num = meta.formatted_season
            e_num = meta.formatted_episode
            b_str = f" (Bölüm {item.global_bolum:02d})" if item.global_bolum else ""

            # 1. Clickable Episode title / Source link
            if item.source_link:
                ep_header_line = f"🎬 <b><a href=\"{item.source_link}\">Episode {e_num}</a></b>{b_str} — <i>{ep_title}</i>"
            else:
                ep_header_line = f"🎬 <b>Episode {e_num}</b>{b_str} — <i>{ep_title}</i>"
            lines.append(ep_header_line)

            # 2. Episode Header banner format
            if sess.episode_header_enabled:
                hdr_text = UploadConfigManager.render_episode_header(
                    template=sess.episode_header_template,
                    metadata=meta,
                    calculated_bolum=item.global_bolum,
                    calculated_finale=item.season_finale,
                )
                first_line = hdr_text.split("\n")[0]
                lines.append(f"📌 <b>Header:</b> <code>{first_line}</code>")

            # 3. Clickable Thumbnail link
            if item.thumbnail_link:
                lines.append(f"🖼️ <b>Thumbnail:</b> <a href=\"{item.thumbnail_link}\">Episode {e_num} Cover 👁</a>")
            elif item.thumbnail_target:
                lines.append(f"🖼️ <b>Thumbnail:</b> <code>Auto-mapped (Ep {e_num} ✓)</code>")
            else:
                lines.append("🖼️ <b>Thumbnail:</b> <i>(None)</i>")

            # 4. Outputs per quality
            lines.append("📁 <b>Outputs:</b>")
            for q in sort_qualities_ascending(sess.selected_qualities):
                fn = TemplateEngine.render_filename(
                    template=sess.filename_template,
                    ctx=TemplateContext(
                        metadata=meta,
                        quality=q,
                        calculated_bolum=item.global_bolum,
                    ),
                )
                if item.source_link:
                    lines.append(f"   • <code>{q}</code> ➔ <a href=\"{item.source_link}\">{fn}</a>")
                else:
                    lines.append(f"   • <code>{q}</code> ➔ <code>{fn}</code>")

            # 5. Caption Preview (show in detailed view or for first item)
            if sess.is_detailed_view or item.index == 1:
                cap = TemplateEngine.render_caption(
                    template=sess.caption_template,
                    ctx=TemplateContext(
                        metadata=meta,
                        quality="720p",
                        calculated_bolum=item.global_bolum,
                        calculated_finale=item.season_finale,
                    ),
                )
                if cap:
                    lines.append(f"📝 <b>Caption Preview:</b>\n<blockquote>{cap}</blockquote>")

            lines.append("")

        lines.extend([
            "<code>━━━━━━━━━━━━━━━━━━━━━━━━━━━━</code>",
            f"📤 <b>Destination:</b> <code>{dest_name}</code>",
            f"⏱ <b>Upload Timing:</b> <code>{timing_name}</code>",
        ])

        text = "\n".join(lines)

        # Buttons
        buttons = [
            [success_button("🚀 START BATCH ENCODE", f"batch:start:{sess.batch_id}"), primary_button("🎞 Change Qualities", f"batch:qualities:{sess.batch_id}")],
            [
                primary_button("👁 Detailed View" if not sess.is_detailed_view else "👁 Compact View", f"batch:toggle_view:{sess.batch_id}"),
                danger_button("❌ Cancel Batch", f"batch:cancel:{sess.batch_id}"),
            ],
        ]

        return text, build_keyboard(buttons)


# Global singleton instance
batch_manager = BatchManager()
