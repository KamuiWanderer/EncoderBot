#!/usr/bin/env python3
"""
Telegram Channel Text Search & Replace Userbot
Hardcoded search and replace - Just run and it works!
"""

import asyncio
from telethon import TelegramClient, events, types, functions
from telethon.errors import FloodWaitError, MessageIdInvalidError, MessageNotModifiedError
import re , io
import sqlite3
import os
from datetime import datetime, timezone
from html import escape
from thumbnail_manager import register_thumb_handlers
from upload_manager import register_upload_handlers
from sort_manager import register_sort_handlers


# ==================== CONFIGURATION ====================
API_ID = 8447214
API_HASH = "9ec5782ddd935f7e2763e5e49a590c0d"
CHANNEL_ID = -1003757954796
OWNER_ID = 986380678

SESSION_NAME = "caption_replace"
# SQLite database used by the /caption feature (PHASE 1).
CAPTION_DB_PATH = "caption_bot.db"

# HARDCODED SEARCH AND REPLACE TEXT
SEARCH_TEXT = "🎬 𝐌𝐞𝐡𝐦𝐞𝐝 𝐅𝐞𝐭𝐢𝐡𝐥𝐞𝐫 𝐒𝐮𝐥𝐭𝐚𝐧𝐢" 
REPLACEMENT_TEXT = '[🎬 𝐌𝐞𝐡𝐦𝐞𝐝 𝐅𝐞𝐭𝐢𝐡𝐥𝐞𝐫 𝐒𝐮𝐥𝐭𝐚𝐧𝐢](https://t.me/MehmedUrduSubs)'

# ==================== SESSION STORAGE ====================
class SessionData:
    def __init__(self):
        self.user_id = None
        self.search_text = SEARCH_TEXT
        self.replacement_text = REPLACEMENT_TEXT
        self.matched_messages = []
        self.is_running = False
    
    def clear(self):
        self.matched_messages = []
        self.is_running = False
    
    def is_authorized(self, user_id):
        return user_id == OWNER_ID

session = SessionData()

client = TelegramClient(SESSION_NAME, API_ID, API_HASH)

# ==================== CAPTION FEATURE SESSION STORAGE (PHASE 1) ====================
# Separate from the text-replacement session and the cover session.
# Tracks the multi-step /caption workflow, per user.
#
# waiting_for can be:
#   "start"          -> waiting for the START message link
#   "end"            -> waiting for the END message link
#   "format_choice"  -> range collected, waiting for /confirm or /setcaption
#   "template"       -> waiting for the next message to be saved as the caption template
#   None             -> idle
caption_session = {
    "active": False,
    "user_id": None,
    "channel": None,
    "start_id": None,
    "end_id": None,
    "waiting_for": None,

    # True when /setcaption was invoked while a /caption
    # range was already pending.
    "resume_after_template": False,

    # Metadata collected during the safe preview stage.
    #
    # Nothing is edited while this data is being generated.
    "preview_items": [],
    "preview_stats": {},
}


# ==================== HELPER FUNCTIONS ====================
def truncate_text(text, max_length=200):
    if not text:
        return "[Empty]"
    if len(text) > max_length:
        return text[:max_length] + "..."
    return text

async def get_channel_messages():
    """Fetch all messages from the channel"""
    messages = []
    try:
        print("📥 Fetching channel messages...")
        async for message in client.iter_messages(CHANNEL_ID, limit=5000):
            if message.text:
                messages.append(message)
        print(f"✅ Fetched {len(messages)} messages with text")
    except Exception as e:
        print(f"❌ Error fetching messages: {e}")
    return messages

async def edit_message_with_replacement(message, search_text, replacement_text):
    """Edit a single message with proper formatting - Safe version"""
    try:
        original_text = message.text
        if not original_text:
            return False, "Empty message"
        
        # Check if search text exists (plain string, not regex)
        if search_text not in original_text:
            return False, "Search text not found"
        
        # Replace only the searched text (simple string replace)
        new_text = original_text.replace(search_text, replacement_text)
        
        # Try Markdown first (works better for user accounts)
        try:
            await client.edit_message(CHANNEL_ID, message.id, new_text, parse_mode='markdown')
            return True, "Success (Markdown)"
        except Exception as md_error:
            # Fallback to HTML if Markdown fails
            try:
                await client.edit_message(CHANNEL_ID, message.id, new_text, parse_mode='html')
                return True, "Success (HTML)"
            except:
                # Last resort: send as plain text
                await client.edit_message(CHANNEL_ID, message.id, new_text, parse_mode=None)
                return True, "Success (Plain text)"
        
    except FloodWaitError as e:
        print(f"⏳ Flood wait: {e.seconds} seconds")
        await asyncio.sleep(e.seconds)
        # Retry once
        try:
            new_text = message.text.replace(search_text, replacement_text)
            await client.edit_message(CHANNEL_ID, message.id, new_text, parse_mode='markdown')
            return True, "Success after flood wait"
        except Exception as retry_error:
            return False, f"Flood wait retry failed: {str(retry_error)}"
    except MessageNotModifiedError:
        return False, "Message content identical"
    except MessageIdInvalidError:
        return False, "Invalid message ID (possibly deleted)"
    except Exception as e:
        return False, str(e)
        
        
# ============================================================
# TELEGRAM MESSAGE LINK HELPERS
# ============================================================

def parse_telegram_message_link(link):
    """
    Parse a Telegram message link.

    Supported formats:

        https://t.me/channel/123
        https://t.me/c/1234567890/123

    Also accepts links without https://.

    Query strings and URL fragments are ignored.
    """

    if not link:
        return None

    link = link.strip()

    # Remove query string and fragment.
    link = link.split("?", 1)[0]
    link = link.split("#", 1)[0]

    # --------------------------------------------------------
    # PRIVATE /c/ LINK
    # --------------------------------------------------------

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

    # --------------------------------------------------------
    # PUBLIC CHANNEL LINK
    # --------------------------------------------------------

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


async def resolve_cover_channel(parsed):
    """
    Resolve a parsed Telegram message link to its Telegram entity.

    Public links are resolved directly by username.

    Private /c/ links are resolved by searching the current
    Telegram account's dialogs for the matching channel ID.
    """

    if not parsed:
        raise ValueError(
            "Invalid Telegram message link."
        )

    # --------------------------------------------------------
    # PUBLIC CHANNEL
    # --------------------------------------------------------

    if parsed["type"] == "username":
        return await client.get_entity(
            parsed["username"]
        )

    # --------------------------------------------------------
    # PRIVATE /c/ CHANNEL
    # --------------------------------------------------------

    if parsed["type"] == "private":
        internal_id = parsed["internal_id"]

        async for dialog in client.iter_dialogs():
            entity = dialog.entity

            if isinstance(
                entity,
                types.Channel,
            ):
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


def get_entity_peer_id(entity):
    """
    Return a comparable Telegram peer ID.

    This allows the /caption workflow to verify that
    START and END links belong to the same chat/channel.
    """

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

# ==================== COMMAND HANDLERS ====================

# ==================== CAPTION FEATURE: SQLITE (PHASE 1) ====================
#
# Two tables:
#   caption_formats  - one saved caption template per user/account
#   season_mappings  - global Bölüm -> season start points, per user/account
#
# The database file is created automatically if it does not exist, so no
# manual setup is required.

def get_caption_db_connection():
    """Open a connection to the caption feature's SQLite database."""
    conn = sqlite3.connect(CAPTION_DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_caption_db():
    """Create the caption feature's tables if they do not already exist."""
    conn = get_caption_db_connection()
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS caption_formats (
                user_id INTEGER PRIMARY KEY,
                format_text TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS season_mappings (
                user_id INTEGER NOT NULL,
                season INTEGER NOT NULL,
                start_global_episode INTEGER NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (user_id, season)
            )
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_season_mappings_user_start
            ON season_mappings (user_id, start_global_episode)
            """
        )
        conn.commit()
    finally:
        conn.close()


def get_saved_caption_format(user_id):
    """Return the saved caption template text for a user, or None."""
    conn = get_caption_db_connection()
    try:
        row = conn.execute(
            "SELECT format_text FROM caption_formats WHERE user_id = ?",
            (user_id,),
        ).fetchone()
        return row["format_text"] if row else None
    finally:
        conn.close()


def save_caption_format(user_id, format_text):
    """Insert or update the saved caption template for a user."""
    conn = get_caption_db_connection()
    try:
        conn.execute(
            """
            INSERT INTO caption_formats (user_id, format_text, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                format_text = excluded.format_text,
                updated_at = excluded.updated_at
            """,
            (user_id, format_text, datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()
    finally:
        conn.close()


def save_season_mapping(user_id, season, start_global_episode):
    """Insert or update a global-Bölüm -> season starting point."""
    conn = get_caption_db_connection()
    try:
        conn.execute(
            """
            INSERT INTO season_mappings (user_id, season, start_global_episode, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(user_id, season) DO UPDATE SET
                start_global_episode = excluded.start_global_episode,
                updated_at = excluded.updated_at
            """,
            (user_id, season, start_global_episode, datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()
    finally:
        conn.close()


def get_season_mappings(user_id):
    """
    Return all season mappings for a user, sorted by start_global_episode.

    Each item: {"season": int, "start_global_episode": int}
    """
    conn = get_caption_db_connection()
    try:
        rows = conn.execute(
            """
            SELECT season, start_global_episode
            FROM season_mappings
            WHERE user_id = ?
            ORDER BY start_global_episode ASC
            """,
            (user_id,),
        ).fetchall()
        return [
            {"season": row["season"], "start_global_episode": row["start_global_episode"]}
            for row in rows
        ]
    finally:
        conn.close()


def resolve_season_for_global_episode(user_id, global_episode):
    """
    Given a GLOBAL Bölüm number, resolve (season, season_episode) using the
    user's saved /setseason mappings.

    The global Bölüm number never resets. A season's starting point only
    tells us where, within the continuous numbering, that season begins.

    Returns (season, season_episode) or (None, None) if no mapping applies
    (i.e. the user never ran /setseason, or the episode is before any
    configured season start).
    """
    mappings = get_season_mappings(user_id)

    applicable = None
    for mapping in mappings:
        if mapping["start_global_episode"] <= global_episode:
            applicable = mapping
        else:
            break

    if not applicable:
        return None, None

    season_episode = global_episode - applicable["start_global_episode"] + 1
    return applicable["season"], season_episode


# ==================== CAPTION FEATURE: SESSION HELPERS (PHASE 1) ====================

def clear_caption_session():
    """Reset the current /caption workflow."""

    caption_session["active"] = False
    caption_session["user_id"] = None
    caption_session["channel"] = None
    caption_session["start_id"] = None
    caption_session["end_id"] = None
    caption_session["waiting_for"] = None
    caption_session["resume_after_template"] = False

    # Preview data must never survive a completed/cancelled operation.
    caption_session["preview_items"] = []
    caption_session["preview_stats"] = {}

def format_saved_caption_prompt(format_text):
    """Build the CASE A message shown when a caption format is already saved."""
    return (
        "📝 **SAVED CAPTION FORMAT**\n\n"
        f"{format_text}\n\n"
        "Use this format:\n"
        " `/confirm`\n\n"
        "Change this format:\n"
        " `/setcaption`"
    )


# ==================== CAPTION FEATURE: CAPTION PARSING (PHASE 1) ====================
#
# CRITICAL RULE: metadata is extracted ONLY from the existing video message's
# CAPTION. The filename is never inspected in Phase 1.

CAPTION_QUALITY_PATTERN = re.compile(r"\b(\d{3,4}p)\b", re.IGNORECASE)

CAPTION_SEASON_PATTERN = re.compile(r"\bS(?:eason)?\.?\s*(\d{1,2})\b", re.IGNORECASE)

# "Bölüm 50", "Bolum 50", "Episode 50", "Ep 50", "Ep.50"
# ==================== CAPTION METADATA PATTERNS ====================

CAPTION_QUALITY_PATTERN = re.compile(
    r"\b(\d{3,4}p)\b",
    re.IGNORECASE
)

CAPTION_SEASON_PATTERN = re.compile(
    r"\bS(?:eason)?\.?\s*(\d{1,2})\b",
    re.IGNORECASE
)

# GLOBAL CONTINUOUS BÖLÜM
#
# Examples:
#   Bölüm 50
#   Bolum 50
#   Bölüm: 50
#   Bolum: 50
#
# IMPORTANT:
# This is NOT the same thing as Episode.
#
CAPTION_BOLUM_PATTERN = re.compile(
    r"\bB[öo]l[üu]m\s*[:#-]?\s*(\d{1,4})\b",
    re.IGNORECASE
)

# EPISODE WITHIN THE SEASON
#
# Examples:
#   Episode 1
#   Episode 01
#   Ep 1
#   Ep. 01
#
CAPTION_EPISODE_PATTERN = re.compile(
    r"\b(?:Episode|Epi?\.?|E)\s*#?\s*(\d{1,4})\b",
    re.IGNORECASE
)

CAPTION_YEAR_PATTERN = re.compile(
    r"\b(19\d{2}|20\d{2})\b"
)

CAPTION_SOURCE_PATTERN = re.compile(
    r"#(\w+)"
)

CAPTION_YEAR_PATTERN = re.compile(r"\b(19\d{2}|20\d{2})\b")

CAPTION_SOURCE_PATTERN = re.compile(r"#(\w+)")

CAPTION_LANGUAGES = [
    "Urdu", "Hindi", "English", "Arabic", "Persian", "Farsi", "Turkish",
    "Bangla", "Bengali", "Pashto", "Punjabi", "Sindhi", "Malayalam",
    "Tamil", "Telugu", "Indonesian", "French", "German", "Spanish",
    "Russian", "Kurdish",
]

CAPTION_SUB_KEYWORDS = [
    "Subtitles", "Subtitle", "Softsub", "Hardsub", "Subs", "Sub",
]


def parse_video_caption(caption_text):
    """
    Extract metadata ONLY from an existing video's caption.

    Supported caption examples:

        Ibn-e-Sina S01 E01 - KayiTV | Urdu Subtitles

        Ibn-e-Sina Season 01 Episode 01 - KayiTV | Urdu Subtitles

        Ibn-e-Sina S01 Episode 01 - KayiTV | Urdu Subtitles

        Ibn-e-Sina S01 E01 | 720p | Urdu Subtitles

        Ibn-e-Sina | Season 1 | Episode 1 | Bolum 1 | 720p

    Metadata model:

        season   = season explicitly written in caption
        episode  = episode within that season
        bolum    = GLOBAL continuous Bölüm number, when explicitly present
        quality  = video quality

    IMPORTANT:

    - Filename is NEVER inspected.
    - Everything comes ONLY from the Telegram caption.
    - "E01" is treated as Episode 01.
    - "S01" is treated as Season 01.
    - An explicitly written Bölüm number remains authoritative.
    - If no Bölüm exists, build_caption_metadata() can derive it
      from the /setseason mapping.
    """

    if not caption_text or not caption_text.strip():
        return {}

    text = caption_text.strip()

    metadata = {}

    marker_starts = []

    # ========================================================
    # GLOBAL BÖLÜM
    # ========================================================

    # Examples:
    #
    #   Bölüm 50
    #   Bolum 50
    #   Bölüm: 50
    #   Bolum: 50
    #
    # This remains optional because captions such as
    # "S01 E01" can now derive the global Bölüm from
    # the /setseason mapping.

    bolum_match = CAPTION_BOLUM_PATTERN.search(text)

    if bolum_match:
        metadata["bolum"] = int(
            bolum_match.group(1)
        )

        marker_starts.append(
            bolum_match.start()
        )

    # ========================================================
    # SEASON
    # ========================================================

    season_match = CAPTION_SEASON_PATTERN.search(text)

    if season_match:
        metadata["season"] = int(
            season_match.group(1)
        )

        marker_starts.append(
            season_match.start()
        )

    # ========================================================
    # EPISODE WITHIN SEASON
    # ========================================================

    # Supports:
    #
    #   Episode 1
    #   Episode 01
    #   Ep 1
    #   Ep. 01
    #   E01
    #   E1
    #
    # The short E01 form is important for captions such as:
    #
    #   Ibn-e-Sina S01 E01 - KayiTV | Urdu Subtitles

    episode_match = CAPTION_EPISODE_PATTERN.search(text)

    if episode_match:
        metadata["episode"] = int(
            episode_match.group(1)
        )

        marker_starts.append(
            episode_match.start()
        )

    # ========================================================
    # QUALITY
    # ========================================================

    quality_match = CAPTION_QUALITY_PATTERN.search(text)

    if quality_match:
        metadata["quality"] = (
            quality_match.group(1).lower()
        )

        marker_starts.append(
            quality_match.start()
        )

    # ========================================================
    # LANGUAGE
    # ========================================================

    for lang in CAPTION_LANGUAGES:

        lang_match = re.search(
            r"\b" + re.escape(lang) + r"\b",
            text,
            re.IGNORECASE
        )

        if lang_match:

            metadata["lang"] = lang

            marker_starts.append(
                lang_match.start()
            )

            break

    # ========================================================
    # SUBTITLE
    # ========================================================

    for sub_word in CAPTION_SUB_KEYWORDS:

        sub_match = re.search(
            r"\b" + re.escape(sub_word) + r"\b",
            text,
            re.IGNORECASE
        )

        if sub_match:

            metadata["sub"] = "Subtitle"

            marker_starts.append(
                sub_match.start()
            )

            break

    # ========================================================
    # SOURCE
    # ========================================================

    source_match = CAPTION_SOURCE_PATTERN.search(text)

    if source_match:

        metadata["source"] = (
            "#" + source_match.group(1)
        )

        marker_starts.append(
            source_match.start()
        )

    # ========================================================
    # YEAR
    # ========================================================

    year_match = CAPTION_YEAR_PATTERN.search(text)

    if year_match:

        metadata["year"] = (
            year_match.group(1)
        )

    # ========================================================
    # TITLE
    #
    # Everything before the first recognized metadata marker.
    # ========================================================

    if marker_starts:

        title_end = min(
            marker_starts
        )

        title = text[
            :title_end
        ].strip(" -_|:\t")

        title = re.sub(
            r"\s+",
            " ",
            title
        )

        if title:
            metadata["title"] = title

    return metadata
    
CAPTION_KNOWN_PLACEHOLDERS = [
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
]


def extract_used_placeholders(template_text):
    """Return the set of known placeholders actually used in a template."""
    used = set()
    for placeholder in CAPTION_KNOWN_PLACEHOLDERS:
        if "{" + placeholder + "}" in template_text:
            used.add(placeholder)
    return used


def build_caption_metadata(
    user_id,
    caption_text,
):
    """
    Parse a video's caption and resolve full Phase 1 metadata.

    Global Bölüm handling:

    1. If the caption explicitly contains "Bölüm 50",
       that value is used as the global Bölüm.

    2. If the caption does NOT contain a Bölüm number,
       but contains Season + Episode such as:

           S01 E01

       the global Bölüm is calculated using the user's
       /setseason mapping.

    Example:

        /setseason 1 1
        S01 E01
        -> Global Bölüm 1

        /setseason 1 1
        S01 E10
        -> Global Bölüm 10

        /setseason 2 11
        S02 E01
        -> Global Bölüm 11

        /setseason 2 11
        S02 E10
        -> Global Bölüm 20

    IMPORTANT:

    - Filename is NEVER inspected.
    - Everything comes ONLY from the Telegram caption.
    - /setseason determines the global numbering when the
      caption does not explicitly contain Bölüm.
    - {finale} is calculated from the season mappings.
    """

    # ========================================================
    # PARSE CAPTION ONLY
    # ========================================================

    metadata = parse_video_caption(
        caption_text
    )

    # ========================================================
    # GET ALL SEASON MAPPINGS
    # ========================================================

    try:
        mappings = (
            get_season_mappings(
                user_id
            )
            or []
        )
    except Exception:
        mappings = []

    # ========================================================
    # NORMALIZE MAPPINGS
    # ========================================================

    normalized = []

    for mapping in mappings:

        try:

            normalized.append(
                {
                    "season": int(
                        mapping["season"]
                    ),
                    "start_global_episode": int(
                        mapping[
                            "start_global_episode"
                        ]
                    ),
                }
            )

        except (
            TypeError,
            ValueError,
            KeyError,
        ):
            continue

    mappings = sorted(
        normalized,
        key=lambda mapping:
            mapping[
                "start_global_episode"
            ],
    )

    # ========================================================
    # EXPLICIT GLOBAL BÖLÜM
    # ========================================================

    global_episode = metadata.get(
        "bolum"
    )

    # ========================================================
    # DERIVE GLOBAL BÖLÜM FROM
    # SEASON + EPISODE + /SETSEASON
    # ========================================================

    if global_episode is None:

        season = metadata.get(
            "season"
        )

        episode = metadata.get(
            "episode"
        )

        # Both Season and Episode are required
        # to derive the global Bölüm.

        if (
            season is not None
            and episode is not None
        ):

            matching_mapping = None

            # Find the /setseason mapping for
            # the season explicitly written in
            # the caption.

            for mapping in mappings:

                if (
                    mapping["season"]
                    == int(season)
                ):
                    matching_mapping = mapping
                    break

            if matching_mapping is not None:

                season_start = (
                    matching_mapping[
                        "start_global_episode"
                    ]
                )

                global_episode = (
                    season_start
                    + int(episode)
                    - 1
                )

                metadata["bolum"] = (
                    global_episode
                )

    # ========================================================
    # GLOBAL BÖLÜM IS STILL REQUIRED
    # ========================================================

    if global_episode is None:

        metadata["finale"] = ""

        return (
            metadata,
            None,
        )

    # ========================================================
    # NORMALIZE GLOBAL BÖLÜM
    # ========================================================

    try:

        global_episode = int(
            global_episode
        )

    except (
        TypeError,
        ValueError,
    ):

        metadata["finale"] = ""

        return (
            metadata,
            None,
        )

    # ========================================================
    # RESOLVE SEASON FROM GLOBAL BÖLÜM
    # ========================================================
    #
    # /setseason remains authoritative for the
    # final Season + Season Episode values.
    #
    # Example:
    #
    # /setseason 2 11
    #
    # Global Bölüm 11 -> S02 E01
    # Global Bölüm 12 -> S02 E02
    #
    # ========================================================

    current_mapping = None
    next_mapping = None

    for index, mapping in enumerate(
        mappings
    ):

        start_global_episode = (
            mapping[
                "start_global_episode"
            ]
        )

        if (
            start_global_episode
            <= global_episode
        ):

            current_mapping = mapping

            if (
                index + 1
                < len(mappings)
            ):

                next_mapping = mappings[
                    index + 1
                ]

        else:
            break

    # ========================================================
    # APPLY SEASON MAPPING
    # ========================================================

    if current_mapping is not None:

        mapped_season = (
            current_mapping[
                "season"
            ]
        )

        season_start = (
            current_mapping[
                "start_global_episode"
            ]
        )

        season_episode = (
            global_episode
            - season_start
            + 1
        )

        metadata["season"] = (
            mapped_season
        )

        metadata["season_episode"] = (
            season_episode
        )

    # ========================================================
    # CALCULATE SEASON FINALE
    # ========================================================

    metadata["finale"] = ""

    if (
        current_mapping is not None
        and next_mapping is not None
    ):

        next_season_start = (
            next_mapping[
                "start_global_episode"
            ]
        )

        finale_global_episode = (
            next_season_start
            - 1
        )

        if (
            global_episode
            == finale_global_episode
        ):

            metadata["finale"] = (
                "Season Finale"
            )

    return (
        metadata,
        global_episode,
    )
    
def build_message_link(
    channel,
    message_id,
):
    username = getattr(
        channel,
        "username",
        None,
    )

    if username:
        return (
            f"https://t.me/"
            f"{username}/"
            f"{int(message_id)}"
        )

    channel_id = getattr(
        channel,
        "id",
        None,
    )

    if channel_id is None:
        return ""

    return (
        f"https://t.me/c/"
        f"{int(channel_id)}/"
        f"{int(message_id)}"
    )    
def render_caption_template(template_text, metadata):
    """
    Fill in a saved caption template using parsed metadata.

    HTML tags in the template are preserved.

    {finale} is optional:
        - "Season Finale" for the final episode
        - empty for normal episodes

    When {finale} is empty, its surrounding "|" separator
    is removed as well.
    """

    used_placeholders = extract_used_placeholders(
        template_text
    )

    # ========================================================
    # CHECK REQUIRED PLACEHOLDERS
    # ========================================================

    missing = []

    for placeholder in used_placeholders:

        # Finale is optional.
        if placeholder == "finale":
            continue

        if metadata.get(placeholder) in (
            None,
            ""
        ):
            missing.append(
                placeholder
            )

    if missing:
        return None, missing

    # ========================================================
    # SUBSTITUTE NORMAL PLACEHOLDERS
    # ========================================================

    new_caption = template_text

    for placeholder in used_placeholders:

        if placeholder == "finale":
            continue

        value = metadata.get(
            placeholder,
            ""
        )

        if value is None:
            value = ""

        new_caption = new_caption.replace(
            "{" + placeholder + "}",
            str(value)
        )

    # ========================================================
    # SUBSTITUTE FINALE
    # ========================================================

    finale = metadata.get(
        "finale",
        ""
    )

    if finale:

        new_caption = new_caption.replace(
            "{finale}",
            str(finale)
        )

    else:

        # Remove:
        #
        # | {finale}
        #
        # |{finale}
        #
        # - {finale}
        #
        # before removing the placeholder itself.

        new_caption = re.sub(
            r"\s*\|\s*\{finale\}",
            "",
            new_caption
        )

        new_caption = re.sub(
            r"\s*-\s*\{finale\}",
            "",
            new_caption
        )

        new_caption = new_caption.replace(
            "{finale}",
            ""
        )

    return new_caption, []

def telegram_caption_to_html(caption_text, entities):
    """
    Convert a Telegram message caption + its MessageEntity objects
    into Telegram-compatible HTML.

    IMPORTANT:
    - This function is ONLY for diagnostics/display.
    - It NEVER modifies the Phase 1 parser input.
    - The parser continues to receive plain replied.raw_text.
    - Telegram entities are converted using Telethon's own HTML
      unparser so nested formatting, URLs, spoilers, custom emoji,
      etc. are handled correctly.
    """

    if not caption_text:
        return ""

    try:
        from telethon.extensions import html as telegram_html

        return telegram_html.unparse(
            caption_text,
            entities or []
        )

    except Exception as exc:
        # Diagnostic fallback.
        #
        # We still want /debug to work even if an unusual entity
        # cannot be converted by the HTML unparser.
        return (
            f"[HTML CONVERSION FAILED: {type(exc).__name__}: {exc}]\n"
            f"{caption_text}"
        )        

# ==================== CAPTION FEATURE: DEBUG COMMAND ====================

@client.on(events.NewMessage(pattern=r"^/debug$", outgoing=True))
async def debug_caption_message(event):
    """
    Advanced diagnostic report for Phase 1 caption parsing.

    IMPORTANT:
    - Filename is displayed for diagnostics only.
    - Filename is NEVER passed to the Phase 1 parser.
    - Parser input is ONLY the existing Telegram message caption.
    - Telegram formatting is separately converted to HTML for inspection.
    """

    if not session.is_authorized(event.sender_id):
        await event.reply(
            "❌ <b>Unauthorized</b>",
            parse_mode="html"
        )
        return

    replied = await event.get_reply_message()

    if not replied:
        await event.reply(
            "❌ <b>No message selected</b>\n\n"
            "Reply to a video/message with <code>/debug</code>.",
            parse_mode="html"
        )
        return

    # ========================================================
    # HELPERS
    # ========================================================

    from html import escape

    def esc(value):
        return (
            escape(str(value))
            if value is not None
            else ""
        )

    def format_size(size):
        if size is None:
            return "Unknown"

        try:
            size = float(size)

            if size < 1024:
                return f"{int(size)} B"

            if size < 1024 * 1024:
                return f"{size / 1024:.2f} KB"

            if size < 1024 * 1024 * 1024:
                return f"{size / (1024 * 1024):.2f} MB"

            return (
                f"{size / (1024 * 1024 * 1024):.2f} GB"
            )

        except Exception:
            return str(size)

    def format_duration(value):
        if value is None:
            return "Unknown"

        try:
            value = int(value)

            hours = value // 3600
            minutes = (value % 3600) // 60
            seconds = value % 60

            if hours:
                return (
                    f"{hours:02d}:"
                    f"{minutes:02d}:"
                    f"{seconds:02d}"
                )

            return (
                f"{minutes:02d}:"
                f"{seconds:02d}"
            )

        except Exception:
            return str(value)

    def yes_no(value):
        return "✅ YES" if value else "❌ NO"

    # ========================================================
    # BASIC MESSAGE INFORMATION
    # ========================================================

    message_id = replied.id
    chat_id = getattr(
        replied,
        "chat_id",
        None
    )

    chat = replied.chat

    chat_title = getattr(
        chat,
        "title",
        None
    )

    chat_username = getattr(
        chat,
        "username",
        None
    )

    # ========================================================
    # BUILD TELEGRAM MESSAGE LINK
    # ========================================================

    message_link = None

    if chat_username:

        message_link = (
            f"https://t.me/"
            f"{chat_username}/"
            f"{message_id}"
        )

    elif (
        chat_id
        and str(chat_id).startswith("-100")
    ):

        internal_id = str(chat_id)[4:]

        message_link = (
            f"https://t.me/c/"
            f"{internal_id}/"
            f"{message_id}"
        )

    # ========================================================
    # MEDIA TYPE
    # ========================================================

    media_type = "TEXT / NO MEDIA"

    if replied.video:
        media_type = "VIDEO"

    elif replied.document:
        media_type = "DOCUMENT"

    elif replied.photo:
        media_type = "PHOTO"

    elif replied.audio:
        media_type = "AUDIO"

    elif replied.sticker:
        media_type = "STICKER"
        
        

    # ========================================================
    # FILE INFORMATION
    # ========================================================

    file_name = None
    mime_type = None
    file_size = None

    duration = None
    width = None
    height = None

    if replied.video:

        video = replied.video

        file_name = getattr(
            video,
            "file_name",
            None
        )

        mime_type = getattr(
            video,
            "mime_type",
            None
        )

        file_size = getattr(
            video,
            "size",
            None
        )

        duration = getattr(
            video,
            "duration",
            None
        )

        width = getattr(
            video,
            "width",
            None
        )

        height = getattr(
            video,
            "height",
            None
        )

    elif replied.document:

        document = replied.document

        file_name = getattr(
            document,
            "file_name",
            None
        )

        mime_type = getattr(
            document,
            "mime_type",
            None
        )

        file_size = getattr(
            document,
            "size",
            None
        )
        
        

    # ========================================================
    # CAPTION ONLY
    #
    # THIS IS THE ONLY PHASE 1 PARSER INPUT.
    # ========================================================

    caption = replied.raw_text or ""

    parser_input = caption
    
    

    # ========================================================
    # PHASE 1 PARSER
    # ========================================================

    parsed_metadata = parse_video_caption(
        parser_input
    )

    # ========================================================
    # SEASON MAPPING
    # ========================================================

    global_episode = parsed_metadata.get(
        "bolum"
    )

    mapped_season = None
    season_episode = None

    if global_episode is not None:

        mapped_season, season_episode = (
            resolve_season_for_global_episode(
                event.sender_id,
                global_episode
            )
        )

    # ========================================================
    # CAPTION ENTITIES
    # ========================================================

    entities = (
        getattr(
            replied,
            "entities",
            None
        )
        or []
    )

    entity_lines = []

    for entity in entities:

        try:

            entity_lines.append(
                f"• <code>"
                f"{esc(type(entity).__name__)}"
                f"</code> "
                f"<i>offset="
                f"{entity.offset}, "
                f"length="
                f"{entity.length}</i>"
            )

        except Exception:

            entity_lines.append(
                f"• <code>"
                f"{esc(type(entity).__name__)}"
                f"</code>"
            )

    if not entity_lines:

        entity_lines.append(
            "• <i>No caption entities</i>"
        )

    # ========================================================
    # HTML FORMATTED CAPTION
    #
    # IMPORTANT:
    # This is ONLY a representation of the existing
    # Telegram formatting.
    #
    # It does NOT go into the Phase 1 parser.
    # ========================================================

    html_caption = (
        telegram_caption_to_html(
            caption,
            entities
        )
        if caption
        else ""
    )

    if html_caption:

        # Escape the HTML so Telegram displays the actual
        # HTML tags instead of rendering them.
        html_caption_display = (
            f"<pre>{esc(html_caption)}</pre>"
        )

    else:

        html_caption_display = (
            "<i>[NO CAPTION]</i>"
        )

    # ========================================================
    # DETECTED METADATA
    # ========================================================

    metadata_lines = []

    if parsed_metadata:

        for key, value in parsed_metadata.items():

            metadata_lines.append(
                f"• <b>{esc(key)}</b>: "
                f"<code>{esc(value)}</code>"
            )

    else:

        metadata_lines.append(
            "• <i>Nothing detected</i>"
        )

    # ========================================================
    # SEASON MAPPING
    # ========================================================

    if global_episode is None:

        season_mapping = (
            "• <b>Global Bölüm:</b> "
            "<code>Not detected</code>\n"
            "• <b>Mapped Season:</b> "
            "<code>—</code>\n"
            "• <b>Season Episode:</b> "
            "<code>—</code>"
        )

    elif mapped_season is not None:

        season_mapping = (
            f"• <b>Global Bölüm:</b> "
            f"<code>{esc(global_episode)}</code>\n"
            f"• <b>Mapped Season:</b> "
            f"<code>{esc(mapped_season)}</code>\n"
            f"• <b>Season Episode:</b> "
            f"<code>{season_episode:02d}</code>"
        )

    else:

        season_mapping = (
            f"• <b>Global Bölüm:</b> "
            f"<code>{esc(global_episode)}</code>\n"
            "• <b>Mapped Season:</b> "
            "<code>None</code>\n"
            "• <b>Season Episode:</b> "
            "<code>None</code>"
        )

    # ========================================================
    # MESSAGE LINK
    # ========================================================

    if message_link:

        message_link_line = (
            f'<a href="{message_link}">'
            "🔗 Open Telegram Message"
            "</a>"
        )

    else:

        message_link_line = (
            "🔗 <i>Message link unavailable</i>"
        )

    # ========================================================
    # ORIGINAL CAPTION
    # ========================================================

    if caption:

        caption_display = (
            f"<pre>{esc(caption)}</pre>"
        )

    else:

        caption_display = (
            "<i>[NO CAPTION]</i>"
        )

    # ========================================================
    # BUILD REPORT
    # ========================================================

    lines = [

        "🔍 <b>CAPTION DEBUG REPORT</b>",
        "<code>━━━━━━━━━━━━━━━━━━━━━━━━━━━━</code>",

        "",

        "📌 <b>MESSAGE INFORMATION</b>",
        "",
        f"🆔 <b>Chat ID:</b> "
        f"<code>{esc(chat_id)}</code>",
        f"💬 <b>Chat:</b> "
        f"<code>{esc(chat_title or 'Unknown')}</code>",

        (
            f"👤 <b>Username:</b> "
            f"<code>@{esc(chat_username)}</code>"
            if chat_username
            else
            "👤 <b>Username:</b> "
            "<i>None</i>"
        ),

        f"🔢 <b>Message ID:</b> "
        f"<code>{message_id}</code>",

        message_link_line,

        "",

        "📦 <b>MEDIA INFORMATION</b>",
        "",
        f"🎞️ <b>Type:</b> "
        f"<code>{esc(media_type)}</code>",

        f"📄 <b>File Name:</b> "
        f"<code>{esc(file_name or 'None')}</code>",

        f"🧾 <b>MIME:</b> "
        f"<code>{esc(mime_type or 'None')}</code>",

        f"💾 <b>Size:</b> "
        f"<code>{esc(format_size(file_size))}</code>",

        f"⏱️ <b>Duration:</b> "
        f"<code>{esc(format_duration(duration))}</code>",

        (
            f"📐 <b>Resolution:</b> "
            f"<code>{width} × {height}</code>"
            if width and height
            else
            "📐 <b>Resolution:</b> "
            "<code>Unknown</code>"
        ),

        "",

        "📝 <b>ORIGINAL CAPTION</b>",
        "",
        caption_display,

        "",

        "🌐 <b>HTML FORMATTED CAPTION</b>",
        "",
        html_caption_display,

        "",

        "🎨 <b>CAPTION ENTITIES</b>",
        "",
        *entity_lines,

        "",

        "🧠 <b>PHASE 1 PARSER PIPELINE</b>",
        "",
        "📥 <b>Parser Source:</b> "
        "<code>MESSAGE CAPTION ONLY</code>",

        f"📝 <b>Caption Present:</b> "
        f"{yes_no(bool(caption.strip()))}",

        "🚫 <b>Filename Used:</b> "
        "<code>NO</code>",

        "🔒 <b>Parser Input:</b> "
        "<code>caption = replied.raw_text</code>",

        "",

        "🔎 <b>DETECTED METADATA</b>",
        "",
        *metadata_lines,

        "",

        "🎬 <b>SEASON MAPPING</b>",
        "",
        season_mapping,

        "",

        "📊 <b>RAW CAPTION STATISTICS</b>",
        "",
        f"🔤 <b>Characters:</b> "
        f"<code>{len(caption)}</code>",

        f"🧩 <b>Entities:</b> "
        f"<code>{len(entities)}</code>",

        f"📄 <b>Lines:</b> "
        f"<code>{len(caption.splitlines())}</code>",

        "",

        "<code>━━━━━━━━━━━━━━━━━━━━━━━━━━━━</code>",
        "🛡️ <b>PHASE 1 SAFETY CHECK</b>",
        "",

        "✅ Parser receives caption only",
        "✅ Filename excluded from parser",
        "✅ Global Bölüm used for season resolution",
        "✅ Caption entities inspected separately",
        "✅ Telegram formatting converted separately to HTML",
        "✅ HTML representation never enters parser",
    ]

    output = "\n".join(lines)

    # ========================================================
    # TELEGRAM MESSAGE SIZE SAFETY
    # ========================================================

    if len(output) > 3900:

        truncated_caption = caption[:800]

        caption_display = (
            f"<pre>{esc(truncated_caption)}</pre>\n"
            "<i>⚠️ Original caption display truncated.</i>"
        )

        # Keep HTML representation separately truncated.
        truncated_html = html_caption[:800]

        html_caption_display = (
            f"<pre>{esc(truncated_html)}</pre>\n"
            "<i>⚠️ HTML caption display truncated.</i>"
        )

        output = "\n".join(lines)

        old_caption = (
            f"<pre>{esc(caption)}</pre>"
            if caption
            else
            "<i>[NO CAPTION]</i>"
        )

        output = output.replace(
            old_caption,
            caption_display
        )

        old_html = (
            f"<pre>{esc(html_caption)}</pre>"
            if html_caption
            else
            "<i>[NO CAPTION]</i>"
        )

        output = output.replace(
            old_html,
            html_caption_display
        )

    if len(output) > 4000:

        output = output[:3900] + (
            "\n\n⚠️ "
            "<i>Debug report truncated by "
            "Telegram limit.</i>"
        )

    # ========================================================
    # SEND
    # ========================================================

    await event.reply(
        output,
        parse_mode="html",
        link_preview=False
    )
# ==================== CAPTION FEATURE: RANGE PROCESSING (PHASE 1) ====================

async def apply_caption_to_message(channel, message_id, user_id, template_text):
    """
    Read the EXISTING caption of a single message, parse it, generate the
    new caption from the saved template, and apply it in place.

    The media itself is never downloaded or re-uploaded -- only the caption
    text of the existing message is edited.

    Returns (status, detail) where status is one of:
        "updated", "skipped", "failed"
    """
    message = await client.get_messages(channel, ids=message_id)

    if not message:
        return "failed", "Message not found"

    is_video = bool(getattr(message, "video", None))
    if not is_video:
        document = getattr(message, "document", None)
        mime_type = getattr(document, "mime_type", "") or "" if document else ""
        is_video = mime_type.startswith("video/")

    if not is_video:
        return "skipped", "Not a video"

    original_caption = message.raw_text or message.text

    if not original_caption or not original_caption.strip():
        return "failed", "No existing caption to read metadata from"

    metadata, global_episode = build_caption_metadata(user_id, original_caption)

    if global_episode is None:
        return "failed", "Could not determine the global Bölüm number from the caption"

    new_caption, missing = render_caption_template(template_text, metadata)

    if new_caption is None:
        return "failed", f"Missing metadata for: {', '.join(missing)}"

    try:
        await client.edit_message(channel, message_id, new_caption, parse_mode="html")
        return "updated", "Caption updated"
    except MessageNotModifiedError:
        return "skipped", "Caption already matches"
    except MessageIdInvalidError:
        return "failed", "Invalid message ID (possibly deleted)"


async def process_caption_range(channel, start_id, end_id, user_id, template_text, status_msg):
    """
    Process every message in the inclusive ID range, replacing captions on
    existing videos only. A single bad message never stops the operation.
    """
    total = end_id - start_id + 1

    video_count = 0
    skipped_count = 0
    failed_count = 0
    failed_messages = []

    for index, message_id in enumerate(range(start_id, end_id + 1), start=1):
        try:
            status, detail = await apply_caption_to_message(
                channel, message_id, user_id, template_text
            )

            if status == "updated":
                video_count += 1
            elif status == "skipped":
                skipped_count += 1
            else:
                failed_count += 1
                failed_messages.append((message_id, detail))

        except FloodWaitError as e:
            await status_msg.edit(
                "⏳ **Telegram FloodWait**\n\n"
                f"Waiting `{e.seconds}` seconds before continuing...\n\n"
                f"📊 Progress: `{index}/{total}`"
            )
            await asyncio.sleep(e.seconds)

            try:
                status, detail = await apply_caption_to_message(
                    channel, message_id, user_id, template_text
                )
                if status == "updated":
                    video_count += 1
                elif status == "skipped":
                    skipped_count += 1
                else:
                    failed_count += 1
                    failed_messages.append((message_id, detail))
            except Exception as retry_error:
                failed_count += 1
                failed_messages.append((message_id, f"Retry failed: {retry_error}"))

        except Exception as e:
            failed_count += 1
            failed_messages.append((message_id, str(e)))

        if index == 1 or index % 10 == 0 or index == total:
            await status_msg.edit(
                "🔄 **Updating captions...**\n\n"
                f"Progress: {index}/{total}\n"
                f"Videos: {video_count}\n"
                f"Skipped: {skipped_count}\n"
                f"Failed: {failed_count}"
            )

        await asyncio.sleep(0.35)

    return {
        "total": total,
        "video_count": video_count,
        "skipped_count": skipped_count,
        "failed_count": failed_count,
        "failed_messages": failed_messages,
    }
            
# ==================== END VIDEO COVER FEATURE ====================

# ==================== CAPTION FEATURE: PREVIEW ENGINE ====================

def build_caption_message_link(channel, message_id):
    """
    Build a clickable Telegram message link for a message.

    Public channels:
        https://t.me/username/message_id

    Private channels/supergroups:
        https://t.me/c/internal_id/message_id

    Returns None if a usable link cannot be constructed.
    """

    username = getattr(channel, "username", None)

    if username:
        return (
            f"https://t.me/"
            f"{username}/"
            f"{message_id}"
        )

    if isinstance(channel, types.Channel):
        return (
            f"https://t.me/c/"
            f"{channel.id}/"
            f"{message_id}"
        )

    return None


async def collect_caption_preview(
    channel,
    start_id,
    end_id,
    user_id,
):
    """
    Safely scan a caption range and build the preview data.

    IMPORTANT:
    - No message is edited here.
    - No media is downloaded.
    - Metadata comes ONLY from message captions.
    - Filename is NEVER passed to the parser.
    - GLOBAL Bölüm is the continuous episode number.
    - /setseason mapping is authoritative when available.
    - Videos are grouped by:
          Season -> Episode -> Bölüm
    - Multiple qualities of the same episode are grouped together.
    """

    preview_items = []

    stats = {
        "total_messages": 0,
        "video_count": 0,
        "episode_count": 0,
        "skipped_count": 0,
        "failed_count": 0,
        "failed_messages": [],
    }

    total = end_id - start_id + 1

    # ========================================================
    # FETCH IN BATCHES
    # ========================================================

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

            messages = await client.get_messages(
                channel,
                ids=message_ids,
            )

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

        # Telethon normally returns a list for multiple IDs.
        if not isinstance(messages, list):
            messages = [messages]

        # ====================================================
        # PROCESS EACH MESSAGE
        # ====================================================

        for message in messages:

            if not message:
                stats["skipped_count"] += 1
                continue

            stats["total_messages"] += 1

            message_id = getattr(
                message,
                "id",
                None,
            )

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

            # Some Telegram videos can appear as documents.
            if not is_video:

                document = getattr(
                    message,
                    "document",
                    None,
                )

                mime_type = (
                    getattr(
                        document,
                        "mime_type",
                        "",
                    )
                    or ""
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
            # CAPTION ONLY
            # ------------------------------------------------
            #
            # THIS IS THE ONLY INPUT TO PHASE 1 PARSING.
            #
            # Filename is deliberately ignored.
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

            # ------------------------------------------------
            # PHASE 1 METADATA
            # ------------------------------------------------

            metadata, global_episode = (
                build_caption_metadata(
                    user_id,
                    caption,
                )
            )

            # ------------------------------------------------
            # BÖLÜM IS REQUIRED
            # ------------------------------------------------

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
            # SEASON
            # ------------------------------------------------

            season = metadata.get(
                "season"
            )

            # ------------------------------------------------
            # SEASON EPISODE
            # ------------------------------------------------
            #
            # If /setseason mapping exists, this is the
            # authoritative Episode number.
            #
            # Example:
            #
            # /setseason 3 50
            #
            # Bölüm 50 -> Season 3 Episode 01
            #
            # We MUST NOT replace this with an explicitly
            # parsed "Episode 50" from the old caption.
            # ------------------------------------------------

            season_episode = metadata.get(
                "season_episode"
            )

            if season_episode is not None:

                display_episode = (
                    season_episode
                )

            else:

                # No mapping exists.
                #
                # In that situation, use the episode explicitly
                # detected from the caption.
                display_episode = metadata.get(
                    "episode"
                )

            # ------------------------------------------------
            # FALLBACK DISPLAY VALUES
            # ------------------------------------------------

            if season is None:
                season = "?"

            if display_episode is None:
                display_episode = "?"

            # ------------------------------------------------
            # QUALITY
            # ------------------------------------------------

            quality = metadata.get(
                "quality"
            )

            if not quality:
                quality = "Unknown"

            quality = str(
                quality
            )

            # ------------------------------------------------
            # MESSAGE LINK
            # ------------------------------------------------

            message_link = (
                build_caption_message_link(
                    channel,
                    message_id,
                )
            )

            # ------------------------------------------------
            # PREVIEW ITEM
            # ------------------------------------------------

            preview_items.append(
                {
                    "message_id": message_id,
                    "season": season,
                    "episode": display_episode,
                    "bolum": global_episode,
                    "quality": quality,
                    "link": message_link,
                }
            )

    # ========================================================
    # SORT
    #
    # Season:
    #     low -> high
    #
    # Episode:
    #     low -> high
    #
    # Bölüm:
    #     low -> high
    #
    # Quality:
    #     low -> high
    # ========================================================

    def numeric_value(value):

        try:
            return int(value)

        except Exception:
            return 999999

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

    preview_items.sort(
        key=lambda item: (
            numeric_value(
                item["season"]
            ),
            numeric_value(
                item["episode"]
            ),
            numeric_value(
                item["bolum"]
            ),
            quality_value(
                item["quality"]
            ),
        )
    )

    # ========================================================
    # GROUP EPISODES
    #
    # Same:
    #
    #     Season + Episode + Bölüm
    #
    # = one episode with multiple qualities.
    # ========================================================

    grouped = {}

    for item in preview_items:

        key = (
            item["season"],
            item["episode"],
            item["bolum"],
        )

        if key not in grouped:
            grouped[key] = []

        grouped[key].append(
            item
        )

    stats["episode_count"] = len(
        grouped
    )

    stats["groups"] = grouped

    return (
        preview_items,
        stats,
    )


def build_caption_preview_message(
    start_id,
    end_id,
    template_text,
    preview_items,
    stats,
):
    """
    Build the formatted Telegram HTML preview.

    Quality names themselves become clickable links
    to their exact source messages.

    Episodes are sorted:
        Season  -> low to high
        Episode -> low to high
        Bölüm   -> low to high
        Quality -> low to high
    """

    def esc(value):
        return escape(str(value))

    def numeric_value(value):
        try:
            return int(value)
        except Exception:
            return 999999

    def quality_value(value):
        match = re.search(
            r"\d+",
            str(value),
        )

        if match:
            return int(match.group())

        return 999999

    # ========================================================
    # GROUP BY SEASON + EPISODE + BÖLÜM
    # ========================================================

    grouped = {}

    for item in preview_items:

        key = (
            item["season"],
            item["episode"],
            item["bolum"],
        )

        grouped.setdefault(
            key,
            []
        ).append(item)

    # ========================================================
    # SORT
    # ========================================================

    sorted_groups = sorted(
        grouped.items(),
        key=lambda pair: (
            numeric_value(pair[0][0]),
            numeric_value(pair[0][1]),
            numeric_value(pair[0][2]),
        ),
    )

    # ========================================================
    # HEADER
    # ========================================================

    lines = [
        "📝 <b>CAPTION UPDATE PREVIEW</b>",
        "<code>━━━━━━━━━━━━━━━━━━━━━━━━━━━━</code>",
        "",
        "📌 <b>RANGE</b>",
        (
            f"<code>{esc(start_id)}</code>"
            " → "
            f"<code>{esc(end_id)}</code>"
        ),
        "",
    ]

    # ========================================================
    # EPISODES
    # ========================================================

    current_season = None

    for key, qualities in sorted_groups:

        season, episode, bolum = key

        # ----------------------------------------------------
        # SEASON HEADER
        # ----------------------------------------------------

        if season != current_season:

            if current_season is not None:
                lines.append("")

            lines.append(
                f"🎬 <b>SEASON {esc(season)}</b>"
            )

            lines.append("")

            current_season = season

        # ----------------------------------------------------
        # QUALITY SORT
        # ----------------------------------------------------

        qualities = sorted(
            qualities,
            key=lambda item: quality_value(
                item["quality"]
            ),
        )

        # ----------------------------------------------------
        # FORMAT EPISODE
        # ----------------------------------------------------

        try:
            episode_display = f"{int(episode):02d}"
        except (TypeError, ValueError):
            episode_display = str(episode)

        # ----------------------------------------------------
        # EPISODE HEADER
        # ----------------------------------------------------

        lines.append(
            f"<b>Episode {esc(episode_display)}</b>"
            f" | "
            f"<b>Bölüm {esc(bolum)}</b>"
        )

        # ----------------------------------------------------
        # QUALITY LINKS
        # ----------------------------------------------------

        for item in qualities:

            quality = esc(
                item["quality"]
            )

            link = item.get("link")

            if link:

                lines.append(
                    f'└─ <a href="{esc(link)}">'
                    f"{quality}"
                    f"</a>"
                )

            else:

                lines.append(
                    f"└─ <code>{quality}</code>"
                )

        lines.append("")

    # ========================================================
    # EMPTY RESULT
    # ========================================================

    if not sorted_groups:

        lines.extend(
            [
                "⚠️ <i>No usable video captions "
                "were detected in this range.</i>",
                "",
            ]
        )

    # ========================================================
    # SUMMARY
    # ========================================================

    lines.extend(
        [
            "<code>━━━━━━━━━━━━━━━━━━━━━━━━━━━━</code>",
            "",
            "📊 <b>SUMMARY</b>",
            "",
        ]
    )

    season_counts = {}

    for season, episode, bolum in grouped.keys():

        season_counts[season] = (
            season_counts.get(season, 0) + 1
        )

    for season in sorted(
        season_counts,
        key=numeric_value,
    ):

        count = season_counts[season]

        lines.append(
            f"🎬 Season <b>{esc(season)}</b>: "
            f"<code>{count}</code> "
            f"episode{'s' if count != 1 else ''}"
        )

    lines.extend(
        [
            "",
            (
                f"🎞️ <b>Total episodes:</b> "
                f"<code>{stats['episode_count']}</code>"
            ),
            (
                f"📦 <b>Total videos:</b> "
                f"<code>{stats['video_count']}</code>"
            ),
        ]
    )

    if stats["skipped_count"]:

        lines.append(
            f"⏭️ <b>Skipped:</b> "
            f"<code>{stats['skipped_count']}</code>"
        )

    if stats["failed_count"]:

        lines.append(
            f"⚠️ <b>Failed:</b> "
            f"<code>{stats['failed_count']}</code>"
        )

    # ========================================================
    # CAPTION FORMAT
    #
    # IMPORTANT:
    # Escape the template so the HTML is displayed as SOURCE.
    # It must NOT be interpreted as preview formatting.
    # ========================================================

    lines.extend(
        [
            "",
            "<code>━━━━━━━━━━━━━━━━━━━━━━━━━━━━</code>",
            "",
            "📝 <b>CAPTION FORMAT</b>",
            "",
        ]
    )

    if template_text:

        escaped_template = escape(
            template_text
        )

        lines.append(
            f"<pre>{escaped_template}</pre>"
        )

    else:

        lines.append(
            "<i>No caption format selected.</i>"
        )

    # ========================================================
    # ACTIONS
    # ========================================================

    lines.extend(
        [
            "",
            "⚠️ <b>This is a preview only.</b>",
            "<i>No captions have been changed yet.</i>",
            "",
            "✅ Use <code>/confirm</code> to apply.",
            "✏️ Use <code>/setcaption</code> "
            "to change the format.",
            "❌ Use <code>/cancel</code> to abort.",
        ]
    )

    return "\n".join(lines)


async def prepare_caption_preview(
    event,
    template_text,
):
    """
    Scan the pending /caption range and send the confirmation
    preview.

    Returns True when the preview was successfully prepared.
    """

    channel = caption_session.get(
        "channel"
    )

    start_id = caption_session.get(
        "start_id"
    )

    end_id = caption_session.get(
        "end_id"
    )

    user_id = caption_session.get(
        "user_id"
    )

    if not (
        channel
        and start_id
        and end_id
        and user_id
    ):
        await event.reply(
            "❌ Caption preview data is incomplete.\n\n"
            "Please restart with `/caption`."
        )

        clear_caption_session()

        return False

    status_msg = await event.reply(
        "🔎 <b>Preparing caption preview...</b>\n\n"
        "Reading existing video captions only.\n"
        "Nothing will be edited yet.",
        parse_mode="html",
    )

    try:

        preview_items, stats = (
            await collect_caption_preview(
                channel=channel,
                start_id=start_id,
                end_id=end_id,
                user_id=user_id,
            )
        )

        caption_session[
            "preview_items"
        ] = preview_items

        caption_session[
            "preview_stats"
        ] = stats

        preview = (
            build_caption_preview_message(
                start_id=start_id,
                end_id=end_id,
                template_text=template_text,
                preview_items=preview_items,
                stats=stats,
            )
        )

        # Telegram's message limit is approximately 4096
        # characters. Keep a little safety margin.
        if len(preview) <= 3900:

            await status_msg.edit(
                preview,
                parse_mode="html",
                link_preview=False,
            )

        else:

            # The complete preview may be too large for one
            # Telegram message. Send the preview in chunks.
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

        caption_session[
            "waiting_for"
        ] = "format_choice"

        return True

    except Exception as e:

        clear_caption_session()

        await status_msg.edit(
            "❌ <b>Could not prepare caption preview.</b>\n\n"
            f"<code>{str(e)[:1200]}</code>",
            parse_mode="html",
        )

        return False

# ==================== CAPTION FEATURE COMMANDS (PHASE 1) ====================

@client.on(events.NewMessage(pattern=r"^/caption$", outgoing=True))
async def caption_command_handler(event):
    """
    Start the caption-replacement workflow.

    Usage:
        /caption
        -> send START message link
        -> send END message link
        -> /confirm (use saved format) or /setcaption (change it)
    """
    if not session.is_authorized(event.sender_id):
        await event.reply("❌ Unauthorized")
        return

    clear_caption_session()

    caption_session["active"] = True
    caption_session["user_id"] = event.sender_id
    caption_session["waiting_for"] = "start"

    await event.reply(
        "📌 Send the **START message link**.\n\n"
        "Example:\n"
        "`https://t.me/YourChannel/1200`\n\n"
        "Or for a private channel:\n"
        "`https://t.me/c/1234567890/1200`\n\n"
        "Use `/cancel` to abort."
    )


@client.on(
    events.NewMessage(
        pattern=r"^(?:https?://)?t\.me/.*$",
        outgoing=True,
    )
)
async def caption_link_handler(event):
    """
    Receive START and END Telegram message links while
    /caption is active.
    """

    if not session.is_authorized(
        event.sender_id
    ):
        return

    if not caption_session["active"]:
        return

    if caption_session["user_id"] != event.sender_id:
        return

    if caption_session["waiting_for"] not in (
        "start",
        "end",
    ):
        return

    link = (
        event.raw_text or ""
    ).strip()

    parsed = parse_telegram_message_link(
        link
    )

    if not parsed:

        await event.reply(
            "❌ <b>Invalid Telegram message link.</b>\n\n"
            "Please send a valid message link such as:\n"
            "<code>https://t.me/YourChannel/1200</code>",
            parse_mode="html",
        )

        return

    # ========================================================
    # START LINK
    # ========================================================

    if caption_session[
        "waiting_for"
    ] == "start":

        try:

            start_entity = (
                await resolve_cover_channel(
                    parsed
                )
            )

            caption_session[
                "channel"
            ] = start_entity

            caption_session[
                "start_id"
            ] = parsed[
                "message_id"
            ]

            caption_session[
                "waiting_for"
            ] = "end"

            await event.reply(
                "✅ <b>START link saved.</b>\n\n"
                f"📌 Start message ID: "
                f"<code>{parsed['message_id']}</code>\n"
                f"📢 Channel: "
                f"<code>{getattr(start_entity, 'title', 'Unknown')}</code>\n\n"
                "📌 Now send the <b>END message link</b>.\n\n"
                "Use <code>/cancel</code> to abort.",
                parse_mode="html",
            )

        except Exception as e:

            await event.reply(
                "❌ <b>Could not resolve the START link.</b>\n\n"
                f"<code>{str(e)[:1000]}</code>",
                parse_mode="html",
            )

        return

    # ========================================================
    # END LINK
    # ========================================================

    if caption_session[
        "waiting_for"
    ] == "end":

        try:

            end_entity = (
                await resolve_cover_channel(
                    parsed
                )
            )

            start_entity = (
                caption_session[
                    "channel"
                ]
            )

            start_peer_id = (
                get_entity_peer_id(
                    start_entity
                )
            )

            end_peer_id = (
                get_entity_peer_id(
                    end_entity
                )
            )

            if start_peer_id != end_peer_id:

                await event.reply(
                    "❌ <b>Different channels!</b>\n\n"
                    "The START and END links must belong "
                    "to the same channel.\n\n"
                    "The caption operation has been cancelled.",
                    parse_mode="html",
                )

                clear_caption_session()

                return

            start_id = (
                caption_session[
                    "start_id"
                ]
            )

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
                    f"<code>{range_end - range_start + 1}</code>.",
                    parse_mode="html",
                )

                clear_caption_session()

                return

            caption_session[
                "start_id"
            ] = range_start

            caption_session[
                "end_id"
            ] = range_end

            # ------------------------------------------------
            # FORMAT SELECTION
            # ------------------------------------------------

            saved_format = (
                get_saved_caption_format(
                    event.sender_id
                )
            )

            if saved_format:

                # Save that we're waiting for the user to
                # choose the saved format.
                caption_session[
                    "waiting_for"
                ] = "format_choice"

                await event.reply(
                    format_saved_caption_prompt(
                        saved_format
                    ),
                    parse_mode="html",
                )

            else:

                caption_session[
                    "waiting_for"
                ] = "template"

                caption_session[
                    "resume_after_template"
                ] = True

                await event.reply(
                    "📝 <b>No caption format is saved.</b>\n\n"
                    "Please use <code>/setcaption</code> "
                    "and send the caption format you want to use.\n\n"
                    "After saving it, I'll prepare the full "
                    "preview before anything is edited.",
                    parse_mode="html",
                )

        except Exception as e:

            clear_caption_session()

            await event.reply(
                "❌ <b>Could not resolve the END link.</b>\n\n"
                f"<code>{str(e)[:1000]}</code>",
                parse_mode="html",
            )

        return


@client.on(events.NewMessage(pattern=r"^/setcaption$", outgoing=True))
async def setcaption_command_handler(event):
    """Ask the user to send the caption template to save."""
    if not session.is_authorized(event.sender_id):
        await event.reply("❌ Unauthorized")
        return

    # /setcaption can be used standalone or in the middle of a /caption flow.
    if caption_session["user_id"] not in (None, event.sender_id):
        await event.reply("❌ Another caption operation is in progress. Use `/cancel` first.")
        return

    caption_session["user_id"] = event.sender_id
    caption_session["waiting_for"] = "template"
    # Preserve whatever range/resume state already existed (set by /caption).

    await event.reply(
    "📝 Send the caption template you want to save.\n\n"
    "HTML formatting (`<b>`, `<i>`, `<u>`, `<code>`, `<s>`, etc.) is supported "
    "and will be preserved.\n\n"
    "Available placeholders:\n"
    "`{title}` `{episode}` `{season}` `{season_episode}` `{bölüm}` "
    "`{quality}` `{lang}` `{source}` `{year}` `{sub}` `{finale}`\n\n"
    "`{finale}` automatically becomes `Season Finale` on the final "
    "episode of a season. Otherwise it remains empty.\n\n"
    "Use `/cancel` to abort."
)


@client.on(events.NewMessage(outgoing=True))
async def caption_template_capture_handler(event):
    """
    Capture the next outgoing message as the caption template.

    HTML formatting behavior:

    1. If the user sends literal HTML:
           <a href="https://example.com">Title</a>

       the HTML is preserved EXACTLY as typed.

    2. If the user uses Telegram's native formatting:
           Bold / Italic / Underline / URL / etc.

       Telethon entities are converted into Telegram-compatible HTML.

    3. Placeholders such as:
           {season}
           {episode}
           {season_episode}
           {bolum}
           {quality}
           {title}

       remain untouched.

    IMPORTANT:
    Literal HTML takes priority over Telegram entities.

    This prevents URLs inside an HTML <a href="..."> tag from
    being converted into nested <a> tags.
    """

    if not session.is_authorized(
        event.sender_id
    ):
        return

    if caption_session.get(
        "waiting_for"
    ) != "template":
        return

    if caption_session.get(
        "user_id"
    ) != event.sender_id:
        return

    # ========================================================
    # RAW MESSAGE TEXT
    # ========================================================

    text = (
        event.raw_text or ""
    )

    # Never swallow a command as the template.
    if text.strip().startswith("/"):
        return

    if not text.strip():
        await event.reply(
            "❌ <b>The caption template cannot be empty.</b>\n\n"
            "Please send it again.",
            parse_mode="html"
        )
        return

    # ========================================================
    # DETECT LITERAL HTML
    # ========================================================
    #
    # If the user has explicitly typed HTML tags, we MUST
    # preserve the raw message exactly.
    #
    # This is important because Telegram may still attach
    # entities to URLs contained inside those literal tags.
    #
    # Example:
    #
    # <a href="https://t.me/example">Title</a>
    #
    # Telegram may report the URL as MessageEntityTextUrl.
    #
    # We must NOT pass that through html.unparse(), otherwise
    # the URL can become:
    #
    # <a href="<a href="https://...">...</a>">
    #
    # which breaks the template.
    # ========================================================

    import re

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
        re.IGNORECASE
    )

    contains_literal_html = bool(
        html_tag_pattern.search(text)
    )

    # ========================================================
    # TELEGRAM FORMATTING -> HTML
    # ========================================================

    if contains_literal_html:

        # ----------------------------------------------------
        # CASE 1:
        # User explicitly supplied HTML.
        #
        # Preserve it EXACTLY.
        # ----------------------------------------------------

        saved_text = text

    else:

        # ----------------------------------------------------
        # CASE 2:
        # No literal HTML detected.
        #
        # Convert Telegram's native formatting entities
        # into HTML.
        # ----------------------------------------------------

        entities = (
            getattr(event, "entities", None)
            or []
        )

        if entities:

            try:
                from telethon.extensions import html as telegram_html

                saved_text = telegram_html.unparse(
                    text,
                    entities
                )

            except Exception:

                # If conversion fails, keep the original
                # text rather than losing the template.
                saved_text = text

        else:

            # Plain text template.
            saved_text = text

    # ========================================================
    # SAVE
    # ========================================================

    save_caption_format(
        event.sender_id,
        saved_text
    )

    resume = caption_session.get(
        "resume_after_template"
    )

    # ========================================================
    # ACTIVE /CAPTION OPERATION
    # ========================================================

    if resume:

        caption_session[
            "waiting_for"
        ] = None

        caption_session[
            "resume_after_template"
        ] = None

        await event.reply(
            "✅ <b>Caption format saved.</b>\n\n"
            "🔎 Preparing the caption preview...",
            parse_mode="html"
        )

        await prepare_caption_preview(
            event,
            saved_text
        )

        return

    # ========================================================
    # STANDALONE /SETCAPTION
    # ========================================================

    caption_session[
        "waiting_for"
    ] = None

    caption_session[
        "resume_after_template"
    ] = None

    await event.reply(
        "✅ <b>Caption format saved successfully.</b>\n\n"
        "The format is now stored and can be used with "
        "<code>/caption</code>.",
        parse_mode="html"
    )


@client.on(events.NewMessage(pattern=r"^/setseason\s+(\d{1,2})\s+(\d{1,4})$", outgoing=True))
async def setseason_command_handler(event):
    """
    /setseason <season> <startep>

    Meaning: the given GLOBAL Bölüm number is where that season begins.
    The global Bölüm number never resets across seasons.
    """
    if not session.is_authorized(event.sender_id):
        await event.reply("❌ Unauthorized")
        return

    try:
        season = int(event.pattern_match.group(1))
        start_ep = int(event.pattern_match.group(2))
    except (TypeError, ValueError):
        await event.reply("❌ Usage: `/setseason <season> <startep>`\nExample: `/setseason 3 50`")
        return

    if season <= 0 or start_ep <= 0:
        await event.reply("❌ Season and starting Bölüm must both be positive numbers.")
        return

    save_season_mapping(event.sender_id, season, start_ep)

    await event.reply(
        "✅ **Season mapping saved.**\n\n"
        f"Global Bölüm `{start_ep}` = Season `{season}` Episode `01`\n\n"
        "The global Bölüm number will keep counting continuously from here."
    )


@client.on(events.NewMessage(pattern=r"^/setseason$", outgoing=True))
async def setseason_usage_handler(event):
    """Show usage when /setseason is sent without arguments."""
    if not session.is_authorized(event.sender_id):
        await event.reply("❌ Unauthorized")
        return

    await event.reply("❌ Usage: `/setseason <season> <startep>`\nExample: `/setseason 3 50`")


async def run_caption_confirm(event):
    """
    Handle /confirm for a pending /caption operation.

    /confirm is the FINAL destructive step.

    The preview must already have been generated before
    this function is allowed to edit anything.
    """

    if caption_session.get(
        "user_id"
    ) != event.sender_id:
        return False

    if caption_session.get(
        "waiting_for"
    ) != "format_choice":
        return False

    start_id = caption_session.get(
        "start_id"
    )

    end_id = caption_session.get(
        "end_id"
    )

    channel = caption_session.get(
        "channel"
    )

    preview_items = caption_session.get(
        "preview_items"
    )

    if not (
        start_id
        and end_id
        and channel
    ):
        return False

    # ========================================================
    # MAKE SURE PREVIEW EXISTS
    # ========================================================

    if not preview_items:

        template_text = (
            get_saved_caption_format(
                event.sender_id
            )
        )

        if not template_text:

            await event.reply(
                "📝 <b>No caption format is saved.</b>\n\n"
                "Please use <code>/setcaption</code> first.",
                parse_mode="html",
            )

            caption_session[
                "waiting_for"
            ] = "template"

            caption_session[
                "resume_after_template"
            ] = True

            return True

        await event.reply(
            "🔎 <b>Preview is not ready.</b>\n\n"
            "Preparing it now. Nothing will be edited yet.",
            parse_mode="html",
        )

        await prepare_caption_preview(
            event,
            template_text,
        )

        return True

    # ========================================================
    # FORMAT
    # ========================================================

    template_text = (
        get_saved_caption_format(
            event.sender_id
        )
    )

    if not template_text:

        await event.reply(
            "📝 <b>No caption format is saved.</b>\n\n"
            "Please use <code>/setcaption</code>.",
            parse_mode="html",
        )

        caption_session[
            "waiting_for"
        ] = "template"

        caption_session[
            "resume_after_template"
        ] = True

        return True

    # ========================================================
    # FINAL CONFIRMATION
    # ========================================================

    status_msg = await event.reply(
        "🔄 <b>Updating captions...</b>\n\n"
        "The preview was already generated.\n"
        "Applying the selected format now.\n\n"
        "Videos are not downloaded or re-uploaded.",
        parse_mode="html",
    )

    user_id = event.sender_id

    result = await process_caption_range(
        channel=channel,
        start_id=start_id,
        end_id=end_id,
        user_id=user_id,
        template_text=template_text,
        status_msg=status_msg,
    )

    completion = (
        "✅ <b>CAPTION UPDATE COMPLETED</b>\n\n"
        f"📌 Range: "
        f"<code>{start_id}</code>"
        f" → "
        f"<code>{end_id}</code>\n\n"
        f"📦 Processed: "
        f"<code>{result['total']}</code>\n"
        f"🎞️ Videos updated: "
        f"<code>{result['video_count']}</code>\n"
        f"⏭️ Skipped: "
        f"<code>{result['skipped_count']}</code>\n"
        f"❌ Failed: "
        f"<code>{result['failed_count']}</code>"
    )

    if result["failed_messages"]:

        completion += (
            "\n\n❌ <b>Failed messages:</b>\n"
        )

        for (
            msg_id,
            reason,
        ) in result[
            "failed_messages"
        ][:10]:

            safe_reason = (
                str(reason)
                .replace("\n", " ")
                [:150]
            )

            completion += (
                f"• <code>{msg_id}</code> — "
                f"{safe_reason}\n"
            )

        remaining = (
            len(
                result[
                    "failed_messages"
                ]
            )
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

    clear_caption_session()

    return True

# ==================== END CAPTION FEATURE COMMANDS (PHASE 1) ====================

# ==================== SAMPLE COMMAND HANDLER ====================
@client.on(events.NewMessage(pattern='/sample', outgoing=True))
async def sample_command_handler(event):
    """Test replacement on up to 3 sample messages without actually editing"""
    if not session.is_authorized(event.sender_id):
        await event.reply("❌ Unauthorized")
        return
    
    # Check if we have search results from previous /search
    if not session.matched_messages:
        await event.reply("❌ No matches found. Run `/search` first to find matching messages.")
        return
    
    # Get up to 3 samples
    sample_count = min(3, len(session.matched_messages))
    samples = session.matched_messages[:sample_count]
    
    await event.reply(f"📝 **Generating {sample_count} sample preview(s)...**\n\nPlease wait.")
    
    result_message = f"🎯 **SAMPLE PREVIEW** (Testing replacement on {sample_count} message(s))\n\n"
    result_message += f"**Search Text:**\n`{SEARCH_TEXT}`\n\n"
    result_message += f"**Replace With:**\n{REPLACEMENT_TEXT}\n\n"
    result_message += "━" * 30 + "\n\n"
    
    success_count = 0
    failed_count = 0
    
    for idx, (msg_id, original_text) in enumerate(samples, 1):
        try:
            # Get the full message
            message = await client.get_messages(CHANNEL_ID, ids=msg_id)
            if not message:
                result_message += f"❌ **Sample {idx}** (ID: {msg_id}): Message not found\n\n"
                failed_count += 1
                continue
            
            original_full = message.text
            
            # Perform replacement (SIMULATION ONLY - NOT ACTUALLY EDITING)
            new_text = original_full.replace(SEARCH_TEXT, REPLACEMENT_TEXT)
            
            # Show before and after
            result_message += f"📌 **Sample {idx}** (Message ID: `{msg_id}`)\n\n"
            result_message += f"**🔴 BEFORE:**\n```\n{truncate_text(original_full, 150)}\n```\n\n"
            result_message += f"**🟢 AFTER:**\n```\n{truncate_text(new_text, 150)}\n```\n\n"
            
            # Highlight what changed
            if SEARCH_TEXT in original_full:
                result_message += f"✅ **Will replace:** `{truncate_text(SEARCH_TEXT, 50)}`\n\n"
            
            result_message += "━" * 30 + "\n\n"
            success_count += 1
            
        except Exception as e:
            result_message += f"❌ **Sample {idx}** (ID: {msg_id}): Error - {str(e)}\n\n"
            failed_count += 1
    
    # Add summary
    result_message += f"📊 **Sample Summary:**\n"
    result_message += f"✅ Successful previews: `{success_count}`\n"
    result_message += f"❌ Failed: `{failed_count}`\n\n"
    
    result_message += f"⚠️ **Note:** This is ONLY a preview. No messages were actually edited.\n\n"
    result_message += f"**Next Steps:**\n"
    result_message += f"• Type `/replace` to start actual replacement\n"
    result_message += f"• Type `/confirm` to execute on all {len(session.matched_messages)} messages\n"
    result_message += f"• Type `/cancel` to abort"
    
    await event.reply(result_message)

# Optional: Add a sample with custom count (e.g., /sample 2)
@client.on(events.NewMessage(pattern=r'/sample\s+(\d+)', outgoing=True))
async def sample_with_count_handler(event):
    """Test replacement on specific number of samples (max 5)"""
    if not session.is_authorized(event.sender_id):
        await event.reply("❌ Unauthorized")
        return
    
    # Get the number from command
    try:
        requested_count = int(event.pattern_match.group(1))
        sample_count = min(requested_count, 5)  # Max 5 samples
        if requested_count > 5:
            await event.reply(f"⚠️ Maximum 5 samples allowed. Showing {sample_count} samples instead.")
    except:
        sample_count = 3
    
    # Check if we have search results
    if not session.matched_messages:
        await event.reply("❌ No matches found. Run `/search` first to find matching messages.")
        return
    
    # Get samples
    sample_count = min(sample_count, len(session.matched_messages))
    samples = session.matched_messages[:sample_count]
    
    await event.reply(f"📝 **Generating {sample_count} sample preview(s)...**\n\nPlease wait.")
    
    result_message = f"🎯 **SAMPLE PREVIEW** (Testing replacement on {sample_count} message(s))\n\n"
    result_message += f"**Search Text:**\n`{SEARCH_TEXT}`\n\n"
    result_message += f"**Replace With:**\n{REPLACEMENT_TEXT}\n\n"
    result_message += "━" * 30 + "\n\n"
    
    for idx, (msg_id, original_text) in enumerate(samples, 1):
        try:
            message = await client.get_messages(CHANNEL_ID, ids=msg_id)
            if not message:
                result_message += f"❌ **Sample {idx}** (ID: {msg_id}): Message not found\n\n"
                continue
            
            original_full = message.text
            new_text = original_full.replace(SEARCH_TEXT, REPLACEMENT_TEXT)
            
            result_message += f"📌 **Sample {idx}** (Message ID: `{msg_id}`)\n\n"
            result_message += f"**🔴 BEFORE:**\n```\n{truncate_text(original_full, 200)}\n```\n\n"
            result_message += f"**🟢 AFTER:**\n```\n{truncate_text(new_text, 200)}\n```\n\n"
            result_message += "━" * 30 + "\n\n"
            
        except Exception as e:
            result_message += f"❌ **Sample {idx}** (ID: {msg_id}): Error - {str(e)}\n\n"
    
    result_message += f"⚠️ **Remember:** This is ONLY a preview. No messages were actually edited.\n\n"
    result_message += f"Type `/replace` then `/confirm` to edit all {len(session.matched_messages)} messages."
    
    await event.reply(result_message)
    
@client.on(events.NewMessage(pattern='/start', outgoing=True))
async def start_command_handler(event):
    """Handle /start command"""
    if not session.is_authorized(event.sender_id):
        await event.reply("❌ You are not authorized to use this bot.")
        return
    
    await event.reply(
        "🤖 **Channel Text Replacement Bot**\n\n"
        f"**Search Text:**\n`{SEARCH_TEXT}`\n\n"
        f"**Replace With:**\n{REPLACEMENT_TEXT}\n\n"
        "**Commands:**\n"
        "🔍 `/search` - Find matching messages\n"
        "✏️ `/replace` - Replace all matches\n"
        "📊 `/stats` - Show match count only\n"
        "❌ `/cancel` - Cancel operation\n"
        "📝 `/caption` - Change captions of existing videos\n"
        "🔧 `/setcaption` - Save a caption template\n"
        "🔢 `/setseason <season> <startep>` - Configure season mapping"
    )

@client.on(events.NewMessage(pattern='/stats', outgoing=True))
async def stats_command_handler(event):
    """Show how many messages will be affected"""
    if not session.is_authorized(event.sender_id):
        await event.reply("❌ Unauthorized")
        return
    
    await event.reply("🔎 Counting matching messages... Please wait.")
    
    messages = await get_channel_messages()
    
    matches = []
    for msg in messages:
        if SEARCH_TEXT in msg.text:
            matches.append((msg.id, msg.text))
    
    if matches:
        preview = truncate_text(matches[0][1], 300)
        await event.reply(
            f"📊 **Statistics**\n\n"
            f"**Search Text:**\n`{SEARCH_TEXT}`\n\n"
            f"**Total matches found:** `{len(matches)}`\n\n"
            f"**Sample Match:**\n━━━━━━━━━━━━━━━━━━━\n{preview}\n━━━━━━━━━━━━━━━━━━━\n\n"
            f"Type `/replace` to replace all {len(matches)} matches."
        )
    else:
        await event.reply("❌ No matching messages found.")

# ==================== FIXED SEARCH COMMAND HANDLER (Regex-Safe) ====================
@client.on(events.NewMessage(pattern='/search', outgoing=True))
async def search_command_handler(event):
    """Search for NON-hyperlinked text only - Regex safe version"""
    if not session.is_authorized(event.sender_id):
        await event.reply("❌ Unauthorized")
        return
    
    session.clear()
    session.user_id = event.sender_id
    
    await event.reply("🔍 Searching for NON-hyperlinked text only...\n\n"
                     f"**Searching for:** `{SEARCH_TEXT}`\n\n"
                     "⚠️ Will ONLY match plain text (not already hyperlinked)")
    
    messages = await get_channel_messages()
    
    # Escape special regex characters in search text
    escaped_search_text = re.escape(SEARCH_TEXT)
    
    # Find all matching messages that are NOT already hyperlinked
    session.matched_messages = []
    
    for msg in messages:
        if not msg.text:
            continue
            
        if SEARCH_TEXT in msg.text:
            # Check if the text is already hyperlinked using simple string methods (no regex)
            text = msg.text
            text_pos = text.find(SEARCH_TEXT)
            
            is_hyperlinked = False
            
            # Check if text is inside markdown link [text](url)
            if text_pos > 0:
                # Look backwards for '[' and forward for ']('
                before_start = max(0, text_pos - 100)
                before_text = text[before_start:text_pos]
                last_bracket = before_text.rfind('[')
                after_text = text[text_pos:text_pos + len(SEARCH_TEXT) + 50]
                
                if last_bracket != -1:
                    # Check if after the search text there is ']('
                    if '](' in after_text:
                        is_hyperlinked = True
                
                # Check for HTML link
                if not is_hyperlinked:
                    # Look for <a href= before and </a> after
                    if '<a href=' in before_text and '</a>' in after_text:
                        is_hyperlinked = True
                
                # Check for surrounding brackets indicating link
                if not is_hyperlinked:
                    if before_text.endswith('[') and after_text.startswith(']('):
                        is_hyperlinked = True
            
            # Also check if the text is inside markdown link using simple pattern
            if not is_hyperlinked:
                # Simple pattern check using string methods
                link_pattern = f'[{SEARCH_TEXT}](http'
                if link_pattern in text:
                    is_hyperlinked = True
                elif f'<a href=' in text and SEARCH_TEXT in text and '</a>' in text:
                    # Check if the search text is between <a> and </a>
                    a_pos = text.find('<a href=')
                    if a_pos != -1:
                        close_a_pos = text.find('</a>', a_pos)
                        if close_a_pos != -1 and a_pos < text_pos < close_a_pos:
                            is_hyperlinked = True
            
            if not is_hyperlinked:
                session.matched_messages.append((msg.id, msg.text))
            else:
                print(f"⏭️ Skipping already hyperlinked message {msg.id}")
    
    total_matches = len(session.matched_messages)
    
    if total_matches == 0:
        await event.reply(
            "❌ **No non-hyperlinked matches found**\n\n"
            f"**Search Text:** `{SEARCH_TEXT}`\n\n"
            "All matching messages may already have hyperlinks.\n"
            f"Run `/repair` to check and fix double hyperlinks."
        )
        return
    
    # Prepare sample preview
    sample_text = truncate_text(session.matched_messages[0][1], 300)
    
    result_message = (
        f"📊 **Search Results** (Plain text only, no hyperlinks)\n\n"
        f"**Search Text:**\n`{SEARCH_TEXT}`\n\n"
        f"**Total plain text matches found:** `{total_matches}`\n\n"
        f"**Sample Match:**\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"{sample_text}\n"
        f"━━━━━━━━━━━━━━━━━━━\n\n"
        f"⚠️ This will edit {total_matches} message(s).\n\n"
        f"**Type `/replace` to start replacement.**\n"
        f"**Type `/cancel` to abort.**"
    )
    
    await event.reply(result_message)
    session.is_running = True
    
# ==================== FINAL REPAIR COMMAND - Remove brackets & keep text only ====================
@client.on(events.NewMessage(pattern='/repair', outgoing=True))
async def repair_command_handler(event):
    """Remove markdown link brackets and keep only the text"""
    if not session.is_authorized(event.sender_id):
        await event.reply("❌ Unauthorized")
        return
    
    await event.reply("🔧 **Repair Mode - Removing Brackets...**\n\n"
                     "Converting:\n"
                     "• `[Text](url)` → `Text`\n"
                     "• Removing raw URLs\n\n"
                     "Please wait...")
    
    messages = await get_channel_messages()
    
    damaged_messages = []
    
    for msg in messages:
        if not msg.text:
            continue
        
        text = msg.text
        original_text = text
        fixed_text = text
        
        # Pattern 1: Remove markdown link [Text](url) and keep only the Text
        import re
        # Find all markdown links and replace with just the text inside brackets
        fixed_text = re.sub(r'\[([^\]]+)\]\([^\)]+\)', r'\1', fixed_text)
        
        # Pattern 2: Remove any remaining raw URLs
        fixed_text = re.sub(r'https?://t\.me/[\w]+', '', fixed_text)
        
        # Pattern 3: Clean up extra blank lines
        fixed_text = re.sub(r'\n\s*\n\s*\n', '\n\n', fixed_text)
        fixed_text = fixed_text.strip()
        
        if fixed_text != original_text:
            damaged_messages.append({
                'id': msg.id,
                'original': original_text,
                'fixed': fixed_text
            })
            print(f"🔧 Found bracketed link in message {msg.id}")
    
    if not damaged_messages:
        await event.reply("✅ **No issues found!**\n\nAll messages are clean.")
        return
    
    # Store for repair confirmation
    session.repair_messages = damaged_messages
    
    # Show preview
    preview = f"🔧 **Found {len(damaged_messages)} messages with brackets to remove**\n\n"
    preview += f"**Fixing:** `[Text](url)` → `Text`\n\n"
    
    for idx, item in enumerate(damaged_messages[:3], 1):
        preview += f"**Message {idx}** (ID: `{item['id']}`)\n\n"
        preview += f"🔴 **BEFORE:**\n```\n{truncate_text(item['original'], 200)}\n```\n\n"
        preview += f"🟢 **AFTER FIX:**\n```\n{truncate_text(item['fixed'], 200)}\n```\n\n"
        preview += "━" * 30 + "\n\n"
    
    preview += f"⚠️ This will repair {len(damaged_messages)} message(s).\n\n"
    preview += f"**Type `/confirm_repair` to fix all.**\n"
    preview += f"**Type `/cancel` to abort.**"
    
    await event.reply(preview)


# ==================== FINAL CONFIRM REPAIR ====================
@client.on(events.NewMessage(pattern='/confirm_repair', outgoing=True))
async def confirm_repair_handler(event):
    """Execute repair - remove brackets and URLs"""
    if not session.is_authorized(event.sender_id):
        await event.reply("❌ Unauthorized")
        return
    
    if not hasattr(session, 'repair_messages') or not session.repair_messages:
        await event.reply("❌ No repair operation pending. Run `/repair` first.")
        return
    
    status_msg = await event.reply("🔄 **Removing brackets and cleaning captions...**\n\nPlease wait.")
    
    success_count = 0
    failed_count = 0
    total = len(session.repair_messages)
    
    for idx, item in enumerate(session.repair_messages, 1):
        try:
            # Edit with fixed text (plain text, no markdown)
            await client.edit_message(CHANNEL_ID, item['id'], item['fixed'])
            success_count += 1
            
            if idx % 10 == 0 or idx == total:
                await status_msg.edit(f"🔄 **Cleaning...**\n\n"
                                     f"Progress: `{idx}/{total}`\n"
                                     f"✅ Fixed: `{success_count}`\n"
                                     f"❌ Failed: `{failed_count}`")
            
            await asyncio.sleep(0.3)
            
        except FloodWaitError as e:
            await asyncio.sleep(e.seconds)
            try:
                await client.edit_message(CHANNEL_ID, item['id'], item['fixed'])
                success_count += 1
            except:
                failed_count += 1
        except Exception as e:
            failed_count += 1
            print(f"Error: {e}")
    
    completion_message = (
        f"✅ **Repair Completed!**\n\n"
        f"📊 **Final Statistics:**\n"
        f"• Total messages: `{total}`\n"
        f"• Successfully cleaned: `{success_count}`\n"
        f"• Failed: `{failed_count}`\n\n"
        f"🔧 Removed `[ ]` brackets, `( )` parentheses, and raw URLs.\n"
        f"📝 Kept only the plain text."
    )
    
    await status_msg.edit(completion_message)
    session.repair_messages = []


# ==================== ONE-CLICK CLEAN ====================
@client.on(events.NewMessage(pattern='/clean', outgoing=True))
async def clean_command_handler(event):
    """One-click fix - remove all brackets and keep text only"""
    if not session.is_authorized(event.sender_id):
        await event.reply("❌ Unauthorized")
        return
    
    await event.reply("🧹 **Quick Clean Mode**\n\nRemoving brackets from all messages...")
    
    messages = await get_channel_messages()
    import re
    fixed_count = 0
    
    for msg in messages:
        if not msg.text:
            continue
        
        text = msg.text
        # Remove markdown links [Text](url) → Text
        fixed_text = re.sub(r'\[([^\]]+)\]\([^\)]+\)', r'\1', text)
        # Remove raw URLs
        fixed_text = re.sub(r'https?://t\.me/[\w]+', '', fixed_text)
        # Clean up extra blank lines
        fixed_text = re.sub(r'\n\s*\n\s*\n', '\n\n', fixed_text)
        fixed_text = fixed_text.strip()
        
        if fixed_text != text:
            try:
                await client.edit_message(CHANNEL_ID, msg.id, fixed_text)
                fixed_count += 1
                await asyncio.sleep(0.3)
            except Exception as e:
                print(f"Error fixing {msg.id}: {e}")
    
    await event.reply(f"✅ **Clean Completed!**\n\n"
                     f"📊 Fixed `{fixed_count}` messages.\n"
                     f"🔧 Removed all `[ ]` brackets, `( )` parentheses, and URLs.\n"
                     f"📝 Kept only the plain text.")
    
@client.on(events.NewMessage(pattern='/replace', outgoing=True))
async def replace_command_handler(event):
    """Execute the replacement"""
    if not session.is_authorized(event.sender_id):
        await event.reply("❌ Unauthorized")
        return
    
    if not session.matched_messages:
        await event.reply("❌ No matches found. Run `/search` first.")
        return
    
    if not session.is_running:
        await event.reply("❌ No active session. Run `/search` first.")
        return
    
    # Show preview and ask for confirmation
    preview_message = (
        f"📝 **Ready to Replace**\n\n"
        f"**Search Text:**\n`{SEARCH_TEXT}`\n\n"
        f"**Replace With:**\n{REPLACEMENT_TEXT}\n\n"
        f"**Matches Found:** `{len(session.matched_messages)}`\n\n"
        f"⚠️ **This action cannot be undone!**\n\n"
        f"**Type `/confirm` to proceed.**\n"
        f"**Type `/cancel` to abort.**"
    )
    
    await event.reply(preview_message)

@client.on(events.NewMessage(pattern='/confirm', outgoing=True))
async def confirm_command_handler(event):
    """Confirm and execute replacement"""
    if not session.is_authorized(event.sender_id):
        await event.reply("❌ Unauthorized")
        return

    # If a /caption operation is pending confirmation, handle that and
    # don't fall through to the text search & replace confirmation below.
    handled = await run_caption_confirm(event)
    if handled:
        return

    if not session.matched_messages or not session.is_running:
        await event.reply("❌ No active session. Run `/search` first.")
        return
    
    await event.reply("🔄 **Processing replacements...**\n\nPlease wait, this may take a while.")
    
    success_count = 0
    failed_count = 0
    failed_messages = []
    
    total = len(session.matched_messages)
    
    # Loop through all matched messages
    for idx, (msg_id, original_text) in enumerate(session.matched_messages, 1):
        try:
            # Get the message object
            message = await client.get_messages(CHANNEL_ID, ids=msg_id)
            if not message:
                failed_count += 1
                failed_messages.append((msg_id, "Message not found"))
                continue
            
            # Edit the message
            success, error_msg = await edit_message_with_replacement(
                message, SEARCH_TEXT, REPLACEMENT_TEXT
            )
            
            if success:
                success_count += 1
                print(f"✅ Edited message {msg_id} ({idx}/{total})")
            else:
                failed_count += 1
                failed_messages.append((msg_id, error_msg))
                print(f"❌ Failed message {msg_id}: {error_msg}")
            
            # Progress update every 10 messages
            if idx % 10 == 0:
                await event.reply(f"📊 Progress: {idx}/{total}\n✅ Success: {success_count}\n❌ Failed: {failed_count}")
            
            # Small delay to avoid rate limits
            await asyncio.sleep(0.5)
            
        except FloodWaitError as e:
            print(f"⏳ Flood wait: {e.seconds} seconds")
            await asyncio.sleep(e.seconds)
            # Retry the failed message
            try:
                message = await client.get_messages(CHANNEL_ID, ids=msg_id)
                if message:
                    success, error_msg = await edit_message_with_replacement(
                        message, SEARCH_TEXT, REPLACEMENT_TEXT
                    )
                    if success:
                        success_count += 1
                        failed_count -= 1
            except Exception:
                pass
        except Exception as e:
            failed_count += 1
            failed_messages.append((msg_id, str(e)))
            print(f"❌ Error on message {msg_id}: {e}")
    
    # Show completion results
    completion_message = (
        f"✅ **Operation Completed!**\n\n"
        f"📊 **Final Statistics:**\n"
        f"• Total messages: `{total}`\n"
        f"• Successfully edited: `{success_count}`\n"
        f"• Failed: `{failed_count}`\n\n"
    )
    
    if failed_messages and len(failed_messages) <= 10:
        completion_message += "**Failed messages:**\n"
        for msg_id, error in failed_messages[:5]:
            completion_message += f"• ID {msg_id}: {error[:50]}\n"
        if len(failed_messages) > 5:
            completion_message += f"• ... and {len(failed_messages) - 5} more\n"
    
    await event.reply(completion_message)
    
    # Clear session
    session.clear()
    print("✅ Replacement operation completed!")

@client.on(events.NewMessage(pattern='/cancel', outgoing=True))
async def cancel_command_handler(event):
    """Cancel current operation"""
    if not session.is_authorized(event.sender_id):
        await event.reply("❌ Unauthorized")
        return
    
    session.clear()
    clear_caption_session()
    await event.reply("✅ Operation cancelled. All active sessions cleared.")
# ==================== FOOTALL COMMAND HANDLER ====================
@client.on(events.NewMessage(pattern='/footall', outgoing=True))
async def football_command_handler(event):
    """Add footer to all media messages without existing footer"""
    
    # Step 1: Verify sender is OWNER_ID
    if not session.is_authorized(event.sender_id):
        await event.reply("❌ You are not authorized to use this command.")
        return
    
    FOOTER_TEXT = '<a href="https://t.me/UrduSubtitlesMain">@UrduSubtitlesMain</a>'
    FOOTER_CHECK = "@UrduSubtitlesMain"
    
    status_msg = await event.reply("🔍 **Scanning channel messages...**\n\nPlease wait, this may take a moment.")
    
    matches = []
    total_checked = 0
    skipped_no_media = 0
    skipped_has_footer = 0
    
    # Step 2 & 3: Iterate through channel messages
    try:
        async for msg in client.iter_messages(CHANNEL_ID):
            total_checked += 1
            
            # Skip if no media
            if not msg.media:
                skipped_no_media += 1
                continue
            
            # Skip if no text/caption
            if not msg.raw_text:
                skipped_no_media += 1
                continue
            
            # Step 4: Skip if footer already exists
            if FOOTER_CHECK in msg.raw_text:
                skipped_has_footer += 1
                continue
            
            # Store matching message
            matches.append({
                'id': msg.id,
                'original_caption': msg.raw_text,
                'media_type': type(msg.media).__name__
            })
            
            # Progress update every 100 messages
            if len(matches) % 100 == 0:
                await status_msg.edit(f"🔍 **Scanning...**\n\n"
                                     f"📊 Checked: {total_checked}\n"
                                     f"✅ Matches found: {len(matches)}\n"
                                     f"⏭️ No media: {skipped_no_media}\n"
                                     f"⏭️ Has footer: {skipped_has_footer}")
            
            # Small delay to avoid rate limits
            await asyncio.sleep(0.1)
            
    except Exception as e:
        await event.reply(f"❌ Error while scanning: {str(e)}")
        return
    
    total_matches = len(matches)
    
    # Step 7: Show results
    if total_matches == 0:
        await status_msg.edit(
            "❌ **No messages found to update**\n\n"
            f"📊 **Scan Results:**\n"
            f"• Total messages checked: `{total_checked}`\n"
            f"• Messages with media: `{total_checked - skipped_no_media}`\n"
            f"• Already have footer: `{skipped_has_footer}`\n"
            f"• No media/empty: `{skipped_no_media}`\n\n"
            f"✅ All messages already have the footer or no media found."
        )
        return
    
    # Store matches in session for confirmation
    session.matched_messages = [(m['id'], m['original_caption']) for m in matches]
    session.footer_operation = True
    session.search_text = None
    session.replacement_text = FOOTER_TEXT
    
    # Prepare sample preview (first match)
    sample = matches[0]
    original_caption = sample['original_caption']
    
    # Build new caption preview
    new_caption_preview = original_caption.rstrip() + "\n\n" + FOOTER_TEXT
    
    # Show preview
    preview_message = (
        f"📝 **Footer Addition Preview**\n\n"
        f"📊 **Total messages to update:** `{total_matches}`\n\n"
        f"**Media type example:** `{sample['media_type']}`\n\n"
        f"**🔴 BEFORE (Original caption):**\n"
        f"```\n{truncate_text(original_caption, 200)}\n```\n\n"
        f"**🟢 AFTER (With footer):**\n"
        f"```\n{truncate_text(new_caption_preview, 200)}\n```\n\n"
        f"**Footer to add:**\n{FOOTER_TEXT}\n\n"
        f"⚠️ **This will add footer to {total_matches} messages!**\n"
        f"Messages without media or with existing footer will be skipped.\n\n"
        f"**Type `/confirm_footall` to proceed.**\n"
        f"**Type `/cancel` to abort.**"
    )
    
    await status_msg.edit(preview_message)

# ==================== CONFIRM FOOTALL COMMAND ====================
@client.on(events.NewMessage(pattern='/confirm_footall', outgoing=True))
async def confirm_footall_handler(event):
    """Execute footer addition to all matched messages"""
    
    if not session.is_authorized(event.sender_id):
        await event.reply("❌ Unauthorized")
        return
    
    if not hasattr(session, 'footer_operation') or not session.footer_operation:
        await event.reply("❌ No footer operation pending. Run `/footall` first.")
        return
    
    if not session.matched_messages:
        await event.reply("❌ No messages to update. Run `/footall` first.")
        return
    
    FOOTER_TEXT = '<a href="https://t.me/UrduSubtitlesMain">@UrduSubtitlesMain</a>'
    
    status_msg = await event.reply("🔄 **Adding footers...**\n\nPlease wait, this may take a while.")
    
    success_count = 0
    failed_count = 0
    failed_messages = []
    
    total = len(session.matched_messages)
    
    # Step 8: Loop through all matched messages
    for idx, (msg_id, original_caption) in enumerate(session.matched_messages, 1):
        try:
            # Fetch the message
            message = await client.get_messages(CHANNEL_ID, ids=msg_id)
            
            if not message:
                failed_count += 1
                failed_messages.append((msg_id, "Message not found"))
                continue
            
            # Check again if footer already exists (double-check)
            if "@UrduSubtitlesMain" in message.raw_text:
                failed_count += 1
                failed_messages.append((msg_id, "Footer already exists (changed during process)"))
                continue
            
            # Build new caption with footer
            new_caption = message.raw_text.rstrip() + "\n\n" + FOOTER_TEXT
            
            # Edit the message with HTML parsing
            try:
                await client.edit_message(CHANNEL_ID, msg_id, new_caption, parse_mode='html')
                success_count += 1
                
                # Progress update every 10 messages
                if idx % 10 == 0:
                    await status_msg.edit(f"🔄 **Adding footers...**\n\n"
                                         f"📊 Progress: `{idx}/{total}`\n"
                                         f"✅ Success: `{success_count}`\n"
                                         f"❌ Failed: `{failed_count}`")
                
                # Small delay to avoid rate limits
                await asyncio.sleep(0.5)
                
            except FloodWaitError as e:
                print(f"⏳ Flood wait: {e.seconds} seconds")
                await asyncio.sleep(e.seconds)
                # Retry once
                try:
                    await client.edit_message(CHANNEL_ID, msg_id, new_caption, parse_mode='html')
                    success_count += 1
                except Exception as retry_error:
                    failed_count += 1
                    failed_messages.append((msg_id, f"Flood retry failed: {str(retry_error)}"))
                    
            except Exception as e:
                failed_count += 1
                failed_messages.append((msg_id, str(e)))
                
        except FloodWaitError as e:
            print(f"⏳ Flood wait outer: {e.seconds} seconds")
            await asyncio.sleep(e.seconds)
            # Retry the message
            try:
                message = await client.get_messages(CHANNEL_ID, ids=msg_id)
                if message and "@UrduSubtitlesMain" not in message.raw_text:
                    new_caption = message.raw_text.rstrip() + "\n\n" + FOOTER_TEXT
                    await client.edit_message(CHANNEL_ID, msg_id, new_caption, parse_mode='html')
                    success_count += 1
                else:
                    failed_count += 1
                    failed_messages.append((msg_id, "Retry failed"))
            except Exception as retry_error:
                failed_count += 1
                failed_messages.append((msg_id, str(retry_error)))
                
        except Exception as e:
            failed_count += 1
            failed_messages.append((msg_id, str(e)))
    
    # Step 9: Report completion
    completion_message = (
        f"✅ **Footer Addition Completed!**\n\n"
        f"📊 **Final Statistics:**\n"
        f"• Total messages processed: `{total}`\n"
        f"• Successfully edited: `{success_count}`\n"
        f"• Failed: `{failed_count}`\n\n"
    )
    
    if failed_messages and len(failed_messages) <= 10:
        completion_message += "**Failed messages:**\n"
        for msg_id, error in failed_messages[:5]:
            completion_message += f"• ID `{msg_id}`: {error[:50]}\n"
        if len(failed_messages) > 5:
            completion_message += f"• ... and {len(failed_messages) - 5} more\n"
    
    completion_message += f"\n✅ Footer added: `{FOOTER_TEXT}`"
    
    await status_msg.edit(completion_message)
    
    # Clear session
    session.matched_messages = []
    session.footer_operation = False
    print(f"✅ Footer operation completed! {success_count}/{total} successful")

# ==================== PREVIEW FOOTALL COMMAND (Optional) ====================
@client.on(events.NewMessage(pattern='/preview_footall', outgoing=True))
async def preview_footall_handler(event):
    """Preview first 3 messages that will get footers"""
    
    if not session.is_authorized(event.sender_id):
        await event.reply("❌ Unauthorized")
        return
    
    FOOTER_TEXT = '<a href="https://t.me/UrduSubtitlesMain">@UrduSubtitlesMain</a>'
    FOOTER_CHECK = "@UrduSubtitlesMain"
    
    await event.reply("🔍 **Finding sample messages...**")
    
    samples = []
    
    try:
        async for msg in client.iter_messages(CHANNEL_ID, limit=100):
            if msg.media and msg.raw_text and FOOTER_CHECK not in msg.raw_text:
                samples.append({
                    'id': msg.id,
                    'original_caption': msg.raw_text,
                    'media_type': type(msg.media).__name__
                })
                if len(samples) >= 3:
                    break
            await asyncio.sleep(0.1)
    except Exception as e:
        await event.reply(f"❌ Error: {str(e)}")
        return
    
    if not samples:
        await event.reply("❌ No sample messages found to preview.")
        return
    
    preview_message = f"📝 **Sample Preview** (First {len(samples)} messages)\n\n"
    
    for idx, sample in enumerate(samples, 1):
        original = sample['original_caption']
        new_caption = original.rstrip() + "\n\n" + FOOTER_TEXT
        
        preview_message += f"**Sample {idx}** (ID: `{sample['id']}`) - `{sample['media_type']}`\n\n"
        preview_message += f"🔴 **BEFORE:**\n```\n{truncate_text(original, 150)}\n```\n\n"
        preview_message += f"🟢 **AFTER:**\n```\n{truncate_text(new_caption, 150)}\n```\n\n"
        preview_message += "━" * 30 + "\n\n"
    
    preview_message += f"**Footer to add:**\n{FOOTER_TEXT}\n\n"
    preview_message += f"Run `/footall` to see total count and start operation."
    
    await event.reply(preview_message)

# ==================== GET VIDEO COVER COMMAND ====================
@client.on(events.NewMessage(pattern='/gthumb', outgoing=True))
async def gthumb_command_handler(event):
    """Get the actual custom video cover of a replied Telegram video"""

    if not session.is_authorized(event.sender_id):
        await event.reply("❌ Unauthorized")
        return

    # Must reply to a message
    if not event.is_reply:
        await event.reply(
            "❌ **No replied video found.**\n\n"
            "Reply to a video and send `/gthumb`."
        )
        return

    try:
        replied_msg = await event.get_reply_message()

        if not replied_msg:
            await event.reply("❌ Could not get the replied message.")
            return

        # Get the media object
        media = replied_msg.media

        # ==========================================
        # IMPORTANT:
        # Get Telegram's CUSTOM VIDEO COVER
        # NOT document.thumbs
        # ==========================================
        video_cover = getattr(media, "video_cover", None)

        if not video_cover:
            await event.reply(
                "❌ **No custom video cover found.**\n\n"
                "This video either does not have a custom cover "
                "or Telegram did not provide it in the message."
            )
            return

        await event.reply("🖼️ **Getting video cover...**")

        # Download the actual custom cover photo
        cover_path = await client.download_media(video_cover)

        if not cover_path:
            await event.reply(
                "❌ Failed to download the custom video cover."
            )
            return

        print(f"🖼️ Custom video cover downloaded: {cover_path}")

        # Send the actual cover image
        await client.send_file(
            event.chat_id,
            cover_path,
            force_document=False,
            caption="🎬 **Video Cover**"
        )

        # Delete temporary file
        import os

        try:
            os.remove(cover_path)
            print("🗑️ Temporary cover deleted.")
        except Exception as cleanup_error:
            print(
                f"⚠️ Could not delete temporary cover: "
                f"{cleanup_error}"
            )

    except FloodWaitError as e:
        await event.reply(
            f"⏳ **FloodWait**\n\n"
            f"Telegram asks us to wait `{e.seconds}` seconds."
        )

    except Exception as e:
        print(f"❌ Video cover error: {e}")

        await event.reply(
            f"❌ **Failed to get video cover.**\n\n"
            f"Error: `{str(e)[:500]}`"
        )
        
        
        
        
# ==================== STARTUP ====================
async def main():
    init_caption_db()

    await client.start()

    print("✓ replace.py loaded")

    thumb_manager = register_thumb_handlers(
        client=client,
        is_authorized=session.is_authorized,
        build_caption_metadata=build_caption_metadata,
        build_message_link=build_message_link,
    )

    upload_manager = register_upload_handlers(
        client=client,
        is_authorized=session.is_authorized,
        build_caption_metadata=build_caption_metadata,
        build_message_link=build_message_link,
        get_season_mappings=get_season_mappings,
    )
    
    sort_manager = register_sort_handlers(
    client=client,
    is_authorized=session.is_authorized,
    build_message_link=build_message_link,
)

    await client.run_until_disconnected()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("✓ Bot stopped")