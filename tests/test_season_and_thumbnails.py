"""
Unit tests for season global Bölüm arithmetic and thumbnail mapping.
"""

import pytest
from app.database.database import Database
from app.managers.thumbnail_manager import ThumbnailManager
from app.metadata.season import SeasonManager


@pytest.mark.asyncio
async def test_season_bolum_calculation(tmp_path):
    # Test with temporary sqlite DB
    db_file = tmp_path / "test_season.db"
    test_db = Database(db_file)
    await test_db.init()

    # Patch global db
    import app.metadata.season as sm
    sm.db = test_db

    user_id = 999
    # /setseason 2 11 -> S2 starts at Bölüm 11
    await SeasonManager.set_mapping(user_id=user_id, season=2, global_offset=11)

    # S2 E1 -> 11
    b1 = await SeasonManager.get_bolum_for_episode(user_id=user_id, season=2, episode=1)
    assert b1 == 11

    # S2 E2 -> 12
    b2 = await SeasonManager.get_bolum_for_episode(user_id=user_id, season=2, episode=2)
    assert b2 == 12

    # S2 E10 -> 20
    b10 = await SeasonManager.get_bolum_for_episode(user_id=user_id, season=2, episode=10)
    assert b10 == 20

    # S1 (unmapped) -> Episode number
    b_s1 = await SeasonManager.get_bolum_for_episode(user_id=user_id, season=1, episode=5)
    assert b_s1 == 5


def test_thumbnail_preview_formatting():
    mapping = {
        1: "file_id_01",
        2: "file_id_02",
        3: "file_id_03",
        5: "file_id_05",  # Episode 4 is missing
    }

    preview = ThumbnailManager.render_detailed_preview("@SeljuksChannel", mapping)
    assert "THUMBNAIL PREVIEW" in preview
    assert "Episode 01 → ✅ Found" in preview
    assert "Episode 02 → ✅ Found" in preview
    assert "Episode 03 → ✅ Found" in preview
    assert "Episode 04 → ❌ Missing" in preview
    assert "Episode 05 → ✅ Found" in preview
