"""
Sort Manager
============

Standalone /sort module for the Telethon userbot.

This module:
- does NOT create its own TelegramClient
- does NOT start its own client
- does NOT own Telegram authorization
- does NOT contain any global @client.on(...) handlers

replace.py provides:
- the authenticated Telethon client
- authorization callback
- existing Telegram message-link builder

This module owns the complete /sort workflow.
"""

import asyncio
import re
from html import escape
from collections import defaultdict

from telethon import events
from telethon.errors import FloodWaitError
from telethon import types

# ============================================================
# SORT MANAGER
# ============================================================

class SortManager:
    """
    Complete isolated /sort workflow.

    Dependencies are injected by replace.py.
    """

    def __init__(
        self,
        client,
        is_authorized,
        build_message_link,
    ):
        self.client = client
        self.is_authorized = is_authorized
        self.build_message_link = build_message_link

        self.sort_session = self._new_session()
        self._handlers_registered = False

    # ========================================================
    # SESSION
    # ========================================================

    @staticmethod
    def _new_session():
        return {
            "active": False,
            "user_id": None,
            "source_channel": None,
            "start_id": None,
            "end_id": None,
            "waiting_for": None,
            "destination": None,
            "items": [],
            "groups": {},
        }

    def clear_sort_session(self):
        self.sort_session = self._new_session()

    # ========================================================
    # TELEGRAM LINK PARSING
    # ========================================================

    @staticmethod
    def parse_telegram_message_link(link):
        """Parse Telegram message links."""
        if not link:
            return None

        link = link.strip()
        link = link.split("?", 1)[0]
        link = link.split("#", 1)[0]

        # Private /c/ link
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

        # Public channel link
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
        """Resolve a Telegram message link to its actual entity."""
        if not parsed:
            raise ValueError("Invalid Telegram message link.")

        if parsed["type"] == "username":
            return await self.client.get_entity(parsed["username"])

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

        raise ValueError("Unsupported Telegram link type.")

    @staticmethod
    def get_entity_peer_id(entity):
        """Return a comparable Telegram peer ID."""
        if isinstance(entity, types.Channel):
            return -1000000000000 - entity.id
        if isinstance(entity, types.Chat):
            return -entity.id
        if isinstance(entity, types.User):
            return entity.id
        return None

    # ========================================================
    # PARSER
    # ========================================================

    @staticmethod
    def parse_video_info(filename):
        """
        Parse episode number and quality from filename.
        
        Supports formats like:
        - Alparslan S01 - E24 - Bölüm 24 - Urdu Subtitles - 1080p - KayiTV
        - Alparslan S01 E24 1080p
        - Alparslan S01E24 1080p
        - Alparslan - E24 - 1080p
        - Episode 24 - 1080p
        - Ep 24 - 1080p
        - Bölüm 24 - 1080p
        - S01E24.1080p
        - S01 E24 1080p
        """
        
        if not filename:
            return None, None
        
        text = filename
        
        # Try to extract episode number from various patterns
        episode = None
        
        # Pattern: S01E24 (no space)
        match = re.search(r'S\d{1,2}E(\d{1,4})', text, re.IGNORECASE)
        if match:
            episode = int(match.group(1))
        
        # Pattern: S01 E24 (with space)
        if episode is None:
            match = re.search(r'S\d{1,2}\s+E(\d{1,4})', text, re.IGNORECASE)
            if match:
                episode = int(match.group(1))
        
        # Pattern: E24 (standalone)
        if episode is None:
            match = re.search(r'\bE(?:p(?:isode)?\.?)?\s*(\d{1,4})\b', text, re.IGNORECASE)
            if match:
                episode = int(match.group(1))
        
        # Pattern: Episode 24
        if episode is None:
            match = re.search(r'\b(?:Episode|Ep)\s+(\d{1,4})\b', text, re.IGNORECASE)
            if match:
                episode = int(match.group(1))
        
        # Pattern: Bölüm 24
        if episode is None:
            match = re.search(r'B[öo]l[üu]m\s+(\d{1,4})', text, re.IGNORECASE)
            if match:
                episode = int(match.group(1))
        
        # Pattern: - E24 - style
        if episode is None:
            match = re.search(r'[-]\s*E(\d{1,4})\s*[-]', text, re.IGNORECASE)
            if match:
                episode = int(match.group(1))
        
        # Try to extract quality
        quality = None
        
        # Pattern: 1080p, 720p, 480p, 360p, 2160p, etc.
        match = re.search(r'\b(\d{3,4}p)\b', text, re.IGNORECASE)
        if match:
            quality = match.group(1).lower()
        
        # Pattern: 2K, 4K
        if quality is None:
            match = re.search(r'\b(2K|4K)\b', text, re.IGNORECASE)
            if match:
                quality = match.group(1).upper()
        
        return episode, quality
    
    @staticmethod
    def quality_score(quality):
        """Convert quality string to numeric score for sorting."""
        if not quality:
            return 0
        
        quality = quality.lower()
        
        # Handle 2K, 4K
        if quality == '2k':
            return 1440
        if quality == '4k':
            return 2160
        
        # Handle 1080p, 720p, etc.
        match = re.search(r'(\d+)p', quality)
        if match:
            return int(match.group(1))
        
        return 0

    # ========================================================
    # MESSAGE SCANNING
    # ========================================================

    async def scan_messages(self, channel, start_id, end_id):
        """Scan messages and extract episode info."""
        items = []
        stats = {
            "total": 0,
            "video_count": 0,
            "failed_count": 0,
            "failed_messages": [],
        }

        batch_size = 100

        for batch_start in range(start_id, end_id + 1, batch_size):
            batch_end = min(batch_start + batch_size - 1, end_id)
            message_ids = list(range(batch_start, batch_end + 1))

            try:
                messages = await self.client.get_messages(
                    channel,
                    ids=message_ids,
                )
            except Exception as e:
                stats["failed_count"] += len(message_ids)
                stats["failed_messages"].append(
                    (f"{batch_start}-{batch_end}", str(e))
                )
                continue

            if not isinstance(messages, list):
                messages = [messages]

            for message in messages:
                if not message:
                    continue

                stats["total"] += 1

                # Check if it's a video
                is_video = bool(getattr(message, "video", None))
                if not is_video:
                    document = getattr(message, "document", None)
                    mime_type = getattr(document, "mime_type", "") if document else ""
                    is_video = mime_type.startswith("video/")

                if not is_video:
                    continue

                stats["video_count"] += 1

                # Get filename or fallback to message ID
                filename = None
                if message.document:
                    filename = getattr(message.document, "file_name", None)
                
                # If no filename, try to get caption
                if not filename:
                    filename = getattr(message, "raw_text", "") or ""
                
                if not filename:
                    # Fallback to message ID
                    filename = f"Video_{message.id}"

                episode, quality = self.parse_video_info(filename)

                if episode is None:
                    stats["failed_count"] += 1
                    stats["failed_messages"].append(
                        (message.id, "Could not parse episode number")
                    )
                    continue

                items.append({
                    "message": message,
                    "message_id": message.id,
                    "filename": filename,
                    "episode": episode,
                    "quality": quality or "Unknown",
                    "link": self.build_message_link(channel, message.id),
                })

        return items, stats

    # ========================================================
    # GROUPING
    # ========================================================

    def group_items(self, items):
        """Group items by episode number."""
        groups = defaultdict(list)
        
        for item in items:
            episode = item["episode"]
            groups[episode].append(item)
        
        # Sort qualities within each episode (low to high)
        for episode in groups:
            groups[episode].sort(
                key=lambda x: self.quality_score(x["quality"])
            )
        
        # Return sorted by episode number
        return dict(sorted(groups.items()))

    # ========================================================
    # PREVIEW BUILDER
    # ========================================================

    def build_preview(self, groups, stats, start_id, end_id):
        """Build preview message."""
        lines = [
            "📝 <b>SORTED VIDEO PREVIEW</b>",
            "<code>━━━━━━━━━━━━━━━━━━━━━━━━━━━━</code>",
            "",
            f"📌 Range: <code>{start_id}</code> → <code>{end_id}</code>",
            "",
        ]

        if not groups:
            lines.append("⚠️ <i>No videos found in this range.</i>")
            lines.append("")
        else:
            for episode, items in groups.items():
                lines.append(f"🎬 <b>Episode {episode:02d}</b>")
                
                for idx, item in enumerate(items, 1):
                    quality = item["quality"]
                    link = item.get("link")
                    
                    if link:
                        lines.append(
                            f'   {idx:02d}. <a href="{link}">{quality}</a>'
                        )
                    else:
                        lines.append(
                            f"   {idx:02d}. <code>{quality}</code>"
                        )
                
                lines.append("")

        lines.extend([
            "<code>━━━━━━━━━━━━━━━━━━━━━━━━━━━━</code>",
            "",
            "📊 <b>SUMMARY</b>",
            "",
            f"🎬 Episodes: <code>{len(groups)}</code>",
            f"📦 Videos: <code>{stats['video_count']}</code>",
        ])

        if stats["failed_count"] > 0:
            lines.append(f"⚠️ Failed: <code>{stats['failed_count']}</code>")

        lines.extend([
            "",
            "⚠️ <b>This is a preview only.</b>",
            "<i>No files have been sent yet.</i>",
            "",
            "✅ Use <code>/sortconfirm</code> to send sorted files.",
            "✅ Use <code>/sortconfirm &lt;destination_id&gt;</code> to send to a specific chat.",
            "❌ Use <code>/sortcancel</code> to abort.",
        ])

        return "\n".join(lines)

    # ========================================================
    # SEND SORTED FILES
    # ========================================================

    async def send_sorted_files(self, event, destination=None):
        """Send videos in sorted order."""
        groups = self.sort_session.get("groups", {})
        
        if not groups:
            await event.reply("❌ No sorted videos to send.")
            return

        # Resolve destination
        if destination is None:
            # Send to current chat
            dest_entity = await self.client.get_entity(event.chat_id)
        else:
            try:
                dest_id = int(destination)
                dest_entity = await self.client.get_entity(dest_id)
            except Exception as e:
                await event.reply(
                    f"❌ Invalid destination ID: {e}\n\n"
                    "Use <code>/sortconfirm</code> for current chat."
                )
                return

        total_videos = sum(len(items) for items in groups.values())
        sent = 0
        failed = 0
        failed_messages = []

        status_msg = await event.reply(
            "🔄 <b>Sending sorted videos...</b>\n\n"
            f"Total: <code>{total_videos}</code>",
            parse_mode="html",
        )

        for episode, items in groups.items():
            # Send a header for the episode
            try:
                await self.client.send_message(
                    dest_entity,
                    f"🎬 <b>Episode {episode:02d}</b>",
                    parse_mode="html",
                )
                await asyncio.sleep(0.35)
            except Exception:
                pass

            for idx, item in enumerate(items, 1):
                try:
                    await self.client.send_message(
                        dest_entity,
                        item["message"],
                    )
                    sent += 1
                    await asyncio.sleep(0.35)
                except FloodWaitError as e:
                    await status_msg.edit(
                        f"⏳ <b>FloodWait</b>\n\n"
                        f"Waiting <code>{e.seconds}</code> seconds..."
                    )
                    await asyncio.sleep(e.seconds)
                    # Retry
                    try:
                        await self.client.send_message(
                            dest_entity,
                            item["message"],
                        )
                        sent += 1
                    except Exception as retry_error:
                        failed += 1
                        failed_messages.append(
                            (item["message_id"], str(retry_error))
                        )
                except Exception as e:
                    failed += 1
                    failed_messages.append(
                        (item["message_id"], str(e))
                    )

                # Update progress every 5 items
                if (sent + failed) % 5 == 0 or (sent + failed) == total_videos:
                    await status_msg.edit(
                        "🔄 <b>Sending sorted videos...</b>\n\n"
                        f"📊 Progress: <code>{sent + failed}/{total_videos}</code>\n"
                        f"✅ Sent: <code>{sent}</code>\n"
                        f"❌ Failed: <code>{failed}</code>",
                        parse_mode="html",
                    )

        # Completion message
        completion = (
            "✅ <b>SORTED VIDEOS SENT</b>\n"
            "<code>━━━━━━━━━━━━━━━━━━━━━━━━━━━━</code>\n\n"
            f"✅ Sent: <code>{sent}</code>\n"
            f"❌ Failed: <code>{failed}</code>\n"
            f"🎬 Episodes: <code>{len(groups)}</code>"
        )

        if failed_messages and len(failed_messages) <= 10:
            completion += "\n\n❌ <b>Failed:</b>\n"
            for msg_id, reason in failed_messages[:5]:
                completion += f"• <code>{msg_id}</code> — {reason[:100]}\n"
            if len(failed_messages) > 5:
                completion += f"• ... and {len(failed_messages) - 5} more"

        await status_msg.edit(completion, parse_mode="html")
        self.clear_sort_session()

    # ========================================================
    # COMMAND HANDLERS
    # ========================================================

    async def sort_command_handler(self, event):
        """Start the /sort workflow."""
        if not self.is_authorized(event.sender_id):
            await event.reply("❌ Unauthorized")
            return

        self.clear_sort_session()
        self.sort_session["active"] = True
        self.sort_session["user_id"] = event.sender_id
        self.sort_session["waiting_for"] = "start"

        await event.reply(
            "📤 <b>START SORT OPERATION</b>\n"
            "<code>━━━━━━━━━━━━━━━━━━━━━━━━━━━━</code>\n\n"
            "📌 Send the <b>START message link</b>.\n\n"
            "Example:\n"
            "<code>https://t.me/YourChannel/1000</code>\n\n"
            "Use <code>/sortcancel</code> to abort."
        )

    async def sort_link_handler(self, event):
        """Handle START and END links."""
        if not self.is_authorized(event.sender_id):
            return

        session = self.sort_session
        if not session["active"]:
            return
        if session["user_id"] != event.sender_id:
            return
        if session["waiting_for"] not in ("start", "end"):
            return

        link = event.raw_text.strip()
        parsed = self.parse_telegram_message_link(link)

        if not parsed:
            await event.reply("❌ Invalid Telegram message link.")
            return

        try:
            entity = await self.resolve_channel(parsed)

            if session["waiting_for"] == "start":
                session["source_channel"] = entity
                session["start_id"] = parsed["message_id"]
                session["waiting_for"] = "end"

                await event.reply(
                    "✅ <b>START link saved.</b>\n\n"
                    f"📌 Message ID: <code>{parsed['message_id']}</code>\n"
                    f"📢 Channel: <code>{getattr(entity, 'title', 'Unknown')}</code>\n\n"
                    "📌 Now send the <b>END message link</b>.\n\n"
                    "Use <code>/sortcancel</code> to abort."
                )
                return

            if session["waiting_for"] == "end":
                start_entity = session["source_channel"]
                
                if self.get_entity_peer_id(start_entity) != self.get_entity_peer_id(entity):
                    await event.reply(
                        "❌ <b>Different channels!</b>\n\n"
                        "START and END links must belong to the same channel."
                    )
                    self.clear_sort_session()
                    return

                start_id = session["start_id"]
                end_id = parsed["message_id"]
                range_start = min(start_id, end_id)
                range_end = max(start_id, end_id)

                max_range = 10000
                if range_end - range_start + 1 > max_range:
                    await event.reply(
                        f"❌ <b>Range too large.</b>\n\n"
                        f"Maximum: <code>{max_range}</code> messages."
                    )
                    self.clear_sort_session()
                    return

                session["start_id"] = range_start
                session["end_id"] = range_end

                status_msg = await event.reply(
                    "🔎 <b>Scanning messages...</b>\n\n"
                    "Reading filenames and captions only.",
                    parse_mode="html",
                )

                try:
                    items, stats = await self.scan_messages(
                        start_entity,
                        range_start,
                        range_end,
                    )

                    if not items:
                        await status_msg.edit(
                            "❌ <b>No videos with parseable episodes found.</b>"
                        )
                        self.clear_sort_session()
                        return

                    groups = self.group_items(items)
                    session["items"] = items
                    session["groups"] = groups

                    preview = self.build_preview(
                        groups,
                        stats,
                        range_start,
                        range_end,
                    )

                    # Split preview if too long
                    if len(preview) <= 3900:
                        await status_msg.edit(preview, parse_mode="html")
                    else:
                        chunks = []
                        current = ""
                        for line in preview.splitlines(keepends=True):
                            if len(current) + len(line) > 3800:
                                if current:
                                    chunks.append(current)
                                current = line
                            else:
                                current += line
                        if current:
                            chunks.append(current)

                        await status_msg.edit(chunks[0], parse_mode="html")
                        for chunk in chunks[1:]:
                            await event.reply(chunk, parse_mode="html")

                    session["waiting_for"] = "confirm"

                except Exception as e:
                    self.clear_sort_session()
                    await status_msg.edit(
                        f"❌ <b>Could not scan messages.</b>\n\n"
                        f"<code>{str(e)[:1000]}</code>"
                    )

        except Exception as e:
            await event.reply(
                f"❌ <b>Could not resolve link.</b>\n\n"
                f"<code>{str(e)[:1000]}</code>"
            )

    async def sortconfirm_command_handler(self, event):
        """Confirm and send sorted videos."""
        if not self.is_authorized(event.sender_id):
            await event.reply("❌ Unauthorized")
            return

        session = self.sort_session
        if not session["active"] or session["user_id"] != event.sender_id:
            await event.reply("ℹ️ No active sort operation.\n\nStart with <code>/sort</code>.")
            return

        if session["waiting_for"] != "confirm":
            await event.reply("❌ Please complete the setup first.")
            return

        # Parse destination ID if provided
        text = event.raw_text.strip()
        parts = text.split()

        destination = None
        if len(parts) > 1:
            destination = parts[1]
            try:
                int(destination)
            except ValueError:
                await event.reply("❌ Destination ID must be numeric.")
                return

        await self.send_sorted_files(event, destination)

    async def sortcancel_command_handler(self, event):
        """Cancel the sort operation."""
        if not self.is_authorized(event.sender_id):
            await event.reply("❌ Unauthorized")
            return

        session = self.sort_session
        if not session["active"] or session["user_id"] != event.sender_id:
            await event.reply("ℹ️ No active sort operation.")
            return

        self.clear_sort_session()
        await event.reply("❌ <b>Sort operation cancelled.</b>")

    # ========================================================
    # HANDLER REGISTRATION
    # ========================================================

    def register_handlers(self):
        """Register all /sort handlers."""
        if self._handlers_registered:
            return

        self.client.add_event_handler(
            self.sort_command_handler,
            events.NewMessage(pattern=r"^/sort$", outgoing=True),
        )

        self.client.add_event_handler(
            self.sort_link_handler,
            events.NewMessage(pattern=r"^(?:https?://)?t\.me/.*$", outgoing=True),
        )

        self.client.add_event_handler(
            self.sortconfirm_command_handler,
            events.NewMessage(pattern=r"^/sortconfirm(?:\s+[-]?\d+)?$", outgoing=True),
        )

        self.client.add_event_handler(
            self.sortcancel_command_handler,
            events.NewMessage(pattern=r"^/sortcancel$", outgoing=True),
        )

        self._handlers_registered = True


# ============================================================
# PUBLIC MODULE FUNCTION
# ============================================================

def register_sort_handlers(
    client,
    is_authorized,
    build_message_link,
):
    """
    Create and register the SortManager instance.
    
    replace.py should call this exactly once.
    """
    manager = SortManager(
        client=client,
        is_authorized=is_authorized,
        build_message_link=build_message_link,
    )
    manager.register_handlers()
    return manager


print("✓ sort_manager.py loaded")