"""
Comprehensive metadata parser for filenames and captions based on reference logic.
Extracts title, season, episode, global Bölüm, quality, language, subtitles, source, year, finale, and part.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional


CAPTION_LANGUAGES = [
    "Dual Audio", "Multi Audio", "Urdu", "Hindi", "English", "Arabic", "Persian", "Farsi", "Turkish",
    "Bangla", "Bengali", "Pashto", "Punjabi", "Sindhi", "Malayalam",
    "Tamil", "Telugu", "Indonesian", "French", "German", "Spanish",
    "Russian", "Kurdish", "Eng", "Hin", "Tur", "Jap", "Spa",
]

CAPTION_SUB_KEYWORDS = [
    "Subtitles", "Subtitle", "Softsub", "Hardsub", "Subs", "Sub",
    "ESubs", "ESub", "E-Sub", "EngSub", "Turkish Sub",
]


@dataclass
class ParsedMetadata:
    raw_input: str
    title: str = "Unknown Title"
    season: Optional[int] = None
    episode: Optional[int] = None
    bolum: Optional[int] = None
    quality: Optional[str] = None
    lang: Optional[str] = None
    sub: Optional[str] = None
    source: Optional[str] = None
    year: Optional[int] = None
    finale: str = ""
    part: Optional[int] = None
    extra: dict[str, str] = field(default_factory=dict)

    @property
    def formatted_season(self) -> str:
        if self.season is not None:
            return f"{self.season:02d}"
        return "01"

    @property
    def formatted_episode(self) -> str:
        if self.episode is not None:
            return f"{self.episode:02d}"
        elif self.bolum is not None:
            return f"{self.bolum:02d}"
        return "01"

    @property
    def formatted_bolum(self) -> str:
        if self.bolum is not None:
            return f"{self.bolum:02d}"
        elif self.episode is not None:
            return f"{self.episode:02d}"
        return "01"

    @property
    def season_episode_str(self) -> str:
        s = self.formatted_season
        e = self.formatted_episode
        return f"S{s} E{e}"


class MetadataParser:
    """Extracts rich metadata from filenames, video titles, and captions."""

    EXTENSIONS = r"\.(mp4|mkv|avi|mov|flv|wmv|webm|m4v|ts)$"

    RE_BOLUM = re.compile(
        r"\bB[öo]l[üu]m\s*[:#-]?\s*(\d{1,4})\b",
        re.IGNORECASE,
    )

    RE_SEASON = re.compile(
        r"\bS(?:eason)?\.?\s*(\d{1,2})\b",
        re.IGNORECASE,
    )

    RE_EPISODE = re.compile(
        r"\b(?:Episode|Epi?\.?|E)\s*#?\s*(\d{1,4})\b",
        re.IGNORECASE,
    )

    RE_S_E_COMBINED = re.compile(
        r"(?:[sS](?P<season>\d{1,3})[\s._-]*[eE](?P<episode>\d{1,4}))|(?:\b(?P<season_x>\d{1,2})x(?P<episode_x>\d{1,4})\b)",
        re.IGNORECASE,
    )

    RE_QUALITY = re.compile(
        r"\b(2160p|1440p|1080p|1080i|720p|576p|480p|360p|240p|144p|2K|4K|8K|UHD|FHD|HD|SD)\b",
        re.IGNORECASE,
    )

    RE_YEAR = re.compile(r"\b(19\d{2}|20\d{2})\b")
    RE_SOURCE = re.compile(r"#?(\bWEB-?DL\b|\bWEBRip\b|\bBlu-?Ray\b|\bBRRip\b|\bBDRip\b|\bHDTV\b|\bDVDRip\b|\bKayiTV\b|\bNF\b|\bAMZN\b|\bDSNP\b|\bCR\b|\bATVP\b|\bHMAX\b)", re.IGNORECASE)
    RE_FINALE = re.compile(r"\b(Series[\s._-]?Finale|Season[\s._-]?Finale|Finale|Final)\b", re.IGNORECASE)
    RE_PART = re.compile(r"\b(?:part|pt)[\s._-]*#?(\d{1,2})\b", re.IGNORECASE)

    @classmethod
    def parse(cls, raw_text: str) -> ParsedMetadata:
        """Parses filename or text string into structured metadata."""
        if not raw_text:
            return ParsedMetadata(raw_input="")

        text = raw_text.strip()
        meta = ParsedMetadata(raw_input=text)
        marker_starts: list[int] = []

        # 1. Check Bölüm
        bolum_match = cls.RE_BOLUM.search(text)
        if bolum_match:
            meta.bolum = int(bolum_match.group(1))
            meta.episode = meta.bolum
            marker_starts.append(bolum_match.start())

        # 2. Check S-E Combined or individual Season and Episode
        se_match = cls.RE_S_E_COMBINED.search(text)
        if se_match:
            s = se_match.group("season") or se_match.group("season_x")
            e = se_match.group("episode") or se_match.group("episode_x")
            if s:
                meta.season = int(s)
            if e:
                meta.episode = int(e)
            marker_starts.append(se_match.start())
        else:
            s_match = cls.RE_SEASON.search(text)
            if s_match:
                meta.season = int(s_match.group(1))
                marker_starts.append(s_match.start())

            ep_match = cls.RE_EPISODE.search(text)
            if ep_match:
                meta.episode = int(ep_match.group(1))
                marker_starts.append(ep_match.start())

        # 3. Quality
        q_match = cls.RE_QUALITY.search(text)
        if q_match:
            raw_q = q_match.group(1).upper()
            if raw_q == "2K":
                meta.quality = "1440p"
            elif raw_q in ("4K", "UHD"):
                meta.quality = "2160p"
            elif raw_q == "FHD":
                meta.quality = "1080p"
            elif raw_q == "HD":
                meta.quality = "720p"
            elif raw_q == "SD":
                meta.quality = "480p"
            else:
                meta.quality = raw_q.lower()
            marker_starts.append(q_match.start())

        # 4. Language (support dotted format e.g. Dual.Audio)
        for lang in CAPTION_LANGUAGES:
            pattern = r"\b" + re.escape(lang).replace(r"\ ", r"[\s._-]") + r"\b"
            lang_m = re.search(pattern, text, re.IGNORECASE)
            if lang_m:
                meta.lang = lang
                marker_starts.append(lang_m.start())
                break

        # 5. Subtitle
        for sub_word in CAPTION_SUB_KEYWORDS:
            pattern = r"\b" + re.escape(sub_word).replace(r"\ ", r"[\s._-]") + r"\b"
            sub_m = re.search(pattern, text, re.IGNORECASE)
            if sub_m:
                meta.sub = sub_word
                marker_starts.append(sub_m.start())
                break

        # 6. Source
        source_m = cls.RE_SOURCE.search(text)
        if source_m:
            src_val = source_m.group(1).lstrip("#").upper()
            meta.source = src_val
            marker_starts.append(source_m.start())

        # 7. Year
        year_m = cls.RE_YEAR.search(text)
        if year_m:
            meta.year = int(year_m.group(1))

        # 8. Finale
        fin_m = cls.RE_FINALE.search(text)
        if fin_m:
            meta.finale = "Finale"
            marker_starts.append(fin_m.start())

        # 9. Part
        part_m = cls.RE_PART.search(text)
        if part_m:
            meta.part = int(part_m.group(1))
            marker_starts.append(part_m.start())

        # 10. Title (everything before the first recognized metadata marker)
        cleaned = re.sub(cls.EXTENSIONS, "", text, flags=re.IGNORECASE)
        if marker_starts:
            title_end = min(marker_starts)
            title = cleaned[:title_end].strip(" -_|:\t")
            title = re.sub(r"[\._]+", " ", title)
            title = re.sub(r"\s+", " ", title).strip()
            if title:
                meta.title = title.title()
        else:
            title = re.sub(r"[\._]+", " ", cleaned).strip()
            if title:
                meta.title = title.title()

        return meta
