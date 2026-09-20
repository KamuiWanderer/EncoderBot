"""
Interactive /encode wizard session and state manager.
Manages step-by-step inline keyboard flow before job dispatch with colorful buttons.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional
from pyrogram.types import InlineKeyboardMarkup

from app.database.models import EncodeJob
from app.encoder.presets import STANDARD_QUALITIES, sort_qualities_ascending
from app.jobs.job import create_job
from app.metadata.parser import MetadataParser, ParsedMetadata
from app.metadata.placeholders import TemplateContext, TemplateEngine
from app.utils.formatting import human_bytes
from app.utils.ui import (
    ButtonStyle,
    build_keyboard,
    cancel_button,
    confirm_start_button,
    danger_button,
    make_button,
    primary_button,
    success_button,
)


@dataclass
class WizardSession:
    user_id: int
    chat_id: int
    step: str = "init"
    source_type: str = "telegram_file"
    source_file_id: Optional[str] = None
    source_link: Optional[str] = None
    source_title: str = "video.mp4"
    source_size_bytes: int = 0
    parsed_metadata: ParsedMetadata = field(default_factory=lambda: ParsedMetadata(raw_input=""))
    filename_template: Optional[str] = None
    caption_template: Optional[str] = None
    thumbnail_mode: str = "none"
    selected_qualities: list[str] = field(default_factory=lambda: ["720p"])
    encoding_profile: str = "standard"
    destination_chat: Optional[str] = None
    message_id: Optional[int] = None
    waiting_for_input: Optional[str] = None
    temp_thumb_start_link: Optional[str] = None


class EncodeWizardManager:
    """Manages active wizard sessions for users."""

    def __init__(self) -> None:
        self.sessions: dict[int, WizardSession] = {}

    def get_session(self, user_id: int) -> Optional[WizardSession]:
        return self.sessions.get(user_id)

    def start_session(
        self,
        user_id: int,
        chat_id: int,
        source_title: str,
        source_type: str = "telegram_file",
        source_file_id: Optional[str] = None,
        source_link: Optional[str] = None,
        source_size_bytes: int = 0,
    ) -> WizardSession:
        meta = MetadataParser.parse(source_title)
        sess = WizardSession(
            user_id=user_id,
            chat_id=chat_id,
            step="filename",
            source_type=source_type,
            source_file_id=source_file_id,
            source_link=source_link,
            source_title=source_title,
            source_size_bytes=source_size_bytes,
            parsed_metadata=meta,
            selected_qualities=["480p", "720p", "1080p"],
        )
        self.sessions[user_id] = sess
        return sess

    def clear_session(self, user_id: int) -> None:
        if user_id in self.sessions:
            del self.sessions[user_id]

    @staticmethod
    def build_filename_step_ui(current_saved_template: Optional[str]) -> tuple[str, InlineKeyboardMarkup]:
        if current_saved_template:
            text = (
                "📁 **Filename Configuration**\n\n"
                f"**Current Format:**\n`{current_saved_template}`\n\n"
                "Select an option:"
            )
            buttons = [
                [success_button("✅ Use Current Format", "wiz:fn:use_current")],
                [primary_button("🔵 ✏️ Add New Format", "wiz:fn:add_new")],
                [primary_button("🔵 ↩️ Keep Original Filename", "wiz:fn:keep_default")],
                [cancel_button()],
            ]
        else:
            text = (
                "📁 **Filename Configuration**\n\n"
                "No custom filename configured.\n"
                "**Default:** Original filename + quality suffix\n\n"
                "Select an option:"
            )
            buttons = [
                [success_button("✅ Keep Default", "wiz:fn:keep_default")],
                [primary_button("🔵 ✏️ Add Custom Filename", "wiz:fn:add_new")],
                [cancel_button()],
            ]
        return text, build_keyboard(buttons)

    @staticmethod
    def build_caption_step_ui(current_saved_caption: Optional[str]) -> tuple[str, InlineKeyboardMarkup]:
        if current_saved_caption:
            text = (
                "📝 **Caption Configuration**\n\n"
                f"**Current Template:**\n`{current_saved_caption}`\n\n"
                "Select an option:"
            )
            buttons = [
                [success_button("✅ Use Current Caption", "wiz:cap:use_current")],
                [primary_button("🔵 ✏️ Add New Caption", "wiz:cap:add_new")],
                [danger_button("🚫 No Caption", "wiz:cap:no_caption")],
                [cancel_button()],
            ]
        else:
            text = (
                "📝 **Caption Configuration**\n\n"
                "No custom caption configured.\n\n"
                "Select an option:"
            )
            buttons = [
                [success_button("✅ Keep Empty (No Caption)", "wiz:cap:no_caption")],
                [primary_button("🔵 ✏️ Add Custom Caption", "wiz:cap:add_new")],
                [cancel_button()],
            ]
        return text, build_keyboard(buttons)

    @staticmethod
    def build_thumbnail_step_ui(has_saved_thumb: bool, thumb_info_str: str) -> tuple[str, InlineKeyboardMarkup]:
        if has_saved_thumb:
            text = (
                "🖼️ **Thumbnail Configuration**\n\n"
                f"**Current configuration:**\n{thumb_info_str}\n\n"
                "Select an option:"
            )
            buttons = [
                [success_button("✅ Use Current Thumbnail", "wiz:th:use_current")],
                [primary_button("🔵 ➕ Configure New Thumbnail", "wiz:th:add_new")],
                [danger_button("🚫 No Thumbnail", "wiz:th:no_thumb")],
                [cancel_button()],
            ]
        else:
            text = (
                "🖼️ **Thumbnail Configuration**\n\n"
                "No thumbnail configured.\n\n"
                "Select an option:"
            )
            buttons = [
                [primary_button("🔵 ➕ Add Thumbnail", "wiz:th:add_new")],
                [success_button("✅ Keep Default (None)", "wiz:th:no_thumb")],
                [cancel_button()],
            ]
        return text, build_keyboard(buttons)

    @staticmethod
    def build_quality_step_ui(selected_qualities: list[str]) -> tuple[str, InlineKeyboardMarkup]:
        text = "🎞️ **SELECT QUALITIES**\n\nChoose which resolutions to encode:"
        rows: list[list[Any]] = []

        all_std = ["240p", "360p", "480p", "720p", "1080p"]
        row: list[Any] = []
        for q in all_std:
            checked = "🟢 ☑" if q in selected_qualities else "⚪ ☐"
            row.append(primary_button(f"{checked} {q}", f"wiz:q:toggle:{q}"))
            if len(row) == 2:
                rows.append(row)
                row = []
        if row:
            rows.append(row)

        rows.append([primary_button("🔵 ✨ Select All", "wiz:q:all"), primary_button("🔵 🔄 Invert", "wiz:q:invert")])
        rows.append([success_button("➡️ Continue", "wiz:q:continue")])
        rows.append([cancel_button()])

        return text, build_keyboard(rows)

    @staticmethod
    def build_profile_step_ui(current_profile: str) -> tuple[str, InlineKeyboardMarkup]:
        text = "⚙️ **Select Encoding Profile**\n\nChoose an optimization profile:"
        buttons = [
            [primary_button(f"{'🟢 🔘' if current_profile == 'standard' else '⚪ ⚪'} 🎬 Standard (Balanced)", "wiz:prof:standard")],
            [primary_button(f"{'🟢 🔘' if current_profile == 'mobile' else '⚪ ⚪'} 📱 Mobile (Fast & Compact)", "wiz:prof:mobile")],
            [primary_button(f"{'🟢 🔘' if current_profile == 'hq' else '⚪ ⚪'} 💎 High Quality (Crisp)", "wiz:prof:hq")],
            [success_button("➡️ Continue", "wiz:prof:continue")],
            [cancel_button()],
        ]
        return text, build_keyboard(buttons)

    @staticmethod
    def build_destination_step_ui(saved_dest: Optional[str]) -> tuple[str, InlineKeyboardMarkup]:
        text = (
            "📤 **Select Upload Destination**\n\n"
            f"**Default Saved:** `{saved_dest or 'Current Chat'}`\n\n"
            "Choose where to upload the encoded videos:"
        )
        buttons = [
            [success_button("💬 Current Chat", "wiz:dest:current")],
            [primary_button(f"🔵 📢 Saved: {saved_dest}", "wiz:dest:saved")] if saved_dest else [],
            [primary_button("🔵 ✏️ Set Custom Channel/Chat", "wiz:dest:custom")],
            [cancel_button()],
        ]
        filtered = [r for r in buttons if r]
        return text, build_keyboard(filtered)

    @classmethod
    def build_preview_step_ui(
        cls,
        sess: WizardSession,
        global_bolum: Optional[int] = None,
    ) -> tuple[str, InlineKeyboardMarkup]:
        fn_preview = TemplateEngine.render_filename(
            template=sess.filename_template,
            ctx=TemplateContext(
                metadata=sess.parsed_metadata,
                quality="720p",
                width=1280,
                height=720,
                calculated_bolum=global_bolum,
            ),
        )

        cap_preview = TemplateEngine.render_caption(
            template=sess.caption_template,
            ctx=TemplateContext(
                metadata=sess.parsed_metadata,
                quality="720p",
                width=1280,
                height=720,
                calculated_bolum=global_bolum,
            ),
        )

        thumb_str = "None"
        if sess.thumbnail_mode == "single":
            thumb_str = "Single Thumbnail ✓"
        elif sess.thumbnail_mode == "multiple":
            thumb_str = f"Multiple — Episode {sess.parsed_metadata.formatted_episode} ✓"

        qualities_str = ", ".join(sort_qualities_ascending(sess.selected_qualities))
        num_encodes = len(sess.selected_qualities)

        lines = [
            "📋 **ENCODE PREVIEW**\n",
            f"🎬 **Source:**\n`{sess.source_title}` ({human_bytes(sess.source_size_bytes)})\n",
            f"📁 **Filename:**\n`{fn_preview}`\n",
            f"📝 **Caption:**\n{cap_preview or '_(No caption)_'}\n",
            f"🖼️ **Thumbnail:** `{thumb_str}`",
            f"🎞️ **Qualities:** `{qualities_str}`",
            "🎥 **Codec:** `H.264 / AAC (MP4)`",
            f"⚙️ **Profile:** `{sess.encoding_profile.capitalize()}`",
            f"📤 **Destination:** `{sess.destination_chat or 'Current Chat'}`\n",
            "━━━━━━━━━━━━━━━━━━━━",
            "📥 **Source download:** `1×`",
            f"⚙️ **Encodes:** `{num_encodes}×`",
            "━━━━━━━━━━━━━━━━━━━━",
        ]

        text = "\n".join(lines)
        buttons = [
            [confirm_start_button("wiz:start_job")],
            [primary_button("🔵 🎞️ Edit Qualities", "wiz:edit:qualities"), primary_button("🔵 ⚙️ Edit Profile", "wiz:edit:profile")],
            [cancel_button()],
        ]
        return text, build_keyboard(buttons)


wizard_manager = EncodeWizardManager()
