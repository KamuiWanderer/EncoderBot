"""
/status, /refresh, and /cancel command and callback handlers with live buttons.
"""

from __future__ import annotations

from typing import Any
from pyrogram import Client, filters
from pyrogram.enums import ParseMode
from pyrogram.types import CallbackQuery, Message

from app.config import config
from app.database.database import db
from app.jobs.job import JobStatus
from app.utils.formatting import human_duration, human_eta, progress_bar
from app.utils.ui import build_keyboard, danger_button, primary_button


def build_status_card(active_job: Any, queued_jobs: list[Any]) -> tuple[str, Any]:
    """Renders formatted status text and interactive buttons."""
    if not active_job:
        text = (
            "📊 **ENCODER STATUS**\n\n"
            "💤 **Status:** `Idle (No active encoding job)`\n"
            f"📋 **Queued Jobs:** `{len(queued_jobs)}`\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "💡 _Send a video, forwarded message, or link to start._"
        )
        keyboard = build_keyboard([
            [primary_button("🔄 Refresh Status", "nav:status")],
        ])
        return text, keyboard

    cur_q = active_job.current_quality or "Preparing"
    bar = progress_bar(active_job.progress_percent)
    status_str = active_job.status.capitalize()
    total_q = len(active_job.selected_qualities)
    done_q = len(active_job.completed_qualities)

    text = (
        f"📊 **CURRENT ENCODING STATUS [{active_job.batch_index}/{active_job.batch_total}]**\n\n"
        f"🎬 **Source:**\n`{active_job.source_title}`\n\n"
        f"📌 **Stage:** `{status_str}` ({cur_q})\n"
        f"`{bar}`\n\n"
        f"⚡ **Speed:** `{active_job.progress_speed}`\n"
        f"⏳ **ETA:** `{active_job.progress_eta}`\n"
        f"🎞 **Qualities:** `{done_q}/{total_q}` completed ({', '.join(active_job.completed_qualities) or 'None'})\n"
        f"📋 **Queue:** `{len(queued_jobs)} waiting`"
    )

    keyboard = build_keyboard([
        [
            primary_button("🔄 Refresh", f"status:refresh:{active_job.job_id}"),
            danger_button("⛔ Cancel Job", f"job:cancel:{active_job.job_id}"),
        ],
    ])
    return text, keyboard


async def status_handler(client: Client, message: Message, worker: Any) -> None:
    """Handle /status and /refresh commands."""
    user_id = message.from_user.id if message.from_user else 0
    if not config.is_authorized(user_id):
        return

    active_job = worker.current_job
    queued_jobs = await db.get_queued_jobs()

    text, keyboard = build_status_card(active_job, queued_jobs)
    await message.reply_text(text, reply_markup=keyboard, parse_mode=ParseMode.MARKDOWN)


async def cancel_handler(client: Client, message: Message, worker: Any) -> None:
    """Handle /cancel command (supports optional /cancel <job_id>)."""
    user_id = message.from_user.id if message.from_user else 0
    if not config.is_authorized(user_id):
        return

    parts = message.text.strip().split()
    target_job_id = parts[1].strip() if len(parts) > 1 else None

    active_job = worker.current_job

    if target_job_id:
        if active_job and active_job.job_id == target_job_id:
            worker.cancel_active_job(target_job_id)
            await message.reply_text(f"🛑 **Cancel signal sent to active job:** `{target_job_id}`")
            return
        # Cancel queued job in DB
        db_job = await db.get_job(target_job_id)
        if db_job and db_job.status in (JobStatus.QUEUED, JobStatus.DOWNLOADING, JobStatus.ENCODING):
            db_job.status = JobStatus.CANCELLED
            await db.save_job(db_job)
            await message.reply_text(f"🛑 **Job `{target_job_id}` removed and marked cancelled.**")
            return
        await message.reply_text(f"⚠️ **Could not find active/queued job with ID:** `{target_job_id}`")
        return

    if not active_job:
        await message.reply_text("ℹ️ **There is no active encoding job running right now.**")
        return

    success = worker.cancel_active_job(active_job.job_id)
    if success:
        await message.reply_text(f"🛑 **Cancel signal sent to active job:** `{active_job.job_id}`")
    else:
        await message.reply_text("⚠️ **Failed to cancel job or job is no longer active.**")


async def handle_status_callbacks(client: Client, query: CallbackQuery, worker: Any) -> None:
    """Handle refresh and cancel button callbacks."""
    data = query.data or ""
    active_job = worker.current_job
    queued_jobs = await db.get_queued_jobs()

    if data.startswith("status:refresh:") or data == "nav:status":
        text, keyboard = build_status_card(active_job, queued_jobs)
        try:
            await query.message.edit_text(text, reply_markup=keyboard, parse_mode=ParseMode.MARKDOWN)
            await query.answer("🔄 Status updated!", show_alert=False)
        except Exception:
            await query.answer("Status already up to date!", show_alert=False)
        return

    if data.startswith("job:cancel:"):
        job_id = data.split(":")[-1]
        if active_job and active_job.job_id == job_id:
            worker.cancel_active_job(job_id)
            await query.answer(f"🛑 Cancelling job {job_id}...", show_alert=True)
            try:
                await query.message.edit_text(
                    f"⛔ **Job `{job_id}` is being cancelled by user...**",
                    reply_markup=None,
                    parse_mode=ParseMode.MARKDOWN,
                )
            except Exception:
                pass
            return

        # Check queued job
        db_job = await db.get_job(job_id)
        if db_job and db_job.status in (JobStatus.QUEUED, JobStatus.DOWNLOADING, JobStatus.ENCODING):
            db_job.status = JobStatus.CANCELLED
            await db.save_job(db_job)
            await query.answer(f"🛑 Job {job_id} cancelled!", show_alert=True)
            try:
                await query.message.edit_text(
                    f"⛔ **Job `{job_id}` has been cancelled.**",
                    reply_markup=None,
                    parse_mode=ParseMode.MARKDOWN,
                )
            except Exception:
                pass
            return

        await query.answer("Job is not currently active.", show_alert=True)
