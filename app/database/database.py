"""
Async SQLite Database engine, schema migrations, and CRUD operations.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Optional
import aiosqlite

from app.config import config
from app.database.models import (
    CaptionFormat,
    EncodeJob,
    FilenameFormat,
    JobOutput,
    SeasonMapping,
    ThumbnailConfig,
    UserSettings,
)
from app.utils.logging import logger


class Database:
    """Async SQLite Database Connection and Query Manager."""

    def __init__(self, db_path: Optional[Path] = None) -> None:
        self.db_path = db_path or config.storage.database_path

    async def init(self) -> None:
        """Initialize database schema, tables, and perform auto-migrations."""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("PRAGMA journal_mode=WAL;")
            await db.execute("PRAGMA foreign_keys=ON;")

            # Users table
            await db.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    user_id INTEGER PRIMARY KEY,
                    default_qualities TEXT NOT NULL,
                    default_codec TEXT NOT NULL DEFAULT 'h264',
                    default_profile TEXT NOT NULL DEFAULT 'standard',
                    default_destination TEXT,
                    sticker_id TEXT,
                    upload_timing TEXT NOT NULL DEFAULT 'staging',
                    staging_channel TEXT,
                    episode_header_enabled INTEGER NOT NULL DEFAULT 1,
                    episode_header_template TEXT,
                    sticker_mode TEXT NOT NULL DEFAULT 'per_episode',
                    destination_mode TEXT NOT NULL DEFAULT 'default',
                    backup_destination TEXT,
                    send_delay REAL NOT NULL DEFAULT 1.0,
                    allow_upscale INTEGER NOT NULL DEFAULT 0,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                );
            """)

            # Filename Formats table
            await db.execute("""
                CREATE TABLE IF NOT EXISTS filename_formats (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    template TEXT NOT NULL,
                    is_active INTEGER NOT NULL DEFAULT 1,
                    created_at REAL NOT NULL
                );
            """)

            # Caption Formats table
            await db.execute("""
                CREATE TABLE IF NOT EXISTS caption_formats (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    template TEXT NOT NULL,
                    is_active INTEGER NOT NULL DEFAULT 1,
                    created_at REAL NOT NULL
                );
            """)

            # Season Mappings table (/setseason)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS season_mappings (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    season INTEGER NOT NULL,
                    global_bolum_offset INTEGER NOT NULL,
                    updated_at REAL NOT NULL,
                    UNIQUE(user_id, season)
                );
            """)

            # Thumbnail Configs table
            await db.execute("""
                CREATE TABLE IF NOT EXISTS thumbnail_configs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    mode TEXT NOT NULL,
                    single_file_id TEXT,
                    single_file_path TEXT,
                    channel_username_or_id TEXT,
                    start_message_id INTEGER,
                    end_message_id INTEGER,
                    mapping_json TEXT NOT NULL DEFAULT '{}',
                    mapping_links_json TEXT NOT NULL DEFAULT '{}',
                    is_active INTEGER NOT NULL DEFAULT 1,
                    updated_at REAL NOT NULL
                );
            """)

            # Jobs table
            await db.execute("""
                CREATE TABLE IF NOT EXISTS jobs (
                    job_id TEXT PRIMARY KEY,
                    user_id INTEGER NOT NULL,
                    chat_id INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    source_type TEXT NOT NULL,
                    source_file_id TEXT,
                    source_link TEXT,
                    source_path TEXT,
                    source_title TEXT NOT NULL,
                    source_size_bytes INTEGER NOT NULL DEFAULT 0,
                    source_duration_seconds REAL NOT NULL DEFAULT 0.0,
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    selected_qualities_json TEXT NOT NULL DEFAULT '[]',
                    current_quality TEXT,
                    completed_qualities_json TEXT NOT NULL DEFAULT '[]',
                    failed_qualities_json TEXT NOT NULL DEFAULT '[]',
                    filename_template TEXT,
                    caption_template TEXT,
                    thumbnail_config_id INTEGER,
                    thumbnail_mode TEXT NOT NULL DEFAULT 'none',
                    thumbnail_link TEXT,
                    destination_chat_id TEXT,
                    staging_channel TEXT,
                    upload_timing TEXT NOT NULL DEFAULT 'staging',
                    episode_header_enabled INTEGER NOT NULL DEFAULT 1,
                    episode_header_template TEXT,
                    sticker_id TEXT,
                    sticker_mode TEXT NOT NULL DEFAULT 'per_episode',
                    send_delay REAL NOT NULL DEFAULT 1.0,
                    encoding_profile TEXT NOT NULL DEFAULT 'standard',
                    batch_id TEXT,
                    batch_index INTEGER NOT NULL DEFAULT 1,
                    batch_total INTEGER NOT NULL DEFAULT 1,
                    staged_outputs_json TEXT NOT NULL DEFAULT '{}',
                    progress_percent REAL NOT NULL DEFAULT 0.0,
                    progress_speed TEXT NOT NULL DEFAULT '0x',
                    progress_eta TEXT NOT NULL DEFAULT 'N/A',
                    error_message TEXT,
                    status_message_id INTEGER,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                );
            """)

            # Auto-migrations for existing tables
            await self._run_migrations(db)

            await db.commit()
            logger.info("SQLite Database initialized and schema verified.")

    async def _run_migrations(self, db: aiosqlite.Connection) -> None:
        """Checks and adds missing columns dynamically."""
        cursor = await db.execute("PRAGMA table_info(users)")
        columns = [row[1] for row in await cursor.fetchall()]

        user_new_cols = {
            "upload_timing": "TEXT NOT NULL DEFAULT 'staging'",
            "staging_channel": "TEXT",
            "episode_header_enabled": "INTEGER NOT NULL DEFAULT 1",
            "episode_header_template": "TEXT",
            "sticker_mode": "TEXT NOT NULL DEFAULT 'per_episode'",
            "destination_mode": "TEXT NOT NULL DEFAULT 'default'",
            "backup_destination": "TEXT",
            "send_delay": "REAL NOT NULL DEFAULT 1.0",
            "allow_upscale": "INTEGER NOT NULL DEFAULT 0",
        }

        for col_name, col_def in user_new_cols.items():
            if col_name not in columns:
                try:
                    await db.execute(f"ALTER TABLE users ADD COLUMN {col_name} {col_def};")
                except Exception:
                    pass

        # Check jobs table
        c_jobs = await db.execute("PRAGMA table_info(jobs)")
        job_cols = [row[1] for row in await c_jobs.fetchall()]
        job_new_cols = {
            "thumbnail_link": "TEXT",
            "staging_channel": "TEXT",
            "upload_timing": "TEXT NOT NULL DEFAULT 'staging'",
            "episode_header_enabled": "INTEGER NOT NULL DEFAULT 1",
            "episode_header_template": "TEXT",
            "sticker_mode": "TEXT NOT NULL DEFAULT 'per_episode'",
            "send_delay": "REAL NOT NULL DEFAULT 1.0",
            "batch_id": "TEXT",
            "batch_index": "INTEGER NOT NULL DEFAULT 1",
            "batch_total": "INTEGER NOT NULL DEFAULT 1",
            "staged_outputs_json": "TEXT NOT NULL DEFAULT '{}'",
        }
        for col_name, col_def in job_new_cols.items():
            if col_name not in job_cols:
                try:
                    await db.execute(f"ALTER TABLE jobs ADD COLUMN {col_name} {col_def};")
                except Exception:
                    pass

    # ---------------- USER SETTINGS ----------------

    async def get_user_settings(self, user_id: int) -> UserSettings:
        """Fetch settings for a user or create defaults."""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
            row = await cursor.fetchone()
            if row:
                return UserSettings(
                    user_id=row["user_id"],
                    default_qualities=json.loads(row["default_qualities"]),
                    default_codec=row["default_codec"],
                    default_profile=row["default_profile"],
                    default_destination=row["default_destination"],
                    sticker_id=row["sticker_id"],
                    upload_timing=row["upload_timing"] if "upload_timing" in row.keys() else "staging",
                    staging_channel=row["staging_channel"] if "staging_channel" in row.keys() else None,
                    episode_header_enabled=bool(row["episode_header_enabled"]) if "episode_header_enabled" in row.keys() else True,
                    episode_header_template=row["episode_header_template"] if "episode_header_template" in row.keys() else None,
                    sticker_mode=row["sticker_mode"] if "sticker_mode" in row.keys() else "per_episode",
                    destination_mode=row["destination_mode"] if "destination_mode" in row.keys() else "default",
                    backup_destination=row["backup_destination"] if "backup_destination" in row.keys() else None,
                    send_delay=float(row["send_delay"]) if "send_delay" in row.keys() else 1.0,
                    allow_upscale=bool(row["allow_upscale"]) if "allow_upscale" in row.keys() else False,
                    created_at=row["created_at"],
                    updated_at=row["updated_at"],
                )

            default_settings = UserSettings(user_id=user_id)
            await db.execute(
                """
                INSERT INTO users (
                    user_id, default_qualities, default_codec, default_profile, default_destination,
                    sticker_id, upload_timing, staging_channel, episode_header_enabled,
                    episode_header_template, sticker_mode, destination_mode, backup_destination,
                    send_delay, allow_upscale, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    user_id,
                    json.dumps(default_settings.default_qualities),
                    default_settings.default_codec,
                    default_settings.default_profile,
                    default_settings.default_destination,
                    default_settings.sticker_id,
                    default_settings.upload_timing,
                    default_settings.staging_channel,
                    int(default_settings.episode_header_enabled),
                    default_settings.episode_header_template,
                    default_settings.sticker_mode,
                    default_settings.destination_mode,
                    default_settings.backup_destination,
                    default_settings.send_delay,
                    int(default_settings.allow_upscale),
                    default_settings.created_at,
                    default_settings.updated_at,
                ),
            )
            await db.commit()
            return default_settings

    async def update_user_settings(self, settings: UserSettings) -> None:
        """Save updated user settings."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                INSERT INTO users (
                    user_id, default_qualities, default_codec, default_profile, default_destination,
                    sticker_id, upload_timing, staging_channel, episode_header_enabled,
                    episode_header_template, sticker_mode, destination_mode, backup_destination,
                    send_delay, allow_upscale, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    default_qualities = excluded.default_qualities,
                    default_codec = excluded.default_codec,
                    default_profile = excluded.default_profile,
                    default_destination = excluded.default_destination,
                    sticker_id = excluded.sticker_id,
                    upload_timing = excluded.upload_timing,
                    staging_channel = excluded.staging_channel,
                    episode_header_enabled = excluded.episode_header_enabled,
                    episode_header_template = excluded.episode_header_template,
                    sticker_mode = excluded.sticker_mode,
                    destination_mode = excluded.destination_mode,
                    backup_destination = excluded.backup_destination,
                    send_delay = excluded.send_delay,
                    allow_upscale = excluded.allow_upscale,
                    updated_at = excluded.updated_at
                """,
                (
                    settings.user_id,
                    json.dumps(settings.default_qualities),
                    settings.default_codec,
                    settings.default_profile,
                    settings.default_destination,
                    settings.sticker_id,
                    settings.upload_timing,
                    settings.staging_channel,
                    int(settings.episode_header_enabled),
                    settings.episode_header_template,
                    settings.sticker_mode,
                    settings.destination_mode,
                    settings.backup_destination,
                    settings.send_delay,
                    int(settings.allow_upscale),
                    settings.created_at,
                    time.time(),
                ),
            )
            await db.commit()

    # ---------------- FILENAME FORMATS ----------------

    async def get_active_filename_format(self, user_id: int) -> Optional[FilenameFormat]:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM filename_formats WHERE user_id = ? AND is_active = 1 ORDER BY id DESC LIMIT 1",
                (user_id,),
            )
            row = await cursor.fetchone()
            if row:
                return FilenameFormat(
                    id=row["id"],
                    user_id=row["user_id"],
                    template=row["template"],
                    is_active=bool(row["is_active"]),
                    created_at=row["created_at"],
                )
            return None

    async def set_filename_format(self, user_id: int, template: str) -> FilenameFormat:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("UPDATE filename_formats SET is_active = 0 WHERE user_id = ?", (user_id,))
            now = time.time()
            cursor = await db.execute(
                "INSERT INTO filename_formats (user_id, template, is_active, created_at) VALUES (?, ?, 1, ?)",
                (user_id, template, now),
            )
            await db.commit()
            return FilenameFormat(id=cursor.lastrowid, user_id=user_id, template=template, is_active=True, created_at=now)

    async def clear_filename_format(self, user_id: int) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("UPDATE filename_formats SET is_active = 0 WHERE user_id = ?", (user_id,))
            await db.commit()

    # ---------------- CAPTION FORMATS ----------------

    async def get_active_caption_format(self, user_id: int) -> Optional[CaptionFormat]:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM caption_formats WHERE user_id = ? AND is_active = 1 ORDER BY id DESC LIMIT 1",
                (user_id,),
            )
            row = await cursor.fetchone()
            if row:
                return CaptionFormat(
                    id=row["id"],
                    user_id=row["user_id"],
                    template=row["template"],
                    is_active=bool(row["is_active"]),
                    created_at=row["created_at"],
                )
            return None

    async def set_caption_format(self, user_id: int, template: str) -> CaptionFormat:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("UPDATE caption_formats SET is_active = 0 WHERE user_id = ?", (user_id,))
            now = time.time()
            cursor = await db.execute(
                "INSERT INTO caption_formats (user_id, template, is_active, created_at) VALUES (?, ?, 1, ?)",
                (user_id, template, now),
            )
            await db.commit()
            return CaptionFormat(id=cursor.lastrowid, user_id=user_id, template=template, is_active=True, created_at=now)

    async def clear_caption_format(self, user_id: int) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("UPDATE caption_formats SET is_active = 0 WHERE user_id = ?", (user_id,))
            await db.commit()

    # ---------------- SEASON MAPPINGS ----------------

    async def set_season_mapping(self, user_id: int, season: int, global_bolum_offset: int) -> SeasonMapping:
        now = time.time()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                INSERT INTO season_mappings (user_id, season, global_bolum_offset, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(user_id, season) DO UPDATE SET
                    global_bolum_offset = excluded.global_bolum_offset,
                    updated_at = excluded.updated_at
                """,
                (user_id, season, global_bolum_offset, now),
            )
            await db.commit()
            return SeasonMapping(id=None, user_id=user_id, season=season, global_bolum_offset=global_bolum_offset, updated_at=now)

    async def get_season_mapping(self, user_id: int, season: int) -> Optional[SeasonMapping]:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM season_mappings WHERE user_id = ? AND season = ?",
                (user_id, season),
            )
            row = await cursor.fetchone()
            if row:
                return SeasonMapping(
                    id=row["id"],
                    user_id=row["user_id"],
                    season=row["season"],
                    global_bolum_offset=row["global_bolum_offset"],
                    updated_at=row["updated_at"],
                )
            return None

    async def get_all_season_mappings(self, user_id: int) -> list[SeasonMapping]:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM season_mappings WHERE user_id = ? ORDER BY season ASC",
                (user_id,),
            )
            rows = await cursor.fetchall()
            return [
                SeasonMapping(
                    id=r["id"],
                    user_id=r["user_id"],
                    season=r["season"],
                    global_bolum_offset=r["global_bolum_offset"],
                    updated_at=r["updated_at"],
                )
                for r in rows
            ]

    # ---------------- THUMBNAILS ----------------

    async def get_active_thumbnail_config(self, user_id: int) -> Optional[ThumbnailConfig]:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM thumbnail_configs WHERE user_id = ? AND is_active = 1 ORDER BY id DESC LIMIT 1",
                (user_id,),
            )
            row = await cursor.fetchone()
            if row:
                links_json = row["mapping_links_json"] if "mapping_links_json" in row.keys() else "{}"
                return ThumbnailConfig(
                    id=row["id"],
                    user_id=row["user_id"],
                    mode=row["mode"],
                    single_file_id=row["single_file_id"],
                    single_file_path=row["single_file_path"],
                    channel_username_or_id=row["channel_username_or_id"],
                    start_message_id=row["start_message_id"],
                    end_message_id=row["end_message_id"],
                    mapping_data=json.loads(row["mapping_json"]),
                    mapping_links=json.loads(links_json),
                    is_active=bool(row["is_active"]),
                    updated_at=row["updated_at"],
                )
            return None

    async def save_thumbnail_config(self, config_obj: ThumbnailConfig) -> ThumbnailConfig:
        now = time.time()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("UPDATE thumbnail_configs SET is_active = 0 WHERE user_id = ?", (config_obj.user_id,))
            cursor = await db.execute(
                """
                INSERT INTO thumbnail_configs
                (user_id, mode, single_file_id, single_file_path, channel_username_or_id, start_message_id, end_message_id, mapping_json, mapping_links_json, is_active, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
                """,
                (
                    config_obj.user_id,
                    config_obj.mode,
                    config_obj.single_file_id,
                    config_obj.single_file_path,
                    config_obj.channel_username_or_id,
                    config_obj.start_message_id,
                    config_obj.end_message_id,
                    json.dumps(config_obj.mapping_data),
                    json.dumps(config_obj.mapping_links),
                    now,
                ),
            )
            await db.commit()
            config_obj.id = cursor.lastrowid
            config_obj.updated_at = now
            return config_obj

    async def clear_thumbnail_config(self, user_id: int) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("UPDATE thumbnail_configs SET is_active = 0 WHERE user_id = ?", (user_id,))
            await db.commit()

    # ---------------- JOBS ----------------

    async def save_job(self, job: EncodeJob) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                INSERT INTO jobs (
                    job_id, user_id, chat_id, status, source_type, source_file_id, source_link,
                    source_path, source_title, source_size_bytes, source_duration_seconds,
                    metadata_json, selected_qualities_json, current_quality,
                    completed_qualities_json, failed_qualities_json, filename_template,
                    caption_template, thumbnail_config_id, thumbnail_mode, thumbnail_link,
                    destination_chat_id, staging_channel, upload_timing, episode_header_enabled,
                    episode_header_template, sticker_id, sticker_mode, send_delay,
                    encoding_profile, batch_id, batch_index, batch_total, staged_outputs_json,
                    progress_percent, progress_speed, progress_eta, error_message,
                    status_message_id, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(job_id) DO UPDATE SET
                    status = excluded.status,
                    source_path = excluded.source_path,
                    source_size_bytes = excluded.source_size_bytes,
                    source_duration_seconds = excluded.source_duration_seconds,
                    metadata_json = excluded.metadata_json,
                    selected_qualities_json = excluded.selected_qualities_json,
                    current_quality = excluded.current_quality,
                    completed_qualities_json = excluded.completed_qualities_json,
                    failed_qualities_json = excluded.failed_qualities_json,
                    staged_outputs_json = excluded.staged_outputs_json,
                    progress_percent = excluded.progress_percent,
                    progress_speed = excluded.progress_speed,
                    progress_eta = excluded.progress_eta,
                    error_message = excluded.error_message,
                    status_message_id = excluded.status_message_id,
                    updated_at = excluded.updated_at
                """,
                (
                    job.job_id,
                    job.user_id,
                    job.chat_id,
                    job.status,
                    job.source_type,
                    job.source_file_id,
                    job.source_link,
                    job.source_path,
                    job.source_title,
                    job.source_size_bytes,
                    job.source_duration_seconds,
                    json.dumps(job.metadata),
                    json.dumps(job.selected_qualities),
                    job.current_quality,
                    json.dumps(job.completed_qualities),
                    json.dumps(job.failed_qualities),
                    job.filename_template,
                    job.caption_template,
                    job.thumbnail_config_id,
                    job.thumbnail_mode,
                    job.thumbnail_link,
                    job.destination_chat_id,
                    job.staging_channel,
                    job.upload_timing,
                    int(job.episode_header_enabled),
                    job.episode_header_template,
                    job.sticker_id,
                    job.sticker_mode,
                    job.send_delay,
                    job.encoding_profile,
                    job.batch_id,
                    job.batch_index,
                    job.batch_total,
                    json.dumps(job.staged_outputs),
                    job.progress_percent,
                    job.progress_speed,
                    job.progress_eta,
                    job.error_message,
                    job.status_message_id,
                    job.created_at,
                    time.time(),
                ),
            )
            await db.commit()

    async def get_job(self, job_id: str) -> Optional[EncodeJob]:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,))
            row = await cursor.fetchone()
            if row:
                return self._row_to_job(row)
            return None

    async def get_queued_jobs(self) -> list[EncodeJob]:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM jobs WHERE status = 'queued' ORDER BY created_at ASC"
            )
            rows = await cursor.fetchall()
            return [self._row_to_job(r) for r in rows]

    async def get_unfinished_jobs(self) -> list[EncodeJob]:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM jobs WHERE status IN ('queued', 'downloading', 'encoding', 'staging', 'uploading') ORDER BY created_at ASC"
            )
            rows = await cursor.fetchall()
            return [self._row_to_job(r) for r in rows]

    async def get_batch_jobs(self, batch_id: str) -> list[EncodeJob]:
        """Fetch all jobs belonging to a batch, ordered by batch_index."""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM jobs WHERE batch_id = ? ORDER BY batch_index ASC",
                (batch_id,),
            )
            rows = await cursor.fetchall()
            return [self._row_to_job(r) for r in rows]

    def _row_to_job(self, row: Any) -> EncodeJob:
        return EncodeJob(
            job_id=row["job_id"],
            user_id=row["user_id"],
            chat_id=row["chat_id"],
            status=row["status"],
            source_type=row["source_type"],
            source_file_id=row["source_file_id"],
            source_link=row["source_link"],
            source_path=row["source_path"],
            source_title=row["source_title"],
            source_size_bytes=row["source_size_bytes"],
            source_duration_seconds=row["source_duration_seconds"],
            metadata=json.loads(row["metadata_json"]),
            selected_qualities=json.loads(row["selected_qualities_json"]),
            current_quality=row["current_quality"],
            completed_qualities=json.loads(row["completed_qualities_json"]),
            failed_qualities=json.loads(row["failed_qualities_json"]),
            filename_template=row["filename_template"],
            caption_template=row["caption_template"],
            thumbnail_config_id=row["thumbnail_config_id"],
            thumbnail_mode=row["thumbnail_mode"],
            thumbnail_link=row["thumbnail_link"] if "thumbnail_link" in row.keys() else None,
            destination_chat_id=row["destination_chat_id"],
            staging_channel=row["staging_channel"] if "staging_channel" in row.keys() else None,
            upload_timing=row["upload_timing"] if "upload_timing" in row.keys() else "staging",
            episode_header_enabled=bool(row["episode_header_enabled"]) if "episode_header_enabled" in row.keys() else True,
            episode_header_template=row["episode_header_template"] if "episode_header_template" in row.keys() else None,
            sticker_id=row["sticker_id"],
            sticker_mode=row["sticker_mode"] if "sticker_mode" in row.keys() else "per_episode",
            send_delay=float(row["send_delay"]) if "send_delay" in row.keys() else 1.0,
            encoding_profile=row["encoding_profile"],
            batch_id=row["batch_id"] if "batch_id" in row.keys() else None,
            batch_index=row["batch_index"] if "batch_index" in row.keys() else 1,
            batch_total=row["batch_total"] if "batch_total" in row.keys() else 1,
            staged_outputs=json.loads(row["staged_outputs_json"]) if "staged_outputs_json" in row.keys() else {},
            progress_percent=row["progress_percent"],
            progress_speed=row["progress_speed"],
            progress_eta=row["progress_eta"],
            error_message=row["error_message"],
            status_message_id=row["status_message_id"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )


# Global singleton instance
db = Database()
