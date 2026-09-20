"""
Unit tests for MetadataParser.
"""

from app.metadata.parser import MetadataParser


def test_standard_season_episode():
    meta = MetadataParser.parse("The.Great.Seljuks.S01E05.1080p.WEB-DL.Dual.Audio.mp4")
    assert meta.title == "The Great Seljuks"
    assert meta.season == 1
    assert meta.episode == 5
    assert meta.formatted_season == "01"
    assert meta.formatted_episode == "05"
    assert meta.quality == "1080p"
    assert meta.source == "WEB-DL"
    assert meta.lang == "Dual Audio"


def test_space_separated_season_episode():
    meta = MetadataParser.parse("Kurulus Osman S02 E15 720p HDTV.mkv")
    assert meta.title == "Kurulus Osman"
    assert meta.season == 2
    assert meta.episode == 15
    assert meta.quality == "720p"
    assert meta.source == "HDTV"


def test_standalone_episode_and_bolum():
    meta1 = MetadataParser.parse("Ertugrul Episode 10.mp4")
    assert meta1.episode == 10

    meta2 = MetadataParser.parse("Teskilat Bölüm 24 1080p.mp4")
    assert meta2.bolum == 24
    assert meta2.episode == 24


def test_quality_and_special_resolutions():
    meta_2k = MetadataParser.parse("Movie.Title.2024.2K.WEB-DL.mkv")
    assert meta_2k.quality == "1440p"
    assert meta_2k.year == 2024

    meta_4k = MetadataParser.parse("Avatar.2022.4K.UHD.BluRay.mp4")
    assert meta_4k.quality == "2160p"
    assert meta_4k.year == 2022
    assert meta_4k.source == "BLURAY"


def test_subtitles_and_finale():
    meta = MetadataParser.parse("Stranger.Things.S04E09.Season.Finale.1080p.NF.WEB-DL.EngSub.mkv")
    assert meta.season == 4
    assert meta.episode == 9
    assert meta.finale == "Finale"
    assert meta.sub in ("EngSub", "Engsub")
