"""
Season to global Bölüm mapping manager with Finale calculation.
Handles `/setseason <season> <offset>` logic, calculating global episode numbers,
and detecting Season Finale boundaries based on reference replace.py rules.
"""

from __future__ import annotations

from typing import Optional
from app.database.database import db
from app.database.models import SeasonMapping
from app.utils.logging import logger


class SeasonManager:
    """Calculates global Bölüm numbers and season boundaries using persistent season offsets."""

    @staticmethod
    async def set_mapping(user_id: int, season: int, global_offset: int) -> SeasonMapping:
        """
        Store mapping: e.g. /setseason 2 11 means Season 2 Episode 1 = Bölüm 11.
        """
        mapping = await db.set_season_mapping(user_id=user_id, season=season, global_bolum_offset=global_offset)
        logger.info(f"Updated season mapping for user {user_id}: Season {season} -> Bölüm {global_offset}")
        return mapping

    @staticmethod
    async def get_bolum_for_episode(user_id: int, season: Optional[int], episode: Optional[int]) -> int:
        """
        Calculate global Bölüm number based on season and episode.
        Example: /setseason 2 11:
          S2 E1 -> 11
          S2 E2 -> 12
          S2 E10 -> 20
        If season 1 and not mapped, returns episode number.
        """
        ep = episode or 1
        s = season or 1

        mapping = await db.get_season_mapping(user_id, s)
        if mapping:
            return mapping.global_bolum_offset + (ep - 1)

        if s == 1:
            return ep

        return ep

    @staticmethod
    async def check_season_finale(user_id: int, season: Optional[int], global_bolum: Optional[int]) -> str:
        """
        Calculate if current episode is a Season Finale based on season mappings.
        Rule: If next season starts at N, episode N-1 is the Season Finale.
        """
        if season is None or global_bolum is None:
            return ""

        mappings = await db.get_all_season_mappings(user_id)
        if not mappings:
            return ""

        # Sort by global_bolum_offset
        sorted_maps = sorted(mappings, key=lambda m: m.global_bolum_offset)

        for i, m in enumerate(sorted_maps):
            if m.season == season:
                if i + 1 < len(sorted_maps):
                    next_start = sorted_maps[i + 1].global_bolum_offset
                    if global_bolum == (next_start - 1):
                        return "Season Finale"
                break

        return ""

    @staticmethod
    async def get_all_mappings(user_id: int) -> list[SeasonMapping]:
        return await db.get_all_season_mappings(user_id)
