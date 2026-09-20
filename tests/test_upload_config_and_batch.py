"""
Unit tests for Upload Configuration Manager and Batch Review builder.
"""

import pytest
from app.database.database import Database
from app.managers.batch_manager import BatchSession, BatchVideoItem, BatchManager
from app.managers.upload_config_manager import UploadConfigManager
from app.metadata.parser import MetadataParser


@pytest.mark.asyncio
async def test_upload_config_crud(tmp_path):
    db_file = tmp_path / "test_upload.db"
    test_db = Database(db_file)
    await test_db.init()

    import app.managers.upload_config_manager as ucm
    ucm.db = test_db

    user_id = 777
    st = await UploadConfigManager.get_settings(user_id)
    assert st.upload_timing == "staging"
    assert st.episode_header_enabled is True

    # Switch timing to immediate
    await UploadConfigManager.set_upload_timing(user_id, "immediate")
    st2 = await UploadConfigManager.get_settings(user_id)
    assert st2.upload_timing == "immediate"

    # Set staging channel
    await UploadConfigManager.set_staging_channel(user_id, "@MyPrivateStaging")
    st3 = await UploadConfigManager.get_settings(user_id)
    assert st3.staging_channel == "@MyPrivateStaging"

    # Set delay
    await UploadConfigManager.set_send_delay(user_id, 2.5)
    st4 = await UploadConfigManager.get_settings(user_id)
    assert st4.send_delay == 2.5


def test_episode_header_rendering():
    meta = MetadataParser.parse("Kurulus Osman S02E15 1080p HDTV.mkv")
    template = "🎬 <b>{title}</b>\n📌 Season {season} Episode {episode} (Bölüm {bolum})\n✨ <b>{finale}</b>"

    header_normal = UploadConfigManager.render_episode_header(
        template=template,
        metadata=meta,
        calculated_bolum=42,
        calculated_finale=None,
    )
    assert "<b>Kurulus Osman</b>" in header_normal
    assert "Season 02 Episode 15 (Bölüm 42)" in header_normal
    assert "<b>{finale}</b>" not in header_normal

    header_finale = UploadConfigManager.render_episode_header(
        template=template,
        metadata=meta,
        calculated_bolum=50,
        calculated_finale="Season Finale",
    )
    assert "Season Finale" in header_finale


@pytest.mark.asyncio
async def test_batch_review_card_building():
    sess = BatchSession(
        batch_id="batch_test123",
        user_id=123,
        chat_id=456,
        selected_qualities=["360p", "720p"],
        destination_chat="@MainChannel",
        upload_timing="staging",
    )

    meta1 = MetadataParser.parse("The.Great.Seljuks.S01E01.1080p.mkv")
    item1 = BatchVideoItem(
        index=1,
        source_type="telegram_link",
        source_link="https://t.me/c/123/101",
        source_title="The.Great.Seljuks.S01E01.1080p.mkv",
        source_size_bytes=1000000000,
        parsed_metadata=meta1,
        global_bolum=1,
        thumbnail_link="https://t.me/c/123/201",
    )
    sess.items.append(item1)

    text, markup = await BatchManager.build_review_card(sess)
    assert "BATCH ENCODE REVIEW (1 Episode)" in text
    assert '<a href="https://t.me/c/123/101">Episode 01</a>' in text
    assert '<a href="https://t.me/c/123/201">Episode 01 Cover 👁</a>' in text
    assert "@MainChannel" in text
    assert "Private Staging (Ordered Delivery)" in text
