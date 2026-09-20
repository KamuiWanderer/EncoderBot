"""
Thumbnail Manager
=================

Standalone /thumb module for the Telethon userbot.

This module:
- does NOT create its own TelegramClient
- does NOT start its own client
- does NOT own Telegram authorization
- does NOT contain any global @client.on(...) handlers
- does NOT contain /debug
- does NOT contain /cover
- does NOT contain any unrelated handlers
- does NOT implement a second caption parser

replace.py provides:
- the authenticated Telethon client
- authorization callback
- existing caption metadata parser
- existing Telegram message-link builder

This module owns the complete /thumb workflow.
"""

import asyncio
import io
import re
import struct
from html import escape

from telethon import events, types, functions
from telethon.errors import (
    FloodWaitError,
    MessageNotModifiedError,
)
from telethon.tl.tlobject import TLObject


# ============================================================
# TELEGRAM RAW INPUT MEDIA DOCUMENT WITH VIDEO COVER
# ============================================================

class RawInputMediaDocumentWithCover(TLObject):
    """
    Telethon compatibility layer for InputMediaDocument with
    video_cover support.

    The normal Telethon InputMediaDocument constructor in the
    targeted Telethon version does not expose video_cover, so
    this class serializes the required Telegram constructor
    manually.
    """

    CONSTRUCTOR_ID = 0xA8763AB5
    SUBCLASS_OF_ID = 0xFAF846F4

    def __init__(
        self,
        id,
        video_cover=None,
        spoiler=None,
        ttl_seconds=None,
        query=None,
        video_timestamp=None,
    ):
        self.id = id
        self.video_cover = video_cover
        self.spoiler = spoiler
        self.ttl_seconds = ttl_seconds
        self.query = query
        self.video_timestamp = video_timestamp

    def to_dict(self):
        return {
            "_": "InputMediaDocument",
            "id": (
                self.id.to_dict()
                if isinstance(self.id, TLObject)
                else self.id
            ),
            "video_cover": (
                self.video_cover.to_dict()
                if isinstance(self.video_cover, TLObject)
                else self.video_cover
            ),
            "spoiler": self.spoiler,
            "ttl_seconds": self.ttl_seconds,
            "query": self.query,
            "video_timestamp": self.video_timestamp,
        }

    def _bytes(self):
        flags = (
            (0 if self.ttl_seconds is None else 1)
            | (0 if self.query is None else 2)
            | (
                0
                if self.spoiler is None or self.spoiler is False
                else 4
            )
            | (0 if self.video_cover is None else 8)
            | (0 if self.video_timestamp is None else 16)
        )

        return b"".join(
            (
                struct.pack("<I", self.CONSTRUCTOR_ID),
                struct.pack("<I", flags),
                self.id._bytes(),

                (
                    b""
                    if self.video_cover is None
                    else self.video_cover._bytes()
                ),

                (
                    b""
                    if self.video_timestamp is None
                    else struct.pack(
                        "<i",
                        self.video_timestamp,
                    )
                ),

                (
                    b""
                    if self.ttl_seconds is None
                    else struct.pack(
                        "<i",
                        self.ttl_seconds,
                    )
                ),

                (
                    b""
                    if self.query is None
                    else self.serialize_bytes(
                        self.query
                    )
                ),
            )
        )


# ============================================================
# THUMBNAIL MANAGER
# ============================================================

class ThumbnailManager:
    """
    Complete isolated /thumb workflow.

    Dependencies are injected by replace.py.

    Required callbacks:

        client
            Existing authenticated Telethon client.

        is_authorized(user_id)
            Existing authorization check.

        build_caption_metadata(user_id, caption)
            Existing caption parser.

        build_message_link(channel, message_id)
            Existing Telegram clickable-message-link builder.
    """

    def __init__(
        self,
        client,
        is_authorized,
        build_caption_metadata,
        build_message_link,
    ):
        self.client = client
        self.is_authorized = is_authorized
        self.build_caption_metadata = build_caption_metadata
        self.build_message_link = build_message_link

        self.thumb_session = self._new_session()

        self._handlers_registered = False

    # ========================================================
    # SESSION
    # ========================================================

    @staticmethod
    def _new_session():
        return {
            "active": False,
            "user_id": None,
            "starting_bolum": None,

            "thumbnail_channel": None,
            "thumbnail_start_id": None,
            "thumbnail_end_id": None,

            "video_channel": None,
            "video_start_id": None,
            "video_end_id": None,

            "waiting_for": None,

            "thumbnail_items": [],
            "single_thumbnail": None,  # For /thumball mode

            "preview_items": [],
            "preview_stats": {},

            "thumbnail_cache": {},
            
            "mode": "multi",  # "multi" for /thumb, "single" for /thumball
        }

    def clear_thumb_session(self):
        """
        Reset ONLY the /thumb workflow.
        """
        self.thumb_session = self._new_session()

    # ========================================================
    # TELEGRAM LINK PARSING
    # ========================================================

    @staticmethod
    def parse_telegram_message_link(link):
        """
        Supported:

            https://t.me/channel/123
            https://t.me/c/1234567890/123

        Also accepts links without https://.
        Query strings and fragments are ignored.
        """

        if not link:
            return None

        link = link.strip()

        link = link.split("?", 1)[0]
        link = link.split("#", 1)[0]

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
                "internal_id": int(match.group(1)),
                "message_id": int(match.group(2)),
            }

        # ----------------------------------------------------
        # PUBLIC CHANNEL LINK
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
                "message_id": int(match.group(2)),
            }

        return None

    async def resolve_channel(self, parsed):
        """
        Resolve a Telegram message link to its actual entity.
        """

        if not parsed:
            raise ValueError(
                "Invalid Telegram message link."
            )

        # ----------------------------------------------------
        # PUBLIC CHANNEL
        # ----------------------------------------------------

        if parsed["type"] == "username":
            return await self.client.get_entity(
                parsed["username"]
            )

        # ----------------------------------------------------
        # PRIVATE /c/ CHANNEL
        # ----------------------------------------------------

        if parsed["type"] == "private":
            internal_id = parsed["internal_id"]

            async for dialog in self.client.iter_dialogs():
                entity = dialog.entity

                if isinstance(entity, types.Channel):
                    if entity.id == internal_id:
                        return entity

            raise ValueError(
                "Could not find channel with internal ID "
                f"`{internal_id}` in your Telegram dialogs.\n\n"
                "Make sure the userbot account is a member "
                "of that channel."
            )

        raise ValueError(
            "Unsupported Telegram link type."
        )

    @staticmethod
    def get_entity_peer_id(entity):
        """
        Return a comparable Telegram peer ID.
        """

        if isinstance(entity, types.Channel):
            return -1000000000000 - entity.id

        if isinstance(entity, types.Chat):
            return -entity.id

        if isinstance(entity, types.User):
            return entity.id

        return None

    # ========================================================
    # COVER PREPARATION
    # ========================================================

    async def upload_cover_as_input_photo(
        self,
        cover_message,
    ):
        """
        Prepare an existing Telegram photo as InputPhoto.

        The video itself is NEVER downloaded.

        Only the source thumbnail/photo is downloaded,
        converted to JPEG and uploaded once so Telegram can
        provide an InputPhoto reference for video_cover.
        """

        from PIL import Image

        cover_buffer = io.BytesIO()

        await self.client.download_media(
            cover_message,
            file=cover_buffer,
        )

        cover_bytes = cover_buffer.getvalue()

        if not cover_bytes:
            raise ValueError(
                "Could not download the selected cover image."
            )

        try:
            image = Image.open(
                io.BytesIO(cover_bytes)
            )
        except Exception as e:
            raise ValueError(
                f"Could not open cover image: {e}"
            )

        if image.mode != "RGB":
            image = image.convert("RGB")

        jpeg_buffer = io.BytesIO()

        image.save(
            jpeg_buffer,
            format="JPEG",
            quality=100,
            subsampling=0,
            optimize=True,
        )

        jpeg_buffer.seek(0)

        uploaded_file = await self.client.upload_file(
            jpeg_buffer,
            file_name="video_cover.jpg",
        )

        result = await self.client(
            functions.messages.UploadMediaRequest(
                peer=types.InputPeerSelf(),
                media=types.InputMediaUploadedPhoto(
                    file=uploaded_file
                ),
            )
        )

        if not result.photo:
            raise ValueError(
                "Telegram did not return a photo after "
                "uploading the cover."
            )

        photo = result.photo

        return types.InputPhoto(
            id=photo.id,
            access_hash=photo.access_hash,
            file_reference=photo.file_reference,
        )

    async def change_existing_video_cover(
        self,
        channel,
        message_id,
        input_photo,
    ):
        """
        Change ONLY the custom cover of an existing Telegram
        video.

        No video download.
        No video upload.
        No new message.
        No caption modification.
        """

        try:
            message = await self.client.get_messages(
                channel,
                ids=message_id,
            )

            if not message:
                return False, "Message not found"

            if not message.media:
                return False, "Message has no media"

            document = getattr(
                message.media,
                "document",
                None,
            )

            if not document:
                return (
                    False,
                    "Message does not contain a document",
                )

            input_document = types.InputDocument(
                id=document.id,
                access_hash=document.access_hash,
                file_reference=document.file_reference,
            )

            input_media = RawInputMediaDocumentWithCover(
                id=input_document,
                video_cover=input_photo,
            )

            peer = await self.client.get_input_entity(
                channel
            )

            await self.client(
                functions.messages.EditMessageRequest(
                    peer=peer,
                    id=message_id,
                    media=input_media,
                )
            )

            return True, None

        except MessageNotModifiedError:
            return False, "Message not modified"

        except FloodWaitError:
            raise

        except Exception as e:
            return False, str(e)

    # ========================================================
    # THUMBNAIL RANGE SCANNING
    # ========================================================

    async def scan_thumb_range(
        self,
        channel,
        start_id,
        end_id,
    ):
        """
        Scan ONLY the requested message range.

        Only actual photo messages count as thumbnails.
        """

        photo_messages = []

        batch_size = 100

        for batch_start in range(
            start_id,
            end_id + 1,
            batch_size,
        ):
            batch_end = min(
                batch_start + batch_size - 1,
                end_id,
            )

            message_ids = list(
                range(
                    batch_start,
                    batch_end + 1,
                )
            )

            try:
                messages = await self.client.get_messages(
                    channel,
                    ids=message_ids,
                )

            except FloodWaitError:
                raise

            except Exception:
                continue

            if not isinstance(messages, list):
                messages = [messages]

            for message in messages:
                if (
                    message
                    and getattr(
                        message,
                        "photo",
                        None,
                    )
                ):
                    photo_messages.append(message)

        photo_messages.sort(
            key=lambda message: message.id
        )

        thumbnail_items = []

        for index, message in enumerate(
            photo_messages,
            start=1,
        ):
            thumbnail_items.append(
                {
                    "index": index,
                    "message_id": message.id,
                    "link": self.build_message_link(
                        channel,
                        message.id,
                    ),
                }
            )

        return thumbnail_items

    # ========================================================
    # VIDEO PREVIEW SCANNING
    # ========================================================

    async def collect_thumb_preview(
        self,
        user_id,
        thumbnail_items,
        starting_bolum,
        video_channel,
        video_start_id,
        video_end_id,
        mode="multi",
        single_thumbnail=None,
    ):
        """
        Read the requested video range and build the exact
        mapping that will later be used by /thumbconfirm.

        Nothing is edited or uploaded here.
        
        mode: "multi" = one thumbnail per episode
              "single" = one thumbnail for all episodes
        """

        stats = {
            "total_messages": 0,
            "video_count": 0,
            "skipped_count": 0,
            "failed_count": 0,
            "failed_messages": [],
            "episode_count": 0,
        }

        thumbnail_by_index = {
            item["index"]: item
            for item in thumbnail_items
        }

        groups = {}

        batch_size = 100

        for batch_start in range(
            video_start_id,
            video_end_id + 1,
            batch_size,
        ):
            batch_end = min(
                batch_start + batch_size - 1,
                video_end_id,
            )

            message_ids = list(
                range(
                    batch_start,
                    batch_end + 1,
                )
            )

            try:
                messages = await self.client.get_messages(
                    video_channel,
                    ids=message_ids,
                )

            except FloodWaitError:
                raise

            except Exception as e:
                stats["failed_count"] += len(
                    message_ids
                )

                stats["failed_messages"].append(
                    (
                        f"{batch_start}-{batch_end}",
                        str(e),
                    )
                )

                continue

            if not isinstance(messages, list):
                messages = [messages]

            for message in messages:

                if not message:
                    stats["skipped_count"] += 1
                    continue

                stats["total_messages"] += 1

                message_id = message.id

                # ------------------------------------------------
                # VIDEO CHECK
                # ------------------------------------------------

                is_video = bool(
                    getattr(
                        message,
                        "video",
                        None,
                    )
                )

                if not is_video:
                    document = getattr(
                        message,
                        "document",
                        None,
                    )

                    mime_type = (
                        (
                            getattr(
                                document,
                                "mime_type",
                                "",
                            )
                            or ""
                        )
                        if document
                        else ""
                    )

                    is_video = mime_type.startswith(
                        "video/"
                    )

                if not is_video:
                    stats["skipped_count"] += 1
                    continue

                stats["video_count"] += 1

                # ------------------------------------------------
                # EXISTING CAPTION PARSER
                # ------------------------------------------------

                caption = (
                    message.raw_text or ""
                ).strip()

                if not caption:
                    stats["failed_count"] += 1

                    stats["failed_messages"].append(
                        (
                            message_id,
                            "No caption",
                        )
                    )

                    continue

                metadata, global_episode = (
                    self.build_caption_metadata(
                        user_id,
                        caption,
                    )
                )

                if global_episode is None:
                    stats["failed_count"] += 1

                    stats["failed_messages"].append(
                        (
                            message_id,
                            "Bölüm not detected in caption",
                        )
                    )

                    continue

                # ------------------------------------------------
                # THUMBNAIL MAPPING
                # ------------------------------------------------
                
                if mode == "single":
                    # Single mode: use the same thumbnail for all episodes
                    if single_thumbnail is None:
                        stats["failed_count"] += 1
                        stats["failed_messages"].append(
                            (
                                message_id,
                                "No single thumbnail configured",
                            )
                        )
                        continue
                    
                    thumbnail_index = 1
                    thumbnail_item = single_thumbnail
                    
                else:
                    # Multi mode: one thumbnail per episode
                    thumbnail_index = (
                        global_episode
                        - starting_bolum
                        + 1
                    )

                    thumbnail_item = (
                        thumbnail_by_index.get(
                            thumbnail_index
                        )
                    )

                    if (
                        thumbnail_index < 1
                        or not thumbnail_item
                    ):
                        stats["failed_count"] += 1

                        stats["failed_messages"].append(
                            (
                                message_id,
                                (
                                    "No thumbnail mapped for "
                                    f"Bölüm {global_episode}"
                                ),
                            )
                        )

                        continue

                # ------------------------------------------------
                # SEASON / EPISODE
                # ------------------------------------------------

                season = metadata.get(
                    "season"
                )

                season_episode = metadata.get(
                    "season_episode"
                )

                display_episode = (
                    season_episode
                    if season_episode is not None
                    else metadata.get(
                        "episode"
                    )
                )

                if season is None:
                    season = "?"

                if display_episode is None:
                    display_episode = "?"

                # ------------------------------------------------
                # QUALITY
                # ------------------------------------------------

                quality = (
                    metadata.get("quality")
                    or "Unknown"
                )

                quality = str(quality)

                # ------------------------------------------------
                # MESSAGE LINK
                # ------------------------------------------------

                link = self.build_message_link(
                    video_channel,
                    message_id,
                )

                # ------------------------------------------------
                # EPISODE GROUP
                # ------------------------------------------------

                group = groups.get(
                    global_episode
                )

                if group is None:
                    group = {
                        "bolum": global_episode,
                        "season": season,
                        "episode": display_episode,
                        "thumbnail_index": thumbnail_index,
                        "thumbnail_message_id": (
                            thumbnail_item[
                                "message_id"
                            ]
                        ),
                        "thumbnail_link": (
                            thumbnail_item[
                                "link"
                            ]
                        ),
                        "qualities": [],
                    }

                    groups[
                        global_episode
                    ] = group

                group[
                    "qualities"
                ].append(
                    {
                        "message_id": message_id,
                        "quality": quality,
                        "link": link,
                    }
                )

        stats["episode_count"] = len(
            groups
        )

        return groups, stats

    # ========================================================
    # PREVIEW BUILDER
    # ========================================================

    def build_thumb_preview_message(
        self,
        thumbnail_start_id,
        thumbnail_end_id,
        video_start_id,
        video_end_id,
        sorted_groups,
        stats,
        unique_thumbnail_count,
        mode="multi",
    ):
        def esc(value):
            return escape(
                str(value)
            )

        def quality_value(value):
            match = re.search(
                r"\d+",
                str(value),
            )

            if match:
                return int(
                    match.group()
                )

            return 999999

        lines = [
            "📝 <b>THUMBNAIL UPDATE PREVIEW</b>",
            (
                "<code>"
                "━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
                "</code>"
            ),
            "",
        ]
        
        # Show mode
        if mode == "single":
            lines.append(
                "🔄 <b>Mode:</b> "
                "<code>SINGLE THUMBNAIL FOR ALL EPISODES</code>"
            )
            lines.append("")
        
        no_season_yet = object()
        current_season = no_season_yet

        for group in sorted_groups:

            season = group["season"]

            if season != current_season:

                if (
                    current_season
                    is not no_season_yet
                ):
                    lines.append("")

                lines.append(
                    f"🎬 <b>SEASON "
                    f"{esc(season)}</b>"
                )

                lines.append("")

                current_season = season

            # ------------------------------------------------
            # EPISODE DISPLAY
            # ------------------------------------------------

            try:
                episode_display = (
                    f"{int(group['episode']):02d}"
                )

            except (
                TypeError,
                ValueError,
            ):
                episode_display = str(
                    group["episode"]
                )

            lines.append(
                f"<b>Episode "
                f"{esc(episode_display)}</b>"
                f" | <b>Bölüm "
                f"{esc(group['bolum'])}</b>"
            )

            # ------------------------------------------------
            # THUMBNAIL
            # ------------------------------------------------

            if mode == "single":
                thumb_label = "🖼️ SINGLE THUMBNAIL (ALL EPISODES)"
            else:
                thumb_label = (
                    f"Thumbnail "
                    f"{group['thumbnail_index']:02d}"
                )

            thumb_link = group.get(
                "thumbnail_link"
            )

            if thumb_link:
                lines.append(
                    f'<a href="{esc(thumb_link)}">'
                    f"└─ 🖼️ "
                    f"{esc(thumb_label)}"
                    f"</a>"
                )
            else:
                lines.append(
                    f"└─ 🖼️ <code>"
                    f"{esc(thumb_label)}"
                    f"</code>"
                )

            lines.append("")

            # ------------------------------------------------
            # QUALITIES
            # ------------------------------------------------

            qualities = sorted(
                group["qualities"],
                key=lambda item: quality_value(
                    item["quality"]
                ),
            )

            for index, item in enumerate(
                qualities
            ):
                is_last = (
                    index
                    == len(qualities) - 1
                )

                branch = (
                    "└─"
                    if is_last
                    else "├─"
                )

                quality_esc = esc(
                    item["quality"]
                )

                link = item.get(
                    "link"
                )

                if link:
                    lines.append(
                        f'   {branch} '
                        f'<a href="{esc(link)}">'
                        f"{quality_esc}"
                        f"</a>"
                    )
                else:
                    lines.append(
                        f"   {branch} "
                        f"<code>"
                        f"{quality_esc}"
                        f"</code>"
                    )

            lines.append("")

        # ====================================================
        # NO MAPPINGS
        # ====================================================

        if not sorted_groups:
            lines.extend(
                [
                    (
                        "⚠️ <i>No videos could be "
                        "mapped to a thumbnail "
                        "in this range.</i>"
                    ),
                    "",
                ]
            )

        total_videos = sum(
            len(group["qualities"])
            for group in sorted_groups
        )

        # ====================================================
        # SUMMARY
        # ====================================================

        lines.extend(
            [
                (
                    "<code>"
                    "━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
                    "</code>"
                ),
                "",
                "📊 <b>SUMMARY</b>",
                "",
                (
                    f"📌 Thumbnail range: "
                    f"<code>"
                    f"{esc(thumbnail_start_id)}"
                    f"</code>"
                    f" → "
                    f"<code>"
                    f"{esc(thumbnail_end_id)}"
                    f"</code>"
                ),
                (
                    f"📌 Video range: "
                    f"<code>"
                    f"{esc(video_start_id)}"
                    f"</code>"
                    f" → "
                    f"<code>"
                    f"{esc(video_end_id)}"
                    f"</code>"
                ),
                "",
                (
                    f"🖼️ Thumbnails: "
                    f"<code>"
                    f"{unique_thumbnail_count}"
                    f"</code>"
                ),
                (
                    f"🎬 Episodes: "
                    f"<code>"
                    f"{stats['episode_count']}"
                    f"</code>"
                ),
                (
                    f"📦 Total videos: "
                    f"<code>"
                    f"{total_videos}"
                    f"</code>"
                ),
            ]
        )

        if stats["skipped_count"]:
            lines.append(
                f"⏭️ Skipped: "
                f"<code>"
                f"{stats['skipped_count']}"
                f"</code>"
            )

        if stats["failed_count"]:
            lines.append(
                f"⚠️ Failed: "
                f"<code>"
                f"{stats['failed_count']}"
                f"</code>"
            )

        lines.extend(
            [
                "",
                (
                    "<code>"
                    "━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
                    "</code>"
                ),
                "",
                "⚠️ <b>This is a preview only.</b>",
                (
                    "<i>No thumbnails have been "
                    "changed yet.</i>"
                ),
                "",
                (
                    "✅ Use "
                    "<code>/thumbconfirm</code> "
                    "to apply."
                ),
                (
                    "❌ Use "
                    "<code>/thumbcancel</code> "
                    "to abort."
                ),
            ]
        )

        return "\n".join(lines)

    async def send_thumb_preview(
        self,
        event,
        status_msg,
    ):
        sorted_groups = sorted(
            self.thumb_session[
                "preview_items"
            ],
            key=lambda group: group[
                "bolum"
            ],
        )

        stats = self.thumb_session[
            "preview_stats"
        ]

        unique_thumbnail_count = len(
            {
                group[
                    "thumbnail_message_id"
                ]
                for group in sorted_groups
            }
        )
        
        mode = self.thumb_session.get(
            "mode", "multi"
        )

        preview = (
            self.build_thumb_preview_message(
                thumbnail_start_id=(
                    self.thumb_session[
                        "thumbnail_start_id"
                    ]
                ),
                thumbnail_end_id=(
                    self.thumb_session[
                        "thumbnail_end_id"
                    ]
                ),
                video_start_id=(
                    self.thumb_session[
                        "video_start_id"
                    ]
                ),
                video_end_id=(
                    self.thumb_session[
                        "video_end_id"
                    ]
                ),
                sorted_groups=sorted_groups,
                stats=stats,
                unique_thumbnail_count=(
                    unique_thumbnail_count
                ),
                mode=mode,
            )
        )

        # ====================================================
        # SINGLE MESSAGE
        # ====================================================

        if len(preview) <= 3900:
            await status_msg.edit(
                preview,
                parse_mode="html",
                link_preview=False,
            )

            return

        # ====================================================
        # SPLIT LARGE PREVIEW
        # ====================================================

        chunks = []
        current = ""

        for line in preview.splitlines(
            keepends=True
        ):
            if (
                len(current)
                + len(line)
                > 3800
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

        await status_msg.edit(
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

    async def prepare_thumb_preview(
        self,
        event,
    ):
        status_msg = await event.reply(
            "🔎 <b>Preparing thumbnail preview...</b>\n\n"
            "Reading existing thumbnails and video "
            "captions only.\n"
            "Nothing will be changed yet.",
            parse_mode="html",
        )

        try:
            mode = self.thumb_session.get(
                "mode", "multi"
            )
            
            single_thumbnail = self.thumb_session.get(
                "single_thumbnail"
            )
            
            groups, stats = (
                await self.collect_thumb_preview(
                    user_id=self.thumb_session[
                        "user_id"
                    ],
                    thumbnail_items=self.thumb_session[
                        "thumbnail_items"
                    ],
                    starting_bolum=self.thumb_session[
                        "starting_bolum"
                    ],
                    video_channel=self.thumb_session[
                        "video_channel"
                    ],
                    video_start_id=self.thumb_session[
                        "video_start_id"
                    ],
                    video_end_id=self.thumb_session[
                        "video_end_id"
                    ],
                    mode=mode,
                    single_thumbnail=single_thumbnail,
                )
            )

            self.thumb_session[
                "preview_items"
            ] = list(
                groups.values()
            )

            self.thumb_session[
                "preview_stats"
            ] = stats

            await self.send_thumb_preview(
                event,
                status_msg,
            )

            self.thumb_session[
                "waiting_for"
            ] = "confirm"

        except FloodWaitError as e:
            self.clear_thumb_session()

            await status_msg.edit(
                "⏳ <b>Telegram FloodWait</b>\n\n"
                f"Please wait "
                f"<code>{e.seconds}</code> "
                "seconds and start the /thumb operation "
                "again.",
                parse_mode="html",
            )

        except Exception as e:
            self.clear_thumb_session()

            await status_msg.edit(
                "❌ <b>Could not prepare "
                "thumbnail preview.</b>\n\n"
                f"<code>{escape(str(e)[:1200])}</code>",
                parse_mode="html",
            )

    # ========================================================
    # /THUMB
    # ========================================================

    async def thumb_command_handler(
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

        try:
            starting_bolum = int(
                event.pattern_match.group(1)
            )

        except (
            TypeError,
            ValueError,
        ):
            await event.reply(
                "❌ Usage: "
                "`/thumb <starting_global_bolum>`\n"
                "Example: `/thumb 50`"
            )

            return

        if starting_bolum <= 0:
            await event.reply(
                "❌ The starting Bölüm "
                "must be a positive number."
            )

            return

        self.clear_thumb_session()

        self.thumb_session[
            "active"
        ] = True

        self.thumb_session[
            "user_id"
        ] = event.sender_id

        self.thumb_session[
            "starting_bolum"
        ] = starting_bolum
        
        self.thumb_session[
            "mode"
        ] = "multi"

        self.thumb_session[
            "waiting_for"
        ] = "thumbnail_start"

        await event.reply(
            "✅ <b>Starting Bölüm saved:</b> "
            f"<code>{starting_bolum}</code>\n\n"
            "📌 Now send the "
            "<b>START thumbnail message link</b>.\n\n"
            "Example:\n"
            "<code>https://t.me/YourChannel/6703</code>\n\n"
            "Use <code>/thumbcancel</code> to abort.\n\n"
            "💡 For a single thumbnail for ALL episodes, "
            "use <code>/thumball</code> instead.",
            parse_mode="html",
        )

    async def thumb_usage_handler(
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

        await event.reply(
            "❌ Usage: "
            "`/thumb <starting_global_bolum>`\n"
            "Example: `/thumb 50`\n\n"
            "💡 For a single thumbnail for ALL episodes, "
            "use <code>/thumball</code> instead."
        )

    # ========================================================
    # /THUMBALL
    # ========================================================

    async def thumball_command_handler(
        self,
        event,
    ):
        """
        Apply ONE thumbnail to ALL videos in the range.
        
        Usage:
            /thumball <starting_global_bolum>
            
        The workflow is simplified:
            1. Provide the starting Bölüm
            2. Send ONE thumbnail message (just the link)
            3. Send START and END video links
        """
        if not self.is_authorized(
            event.sender_id
        ):
            await event.reply(
                "❌ Unauthorized"
            )
            return

        try:
            starting_bolum = int(
                event.pattern_match.group(1)
            )

        except (
            TypeError,
            ValueError,
        ):
            await event.reply(
                "❌ Usage: "
                "`/thumball <starting_global_bolum>`\n"
                "Example: `/thumball 50`\n\n"
                "This will apply ONE thumbnail to ALL videos "
                "in the range."
            )

            return

        if starting_bolum <= 0:
            await event.reply(
                "❌ The starting Bölüm "
                "must be a positive number."
            )

            return

        self.clear_thumb_session()

        self.thumb_session[
            "active"
        ] = True

        self.thumb_session[
            "user_id"
        ] = event.sender_id

        self.thumb_session[
            "starting_bolum"
        ] = starting_bolum
        
        self.thumb_session[
            "mode"
        ] = "single"

        self.thumb_session[
            "waiting_for"
        ] = "single_thumbnail"

        await event.reply(
            "✅ <b>Starting Bölüm saved:</b> "
            f"<code>{starting_bolum}</code>\n\n"
            "🔄 <b>Mode:</b> "
            "<code>SINGLE THUMBNAIL FOR ALL EPISODES</code>\n\n"
            "📌 Now send the <b>thumbnail message link</b>.\n\n"
            "Send the link to the ONE thumbnail you want to apply "
            "to ALL videos in the range.\n\n"
            "Example:\n"
            "<code>https://t.me/YourChannel/6703</code>\n\n"
            "Use <code>/thumbcancel</code> to abort.",
            parse_mode="html",
        )

    async def thumball_usage_handler(
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

        await event.reply(
            "❌ Usage: "
            "`/thumball <starting_global_bolum>`\n"
            "Example: `/thumball 50`\n\n"
            "This will apply ONE thumbnail to ALL videos "
            "in the range.\n\n"
            "Workflow:\n"
            "1. Send the thumbnail message link\n"
            "2. Send START video message link\n"
            "3. Send END video message link\n"
            "4. Review the preview\n"
            "5. Confirm with /thumbconfirm"
        )

    # ========================================================
    # SINGLE THUMBNAIL HANDLER
    # ========================================================

    async def single_thumbnail_link_handler(
        self,
        event,
    ):
        """
        Handle the single thumbnail link for /thumball.
        """
        if not self.is_authorized(
            event.sender_id
        ):
            return

        session = self.thumb_session

        if not session["active"]:
            return

        if (
            session["user_id"]
            != event.sender_id
        ):
            return

        # ONLY process when waiting for single thumbnail
        if session["waiting_for"] != "single_thumbnail":
            return

        link = (
            event.raw_text or ""
        ).strip()

        parsed = (
            self.parse_telegram_message_link(
                link
            )
        )

        if not parsed:
            await event.reply(
                "❌ <b>Invalid Telegram message link.</b>\n\n"
                "Please send a valid message link such as:\n"
                "<code>https://t.me/YourChannel/6703</code>",
                parse_mode="html",
            )

            return

        try:
            entity = await self.resolve_channel(
                parsed
            )

            # Get the thumbnail message
            thumbnail_message = await self.client.get_messages(
                entity,
                ids=parsed["message_id"],
            )

            if not thumbnail_message:
                await event.reply(
                    "❌ <b>Thumbnail message not found.</b>\n\n"
                    "Please send a valid message link.",
                    parse_mode="html",
                )
                return

            # Check if it's a photo
            if not getattr(thumbnail_message, "photo", None):
                await event.reply(
                    "❌ <b>The linked message is not a photo.</b>\n\n"
                    "Please send a link to a photo message.",
                    parse_mode="html",
                )
                return

            # Store the single thumbnail
            session["thumbnail_channel"] = entity
            session["thumbnail_start_id"] = parsed["message_id"]
            session["thumbnail_end_id"] = parsed["message_id"]
            
            session["thumbnail_items"] = [{
                "index": 1,
                "message_id": parsed["message_id"],
                "link": self.build_message_link(
                    entity,
                    parsed["message_id"],
                ),
            }]
            
            session["single_thumbnail"] = session["thumbnail_items"][0]

            # Move to video start
            session["waiting_for"] = "video_start"

            await event.reply(
                "✅ <b>Single thumbnail saved.</b>\n\n"
                f"📌 Thumbnail ID: <code>{parsed['message_id']}</code>\n"
                f"📢 Channel: "
                f"<code>"
                f"{escape(str(getattr(entity, 'title', 'Unknown')))}"
                f"</code>\n\n"
                "📌 Now send the "
                "<b>START video message link</b>.\n\n"
                "Use <code>/thumbcancel</code> to abort.",
                parse_mode="html",
            )
            
            # IMPORTANT: Return True to stop event propagation
            return True

        except Exception as e:
            await event.reply(
                "❌ <b>Could not resolve the thumbnail link.</b>\n\n"
                f"<code>"
                f"{escape(str(e)[:1000])}"
                f"</code>",
                parse_mode="html",
            )
            return True

    # ========================================================
    # LINK HANDLER (for thumbnails and videos)
    # ========================================================

    async def thumb_link_handler(
        self,
        event,
    ):
        if not self.is_authorized(
            event.sender_id
        ):
            return

        session = self.thumb_session

        if not session["active"]:
            return

        if (
            session["user_id"]
            != event.sender_id
        ):
            return

        # Skip if we're waiting for a single thumbnail - that's handled separately
        if session["waiting_for"] == "single_thumbnail":
            return

        if session["waiting_for"] not in (
            "thumbnail_start",
            "thumbnail_end",
            "video_start",
            "video_end",
        ):
            return

        link = (
            event.raw_text or ""
        ).strip()

        parsed = (
            self.parse_telegram_message_link(
                link
            )
        )

        if not parsed:
            await event.reply(
                "❌ <b>Invalid Telegram message link.</b>\n\n"
                "Please send a valid message link such as:\n"
                "<code>https://t.me/YourChannel/6703</code>",
                parse_mode="html",
            )

            return

        stage = session[
            "waiting_for"
        ]

        # ====================================================
        # THUMBNAIL START
        # ====================================================

        if stage == "thumbnail_start":
            try:
                entity = await self.resolve_channel(
                    parsed
                )

                session[
                    "thumbnail_channel"
                ] = entity

                session[
                    "thumbnail_start_id"
                ] = parsed[
                    "message_id"
                ]

                session[
                    "waiting_for"
                ] = "thumbnail_end"

                mode = session.get("mode", "multi")
                
                if mode == "single":
                    mode_text = "SINGLE THUMBNAIL MODE"
                else:
                    mode_text = "MULTI THUMBNAIL MODE"
                
                await event.reply(
                    "✅ <b>Thumbnail START link saved.</b>\n\n"
                    f"📌 Message ID: "
                    f"<code>"
                    f"{parsed['message_id']}"
                    f"</code>\n"
                    f"📢 Channel: "
                    f"<code>"
                    f"{escape(str(getattr(entity, 'title', 'Unknown')))}"
                    f"</code>\n"
                    f"🔄 Mode: "
                    f"<code>{mode_text}</code>\n\n"
                    "📌 Now send the "
                    "<b>END thumbnail message link</b>.\n\n"
                    "Use <code>/thumbcancel</code> to abort.",
                    parse_mode="html",
                )

            except Exception as e:
                await event.reply(
                    "❌ <b>Could not resolve "
                    "the thumbnail START link.</b>\n\n"
                    f"<code>"
                    f"{escape(str(e)[:1000])}"
                    f"</code>",
                    parse_mode="html",
                )

            return

        # ====================================================
        # THUMBNAIL END
        # ====================================================

        if stage == "thumbnail_end":
            try:
                end_entity = (
                    await self.resolve_channel(
                        parsed
                    )
                )

                start_entity = session[
                    "thumbnail_channel"
                ]

                if (
                    self.get_entity_peer_id(
                        start_entity
                    )
                    != self.get_entity_peer_id(
                        end_entity
                    )
                ):
                    await event.reply(
                        "❌ <b>Different channels!</b>\n\n"
                        "The thumbnail START and END links "
                        "must belong to the same channel.\n\n"
                        "The /thumb operation has been cancelled.",
                        parse_mode="html",
                    )

                    self.clear_thumb_session()

                    return

                start_id = session[
                    "thumbnail_start_id"
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

                max_range = 10000

                if (
                    range_end
                    - range_start
                    + 1
                    > max_range
                ):
                    await event.reply(
                        "❌ <b>Range too large.</b>\n\n"
                        f"Maximum allowed range is "
                        f"<code>{max_range}</code> messages.\n"
                        f"Your range contains "
                        f"<code>"
                        f"{range_end - range_start + 1}"
                        f"</code>.",
                        parse_mode="html",
                    )

                    self.clear_thumb_session()

                    return

                session[
                    "thumbnail_start_id"
                ] = range_start

                session[
                    "thumbnail_end_id"
                ] = range_end

                status_msg = await event.reply(
                    "🔎 <b>Scanning thumbnail range...</b>",
                    parse_mode="html",
                )

                thumbnail_items = (
                    await self.scan_thumb_range(
                        start_entity,
                        range_start,
                        range_end,
                    )
                )

                if not thumbnail_items:
                    await status_msg.edit(
                        "❌ <b>No photo messages "
                        "found in that range.</b>\n\n"
                        "The /thumb operation "
                        "has been cancelled.",
                        parse_mode="html",
                    )

                    self.clear_thumb_session()

                    return

                session[
                    "thumbnail_items"
                ] = thumbnail_items

                session[
                    "waiting_for"
                ] = "video_start"

                await status_msg.edit(
                    f"✅ <b>Found "
                    f"{len(thumbnail_items)} "
                    f"thumbnail(s).</b>\n\n"
                    f"Thumbnail 01 → Bölüm "
                    f"{session['starting_bolum']}\n\n"
                    "📌 Now send the "
                    "<b>START video message link</b>.\n\n"
                    "Use <code>/thumbcancel</code> to abort.",
                    parse_mode="html",
                )

            except FloodWaitError:
                raise

            except Exception as e:
                self.clear_thumb_session()

                await event.reply(
                    "❌ <b>Could not resolve "
                    "the thumbnail END link.</b>\n\n"
                    f"<code>"
                    f"{escape(str(e)[:1000])}"
                    f"</code>",
                    parse_mode="html",
                )

            return

        # ====================================================
        # VIDEO START
        # ====================================================

        if stage == "video_start":
            try:
                entity = await self.resolve_channel(
                    parsed
                )

                session[
                    "video_channel"
                ] = entity

                session[
                    "video_start_id"
                ] = parsed[
                    "message_id"
                ]

                session[
                    "waiting_for"
                ] = "video_end"

                await event.reply(
                    "✅ <b>Video START link saved.</b>\n\n"
                    f"📌 Message ID: "
                    f"<code>"
                    f"{parsed['message_id']}"
                    f"</code>\n"
                    f"📢 Channel: "
                    f"<code>"
                    f"{escape(str(getattr(entity, 'title', 'Unknown')))}"
                    f"</code>\n\n"
                    "📌 Now send the "
                    "<b>END video message link</b>.\n\n"
                    "Use <code>/thumbcancel</code> to abort.",
                    parse_mode="html",
                )

            except Exception as e:
                await event.reply(
                    "❌ <b>Could not resolve "
                    "the video START link.</b>\n\n"
                    f"<code>"
                    f"{escape(str(e)[:1000])}"
                    f"</code>",
                    parse_mode="html",
                )

            return

        # ====================================================
        # VIDEO END
        # ====================================================

        if stage == "video_end":
            try:
                end_entity = (
                    await self.resolve_channel(
                        parsed
                    )
                )

                start_entity = session[
                    "video_channel"
                ]

                if (
                    self.get_entity_peer_id(
                        start_entity
                    )
                    != self.get_entity_peer_id(
                        end_entity
                    )
                ):
                    await event.reply(
                        "❌ <b>Different channels!</b>\n\n"
                        "The video START and END links "
                        "must belong to the same channel.\n\n"
                        "The /thumb operation has been cancelled.",
                        parse_mode="html",
                    )

                    self.clear_thumb_session()

                    return

                start_id = session[
                    "video_start_id"
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

                max_range = 10000

                if (
                    range_end
                    - range_start
                    + 1
                    > max_range
                ):
                    await event.reply(
                        "❌ <b>Range too large.</b>\n\n"
                        f"Maximum allowed range is "
                        f"<code>{max_range}</code> messages.\n"
                        f"Your range contains "
                        f"<code>"
                        f"{range_end - range_start + 1}"
                        f"</code>.",
                        parse_mode="html",
                    )

                    self.clear_thumb_session()

                    return

                session[
                    "video_start_id"
                ] = range_start

                session[
                    "video_end_id"
                ] = range_end

                await self.prepare_thumb_preview(
                    event
                )

            except FloodWaitError:
                raise

            except Exception as e:
                self.clear_thumb_session()

                await event.reply(
                    "❌ <b>Could not resolve "
                    "the video END link.</b>\n\n"
                    f"<code>"
                    f"{escape(str(e)[:1000])}"
                    f"</code>",
                    parse_mode="html",
                )

            return

    # ========================================================
    # /THUMBCONFIRM
    # ========================================================

    async def thumbconfirm_command_handler(
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

        session = self.thumb_session

        if (
            not session["active"]
            or session["user_id"]
            != event.sender_id
        ):
            await event.reply(
                "ℹ️ No thumbnail operation is active.\n\n"
                "Start one with "
                "`/thumb <starting_global_bolum>` "
                "or `/thumball <starting_global_bolum>`."
            )

            return

        if session[
            "waiting_for"
        ] != "confirm":
            await event.reply(
                "❌ <b>No preview is ready yet.</b>\n\n"
                "Finish the /thumb setup steps first.",
                parse_mode="html",
            )

            return

        preview_items = (
            session.get(
                "preview_items"
            )
            or []
        )

        if not preview_items:
            await event.reply(
                "❌ <b>Thumbnail preview data "
                "is missing.</b>\n\n"
                "Please restart with "
                "<code>/thumb</code>.",
                parse_mode="html",
            )

            self.clear_thumb_session()

            return

        video_channel = session[
            "video_channel"
        ]

        thumbnail_channel = session[
            "thumbnail_channel"
        ]

        sorted_groups = sorted(
            preview_items,
            key=lambda group: group[
                "bolum"
            ],
        )

        total = sum(
            len(group["qualities"])
            for group in sorted_groups
        )

        mode = session.get("mode", "multi")
        
        if mode == "single":
            status_text = (
                "🔄 <b>Updating thumbnails...</b>\n\n"
                "🔄 <b>Mode:</b> SINGLE THUMBNAIL FOR ALL EPISODES\n\n"
                "The preview was already generated.\n"
                "Applying the confirmed mapping now.\n\n"
                "Videos are not downloaded or re-uploaded."
            )
        else:
            status_text = (
                "🔄 <b>Updating thumbnails...</b>\n\n"
                "The preview was already generated.\n"
                "Applying the confirmed mapping now.\n\n"
                "Videos are not downloaded or re-uploaded."
            )

        status_msg = await event.reply(
            status_text,
            parse_mode="html",
        )

        thumbnail_cache = {}

        processed_count = 0
        updated_count = 0
        skipped_count = 0
        failed_count = 0

        failed_messages = []

        # ====================================================
        # EPISODE GROUPS
        # ====================================================

        for group in sorted_groups:

            thumb_id = group[
                "thumbnail_message_id"
            ]

            # ------------------------------------------------
            # PREPARE UNIQUE THUMBNAIL ONCE
            # ------------------------------------------------

            if thumb_id not in thumbnail_cache:

                try:
                    thumb_message = (
                        await self.client.get_messages(
                            thumbnail_channel,
                            ids=thumb_id,
                        )
                    )

                    if not thumb_message:
                        raise ValueError(
                            "Thumbnail message not found"
                        )

                    thumbnail_cache[
                        thumb_id
                    ] = (
                        await self.upload_cover_as_input_photo(
                            thumb_message
                        )
                    )

                except FloodWaitError as e:

                    await status_msg.edit(
                        "⏳ <b>Telegram FloodWait</b>\n\n"
                        f"Waiting <code>{e.seconds}</code> "
                        "seconds before continuing...",
                        parse_mode="html",
                    )

                    await asyncio.sleep(
                        e.seconds
                    )

                    try:
                        thumb_message = (
                            await self.client.get_messages(
                                thumbnail_channel,
                                ids=thumb_id,
                            )
                        )

                        if not thumb_message:
                            raise ValueError(
                                "Thumbnail message not found"
                            )

                        thumbnail_cache[
                            thumb_id
                        ] = (
                            await self.upload_cover_as_input_photo(
                                thumb_message
                            )
                        )

                    except Exception as retry_error:

                        for item in group[
                            "qualities"
                        ]:
                            processed_count += 1
                            failed_count += 1

                            failed_messages.append(
                                (
                                    item[
                                        "message_id"
                                    ],
                                    (
                                        "Thumbnail prep failed: "
                                        f"{retry_error}"
                                    ),
                                )
                            )

                        continue

                except Exception as e:

                    for item in group[
                        "qualities"
                    ]:
                        processed_count += 1
                        failed_count += 1

                        failed_messages.append(
                            (
                                item[
                                    "message_id"
                                ],
                                (
                                    "Thumbnail prep failed: "
                                    f"{e}"
                                ),
                            )
                        )

                    continue

            input_photo = thumbnail_cache[
                thumb_id
            ]

            # ------------------------------------------------
            # APPLY SAME THUMBNAIL TO ALL QUALITIES
            # ------------------------------------------------

            for item in group[
                "qualities"
            ]:

                processed_count += 1

                message_id = item[
                    "message_id"
                ]

                try:
                    (
                        success,
                        reason,
                    ) = (
                        await self.change_existing_video_cover(
                            channel=video_channel,
                            message_id=message_id,
                            input_photo=input_photo,
                        )
                    )

                    if success:
                        updated_count += 1

                    elif (
                        reason
                        == "Message not modified"
                    ):
                        skipped_count += 1

                    else:
                        failed_count += 1

                        failed_messages.append(
                            (
                                message_id,
                                reason,
                            )
                        )

                except FloodWaitError as e:

                    await status_msg.edit(
                        "⏳ <b>Telegram FloodWait</b>\n\n"
                        f"Waiting <code>{e.seconds}</code> "
                        "seconds before continuing...\n\n"
                        f"📊 Progress: "
                        f"<code>"
                        f"{processed_count}/{total}"
                        f"</code>",
                        parse_mode="html",
                    )

                    await asyncio.sleep(
                        e.seconds
                    )

                    try:
                        (
                            success,
                            reason,
                        ) = (
                            await self.change_existing_video_cover(
                                channel=video_channel,
                                message_id=message_id,
                                input_photo=input_photo,
                            )
                        )

                        if success:
                            updated_count += 1

                        elif (
                            reason
                            == "Message not modified"
                        ):
                            skipped_count += 1

                        else:
                            failed_count += 1

                            failed_messages.append(
                                (
                                    message_id,
                                    reason,
                                )
                            )

                    except Exception as retry_error:

                        failed_count += 1

                        failed_messages.append(
                            (
                                message_id,
                                (
                                    "Retry failed: "
                                    f"{retry_error}"
                                ),
                            )
                        )

                except Exception as e:

                    failed_count += 1

                    failed_messages.append(
                        (
                            message_id,
                            str(e),
                        )
                    )

                # ------------------------------------------------
                # PROGRESS UPDATE
                # ------------------------------------------------

                if (
                    processed_count == 1
                    or processed_count % 10 == 0
                    or processed_count == total
                ):
                    try:
                        await status_msg.edit(
                            "🎨 <b>THUMBNAIL UPDATE "
                            "IN PROGRESS</b>\n"
                            "<code>"
                            "━━━━━━━━━━━━━━━━━━━━"
                            "</code>\n\n"
                            f"📊 Progress: "
                            f"<code>"
                            f"{processed_count}/{total}"
                            f"</code>\n\n"
                            f"✅ Updated: "
                            f"<code>"
                            f"{updated_count}"
                            f"</code>\n"
                            f"⏭️ Skipped: "
                            f"<code>"
                            f"{skipped_count}"
                            f"</code>\n"
                            f"❌ Failed: "
                            f"<code>"
                            f"{failed_count}"
                            f"</code>",
                            parse_mode="html",
                        )
                    except Exception:
                        pass

                await asyncio.sleep(
                    0.35
                )

        # ====================================================
        # COMPLETION
        # ====================================================

        mode_text = "SINGLE THUMBNAIL" if mode == "single" else "MULTI THUMBNAIL"
        
        completion = (
            "✅ <b>THUMBNAIL UPDATE COMPLETED</b>\n"
            "<code>"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
            "</code>\n\n"
            f"🔄 <b>Mode:</b> <code>{mode_text}</code>\n\n"
            f"📦 Processed: "
            f"<code>"
            f"{processed_count}"
            f"</code>\n\n"
            f"✅ Updated: "
            f"<code>"
            f"{updated_count}"
            f"</code>\n"
            f"⏭️ Skipped: "
            f"<code>"
            f"{skipped_count}"
            f"</code>\n"
            f"❌ Failed: "
            f"<code>"
            f"{failed_count}"
            f"</code>\n\n"
            f"🖼️ Unique thumbnails used: "
            f"<code>"
            f"{len(thumbnail_cache)}"
            f"</code>"
        )

        # ====================================================
        # FAILED MESSAGE REPORT
        # ====================================================

        if failed_messages:
            completion += (
                "\n\n❌ <b>Failed messages:</b>\n"
            )

            for (
                msg_id,
                reason,
            ) in failed_messages[:10]:

                safe_reason = (
                    str(reason)
                    .replace("\n", " ")
                    [:150]
                )

                completion += (
                    f"• <code>"
                    f"{msg_id}"
                    f"</code>"
                    f" — "
                    f"{escape(safe_reason)}\n"
                )

            remaining = (
                len(failed_messages)
                - 10
            )

            if remaining > 0:
                completion += (
                    f"• ... and "
                    f"{remaining} more"
                )

        await status_msg.edit(
            completion,
            parse_mode="html",
        )

        self.clear_thumb_session()

    # ========================================================
    # /THUMBCANCEL
    # ========================================================

    async def thumbcancel_command_handler(
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

        session = self.thumb_session

        if (
            not session["active"]
            or session["user_id"]
            != event.sender_id
        ):
            await event.reply(
                "ℹ️ No thumbnail operation is active."
            )

            return

        self.clear_thumb_session()

        await event.reply(
            "❌ Thumbnail operation cancelled.\n\n"
            "No thumbnails were changed."
        )

    # ========================================================
    # HANDLER REGISTRATION
    # ========================================================

    def register_handlers(self):
        """
        Register exactly ONE set of /thumb handlers.

        This function is called by replace.py.

        There are NO global @client.on(...) decorators in
        this module.
        """

        if self._handlers_registered:
            return

        # ----------------------------------------------------
        # /thumb <starting_global_bolum>
        # ----------------------------------------------------

        self.client.add_event_handler(
            self.thumb_command_handler,
            events.NewMessage(
                pattern=r"^/thumb\s+(\d{1,5})$",
                outgoing=True,
            ),
        )

        # ----------------------------------------------------
        # /thumb
        # ----------------------------------------------------

        self.client.add_event_handler(
            self.thumb_usage_handler,
            events.NewMessage(
                pattern=r"^/thumb$",
                outgoing=True,
            ),
        )

        # ----------------------------------------------------
        # /thumball <starting_global_bolum>
        # ----------------------------------------------------

        self.client.add_event_handler(
            self.thumball_command_handler,
            events.NewMessage(
                pattern=r"^/thumball\s+(\d{1,5})$",
                outgoing=True,
            ),
        )

        # ----------------------------------------------------
        # /thumball
        # ----------------------------------------------------

        self.client.add_event_handler(
            self.thumball_usage_handler,
            events.NewMessage(
                pattern=r"^/thumball$",
                outgoing=True,
            ),
        )

        # ----------------------------------------------------
        # SINGLE THUMBNAIL LINK HANDLER
        # ----------------------------------------------------

        self.client.add_event_handler(
            self.single_thumbnail_link_handler,
            events.NewMessage(
                pattern=r"^(?:https?://)?t\.me/.*$",
                outgoing=True,
            ),
        )

        # ----------------------------------------------------
        # TELEGRAM MESSAGE LINKS (for multi mode and videos)
        # ----------------------------------------------------

        self.client.add_event_handler(
            self.thumb_link_handler,
            events.NewMessage(
                pattern=r"^(?:https?://)?t\.me/.*$",
                outgoing=True,
            ),
        )

        # ----------------------------------------------------
        # /thumbconfirm
        # ----------------------------------------------------

        self.client.add_event_handler(
            self.thumbconfirm_command_handler,
            events.NewMessage(
                pattern=r"^/thumbconfirm$",
                outgoing=True,
            ),
        )

        # ----------------------------------------------------
        # /thumbcancel
        # ----------------------------------------------------

        self.client.add_event_handler(
            self.thumbcancel_command_handler,
            events.NewMessage(
                pattern=r"^/thumbcancel$",
                outgoing=True,
            ),
        )

        self._handlers_registered = True


# ============================================================
# PUBLIC MODULE FUNCTION
# ============================================================

def register_thumb_handlers(
    client,
    is_authorized,
    build_caption_metadata,
    build_message_link,
):
    """
    Create and register the single ThumbnailManager instance.

    replace.py should call this exactly once.
    """

    manager = ThumbnailManager(
        client=client,
        is_authorized=is_authorized,
        build_caption_metadata=build_caption_metadata,
        build_message_link=build_message_link,
    )

    manager.register_handlers()

    return manager
    
print("✓ thumbnail_manager.py loaded")