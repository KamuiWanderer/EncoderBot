"""
Episode Upload Manager
======================

Standalone /episode + /sticker + /upload workflow for the
Telethon userbot.

This module:

- Does NOT create its own TelegramClient.
- Does NOT start its own client.
- Does NOT own Telegram authorization.
- Does NOT implement a second caption parser.
- Uses the existing replace.py caption metadata parser.
- Uses the existing replace.py /setseason mappings.
- Builds a complete preview before uploading anything.
- Sorts episodes by global Bölüm.
- Sorts qualities from LOW -> HIGH.
- Allows missing episodes to be supplied through Telegram message links.
- Supports /skip for a missing episode.
- Supports /uploadconfirm with an optional destination ID.
- Invalid destination IDs do NOT cancel the session.
- /episode stores a reusable HTML-capable episode header template.
- /sticker stores a reusable KayiTV sticker.
- {finale} is supported in the episode header.
- HTML including <blockquote> is preserved.
- Source media is reused through Telegram media references whenever possible.

Expected architecture:

    replace.py
        |
        +-- authenticated Telethon client
        +-- session.is_authorized(...)
        +-- build_caption_metadata(...)
        +-- build_caption_message_link(...)
        +-- get_season_mappings(...)
        |
        v
    EpisodeUploadManager
"""

import asyncio
import json
import os
import re
from collections import Counter
from datetime import datetime, timezone
from html import escape
from pathlib import Path

from telethon import events, types, functions
from telethon.errors import FloodWaitError
from telethon.extensions import html as telegram_html
from temp_copy_manager import TempCopyManager
from thumbnail_manager import RawInputMediaDocumentWithCover

# ============================================================
# CONFIGURATION
# ============================================================

MAX_RANGE = 10000
BATCH_SIZE = 100

# Telegram message safety margin.
TELEGRAM_TEXT_LIMIT = 3900

# Delay between publication operations.
SEND_DELAY = 0.35

# Separate persistence file for this module.
# This avoids touching replace.py's existing caption database.
DATA_FILE = Path("episode_upload_data.json")


# ============================================================
# EPISODE PLACEHOLDERS
# ============================================================

KNOWN_EPISODE_PLACEHOLDERS = (
    "title",
    "season",
    "episode",
    "bolum",
    "season_episode",
    "quality",
    "lang",
    "source",
    "year",
    "sub",
    "finale",
    "part",
)


# ============================================================
# PERSISTENCE
# ============================================================

def _load_persistent_data():
    if not DATA_FILE.exists():
        return {
            "episode_formats": {},
            "episode_stickers": {},
        }

    try:
        with open(
            DATA_FILE,
            "r",
            encoding="utf-8",
        ) as file:
            data = json.load(file)

        if not isinstance(data, dict):
            raise ValueError("Invalid persistence format.")

        return {
            "episode_formats": data.get(
                "episode_formats",
                {},
            ),
            "episode_stickers": data.get(
                "episode_stickers",
                {},
            ),
        }

    except Exception:
        return {
            "episode_formats": {},
            "episode_stickers": {},
        }


def _save_persistent_data(data):
    temporary = DATA_FILE.with_suffix(
        DATA_FILE.suffix + ".tmp"
    )

    with open(
        temporary,
        "w",
        encoding="utf-8",
    ) as file:
        json.dump(
            data,
            file,
            ensure_ascii=False,
            indent=2,
        )

    os.replace(
        temporary,
        DATA_FILE,
    )


_PERSISTENT_DATA = _load_persistent_data()


# ============================================================
# EPISODE FORMAT STORAGE
# ============================================================

def get_saved_episode_format(user_id):
    return _PERSISTENT_DATA.get(
        "episode_formats",
        {},
    ).get(
        str(user_id)
    )


def save_episode_format(
    user_id,
    format_text,
):
    _PERSISTENT_DATA.setdefault(
        "episode_formats",
        {},
    )[str(user_id)] = format_text

    _save_persistent_data(
        _PERSISTENT_DATA
    )


def get_saved_episode_sticker(user_id):
    return _PERSISTENT_DATA.get(
        "episode_stickers",
        {},
    ).get(
        str(user_id)
    )


def save_episode_sticker(
    user_id,
    sticker_data,
):
    _PERSISTENT_DATA.setdefault(
        "episode_stickers",
        {},
    )[str(user_id)] = sticker_data

    _save_persistent_data(
        _PERSISTENT_DATA
    )


# ============================================================
# TEXT HELPERS
# ============================================================

def extract_used_placeholders(text):
    used = set()

    for placeholder in KNOWN_EPISODE_PLACEHOLDERS:
        token = "{" + placeholder + "}"

        if token in text:
            used.add(placeholder)

    return used


def _numeric_value(value):
    try:
        return int(value)
    except Exception:
        return 999999


def _quality_value(value):
    match = re.search(
        r"\d+",
        str(value),
    )

    if match:
        return int(match.group())

    return 999999


def _safe_int(value):
    try:
        return int(value)
    except Exception:
        return None


def _format_episode_number(value):
    try:
        return f"{int(value):02d}"
    except Exception:
        return str(value)


def _format_season_number(value):
    try:
        return f"{int(value):02d}"
    except Exception:
        return str(value)


# ============================================================
# HTML TEMPLATE RENDERING
# ============================================================

def render_episode_template(
    template,
    metadata,
):
    """
    Render the saved /episode template.

    Unknown placeholders are intentionally NOT silently removed.

    Supported:

        {title}
        {season}
        {episode}
        {bolum}
        {season_episode}
        {quality}
        {lang}
        {source}
        {year}
        {sub}
        {finale}
        {part}
    """

    values = {}

    for key in KNOWN_EPISODE_PLACEHOLDERS:
        value = metadata.get(
            key,
            "",
        )

        if value is None:
            value = ""

        values[key] = str(value)

    # Numeric display formatting.
    if metadata.get("season") is not None:
        values["season"] = _format_season_number(
            metadata["season"]
        )

    if metadata.get("season_episode") is not None:
        values["season_episode"] = _format_episode_number(
            metadata["season_episode"]
        )

    if metadata.get("episode") is not None:
        values["episode"] = _format_episode_number(
            metadata["episode"]
        )

    if metadata.get("part") is None:
        values["part"] = ""

    rendered = template

    for key, value in values.items():
        rendered = rendered.replace(
            "{" + key + "}",
            value,
        )

    return rendered


# ============================================================
# TELEGRAM ENTITY -> HTML
# ============================================================

def message_text_as_html(message):
    """
    Convert Telegram native formatting to HTML.

    If the message itself contains literal HTML, preserve it.

    This is particularly important for:

        <blockquote>...</blockquote>

    which is supported by the upload header workflow.
    """

    text = (
        getattr(message, "raw_text", None)
        or getattr(message, "text", None)
        or ""
    )

    if not text:
        return ""

    html_tag_pattern = re.compile(
        r"<\s*/?\s*"
        r"(?:"
        r"a|"
        r"b|"
        r"strong|"
        r"i|"
        r"em|"
        r"u|"
        r"s|"
        r"strike|"
        r"del|"
        r"code|"
        r"pre|"
        r"blockquote|"
        r"tg-spoiler|"
        r"span"
        r")"
        r"(?:\s+[^>]*)?"
        r"\s*/?>",
        re.IGNORECASE,
    )

    if html_tag_pattern.search(text):
        return text

    entities = (
        getattr(message, "entities", None)
        or []
    )

    if entities:
        try:
            return telegram_html.unparse(
                text,
                entities,
            )
        except Exception:
            return escape(text)

    return escape(text)


# ============================================================  
# MANAGER  
# ============================================================  
  
class EpisodeUploadManager:  
  
    def __init__(  
        self,  
        client,  
        is_authorized,  
        build_caption_metadata,  
        build_message_link,  
        get_season_mappings,  
    ):  
        self.client = client  
  
        self.is_authorized = (  
            is_authorized  
        )  
  
        self.build_caption_metadata = (  
            build_caption_metadata  
        )  
  
        self.build_message_link = (  
            build_message_link  
        )  
  
        self.get_season_mappings = (  
            get_season_mappings  
        )  
  
        self.session = (  
            self._new_session()  
        )  
  
        self._handlers_registered = False  
  
        self._episode_sticker_waiting = set()  
  
    # ========================================================  
    # SESSION  
    # ========================================================  
  
    @staticmethod  
    def _new_session():  
        return {  
            "active": False,  
            "user_id": None,  
  
            "source_channel": None,  
            "source_start_id": None,  
            "source_end_id": None,  
  
            "destination": None,  
            "destination_id": None,  
            "destination_valid": False,  
  
            "waiting_for": None,  
  
            "episode_format": None,  
            "episode_sticker": None,  
  
            "items": [],  
            "groups": {},  
  
            "missing_episodes": [],  
            "missing_index": 0,  
  
            "skipped_episodes": [],  
  
            "manually_added": {},  
  
            "preview_text": None,  
  
            "stats": {},  
        }  
  
    def clear_session(self):  
        self.session = (  
            self._new_session()  
        )  
  
    # ========================================================  
    # DESTINATION HELPERS  
    # ========================================================  
  
    async def resolve_destination(  
        self,  
        destination_id,  
    ):  
        """  
        Resolve a numeric Telegram destination.  
  
        Supports:  
  
            user ID  
            group ID  
            supergroup/channel ID  
  
        The current chat is handled separately when no ID  
        is supplied.  
        """  
  
        if destination_id is None:  
            raise ValueError(  
                "Destination ID is empty."  
            )  
  
        try:  
            destination_id = int(  
                destination_id  
            )  
        except (TypeError, ValueError):  
            raise ValueError(  
                "Destination ID must be a numeric Telegram ID."  
            )  
  
        try:  
            entity = await self.client.get_entity(  
                destination_id  
            )  
  
        except Exception as error:  
            raise ValueError(  
                f"Could not resolve destination ID "  
                f"{destination_id}: {error}"  
            )  
  
        return entity  
  
    # ========================================================  
    # SOURCE LINK PARSING  
    # ========================================================  
  
    @staticmethod  
    def parse_telegram_message_link(  
        link,  
    ):  
        if not link:  
            return None  
  
        link = link.strip()  
  
        link = link.split(  
            "?",  
            1,  
        )[0]  
  
        link = link.split(  
            "#",  
            1,  
        )[0]  
  
        # ----------------------------------------------------  
        # PRIVATE /c/ LINK  
        # ----------------------------------------------------  
  
        match = re.match(  
            r"^(?:https?://)?t\.me/c/(\d+)/(\d+)$",  
            link,  
            re.IGNORECASE,  
        )  
  
        if match:  
            return {  
                "type": "private",  
                "internal_id": int(  
                    match.group(1)  
                ),  
                "message_id": int(  
                    match.group(2)  
                ),  
            }  
  
        # ----------------------------------------------------  
        # PUBLIC USERNAME LINK  
        # ----------------------------------------------------  
  
        match = re.match(  
            r"^(?:https?://)?t\.me/([A-Za-z0-9_]+)/(\d+)$",  
            link,  
            re.IGNORECASE,  
        )  
  
        if match:  
            return {  
                "type": "username",  
                "username": match.group(1),  
                "message_id": int(  
                    match.group(2)  
                ),  
            }  
  
        return None  
  
    async def resolve_source_channel(  
        self,  
        parsed,  
    ):  
        if not parsed:  
            raise ValueError(  
                "Invalid Telegram message link."  
            )  
  
        if parsed["type"] == "username":  
            return await self.client.get_entity(  
                parsed["username"]  
            )  
  
        if parsed["type"] == "private":  
            internal_id = parsed[  
                "internal_id"  
            ]  
  
            async for dialog in self.client.iter_dialogs():  
                entity = dialog.entity  
  
                if isinstance(  
                    entity,  
                    types.Channel,  
                ):  
                    if entity.id == internal_id:  
                        return entity  
  
            raise ValueError(  
                f"Could not find channel with internal ID "  
                f"`{internal_id}` in your Telegram dialogs.\n\n"  
                "Make sure the userbot account is a member "  
                "of that channel."  
            )  
  
        raise ValueError(  
            "Unsupported Telegram link type."  
        )  
  
    @staticmethod  
    def get_entity_peer_id(  
        entity,  
    ):  
        if isinstance(  
            entity,  
            types.Channel,  
        ):  
            return (  
                -1000000000000  
                - entity.id  
            )  
  
        if isinstance(  
            entity,  
            types.Chat,  
        ):  
            return -entity.id  
  
        if isinstance(  
            entity,  
            types.User,  
        ):  
            return entity.id  
  
        return None  
  
    # ========================================================  
    # VIDEO DETECTION  
    # ========================================================  
  
    @staticmethod  
    def is_video_message(  
        message,  
    ):  
        if not message:  
            return False  
  
        if getattr(  
            message,  
            "video",  
            None,  
        ):  
            return True  
  
        document = getattr(  
            message,  
            "document",  
            None,  
        )  
  
        if document:  
            mime_type = (  
                getattr(  
                    document,  
                    "mime_type",  
                    "",  
                )  
                or ""  
            )  
  
            if mime_type.startswith(  
                "video/"  
            ):  
                return True  
  
        return False  
  
    # ========================================================  
    # METADATA  
    # ========================================================  
  
    def get_video_metadata(
        self,
        message,
        user_id,
    ):
        caption = (
            getattr(
                message,
                "raw_text",
                None,
            )
            or ""
        ).strip()

        if not caption:
            raise ValueError(
                "Video has no caption."
            )

        metadata, global_episode = (
            self.build_caption_metadata(
                user_id,
                caption,
            )
        )

        if global_episode is None:
            raise ValueError(
                "Could not determine global Bölüm from caption."
            )

        global_episode = int(
            global_episode
        )

        season = metadata.get(
            "season"
        )

        season_episode = metadata.get(
            "season_episode"
        )

        display_episode = (
            season_episode
            if season_episode is not None
            else metadata.get("episode")
        )

        quality = (
            metadata.get("quality")
            or "Unknown"
        )

        title = (
            metadata.get("title")
            or ""
        )

        return {
            "message": message,
            "message_id": message.id,

            "metadata": metadata,

            "bolum": global_episode,

            "season": season,
            "episode": display_episode,

            "season_episode": season_episode,

            "quality": str(
                quality
            ),

            "title": title,

            "link": self.build_message_link(
                self.session[
                    "source_channel"
                ],
                message.id,
            ),
        }
  
    # ========================================================  
    # SOURCE RANGE SCANNING  
    # ========================================================  
  
    async def scan_source_range(
        self,
        channel,
        start_id,
        end_id,
        user_id,
    ):
        """
        Scan the complete source range.

        No media is downloaded.

        Only video messages are collected.

        Failure reasons are collected and printed once
        after the scan so the console stays clean.
        """

        items = []

        stats = {
            "total_messages": 0,
            "video_count": 0,
            "skipped_count": 0,
            "failed_count": 0,
            "failed_messages": [],
            "failure_reasons": Counter(),
        }

        for batch_start in range(
            start_id,
            end_id + 1,
            BATCH_SIZE,
        ):
            batch_end = min(
                batch_start
                + BATCH_SIZE
                - 1,
                end_id,
            )

            message_ids = list(
                range(
                    batch_start,
                    batch_end + 1,
                )
            )

            try:
                messages = (
                    await self.client.get_messages(
                        channel,
                        ids=message_ids,
                    )
                )

            except Exception as error:

                reason = (
                    f"{type(error).__name__}: "
                    f"{str(error).strip()}"
                )

                stats[
                    "failed_count"
                ] += len(message_ids)

                stats[
                    "failure_reasons"
                ][reason] += len(message_ids)

                stats[
                    "failed_messages"
                ].append(
                    (
                        f"{batch_start}-{batch_end}",
                        reason,
                    )
                )

                continue

            if not isinstance(
                messages,
                list,
            ):
                messages = [
                    messages
                ]

            for message in messages:

                if not message:
                    stats[
                        "skipped_count"
                    ] += 1

                    continue

                stats[
                    "total_messages"
                ] += 1

                if not self.is_video_message(
                    message
                ):
                    stats[
                        "skipped_count"
                    ] += 1

                    continue

                stats[
                    "video_count"
                ] += 1

                try:
                    item = (
                        self.get_video_metadata(
                            message,
                            user_id,
                        )
                    )

                    items.append(
                        item
                    )

                except Exception as error:

                    reason = (
                        f"{type(error).__name__}: "
                        f"{str(error).strip()}"
                    )

                    stats[
                        "failed_count"
                    ] += 1

                    stats[
                        "failure_reasons"
                    ][reason] += 1

                    stats[
                        "failed_messages"
                    ].append(
                        (
                            message.id,
                            reason,
                        )
                    )

        # ====================================================
        # CLEAN CONSOLE REPORT
        # ====================================================

        failure_reasons = (
            stats[
                "failure_reasons"
            ]
        )

        if failure_reasons:

            print(
                "\n[UPLOAD SCAN] "
                f"{stats['failed_count']} failed messages"
            )

            for reason, count in (
                failure_reasons.most_common()
            ):
                print(
                    f"  - {count}x {reason}"
                )

        return items, stats
  
    # ========================================================  
    # MANUALLY ADD A MISSING VIDEO  
    # ========================================================  
  
    async def inspect_video_link(  
        self,  
        link,  
        expected_bolum,  
    ):  
        parsed = (  
            self.parse_telegram_message_link(  
                link  
            )  
        )  
  
        if not parsed:  
            raise ValueError(  
                "Invalid Telegram message link."  
            )  
  
        channel = (  
            await self.resolve_source_channel(  
                parsed  
            )  
        )  
  
        message = (  
            await self.client.get_messages(  
                channel,  
                ids=parsed[  
                    "message_id"  
                ],  
            )  
        )  
  
        if not message:  
            raise ValueError(  
                "Message not found."  
            )  
  
        if not self.is_video_message(  
            message  
        ):  
            raise ValueError(  
                "The linked message is not a video."  
            )  
  
        caption = (  
            getattr(  
                message,  
                "raw_text",  
                None,  
            )  
            or ""  
        ).strip()  
  
        if not caption:  
            raise ValueError(  
                "The linked video has no caption."  
            )  
  
        metadata, global_episode = (  
            self.build_caption_metadata(  
                self.session[  
                    "user_id"  
                ],  
                caption,  
            )  
        )  
  
        if global_episode is None:  
            raise ValueError(  
                "Could not determine global Bölüm from this video's caption."  
            )  
  
        global_episode = int(  
            global_episode  
        )  
  
        if global_episode != int(  
            expected_bolum  
        ):  
            raise ValueError(  
                "Wrong Bölüm.\n\n"  
                f"Expected: {expected_bolum}\n"  
                f"Found: {global_episode}"  
            )  
  
        season = metadata.get(  
            "season"  
        )  
  
        season_episode = metadata.get(  
            "season_episode"  
        )  
  
        display_episode = (  
            season_episode  
            if season_episode is not None  
            else metadata.get("episode")  
        )  
  
        quality = (  
            metadata.get("quality")  
            or "Unknown"  
        )  
  
        return {  
            "message": message,  
            "message_id": message.id,  
  
            "source_channel": channel,  
  
            "metadata": metadata,  
  
            "bolum": global_episode,  
  
            "season": season,  
  
            "episode": display_episode,  
  
            "season_episode": season_episode,  
  
            "quality": str(  
                quality  
            ),  
  
            "title": (  
                metadata.get("title")  
                or ""  
            ),  
  
            "link": self.build_message_link(  
                channel,  
                message.id,  
            ),  
        }  
  
    # ========================================================  
    # GROUPING  
    # ========================================================  
  
    def group_items(  
        self,  
        items,  
    ):  
        groups = {}  
  
        for item in items:  
  
            key = (  
                item["bolum"],  
            )  
  
            if key not in groups:  
                groups[key] = {  
                    "bolum": item[  
                        "bolum"  
                    ],  
  
                    "season": item[  
                        "season"  
                    ],  
  
                    "episode": item[  
                        "episode"  
                    ],  
  
                    "title": item[  
                        "title"  
                    ],  
  
                    "items": [],  
                }  
  
            groups[key][  
                "items"  
            ].append(item)  
  
        for group in groups.values():  
  
            group["items"].sort(  
                key=lambda item: (  
                    _quality_value(  
                        item[  
                            "quality"  
                        ]  
                    ),  
                    item[  
                        "message_id"  
                    ],  
                )  
            )  
  
        return dict(  
            sorted(  
                groups.items(),  
                key=lambda pair: pair[  
                    0  
                ][0],  
            )  
        )  
  
    # ========================================================  
    # SEASON RANGE EXPECTATION  
    # ========================================================  
  
    def expected_episode_numbers(
        self,
        start_id,
        end_id,
        groups,
    ):
        """
        Determine missing GLOBAL Bölüm numbers.

        IMPORTANT:
        Telegram message IDs are NEVER treated as
        episode/Bölüm numbers.

        The actual Bölüm numbers come from the
        parsed video captions.
        """

        existing = set()

        for group in (
            groups or {}
        ).values():

            try:
                bolum = int(
                    group[
                        "bolum"
                    ]
                )

                existing.add(
                    bolum
                )

            except (
                TypeError,
                ValueError,
                KeyError,
            ):
                continue

        # No successfully parsed videos means
        # there is no reliable episode range to infer.
        if not existing:
            return []

        minimum_bolum = min(
            existing
        )

        maximum_bolum = max(
            existing
        )

        # ----------------------------------------------------
        # Build the continuous GLOBAL Bölüm range from the
        # actual caption metadata.
        # ----------------------------------------------------

        expected = set(
            range(
                minimum_bolum,
                maximum_bolum + 1,
            )
        )

        return sorted(
            expected - existing
        )        
  
    # ========================================================  
    # BUILD EPISODE HEADER METADATA  
    # ========================================================  
  
    def build_header_metadata(  
        self,  
        group,  
    ):  
        first_item = (  
            group["items"][0]  
        )  
  
        metadata = dict(  
            first_item.get(  
                "metadata",  
                {},  
            )  
        )  
  
        metadata[  
            "bolum"  
        ] = group[  
            "bolum"  
        ]  
  
        metadata[  
            "season"  
        ] = group[  
            "season"  
        ]  
  
        metadata[  
            "episode"  
        ] = group[  
            "episode"  
        ]  
  
        metadata[  
            "season_episode"  
        ] = group[  
            "episode"  
        ]  
  
        metadata[  
            "title"  
        ] = group[  
            "title"  
        ]  
  
        metadata[  
            "quality"  
        ] = (  
            first_item.get(  
                "quality"  
            )  
            or ""  
        )  
  
        return metadata  
  
    # ========================================================  
    # PREVIEW GROUP LINE  
    # ========================================================  
  
    def build_group_preview_lines(  
        self,  
        group,  
    ):  
        lines = []  
  
        season = group[  
            "season"  
        ]  
  
        episode = group[  
            "episode"  
        ]  
  
        bolum = group[  
            "bolum"  
        ]  
  
        lines.append(  
            f"🎬 <b>Season "  
            f"{escape(str(season))}"  
            f" | Episode "  
            f"{escape(_format_episode_number(episode))}"  
            f" | Bölüm "  
            f"{escape(str(bolum))}</b>"  
        )  
  
        # Header preview.  
        template = self.session.get(  
            "episode_format"  
        )  
  
        if template:  
            metadata = (  
                self.build_header_metadata(  
                    group  
                )  
            )  
  
            header = render_episode_template(  
                template,  
                metadata,  
            )  
  
            if (  
                metadata.get(  
                    "finale"  
                )  
                and "{finale}" in template  
            ):  
                pass  
  
            if header:  
                lines.append(  
                    "📝 <b>Episode Message:</b>"  
                )  
  
                lines.append(  
                    f"<pre>{escape(header)}</pre>"  
                    if "<"  
                    not in header  
                    else  
                    header  
                )  
  
        lines.append(  
            "📦 <b>Files:</b>"  
        )  
  
        for index, item in enumerate(  
            group["items"],  
            start=1,  
        ):  
            quality = escape(  
                str(  
                    item[  
                        "quality"  
                    ]  
                )  
            )  
  
            link = item.get(  
                "link"  
            )  
  
            label = (  
                f"{index:02d}. "  
                f"{quality}"  
            )  
  
            if link:  
                lines.append(  
                    f'   └─ <a href="{escape(link)}">'  
                    f"{label}</a>"  
                )  
            else:  
                lines.append(  
                    f"   └─ <code>{label}</code>"  
                )  
  
        lines.append(  
            "🎟️ KayiTV sticker"  
        )  
  
        if self.session.get(  
            "episode_sticker"  
        ):  
            lines.append(  
                "   └─ configured"  
            )  
        else:  
            lines.append(  
                "   └─ ⚠️ not configured"  
            )  
  
        return lines  
  
    # ========================================================  
    # FINAL PREVIEW  
    # ========================================================  
  
    def build_preview(  
        self,  
    ):  
        groups = self.session[  
            "groups"  
        ]  
  
        sorted_groups = sorted(  
            groups.values(),  
            key=lambda group: (  
                _numeric_value(  
                    group[  
                        "season"  
                    ]  
                ),  
                _numeric_value(  
                    group[  
                        "episode"  
                    ]  
                ),  
                group[  
                    "bolum"  
                ],  
            ),  
        )  
  
        missing = self.session.get(  
            "missing_episodes",  
            [],  
        )  
  
        skipped = self.session.get(  
            "skipped_episodes",  
            [],  
        )  
  
        stats = self.session.get(  
            "stats",  
            {},  
        )  
  
        lines = [  
            "📝 <b>EPISODE UPLOAD PREVIEW</b>",  
            "<code>━━━━━━━━━━━━━━━━━━━━━━━━━━━━</code>",  
            "",  
        ]  
  
        source_channel = self.session.get(  
            "source_channel"  
        )  
  
        source_title = (  
            getattr(  
                source_channel,  
                "title",  
                None,  
            )  
            or getattr(  
                source_channel,  
                "username",  
                None,  
            )  
            or "Unknown"  
        )  
  
        lines.extend(  
            [  
                "📥 <b>SOURCE</b>",  
                (  
                    f"📢 Channel: "  
                    f"<code>{escape(str(source_title))}</code>"  
                ),  
                (  
                    f"📌 Range: "  
                    f"<code>{self.session['source_start_id']}</code>"  
                    " → "  
                    f"<code>{self.session['source_end_id']}</code>"  
                ),  
                "",  
            ]  
        )  
  
        destination = self.session.get(  
            "destination"  
        )  
  
        if destination:  
            destination_title = (  
                getattr(  
                    destination,  
                    "title",  
                    None,  
                )  
                or getattr(  
                    destination,  
                    "first_name",  
                    None,  
                )  
                or getattr(  
                    destination,  
                    "username",  
                    None,  
                )  
                or str(  
                    self.session.get(  
                        "destination_id"  
                    )  
                )  
            )  
  
            lines.extend(  
                [  
                    "📤 <b>DESTINATION</b>",  
                    (  
                        f"📢 "  
                        f"<code>{escape(str(destination_title))}</code>"  
                    ),  
                    "",  
                ]  
            )  
        else:  
            lines.extend(  
                [  
                    "📤 <b>DESTINATION</b>",  
                    "📍 Current chat",  
                    "",  
                ]  
            )  
  
        if not self.session.get(  
            "episode_format"  
        ):  
            lines.extend(  
                [  
                    "⚠️ <b>Episode format is not configured.</b>",  
                    "",  
                ]  
            )  
  
        if not self.session.get(  
            "episode_sticker"  
        ):  
            lines.extend(  
                [  
                    "⚠️ <b>KayiTV sticker is not configured.</b>",  
                    "",  
                ]  
            )  
  
        current_season = object()  
  
        for group in sorted_groups:  
  
            season = group[  
                "season"  
            ]  
  
            if season != current_season:  
                if current_season is not object():  
                    lines.append("")  
  
                lines.extend(  
                    [  
                        f"🎬 <b>SEASON "  
                        f"{escape(str(season))}</b>",  
                        "",  
                    ]  
                )  
  
                current_season = season  
  
            lines.extend(  
                self.build_group_preview_lines(  
                    group  
                )  
            )  
  
            lines.append("")  
  
        lines.extend(  
            [  
                "<code>━━━━━━━━━━━━━━━━━━━━━━━━━━━━</code>",  
                "",  
                "📊 <b>SUMMARY</b>",  
                "",  
                (  
                    f"🎬 Episodes: "  
                    f"<code>{len(sorted_groups)}</code>"  
                ),  
                (  
                    f"📦 Files: "  
                    f"<code>{sum(len(g['items']) for g in sorted_groups)}</code>"  
                ),  
                (  
                    f"⏭️ Skipped source messages: "  
                    f"<code>{stats.get('skipped_count', 0)}</code>"  
                ),  
                (  
                    f"⚠️ Failed source messages: "  
                    f"<code>{stats.get('failed_count', 0)}</code>"  
                ),  
            ]  
        )  
  
        if missing:  
            lines.extend(  
                [  
                    "",  
                    "🚨 <b>MISSING EPISODES</b>",  
                    "",  
                    escape(  
                        ", ".join(  
                            str(number)  
                            for number in missing  
                        )  
                    ),  
                    "",  
                    "These must be resolved before confirmation.",  
                ]  
            )  
  
        if skipped:  
            lines.extend(  
                [  
                    "",  
                    "⏭️ <b>SKIPPED EPISODES</b>",  
                    "",  
                    escape(  
                        ", ".join(  
                            str(number)  
                            for number in skipped  
                        )  
                    ),  
                ]  
            )  
  
        lines.extend(  
            [  
                "",  
                "<code>━━━━━━━━━━━━━━━━━━━━━━━━━━━━</code>",  
                "",  
            ]  
        )  
  
        if missing:  
            lines.extend(  
                [  
                    "⚠️ <b>Upload is NOT ready yet.</b>",  
                    "",  
                    "Resolve the missing episodes first.",  
                    "Send message links for the requested Bölüm.",  
                    "Use <code>/skip</code> to intentionally skip it.",  
                ]  
            )  
        else:  
            lines.extend(  
                [  
                    "⚠️ <b>PREVIEW ONLY</b>",  
                    "",  
                    "Nothing has been uploaded yet.",  
                    "",  
                    "Use:",  
                    "<code>/uploadconfirm</code>",  
                    "or",  
                    "<code>/uploadconfirm &lt;destination_id&gt;</code>",  
                ]  
            )  
  
        return "\n".join(lines)  
  
    # ========================================================  
    # PREVIEW SENDER  
    # ========================================================  
  
    async def send_preview(  
        self,  
        event,  
    ):  
        preview = self.build_preview()  
  
        self.session[  
            "preview_text"  
        ] = preview  
  
        chunks = []  
  
        current = ""  
  
        for line in preview.splitlines(  
            keepends=True  
        ):  
  
            if (  
                len(current)  
                + len(line)  
                > TELEGRAM_TEXT_LIMIT  
            ):  
                if current:  
                    chunks.append(  
                        current  
                    )  
  
                current = line  
  
            else:  
                current += line  
  
        if current:  
            chunks.append(  
                current  
            )  
  
        if not chunks:  
            chunks = [  
                "⚠️ Empty preview."  
            ]  
  
        first = await event.reply(  
            chunks[0],  
            parse_mode="html",  
            link_preview=False,  
        )  
  
        for chunk in chunks[1:]:  
            await event.reply(  
                chunk,  
                parse_mode="html",  
                link_preview=False,  
            )  
  
        return first  
  
    # ========================================================  
    # BUILD FINALE STATE  
    # ========================================================  
  
    def refresh_finale_values(  
        self,  
    ):  
        """  
        Recalculate {finale} from the configured season  
        boundaries.  
  
        The final global Bölüm before the next /setseason  
        boundary is the season finale.  
        """  
  
        groups = self.session[  
            "groups"  
        ]  
  
        try:  
            mappings = (  
                self.get_season_mappings(  
                    self.session[  
                        "user_id"  
                    ]  
                )  
                or []  
            )  
  
        except Exception:  
            mappings = []  
  
        normalized = []  
  
        for mapping in mappings:  
  
            try:  
                normalized.append(  
                    {  
                        "season": int(  
                            mapping[  
                                "season"  
                            ]  
                        ),  
                        "start": int(  
                            mapping[  
                                "start_global_episode"  
                            ]  
                        ),  
                    }  
                )  
  
            except Exception:  
                continue  
  
        normalized.sort(  
            key=lambda item: item[  
                "start"  
            ]  
        )  
  
        finale_by_bolum = {}  
  
        for index, mapping in enumerate(  
            normalized  
        ):  
  
            if index + 1 >= len(  
                normalized  
            ):  
                continue  
  
            finale_bolum = (  
                normalized[  
                    index + 1  
                ]["start"]  
                - 1  
            )  
  
            finale_by_bolum[  
                finale_bolum  
            ] = True  
  
        for group in groups.values():  
  
            metadata = {}  
  
            if group["items"]:  
                metadata = dict(  
                    group[  
                        "items"  
                    ][0].get(  
                        "metadata",  
                        {},  
                    )  
                )  
  
            metadata[  
                "bolum"  
            ] = group[  
                "bolum"  
            ]  
  
            metadata[  
                "season"  
            ] = group[  
                "season"  
            ]  
  
            metadata[  
                "episode"  
            ] = group[  
                "episode"  
            ]  
  
            metadata[  
                "season_episode"  
            ] = group[  
                "episode"  
            ]  
  
            metadata[  
                "title"  
            ] = group[  
                "title"  
            ]  
  
            if finale_by_bolum.get(  
                group["bolum"]  
            ):  
                metadata[  
                    "finale"  
                ] = "Season Finale"  
            else:  
                metadata[  
                    "finale"  
                ] = ""  
  
            group[  
                "header_metadata"  
            ] = metadata  
  
    # ========================================================  
    # /EPISODE  
    # ========================================================  
  
    async def episode_command_handler(  
        self,  
        event,  
    ):  
        if not self.is_authorized(  
            event.sender_id  
        ):  
            await event.reply(  
                "❌ Unauthorized"  
            )  
            return  
  
        text = (  
            event.raw_text  
            or ""  
        )  
  
        parts = text.split(  
            None,  
            1,  
        )  
  
        # ----------------------------------------------------  
        # /episode  
        # ----------------------------------------------------  
  
        if len(parts) == 1:  
  
            current = (  
                get_saved_episode_format(  
                    event.sender_id  
                )  
            )  
  
            message = (  
                "📝 <b>EPISODE MESSAGE FORMAT</b>\n"  
                "<code>━━━━━━━━━━━━━━━━━━━━━━━━━━━━</code>\n\n"  
                "Send the format you want to use.\n\n"  
                "<b>Supported placeholders:</b>\n"  
                "<code>{title}</code>\n"  
                "<code>{season}</code>\n"  
                "<code>{episode}</code>\n"  
                "<code>{bolum}</code>\n"  
                "<code>{season_episode}</code>\n"  
                "<code>{quality}</code>\n"  
                "<code>{lang}</code>\n"  
                "<code>{source}</code>\n"  
                "<code>{year}</code>\n"  
                "<code>{sub}</code>\n"  
                "<code>{finale}</code>\n"  
                "<code>{part}</code>\n\n"  
                "<b>HTML is supported.</b>\n"  
                "For example:\n"  
                "<code>&lt;blockquote&gt;⚔️ "  
                "Season {season} | "  
                "Bölüm {bolum}&lt;/blockquote&gt;</code>\n\n"  
                "You can send the format as your next message."  
            )  
  
            if current:  
                message += (  
                    "\n\n"  
                    "📌 <b>Current saved format:</b>\n"  
                    f"{current}"  
                )  
  
            await event.reply(  
                message,  
                parse_mode="html",  
            )  
  
            self.session[  
                "active"  
            ] = True  
  
            self.session[  
                "user_id"  
            ] = event.sender_id  
  
            self.session[  
                "waiting_for"  
            ] = "episode_format"  
  
            return  
  
        # ----------------------------------------------------  
        # /episode <format>  
        # ----------------------------------------------------  
  
        raw_format = parts[1].strip()  
  
        if not raw_format:  
            await event.reply(  
                "❌ Episode format cannot be empty."  
            )  
            return  
  
        await self.save_episode_format_from_event(  
            event,  
            raw_format,  
        )  
  
    async def save_episode_format_from_event(  
        self,  
        event,  
        text,  
    ):  
        # If literal HTML exists, preserve it.  
        html_tag_pattern = re.compile(  
            r"<\s*/?\s*"  
            r"(?:"  
            r"a|b|strong|i|em|u|s|strike|del|"  
            r"code|pre|blockquote|tg-spoiler|span"  
            r")"  
            r"(?:\s+[^>]*)?"  
            r"\s*/?>",  
            re.IGNORECASE,  
        )  
  
        if html_tag_pattern.search(  
            text  
        ):  
            saved = text  
  
        else:  
            entities = (  
                getattr(  
                    event,  
                    "entities",  
                    None,  
                )  
                or []  
            )  
  
            if entities:  
                try:  
                    saved = (  
                        telegram_html.unparse(  
                            text,  
                            entities,  
                        )  
                    )  
  
                except Exception:  
                    saved = text  
  
            else:  
                saved = text  
  
        save_episode_format(  
            event.sender_id,  
            saved,  
        )  
  
        self.session[  
            "episode_format"  
        ] = saved  
  
        self.session[  
            "waiting_for"  
        ] = None  
  
        await event.reply(  
            "✅ <b>Episode message format saved.</b>\n\n"  
            f"{saved}\n\n"  
            "It will be used before every episode.",  
            parse_mode="html",  
        )  
  
    async def episode_format_capture_handler(  
        self,  
        event,  
    ):  
        if not self.is_authorized(  
            event.sender_id  
        ):  
            return  
  
        session = self.session  
  
        if not session.get(  
            "active"  
        ):  
            return  
  
        if session.get(  
            "user_id"  
        ) != event.sender_id:  
            return  
  
        if session.get(  
            "waiting_for"  
        ) != "episode_format":  
            return  
  
        text = (  
            event.raw_text  
            or ""  
        )  
  
        if not text.strip():  
            await event.reply(  
                "❌ <b>Episode message format cannot be empty.</b>\n\n"  
                "Please send the format again.",  
                parse_mode="html",  
            )  
            return  
  
        if text.strip().startswith("/"):  
            await event.reply(  
                "❌ <b>That is a command.</b>\n\n"  
                "Please send the episode message format.",  
                parse_mode="html",  
            )  
            return  
  
        await self.save_episode_format_from_event(  
            event,  
            text,  
        )  
  
    # ========================================================  
    # /STICKER  
    # ========================================================  
  
    async def sticker_command_handler(  
        self,  
        event,  
    ):  
        if not self.is_authorized(  
            event.sender_id  
        ):  
            await event.reply(  
                "❌ Unauthorized"  
            )  
            return  
  
        self._episode_sticker_waiting.add(  
            event.sender_id  
        )  
  
        await event.reply(  
            "📩 <b>Send the KayiTV logo sticker now.</b>\n\n"  
            "That sticker will be sent after every episode.",  
            parse_mode="html",  
        )  
  
    async def receive_sticker(
        self,
        event,
    ):
        if not self.is_authorized(
            event.sender_id
        ):
            return

        user_id = event.sender_id

        if user_id not in (
            self._episode_sticker_waiting
        ):
            return

        sticker = getattr(
            event,
            "sticker",
            None,
        )

        if not sticker:
            return

        self._episode_sticker_waiting.discard(
            user_id
        )

        sticker_data = {
            "file_id": getattr(
                sticker,
                "id",
                None,
            ),
            "access_hash": getattr(
                sticker,
                "access_hash",
                None,
            ),
            "file_reference": (
                getattr(
                    sticker,
                    "file_reference",
                    None,
                ).hex()
                if getattr(
                    sticker,
                    "file_reference",
                    None,
                )
                else None
            ),
            "chat_id": event.chat_id,
            "message_id": event.message.id,
        }

        # Keep the actual message available during
        # the current runtime.
        self._runtime_stickers = getattr(
            self,
            "_runtime_stickers",
            {},
        )

        self._runtime_stickers[
            user_id
        ] = event.message

        # Persistent DB.
        # Saving again replaces the previous sticker.
        save_episode_sticker(
            user_id,
            sticker_data,
        )

        self.session[
            "episode_sticker"
        ] = event.message

        await event.reply(
            "✅ <b>KayiTV logo sticker saved.</b>\n\n"
            "It will be used after every episode.",
            parse_mode="html",
        )
  
    # ========================================================  
    # /UPLOAD  
    # ========================================================  
  
    async def upload_command_handler(
        self,
        event,
    ):
        if not self.is_authorized(
            event.sender_id
        ):
            await event.reply(
                "❌ Unauthorized"
            )
            return

        text = (
            event.raw_text
            or ""
        ).strip()

        parts = text.split()

        if len(parts) != 1:
            await event.reply(
                "❌ Usage: <code>/upload</code>",
                parse_mode="html",
            )
            return

        self.clear_session()

        self.session[
            "active"
        ] = True

        self.session[
            "user_id"
        ] = event.sender_id

        saved_format = (
            get_saved_episode_format(
                event.sender_id
            )
        )

        saved_sticker = (
            get_saved_episode_sticker(
                event.sender_id
            )
        )

        self.session[
            "episode_format"
        ] = saved_format

        # ----------------------------------------------------
        # Recover persisted sticker.
        # Telegram file references may expire, so recover
        # the original sticker message when possible.
        # ----------------------------------------------------

        runtime_stickers = getattr(
            self,
            "_runtime_stickers",
            {},
        )

        if event.sender_id in runtime_stickers:

            self.session[
                "episode_sticker"
            ] = runtime_stickers[
                event.sender_id
            ]

        elif saved_sticker:

            try:
                sticker_chat_id = (
                    saved_sticker.get(
                        "chat_id"
                    )
                )

                sticker_message_id = (
                    saved_sticker.get(
                        "message_id"
                    )
                )

                if (
                    sticker_chat_id is not None
                    and sticker_message_id is not None
                ):

                    sticker_message = (
                        await self.client.get_messages(
                            sticker_chat_id,
                            ids=sticker_message_id,
                        )
                    )

                    if sticker_message:

                        self._runtime_stickers = getattr(
                            self,
                            "_runtime_stickers",
                            {},
                        )

                        self._runtime_stickers[
                            event.sender_id
                        ] = sticker_message

                        self.session[
                            "episode_sticker"
                        ] = sticker_message

                    else:
                        self.session[
                            "episode_sticker"
                        ] = None

                else:
                    self.session[
                        "episode_sticker"
                    ] = None

            except Exception:
                self.session[
                    "episode_sticker"
                ] = None

        else:
            self.session[
                "episode_sticker"
            ] = None

        self.session[
            "waiting_for"
        ] = "source_start"

        message = (
            "📤 <b>START EPISODE UPLOAD</b>\n"
            "<code>━━━━━━━━━━━━━━━━━━━━━━━━━━━━</code>\n\n"
            "📌 Send the <b>START source message link</b>.\n\n"
            "This should be the first video message "
            "of the upload range.\n\n"
            "Example:\n"
            "<code>https://t.me/YourChannel/1000</code>\n\n"
            "Use <code>/uploadcancel</code> to abort."
        )

        if not saved_format:
            message += (
                "\n\n⚠️ No episode message format is saved.\n"
                "Set it first with <code>/episode</code>."
            )

        if not saved_sticker:
            message += (
                "\n⚠️ No KayiTV sticker is saved.\n"
                "Set it first with <code>/sticker</code>."
            )

        await event.reply(
            message,
            parse_mode="html",
        )
  
    # ========================================================  
    # UPLOAD LINK HANDLER  
    # ========================================================  
  
    async def upload_link_handler(  
        self,  
        event,  
    ):  
        if not self.is_authorized(  
            event.sender_id  
        ):  
            return  
  
        if not self.session[  
            "active"  
        ]:  
            return  
  
        if self.session[  
            "user_id"  
        ] != event.sender_id:  
            return  
  
        stage = self.session[  
            "waiting_for"  
        ]  
  
        if stage not in (  
            "source_start",  
            "source_end",  
            "missing_links",  
        ):  
            return  
  
        text = (  
            event.raw_text  
            or ""  
        ).strip()  
  
        if stage in (  
            "source_start",  
            "source_end",  
        ):  
            await self.handle_source_range_link(  
                event,  
                text,  
            )  
  
            return  
  
        await self.handle_missing_episode_link(  
            event,  
            text,  
        )  
  
    # ========================================================  
    # SOURCE RANGE LINK  
    # ========================================================  
  
    async def handle_source_range_link(  
        self,  
        event,  
        text,  
    ):  
        parsed = (  
            self.parse_telegram_message_link(  
                text  
            )  
        )  
  
        if not parsed:  
            await event.reply(  
                "❌ <b>Invalid Telegram message link.</b>\n\n"  
                "Send a valid message link.",  
                parse_mode="html",  
            )  
            return  
  
        stage = self.session[  
            "waiting_for"  
        ]  
  
        try:  
            entity = (  
                await self.resolve_source_channel(  
                    parsed  
                )  
            )  
  
        except Exception as error:  
            await event.reply(  
                "❌ <b>Could not resolve the source link.</b>\n\n"  
                f"<code>{escape(str(error)[:1200])}</code>",  
                parse_mode="html",  
            )  
            return  
  
        if stage == "source_start":  
  
            self.session[  
                "source_channel"  
            ] = entity  
  
            self.session[  
                "source_start_id"  
            ] = parsed[  
                "message_id"  
            ]  
  
            self.session[  
                "waiting_for"  
            ] = "source_end"  
  
            await event.reply(  
                "✅ <b>Source START saved.</b>\n\n"  
                f"Message ID: <code>{parsed['message_id']}</code>\n\n"  
                "📌 Now send the <b>END source message link</b>.",  
                parse_mode="html",  
            )  
  
            return  
  
        # ----------------------------------------------------  
        # SOURCE END  
        # ----------------------------------------------------  
  
        start_entity = self.session[  
            "source_channel"  
        ]  
  
        if (  
            self.get_entity_peer_id(  
                start_entity  
            )  
            != self.get_entity_peer_id(  
                entity  
            )  
        ):  
            await event.reply(  
                "❌ <b>Different source channels.</b>\n\n"  
                "START and END links must belong to the same channel.",  
                parse_mode="html",  
            )  
            return  
  
        start_id = self.session[  
            "source_start_id"  
        ]  
  
        end_id = parsed[  
            "message_id"  
        ]  
  
        range_start = min(  
            start_id,  
            end_id,  
        )  
  
        range_end = max(  
            start_id,  
            end_id,  
        )  
  
        if (  
            range_end  
            - range_start  
            + 1  
            > MAX_RANGE  
        ):  
            await event.reply(  
                "❌ <b>Range too large.</b>\n\n"  
                f"Maximum allowed range: "  
                f"<code>{MAX_RANGE}</code> messages.",  
                parse_mode="html",  
            )  
            return  
  
        self.session[  
            "source_start_id"  
        ] = range_start  
  
        self.session[  
            "source_end_id"  
        ] = range_end  
  
        status = await event.reply(  
            "🔎 <b>Scanning upload source...</b>\n\n"  
            "Reading captions and metadata only.\n"  
            "No files are being uploaded yet.",  
            parse_mode="html",  
        )  
  
        try:  
            items, stats = (  
                await self.scan_source_range(  
                    channel=start_entity,  
                    start_id=range_start,  
                    end_id=range_end,  
                    user_id=self.session[  
                        "user_id"  
                    ],  
                )  
            )  
  
            groups = self.group_items(  
                items  
            )  
  
            self.session[  
                "items"  
            ] = items  
  
            self.session[  
                "groups"  
            ] = groups  
  
            self.session[  
                "stats"  
            ] = stats  
  
            missing = (  
                self.expected_episode_numbers(  
                    start_id=range_start,  
                    end_id=range_end,  
                    groups=groups,  
                )  
            )  
  
            self.session[  
                "missing_episodes"  
            ] = missing  
  
            self.session[  
                "missing_index"  
            ] = 0  
  
            self.refresh_finale_values()  
  
            if missing:  
  
                await status.edit(  
                    "🚨 <b>MISSING EPISODES DETECTED</b>\n\n"  
                    f"Missing global Bölüm(s):\n"  
                    f"<code>{escape(', '.join(str(x) for x in missing))}</code>\n\n"  
                    "I will ask for the message link of each "  
                    "missing episode.\n\n"  
                    "Send the video message link for the "  
                    "requested Bölüm.\n"  
                    "Use <code>/skip</code> if you intentionally "  
                    "want to leave it missing.",  
                    parse_mode="html",  
                )  
  
                self.session[  
                    "waiting_for"  
                ] = "missing_links"  
  
                await self.prompt_next_missing_episode(  
                    event  
                )  
  
                return  
  
            self.session[  
                "waiting_for"  
            ] = "preview"  
  
            await status.edit(  
                "✅ <b>Source scan completed.</b>\n\n"  
                "No missing global Bölüm numbers were detected.\n\n"  
                "Generating upload preview...",  
                parse_mode="html",  
            )  
  
            await self.send_preview(  
                event  
            )  
  
            self.session[  
                "waiting_for"  
            ] = "confirm"  
  
        except Exception as error:  
  
            self.clear_session()  
  
            await status.edit(  
                "❌ <b>Could not prepare upload.</b>\n\n"  
                f"<code>{escape(str(error)[:1500])}</code>",  
                parse_mode="html",  
            )  
  
    # ========================================================  
    # MISSING EPISODE PROMPT  
    # ========================================================  
  
    async def prompt_next_missing_episode(  
        self,  
        event,  
    ):  
        missing = self.session.get(  
            "missing_episodes",  
            [],  
        )  
  
        index = self.session.get(  
            "missing_index",  
            0,  
        )  
  
        while index < len(  
            missing  
        ):  
            bolum = missing[  
                index  
            ]  
  
            if bolum in self.session.get(  
                "skipped_episodes",  
                [],  
            ):  
                index += 1  
  
                self.session[  
                    "missing_index"  
                ] = index  
  
                continue  
  
            self.session[  
                "missing_index"  
            ] = index  
  
            await event.reply(  
                "📌 <b>MISSING EPISODE</b>\n\n"  
                f"Global Bölüm: "  
                f"<code>{bolum}</code>\n\n"  
                "Send a Telegram message link for "  
                "this episode's video.\n\n"  
                "You can send multiple quality links "  
                "one after another.\n\n"  
                "Use <code>/skip</code> to intentionally "  
                "skip this episode.\n\n"  
                "Use <code>/missingdone</code> when all "  
                "quality links for this episode have been added.",  
                parse_mode="html",  
            )  
  
            return  
  
        # No missing episodes remain.  
        self.session[  
            "waiting_for"  
        ] = "preview"  
  
        self.refresh_finale_values()  
  
        await event.reply(  
            "✅ <b>Missing-episode check completed.</b>\n\n"  
            "Generating the updated preview...",  
            parse_mode="html",  
        )  
  
        await self.send_preview(  
            event  
        )  
  
        self.session[  
            "waiting_for"  
        ] = "confirm"  
  
    # ========================================================  
    # MISSING EPISODE LINK  
    # ========================================================  
  
    async def handle_missing_episode_link(  
        self,  
        event,  
        text,  
    ):  
        missing = self.session.get(  
            "missing_episodes",  
            [],  
        )  
  
        index = self.session.get(  
            "missing_index",  
            0,  
        )  
  
        if index >= len(  
            missing  
        ):  
            await self.prompt_next_missing_episode(  
                event  
            )  
            return  
  
        expected_bolum = missing[  
            index  
        ]  
  
        # ----------------------------------------------------  
        # Allow multiple links separated by newlines.  
        # ----------------------------------------------------  
  
        links = [  
            line.strip()  
            for line in text.splitlines()  
            if line.strip()  
        ]  
  
        if not links:  
            await event.reply(  
                "❌ Send at least one Telegram message link "  
                "or use <code>/skip</code>.",  
                parse_mode="html",  
            )  
            return  
  
        added = 0  
        errors = []  
  
        for link in links:  
  
            try:  
                item = (  
                    await self.inspect_video_link(  
                        link,  
                        expected_bolum,  
                    )  
                )  
  
                # The manually supplied source may be from a  
                # different channel than the original source.  
                #  
                # That is allowed because the user is explicitly  
                # repairing a missing episode.  
                #  
                # It will still upload the actual linked media.  
  
                item[  
                    "manual_source"  
                ] = True  
  
                item[  
                    "source_channel"  
                ] = item[  
                    "source_channel"  
                ]  
  
                self.session[  
                    "items"  
                ].append(  
                    item  
                )  
  
                group = self.session[  
                    "groups"  
                ].get(  
                    (  
                        expected_bolum,  
                    )  
                )  
  
                if group is None:  
                    group = {  
                        "bolum": expected_bolum,  
                        "season": item[  
                            "season"  
                        ],  
                        "episode": item[  
                            "episode"  
                        ],  
                        "title": item[  
                            "title"  
                        ],  
                        "items": [],  
                    }  
  
                    self.session[  
                        "groups"  
                    ][  
                        (  
                            expected_bolum,  
                        )  
                    ] = group  
  
                # Avoid adding the exact same message twice.  
                already_exists = any(  
                    existing.get(  
                        "message_id"  
                    )  
                    == item[  
                        "message_id"  
                    ]  
                    and self.get_entity_peer_id(  
                        existing.get(  
                            "source_channel"  
                        )  
                    )  
                    == self.get_entity_peer_id(  
                        item[  
                            "source_channel"  
                        ]  
                    )  
                    for existing in group[  
                        "items"  
                    ]  
                )  
  
                if already_exists:  
                    errors.append(  
                        f"{link}: already added"  
                    )  
                    continue  
  
                group[  
                    "items"  
                ].append(  
                    item  
                )  
  
                added += 1  
  
            except Exception as error:  
                errors.append(  
                    f"{link}: {error}"  
                )  
  
        # Sort current episode.  
        group = self.session[  
            "groups"  
        ].get(  
            (  
                expected_bolum,  
            )  
        )  
  
        if group:  
            group[  
                "items"  
            ].sort(  
                key=lambda item: (  
                    _quality_value(  
                        item[  
                            "quality"  
                        ]  
                    ),  
                    item[  
                        "message_id"  
                    ],  
                )  
            )  
  
        if added:  
            await event.reply(  
                "✅ <b>Missing episode link added.</b>\n\n"  
                f"Bölüm: <code>{expected_bolum}</code>\n"  
                f"Added files: <code>{added}</code>\n\n"  
                "Send another quality link if needed,\n"  
                "or use <code>/missingdone</code>.",  
                parse_mode="html",  
            )  
  
        if errors:  
            error_text = "\n".join(  
                f"• {escape(str(error)[:400])}"  
                for error in errors  
            )  
  
            await event.reply(  
                "⚠️ <b>Some links were not added:</b>\n\n"  
                f"{error_text}",  
                parse_mode="html",  
            )  
  
    # ========================================================  
    # /MISSINGDONE  
    # ========================================================  
  
    async def missingdone_command_handler(  
        self,  
        event,  
    ):  
        if not self.is_authorized(  
            event.sender_id  
        ):  
            return  
  
        if not self.session[  
            "active"  
        ]:  
            return  
  
        if self.session[  
            "user_id"  
        ] != event.sender_id:  
            return  
  
        if self.session[  
            "waiting_for"  
        ] != "missing_links":  
            return  
  
        missing = self.session.get(  
            "missing_episodes",  
            [],  
        )  
  
        index = self.session.get(  
            "missing_index",  
            0,  
        )  
  
        if index >= len(  
            missing  
        ):  
            await self.prompt_next_missing_episode(  
                event  
            )  
            return  
  
        expected_bolum = missing[  
            index  
        ]  
  
        group = self.session[  
            "groups"  
        ].get(  
            (  
                expected_bolum,  
            )  
        )  
  
        if not group or not group.get(  
            "items"  
        ):  
            await event.reply(  
                "❌ <b>No video has been added for this episode.</b>\n\n"  
                f"Bölüm: <code>{expected_bolum}</code>\n\n"  
                "Send at least one video message link,\n"  
                "or use <code>/skip</code>.",  
                parse_mode="html",  
            )  
            return  
  
        # Move to next missing episode.  
        self.session[  
            "missing_index"  
        ] = index + 1  
  
        await self.prompt_next_missing_episode(  
            event  
        )  
  
    # ========================================================  
    # /SKIP  
    # ========================================================  
  
    async def skip_command_handler(  
        self,  
        event,  
    ):  
        if not self.is_authorized(  
            event.sender_id  
        ):  
            return  
  
        if not self.session[  
            "active"  
        ]:  
            return  
  
        if self.session[  
            "user_id"  
        ] != event.sender_id:  
            return  
  
        if self.session[  
            "waiting_for"  
        ] != "missing_links":  
            await event.reply(  
                "ℹ️ /skip is only available when "  
                "resolving a missing episode.",  
            )  
            return  
  
        missing = self.session.get(  
            "missing_episodes",  
            [],  
        )  
  
        index = self.session.get(  
            "missing_index",  
            0,  
        )  
  
        if index >= len(  
            missing  
        ):  
            return  
  
        bolum = missing[  
            index  
        ]  
  
        self.session[  
            "skipped_episodes"  
        ].append(  
            bolum  
        )  
  
        self.session[  
            "missing_index"  
        ] = index + 1  
  
        await event.reply(  
            "⏭️ <b>Episode skipped.</b>\n\n"  
            f"Global Bölüm: <code>{bolum}</code>",  
            parse_mode="html",  
        )  
  
        await self.prompt_next_missing_episode(  
            event  
        )  
  
    # ========================================================  
    # DESTINATION  
    # ========================================================  
  
    async def set_destination_from_command(  
        self,  
        event,  
        destination_id,  
    ):  
        try:  
            entity = (  
                await self.resolve_destination(  
                    destination_id  
                )  
            )  
  
        except Exception as error:  
  
            await event.reply(  
                "❌ <b>Invalid destination ID.</b>\n\n"  
                f"<code>{escape(str(error)[:1200])}</code>\n\n"  
                "The upload session is still active.\n"  
                "Send another destination ID with:\n"  
                "<code>/uploadconfirm &lt;id&gt;</code>\n\n"  
                "Or use <code>/uploadconfirm</code> "  
                "for the current chat.",  
                parse_mode="html",  
            )  
  
            return False  
  
        self.session[  
            "destination"  
        ] = entity  
  
        self.session[  
            "destination_id"  
        ] = int(  
            destination_id  
        )  
  
        self.session[  
            "destination_valid"  
        ] = True  
  
        return True  
  
    # ========================================================  
    # /UPLOADCONFIRM  
    # ========================================================  
  
    async def uploadconfirm_command_handler(  
        self,  
        event,  
    ):  
        if not self.is_authorized(  
            event.sender_id  
        ):  
            await event.reply(  
                "❌ Unauthorized"  
            )  
            return  
  
        if not self.session[  
            "active"  
        ]:  
            await event.reply(  
                "ℹ️ No active upload session.\n\n"  
                "Start one with <code>/upload</code>.",  
                parse_mode="html",  
            )  
            return  
  
        if self.session[  
            "user_id"  
        ] != event.sender_id:  
            return  
  
        if self.session[  
            "waiting_for"  
        ] != "confirm":  
            await event.reply(  
                "❌ <b>Upload is not ready for confirmation.</b>\n\n"  
                "Complete the source range and missing-episode "  
                "workflow first.",  
                parse_mode="html",  
            )  
            return  
  
        text = (  
            event.raw_text  
            or ""  
        ).strip()  
  
        parts = text.split()  
  
        if len(parts) > 2:  
            await event.reply(  
                "❌ Usage:\n"  
                "<code>/uploadconfirm</code>\n"  
                "or\n"  
                "<code>/uploadconfirm &lt;destination_id&gt;</code>",  
                parse_mode="html",  
            )  
            return  
  
        # ----------------------------------------------------  
        # Destination explicitly supplied.  
        # ----------------------------------------------------  
  
        if len(parts) == 2:  
  
            destination_id = parts[  
                1  
            ]  
  
            try:  
                int(  
                    destination_id  
                )  
  
            except ValueError:  
                await event.reply(  
                    "❌ <b>Destination ID must be numeric.</b>\n\n"  
                    "The session is still active.",  
                    parse_mode="html",  
                )  
                return  
  
            valid = (  
                await self.set_destination_from_command(  
                    event,  
                    destination_id,  
                )  
            )  
  
            if not valid:  
                return  
  
        # ----------------------------------------------------  
        # No ID = current chat.  
        # ----------------------------------------------------  
  
        else:  
  
            try:  
                destination = (  
                    await self.client.get_entity(  
                        event.chat_id  
                    )  
                )  
  
            except Exception as error:  
                await event.reply(  
                    "❌ <b>Could not use the current chat "  
                    "as destination.</b>\n\n"  
                    f"<code>{escape(str(error)[:1000])}</code>\n\n"  
                    "The session is still active.",  
                    parse_mode="html",  
                )  
                return  
  
            self.session[  
                "destination"  
            ] = destination  
  
            self.session[  
                "destination_id"  
            ] = event.chat_id  
  
            self.session[  
                "destination_valid"  
            ] = True  
  
        # ----------------------------------------------------  
        # Final validation before destructive/publication step.  
        # ----------------------------------------------------  
  
        missing = []  
  
        groups = self.session[  
            "groups"  
        ]  
  
        expected = (  
            self.expected_episode_numbers(  
                start_id=self.session[  
                    "source_start_id"  
                ],  
                end_id=self.session[  
                    "source_end_id"  
                ],  
                groups=groups,  
            )  
        )  
  
        skipped = set(  
            self.session.get(  
                "skipped_episodes",  
                [],  
            )  
        )  
  
        for number in expected:  
            if number not in skipped:  
                missing.append(  
                    number  
                )  
  
        if missing:  
            self.session[  
                "missing_episodes"  
            ] = missing  
  
            self.session[  
                "missing_index"  
            ] = 0  
  
            self.session[  
                "waiting_for"  
            ] = "missing_links"  
  
            await event.reply(  
                "🚨 <b>Upload blocked.</b>\n\n"  
                "New missing episodes were detected "  
                "during final validation:\n\n"  
                f"<code>{escape(', '.join(str(x) for x in missing))}</code>\n\n"  
                "Resolve them first or use <code>/skip</code>.",  
                parse_mode="html",  
            )  
  
            await self.prompt_next_missing_episode(  
                event  
            )  
  
            return  
  
        if not self.session.get(  
            "episode_format"  
        ):  
            await event.reply(  
                "❌ <b>No episode message format is configured.</b>\n\n"  
                "Use <code>/episode</code> first.\n\n"  
                "The upload session remains active.",  
                parse_mode="html",  
            )  
            return  
  
        if not self.session.get(  
            "episode_sticker"  
        ):  
            await event.reply(  
                "❌ <b>No KayiTV sticker is configured.</b>\n\n"  
                "Use <code>/sticker</code> first.\n\n"  
                "The upload session remains active.",  
                parse_mode="html",  
            )  
            return  
  
        await self.execute_upload(  
            event  
        )  
  
    # ========================================================  
    # SEND EPISODE HEADER  
    # ========================================================  
  
    async def send_episode_header(  
        self,  
        destination,  
        group,  
    ):  
        template = self.session[  
            "episode_format"  
        ]  
  
        metadata = (  
            group.get(  
                "header_metadata"  
            )  
            or self.build_header_metadata(  
                group  
            )  
        )  
  
        header = render_episode_template(  
            template,  
            metadata,  
        )  
  
        if not header:  
            raise ValueError(  
                "Rendered episode header is empty."  
            )  
  
        await self.client.send_message(  
            destination,  
            header,  
            parse_mode="html",  
            link_preview=False,  
        )  
  
    # ========================================================  
    # SEND SOURCE MEDIA  
    # ========================================================  
  
    async def send_video_item(
        self,
        destination,
        item,
    ):
        """
        Forward the source video exactly as-is.
        
        This preserves:
        - Video file
        - Caption with formatting
        - Thumbnail (if any)
        - All original attributes
        
        No modifications are made to the video or its caption.
        """
        
        message = item["message"]
        
        # Forward the message exactly as-is
        # This copies the video + caption + all formatting
        await self.client.send_message(
            destination,
            message,
        )
  
    # ========================================================  
    # SEND KAYITV STICKER  
    # ========================================================  
  
    async def send_episode_sticker(  
        self,  
        destination,  
    ):  
        sticker_message = (  
            self.session[  
                "episode_sticker"  
            ]  
        )  
  
        if not sticker_message:  
            raise ValueError(  
                "KayiTV sticker is not configured."  
            )  
  
        await self.client.send_file(  
            destination,  
            sticker_message.media,  
        )  
  
    # ========================================================  
    # UPLOAD EXECUTION  
    # ========================================================  
  
    async def execute_upload(  
        self,  
        event,  
    ):  
        destination = self.session[  
            "destination"  
        ]  
  
        groups = self.session[  
            "groups"  
        ]  
  
        sorted_groups = sorted(  
            groups.values(),  
            key=lambda group: (  
                _numeric_value(  
                    group[  
                        "season"  
                    ]  
                ),  
                _numeric_value(  
                    group[  
                        "episode"  
                    ]  
                ),  
                group[  
                    "bolum"  
                ],  
            ),  
        )  
  
        total_files = sum(  
            len(  
                group[  
                    "items"  
                ]  
            )  
            for group in sorted_groups  
        )  
  
        total_episodes = len(  
            sorted_groups  
        )  
  
        status = await event.reply(  
            "🚀 <b>EPISODE UPLOAD STARTED</b>\n"  
            "<code>━━━━━━━━━━━━━━━━━━━━━━━━━━━━</code>\n\n"  
            f"🎬 Episodes: <code>{total_episodes}</code>\n"  
            f"📦 Files: <code>{total_files}</code>\n\n"  
            "Nothing from the source videos is being "  
            "downloaded to local storage.",  
            parse_mode="html",  
        )  
  
        processed_files = 0  
        uploaded_files = 0  
        failed_files = 0  
        uploaded_episodes = 0  
        failed_episodes = 0  
  
        failed_messages = []  
  
        try:  
  
            for group_index, group in enumerate(  
                sorted_groups,  
                start=1,  
            ):  
  
                bolum = group[  
                    "bolum"  
                ]  
  
                # ------------------------------------------------  
                # FINAL METADATA REFRESH  
                # ------------------------------------------------  
  
                metadata = (  
                    group.get(  
                        "header_metadata"  
                    )  
                    or self.build_header_metadata(  
                        group  
                    )  
                )  
  
                # ------------------------------------------------  
                # EPISODE HEADER  
                # ------------------------------------------------  
  
                try:  
                    await self.send_episode_header(  
                        destination,  
                        group,  
                    )  
  
                except FloodWaitError as error:  
  
                    await status.edit(  
                        "⏳ <b>Telegram FloodWait</b>\n\n"  
                        f"Waiting <code>{error.seconds}</code> "  
                        "seconds...\n\n"  
                        f"Episode: <code>{bolum}</code>",  
                        parse_mode="html",  
                    )  
  
                    await asyncio.sleep(  
                        error.seconds  
                    )  
  
                    try:  
                        await self.send_episode_header(  
                            destination,  
                            group,  
                        )  
  
                    except Exception as retry_error:  
                        failed_episodes += 1  
  
                        failed_messages.append(  
                            (  
                                bolum,  
                                (  
                                    "Episode header failed: "  
                                    f"{retry_error}"  
                                ),  
                            )  
                        )  
  
                        continue  
  
                except Exception as error:  
  
                    failed_episodes += 1  
  
                    failed_messages.append(  
                        (  
                            bolum,  
                            (  
                                "Episode header failed: "  
                                f"{error}"  
                            ),  
                        )  
                    )  
  
                    continue  
  
                await asyncio.sleep(  
                    SEND_DELAY  
                )  
  
                # ------------------------------------------------  
                # FILES: LOW -> HIGH QUALITY  
                # ------------------------------------------------  
  
                episode_failed = False  
  
                qualities = sorted(  
                    group[  
                        "items"  
                    ],  
                    key=lambda item: (  
                        _quality_value(  
                            item[  
                                "quality"  
                            ]  
                        ),  
                        item[  
                            "message_id"  
                        ],  
                    ),  
                )  
  
                for item in qualities:  
  
                    processed_files += 1  
  
                    try:  
  
                        await self.send_video_item(  
                            destination,  
                            item,  
                        )  
  
                        uploaded_files += 1  
  
                    except FloodWaitError as error:  
  
                        await status.edit(  
                            "⏳ <b>Telegram FloodWait</b>\n\n"  
                            f"Waiting <code>{error.seconds}</code> "  
                            "seconds...\n\n"  
                            f"📊 Progress: "  
                            f"<code>{processed_files}/{total_files}</code>",  
                            parse_mode="html",  
                        )  
  
                        await asyncio.sleep(  
                            error.seconds  
                        )  
  
                        try:  
  
                            await self.send_video_item(  
                                destination,  
                                item,  
                            )  
  
                            uploaded_files += 1  
  
                        except Exception as retry_error:  
  
                            failed_files += 1  
                            episode_failed = True  
  
                            failed_messages.append(  
                                (  
                                    item[  
                                        "message_id"  
                                    ],  
                                    (  
                                        "Video retry failed: "  
                                        f"{retry_error}"  
                                    ),  
                                )  
                            )  
  
                    except Exception as error:  
  
                        failed_files += 1  
                        episode_failed = True  
  
                        failed_messages.append(  
                            (  
                                item[  
                                    "message_id"  
                                ],  
                                str(error),  
                            )  
                        )  
  
                    if (  
                        processed_files == 1  
                        or processed_files % 5 == 0  
                        or processed_files  
                        == total_files  
                    ):  
                        await status.edit(  
                            "🚀 <b>EPISODE UPLOAD IN PROGRESS</b>\n"  
                            "<code>━━━━━━━━━━━━━━━━━━━━</code>\n\n"  
                            f"🎬 Episode: "  
                            f"<code>{group_index}/{total_episodes}</code>\n"  
                            f"📦 Files: "  
                            f"<code>{processed_files}/{total_files}</code>\n\n"  
                            f"✅ Uploaded: "  
                            f"<code>{uploaded_files}</code>\n"  
                            f"❌ Failed: "  
                            f"<code>{failed_files}</code>",  
                            parse_mode="html",  
                        )  
  
                    await asyncio.sleep(  
                        SEND_DELAY  
                    )  
  
                # ------------------------------------------------  
                # KAYITV STICKER  
                # ------------------------------------------------  
  
                try:  
  
                    await self.send_episode_sticker(  
                        destination  
                    )  
  
                except FloodWaitError as error:  
  
                    await status.edit(  
                        "⏳ <b>Telegram FloodWait</b>\n\n"  
                        f"Waiting <code>{error.seconds}</code> "  
                        "seconds before sticker...",  
                        parse_mode="html",  
                    )  
  
                    await asyncio.sleep(  
                        error.seconds  
                    )  
  
                    try:  
                        await self.send_episode_sticker(  
                            destination  
                        )  
  
                    except Exception as retry_error:  
  
                        failed_episodes += 1  
  
                        failed_messages.append(  
                            (  
                                bolum,  
                                (  
                                    "KayiTV sticker failed: "  
                                    f"{retry_error}"  
                                ),  
                            )  
                        )  
  
                        continue  
  
                except Exception as error:  
  
                    failed_episodes += 1  
  
                    failed_messages.append(  
                        (  
                            bolum,  
                            (  
                                "KayiTV sticker failed: "  
                                f"{error}"  
                            ),  
                        )  
                    )  
  
                    continue  
  
                if not episode_failed:  
                    uploaded_episodes += 1  
  
                await asyncio.sleep(  
                    SEND_DELAY  
                )  
  
            # ----------------------------------------------------  
            # COMPLETION  
            # ----------------------------------------------------  
  
            completion = (  
                "✅ <b>EPISODE UPLOAD COMPLETED</b>\n"  
                "<code>━━━━━━━━━━━━━━━━━━━━━━━━━━━━</code>\n\n"  
                f"🎬 Episodes processed: "  
                f"<code>{total_episodes}</code>\n"  
                f"📦 Files processed: "  
                f"<code>{processed_files}</code>\n\n"  
                f"✅ Files uploaded: "  
                f"<code>{uploaded_files}</code>\n"  
                f"❌ Failed files: "  
                f"<code>{failed_files}</code>\n"  
                f"🎬 Successful episodes: "  
                f"<code>{uploaded_episodes}</code>\n"  
                f"⚠️ Failed episodes: "  
                f"<code>{failed_episodes}</code>"  
            )  
  
            if self.session.get(  
                "skipped_episodes"  
            ):  
                completion += (  
                    "\n\n⏭️ <b>Skipped Bölüm:</b>\n"  
                    + escape(  
                        ", ".join(  
                            str(x)  
                            for x in self.session[  
                                "skipped_episodes"  
                            ]  
                        )  
                    )  
                )  
  
            if failed_messages:  
                completion += (  
                    "\n\n❌ <b>Failures:</b>\n"  
                )  
  
                for message_id, reason in (  
                    failed_messages[:15]  
                ):  
                    safe_reason = (  
                        str(reason)  
                        .replace(  
                            "\n",  
                            " ",  
                        )  
                        [:180]  
                    )  
  
                    completion += (  
                        f"• <code>{escape(str(message_id))}</code>"  
                        f" — {escape(safe_reason)}\n"  
                    )  
  
                remaining = (  
                    len(failed_messages)  
                    - 15  
                )  
  
                if remaining > 0:  
                    completion += (  
                        f"• ... and {remaining} more"  
                    )  
  
            await status.edit(  
                completion,  
                parse_mode="html",  
            )  
  
            self.clear_session()  
  
        except Exception as error:  
  
            await status.edit(  
                "❌ <b>UPLOAD STOPPED</b>\n\n"  
                f"<code>{escape(str(error)[:1500])}</code>\n\n"  
                "The session has been preserved until "  
                "this command finishes.",  
                parse_mode="html",  
            )  
  
    # ========================================================  
    # /UPLOADCANCEL  
    # ========================================================  
  
    async def uploadcancel_command_handler(  
        self,  
        event,  
    ):  
        if not self.is_authorized(  
            event.sender_id  
        ):  
            await event.reply(  
                "❌ Unauthorized"  
            )  
            return  
  
        if not self.session[  
            "active"  
        ]:  
            await event.reply(  
                "ℹ️ No active upload session."  
            )  
            return  
  
        if self.session[  
            "user_id"  
        ] != event.sender_id:  
            return  
  
        self.clear_session()  
  
        await event.reply(  
            "❌ <b>Episode upload cancelled.</b>\n\n"  
            "No further episodes will be uploaded.",  
            parse_mode="html",  
        )  
  
    # ========================================================  
    # /UPLOAD STATUS  
    # ========================================================  
  
    async def uploadstatus_command_handler(  
        self,  
        event,  
    ):  
        if not self.is_authorized(  
            event.sender_id  
        ):  
            await event.reply(  
                "❌ Unauthorized"  
            )  
            return  
  
        if not self.session[  
            "active"  
        ]:  
            await event.reply(  
                "ℹ️ No active upload session."  
            )  
            return  
  
        groups = self.session.get(  
            "groups",  
            {},  
        )  
  
        missing = self.session.get(  
            "missing_episodes",  
            [],  
        )  
  
        skipped = self.session.get(  
            "skipped_episodes",  
            [],  
        )  
  
        waiting = self.session.get(  
            "waiting_for"  
        )  
  
        await event.reply(  
            "📊 <b>UPLOAD SESSION STATUS</b>\n\n"  
            f"State: <code>{escape(str(waiting))}</code>\n"  
            f"Episodes: <code>{len(groups)}</code>\n"  
            f"Missing: <code>{len(missing)}</code>\n"  
            f"Skipped: <code>{len(skipped)}</code>",  
            parse_mode="html",  
        )  
  
    # ========================================================  
    # REGISTER HANDLERS  
    # ========================================================  
  
    def register_handlers(  
        self,  
    ):  
        """  
        Register all upload-manager handlers.  
  
        This must be called exactly once.  
        """  
  
        if self._handlers_registered:  
            return  
  
        # ====================================================  
        # /EPISODE  
        # ====================================================  
  
        self.client.add_event_handler(  
            self.episode_command_handler,  
            events.NewMessage(  
                pattern=r"^/episode(?:\s+.+)?$",  
                outgoing=True,  
            ),  
        )  
  
        # ====================================================  
        # EPISODE FORMAT CAPTURE  
        # ====================================================  
  
        self.client.add_event_handler(  
            self.episode_format_capture_handler,  
            events.NewMessage(  
                outgoing=True,  
            ),  
        )  
  
        # ====================================================  
        # /STICKER  
        # ====================================================  
  
        self.client.add_event_handler(  
            self.sticker_command_handler,  
            events.NewMessage(  
                pattern=r"^/sticker$",  
                outgoing=True,  
            ),  
        )  
  
        # ====================================================  
        # STICKER RECEIVER  
        # ====================================================  
  
        self.client.add_event_handler(  
            self.receive_sticker,  
            events.NewMessage(  
                func=lambda event: bool(  
                    getattr(  
                        event.message,  
                        "sticker",  
                        None,  
                    )  
                ),  
                outgoing=True,  
            ),  
        )  
  
        # ====================================================  
        # /UPLOAD  
        # ====================================================  
  
        self.client.add_event_handler(  
            self.upload_command_handler,  
            events.NewMessage(  
                pattern=r"^/upload$",  
                outgoing=True,  
            ),  
        )  
  
        # ====================================================  
        # UPLOAD SOURCE / MISSING LINKS  
        # ====================================================  
  
        self.client.add_event_handler(  
            self.upload_link_handler,  
            events.NewMessage(  
                pattern=r"^(?:https?://)?t\.me/.*$",  
                outgoing=True,  
            ),  
        )  
  
        # ====================================================  
        # /MISSINGDONE  
        # ====================================================  
  
        self.client.add_event_handler(  
            self.missingdone_command_handler,  
            events.NewMessage(  
                pattern=r"^/missingdone$",  
                outgoing=True,  
            ),  
        )  
  
        # ====================================================  
        # /SKIP  
        # ====================================================  
  
        self.client.add_event_handler(  
            self.skip_command_handler,  
            events.NewMessage(  
                pattern=r"^/skip$",  
                outgoing=True,  
            ),  
        )  
  
        # ====================================================  
        # /UPLOADCONFIRM  
        # ====================================================  
  
        self.client.add_event_handler(  
            self.uploadconfirm_command_handler,  
            events.NewMessage(  
                pattern=r"^/uploadconfirm(?:\s+[-]?\d+)?$",  
                outgoing=True,  
            ),  
        )  
  
        # ====================================================  
        # /UPLOADCANCEL  
        # ====================================================  
  
        self.client.add_event_handler(  
            self.uploadcancel_command_handler,  
            events.NewMessage(  
                pattern=r"^/uploadcancel$",  
                outgoing=True,  
            ),  
        )  
  
        # ====================================================  
        # /UPLOADSTATUS  
        # ====================================================  
  
        self.client.add_event_handler(  
            self.uploadstatus_command_handler,  
            events.NewMessage(  
                pattern=r"^/uploadstatus$",  
                outgoing=True,  
            ),  
        )  
  
        self._handlers_registered = True

# ============================================================
# PUBLIC REGISTRATION FUNCTION
# ============================================================

def register_upload_handlers(
    client,
    is_authorized,
    build_caption_metadata,
    build_message_link,
    get_season_mappings,
):
    """
    Create and register the single EpisodeUploadManager.

    replace.py should call this EXACTLY ONCE.
    """

    # ------------------------------------------------------------
    # TEMP COPY MANAGER
    # ------------------------------------------------------------
    #
    # The Telegram client is supplied by replace.py, so this
    # manager must be created here rather than at module import.
    #
    temp_copy_manager = TempCopyManager(
        client=client,
        is_authorized=is_authorized,
        get_episode_format=get_saved_episode_format,
        get_episode_sticker=get_saved_episode_sticker,
    )

    temp_copy_manager.register_handlers()

    # ------------------------------------------------------------
    # EPISODE UPLOAD MANAGER
    # ------------------------------------------------------------

    manager = EpisodeUploadManager(
        client=client,
        is_authorized=is_authorized,
        build_caption_metadata=(
            build_caption_metadata
        ),
        build_message_link=(
            build_message_link
        ),
        get_season_mappings=(
            get_season_mappings
        ),
    )

    manager.register_handlers()

    return manager