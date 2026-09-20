"""
Template engine for filename and caption formatting.
Supports all placeholders: {title}, {season}, {episode}, {bolum}, {season_episode}, {quality},
{lang}, {source}, {year}, {sub}, {finale}, {part}, {width}, {height}, {codec}, {ext}.
Implements reference finale separator stripping and HTML tag preservation.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Optional

from app.metadata.parser import ParsedMetadata
from app.utils.files import sanitize_filename


@dataclass
class TemplateContext:
    metadata: ParsedMetadata
    quality: str = "720p"
    width: int = 1280
    height: int = 720
    codec: str = "h264"
    ext: str = "mp4"
    calculated_bolum: Optional[int] = None
    calculated_finale: Optional[str] = None


class TemplateEngine:
    """Renders filenames and captions using customizable templates and placeholders."""

    DEFAULT_FILENAME_TEMPLATE = "{title} S{season} E{episode} {quality}.{ext}"
    DEFAULT_CAPTION_TEMPLATE = "🎬 <b>{title}</b>\n\n📌 <b>Season:</b> {season}\n📌 <b>Episode:</b> {episode}\n🎞 <b>Quality:</b> {quality}"

    @classmethod
    def render_filename(
        cls,
        template: Optional[str],
        ctx: TemplateContext,
    ) -> str:
        """Render a clean, sanitized filename for local disk and Telegram upload."""
        tmpl = template or cls.DEFAULT_FILENAME_TEMPLATE
        rendered = cls._render_string(tmpl, ctx)
        return sanitize_filename(rendered)

    @classmethod
    def render_caption(
        cls,
        template: Optional[str],
        ctx: TemplateContext,
    ) -> str:
        """Render caption string with full HTML tag preservation."""
        if not template:
            return ""
        return cls._render_string(template, ctx)

    @classmethod
    def _render_string(cls, tmpl: str, ctx: TemplateContext) -> str:
        m = ctx.metadata
        s_num = m.season if m.season is not None else 1
        e_num = m.episode if m.episode is not None else (m.bolum if m.bolum is not None else 1)
        b_num = ctx.calculated_bolum if ctx.calculated_bolum is not None else (m.bolum if m.bolum is not None else e_num)

        s_val = f"{s_num:02d}"
        e_val = f"{e_num:02d}"
        b_val = f"{b_num:02d}"

        part_val = f"Part {m.part:02d}" if m.part is not None else ""
        year_val = str(m.year) if m.year is not None else ""
        finale_val = ctx.calculated_finale if ctx.calculated_finale is not None else m.finale

        output = tmpl

        # Handle {finale} and separator cleanup as in reference replace.py
        if finale_val:
            output = re.sub(r"\{finale\}", finale_val, output, flags=re.IGNORECASE)
        else:
            # Remove surrounding separators when finale is empty: "| {finale}", "|{finale}", "{finale} |", "{finale}|"
            output = re.sub(r"\s*\|\s*\{finale\}", "", output, flags=re.IGNORECASE)
            output = re.sub(r"\{finale\}\s*\|\s*", "", output, flags=re.IGNORECASE)
            output = re.sub(r"\{finale\}", "", output, flags=re.IGNORECASE)

        # Support custom formatting e.g. {episode:02d}, {episode:03d}, {episode:1}, {season:02d}, {bolum:02d}
        output = re.sub(
            r"\{season:0?(\d+)d?\}",
            lambda match: f"{s_num:0{int(match.group(1))}d}",
            output,
            flags=re.IGNORECASE,
        )
        output = re.sub(
            r"\{episode:0?(\d+)d?\}",
            lambda match: f"{e_num:0{int(match.group(1))}d}",
            output,
            flags=re.IGNORECASE,
        )
        output = re.sub(
            r"\{bolum:0?(\d+)d?\}",
            lambda match: f"{b_num:0{int(match.group(1))}d}",
            output,
            flags=re.IGNORECASE,
        )

        replacements = {
            "{title}": m.title,
            "{season}": s_val,
            "{season_2}": s_val,
            "{season_02}": s_val,
            "{season_3}": f"{s_num:03d}",
            "{season_03}": f"{s_num:03d}",
            "{season_raw}": str(s_num),
            "{season_1}": str(s_num),
            "{episode}": e_val,
            "{episode_2}": e_val,
            "{episode_02}": e_val,
            "{episode_3}": f"{e_num:03d}",
            "{episode_03}": f"{e_num:03d}",
            "{episode_raw}": str(e_num),
            "{episode_1}": str(e_num),
            "{bolum}": b_val,
            "{bolum_2}": b_val,
            "{bolum_02}": b_val,
            "{bolum_3}": f"{b_num:03d}",
            "{bolum_03}": f"{b_num:03d}",
            "{bolum_raw}": str(b_num),
            "{bolum_1}": str(b_num),
            "{season_episode}": e_val,
            "{season_episode_raw}": str(e_num),
            "{season_episode_tag}": f"S{s_val} E{e_val}",
            "{s_e}": f"S{s_val} E{e_val}",
            "{quality}": ctx.quality,
            "{lang}": m.lang or "",
            "{source}": m.source or "",
            "{year}": year_val,
            "{sub}": m.sub or "",
            "{part}": part_val,
            "{width}": str(ctx.width),
            "{height}": str(ctx.height),
            "{codec}": ctx.codec,
            "{ext}": ctx.ext.lstrip("."),
        }

        for placeholder, value in replacements.items():
            pattern = re.compile(re.escape(placeholder), re.IGNORECASE)
            output = pattern.sub(value, output)

        output = re.sub(r" +", " ", output)
        return output.strip()
