"""
UI button and keyboard styling components.
Uses vibrant emoji color indicators (🟢 Success, 🔴 Danger, 🔵 Primary, 🟡 Options)
to ensure clear, colorful, and distinct button semantics across all Telegram apps.
"""

from __future__ import annotations

from enum import Enum
from typing import Optional
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup


class ButtonStyle(str, Enum):
    PRIMARY = "primary"  # 🔵 Neutral / Navigation
    SUCCESS = "success"  # 🟢 Confirmation / Start / Save / Continue
    DANGER = "danger"    # 🔴 Cancellation / Delete / Reset / Clear
    OPTION = "option"    # 🟡 Selection / Toggles / Qualities


def make_button(
    text: str,
    callback_data: str,
    style: ButtonStyle = ButtonStyle.PRIMARY,
    custom_icon: Optional[str] = None,
) -> InlineKeyboardButton:
    """
    Creates a colorful, styled inline keyboard button using vibrant color dots & icons.
    """
    clean_text = text.strip()

    if custom_icon:
        prefix = f"{custom_icon} "
    elif style == ButtonStyle.SUCCESS:
        if not any(clean_text.startswith(icon) for icon in ("🟢", "✅", "🚀", "💾", "➡️", "➕")):
            prefix = "🟢 "
        else:
            prefix = ""
    elif style == ButtonStyle.DANGER:
        if not any(clean_text.startswith(icon) for icon in ("🔴", "❌", "🗑️", "🔄", "🚫", "⛔")):
            prefix = "🔴 "
        else:
            prefix = ""
    elif style == ButtonStyle.PRIMARY:
        if not any(clean_text.startswith(icon) for icon in ("🔵", "📁", "📝", "🖼️", "🎞️", "⚙️", "📤", "🏷️", "📌", "📖", "📊", "👁", "↩️")):
            prefix = "🔵 "
        else:
            prefix = ""
    elif style == ButtonStyle.OPTION:
        if not any(clean_text.startswith(icon) for icon in ("🟡", "🔘", "⚪", "☑", "☐", "⚡", "⏱", "🛡", "🐢", "🔒")):
            prefix = "🟡 "
        else:
            prefix = ""
    else:
        prefix = ""

    formatted_text = f"{prefix}{clean_text}".strip()
    return InlineKeyboardButton(text=formatted_text, callback_data=callback_data)


def success_button(text: str, callback_data: str) -> InlineKeyboardButton:
    return make_button(text=f"🟢 {text.lstrip('🟢 ')}", callback_data=callback_data, style=ButtonStyle.SUCCESS)


def danger_button(text: str, callback_data: str) -> InlineKeyboardButton:
    return make_button(text=f"🔴 {text.lstrip('🔴 ')}", callback_data=callback_data, style=ButtonStyle.DANGER)


def primary_button(text: str, callback_data: str) -> InlineKeyboardButton:
    return make_button(text=text, callback_data=callback_data, style=ButtonStyle.PRIMARY)


def back_button(callback_data: str = "nav:back") -> InlineKeyboardButton:
    return make_button(text="↩️ Back", callback_data=callback_data, style=ButtonStyle.PRIMARY)


def cancel_button(callback_data: str = "wizard:cancel") -> InlineKeyboardButton:
    return danger_button("❌ Cancel", callback_data)


def confirm_start_button(callback_data: str = "wizard:start") -> InlineKeyboardButton:
    return success_button("🚀 START ENCODE", callback_data)


def build_keyboard(rows: list[list[InlineKeyboardButton]]) -> InlineKeyboardMarkup:
    """Builds an InlineKeyboardMarkup from rows of buttons."""
    return InlineKeyboardMarkup(rows)
