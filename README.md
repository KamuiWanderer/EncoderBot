# 🎬 Telegram Multi-Quality Video Encoder Bot

A high-performance, modular, production-grade Telegram video encoding bot built with Python, Pyrogram/Pyrofork, FFmpeg, and SQLite.

The bot downloads a source video **exactly once**, sequentially encodes it to multiple user-selected resolutions (e.g., `240p`, `360p`, `480p`, `720p`, `1080p`), uploads each generated output in ascending quality order with custom thumbnails, HTML captions, and Episode Headers, and supports **Private Staging** to deliver multi-episode batches in 100% perfect order without filling local disk space.

---

## ✨ Key Features

- **Single-Source Download**: Downloads a 4 GB source video only once, regardless of how many output qualities are requested.
- **Sequential Multi-Quality Pipeline**: Encodes outputs one by one (e.g. `240p` ➔ `360p` ➔ `480p` ➔ `720p` ➔ `1080p`) to keep CPU and thermals low.
- **Configurable Upload & Staging System**:
  - **Immediate Mode**: Uploads and deletes local files as each quality finishes.
  - **Private Staging (Ordered Delivery)**: Stashes encoded qualities on a private Telegram staging buffer channel during encoding, and upon batch completion, delivers them to the public channel in the exact structured order:
    1. Episode Header Banner
    2. Quality Videos (ascending `240p` ➔ `1080p`)
    3. Post-Episode Sticker
    4. Next Episode...
- **Hyperlinked Batch Review Card**:
  - Automatically parses multiple forwarded videos or channel link ranges (`/batch <start_link> <end_link>`).
  - Displays a clickable interactive card with direct links to inspect the source video message, preview the mapped thumbnail cover, and verify rendered HTML captions.
- **Episode Header System**: Automatically sends a banner before each episode's video batch (e.g. `🎬 {title} — S{season} E{episode} | {finale}`). Configurable with `/setheader <template>`.
- **Global Season-to-Bölüm Mapping**: `/setseason <season> <bolum>` (e.g., `/setseason 2 11` maps Season 2 Episode 1 ➔ Bölüm 11, S02E02 ➔ Bölüm 12, S02E10 ➔ Bölüm 20).
- **Decoupled Thumbnail Management**: Standalone `/thumbs` subsystem supporting single covers or multi-episode channel range scanner with detailed preview and link tracking.
- **Standardized UI Button Styling**:
  - 🟢 **Success**: `✅ Confirm`, `🚀 Start`, `💾 Save`, `➡️ Continue`, `➕ Add New`
  - 🔴 **Danger**: `❌ Cancel`, `🗑️ Delete`, `🔄 Reset`, `🚫 No Thumbnail`
  - 🔵 **Primary**: Navigation, selection toggles, and options
- **Anti-Flood Rate Limiter**: Configurable send delay (`0.5s` to `3.0s`) between uploads to prevent Telegram FloodWait.

---

## 📁 Project Architecture

```text
D:\Encoder Bot/
├── app/
│   ├── bot.py                  # Client setup & event dispatching
│   ├── config.py               # Settings & environment variables
│   │
│   ├── database/
│   │   ├── database.py         # Async SQLite engine (aiosqlite)
│   │   └── models.py           # Dataclasses & SQLite schema
│   │
│   ├── encoder/
│   │   ├── benchmark.py        # Hardware encoder benchmark tool
│   │   ├── ffmpeg.py           # Async FFmpeg subprocess manager
│   │   ├── presets.py          # Resolution scaling & quality sorting
│   │   ├── probe.py            # FFprobe stream inspector
│   │   ├── profiles.py         # Standard, Mobile, HQ encoding profiles
│   │   └── progress.py         # Machine-readable progress parser
│   │
│   ├── handlers/
│   │   ├── batch.py            # /batch & hyperlinked review cards
│   │   ├── caption.py          # /setcaption, /caption, /clearcaption
│   │   ├── destination.py      # /setdestination, /sticker, /setquality
│   │   ├── encode.py           # /encode command & wizard callbacks
│   │   ├── filename.py         # /setfilename, /filename, /clearfilename
│   │   ├── season.py           # /setseason, /seasons
│   │   ├── settings.py         # /settings control panel
│   │   ├── start.py            # /start & /help
│   │   ├── status.py           # /status & /cancel
│   │   ├── thumbnails.py       # /thumbs, /thumb, /clearthumbs
│   │   └── upload_settings.py  # /uploadsettings, /setheader, /setstaging, /setdelay
│   │
│   ├── jobs/
│   │   ├── job.py              # EncodeJob entity & states
│   │   ├── queue.py            # Single-worker async queue & staging delivery
│   │   └── recovery.py         # Startup crash recovery
│   │
│   ├── managers/
│   │   ├── batch_manager.py
│   │   ├── caption_manager.py
│   │   ├── destination_manager.py
│   │   ├── encode_manager.py
│   │   ├── filename_manager.py
│   │   ├── settings_manager.py
│   │   ├── sticker_manager.py
│   │   ├── thumbnail_manager.py
│   │   └── upload_config_manager.py
│   │
│   ├── metadata/
│   │   ├── parser.py           # NLP/Regex metadata extractor
│   │   ├── placeholders.py     # Template rendering & HTML preservation
│   │   └── season.py           # Season-to-Bölüm calculator & Finale detector
│   │
│   ├── telegram/
│   │   ├── downloader.py       # Single-pass downloader
│   │   ├── links.py            # Public/private link parser
│   │   ├── media.py            # Video/document/photo validator
│   │   └── uploader.py         # Sequential video & sticker uploader
│   │
│   └── utils/
│       ├── disk.py             # Disk space estimator & safety check
│       ├── files.py            # Safe filename sanitization & file helpers
│       ├── formatting.py       # Bytes, duration, ETA, and progress bar
│       ├── logging.py          # Secret-scrubbing logger
│       ├── retry.py            # FloodWait retry decorator
│       └── ui.py               # Standardized button styling components
│
├── reference/                  # Reference files (replace.py, thumbnail_manager.py, upload_manager.py, sort_manager.py)
├── storage/                    # Local storage (downloads, outputs, thumbnails, temp)
├── tests/                      # Automated test suite (20 passed)
├── .env.example
├── README.md
├── requirements.txt
└── main.py                     # Application startup entry point
```

---

## 🤖 Command Reference

| Command | Description |
|---|---|
| `/start` | Welcome message and interactive quick-start menu |
| `/help` | Detailed command help & placeholder documentation |
| `/encode` | Launch the single video encoding wizard |
| `/batch [start_link] [end_link]` | Launch the multi-video batch encoder with hyperlinked review |
| `/status` | View real-time active job progress, speed, ETA, and queue count |
| `/cancel` | Cancel the currently active encoding job |
| `/settings` | Open main preferences control panel |
| `/uploadsettings` | Open dedicated Upload & Publishing configuration panel |
| `/setheader <template>` | Set Episode Header banner template |
| `/clearheader` | Clear Episode Header |
| `/setstaging <channel>` | Set private staging channel for Ordered Delivery |
| `/setdelay <seconds>` | Set post-upload anti-flood delay (e.g. `1.0`) |
| `/setfilename <template>` | Set custom filename format (e.g. `{title} S{season} E{episode} {quality}.mp4`) |
| `/filename` | View active filename format |
| `/clearfilename` | Reset to default filename format |
| `/setcaption <template>` | Set custom HTML caption template |
| `/caption` | View active caption template |
| `/clearcaption` | Clear caption template |
| `/thumbs` / `/thumb` | Open thumbnail manager (Single / Multi-range scan) |
| `/clearthumbs` | Clear active thumbnail configuration |
| `/setseason <season> <bolum>` | Set global Bölüm offset (e.g. `/setseason 2 11`) |
| `/seasons` | View all configured season mappings |
| `/setdestination <@channel>` | Set default target channel or chat ID |
| `/sticker` | Reply to any sticker with `/sticker` to send upon job completion |
| `/setquality <qualities...>` | Set default quality list (e.g. `/setquality 480p 720p 1080p`) |
| `/setprofile <profile>` | Set default profile (`standard`, `mobile`, `hq`) |
| `/reset` | Reset all user preferences to defaults |

---

## 🧪 Automated Tests

Run pytest across all unit and integration test suites:
```bash
python -m pytest tests -v
```

---

## 🚀 Running the Bot

Run from your terminal in `D:\Encoder Bot`:
```powershell
python "D:\Encoder Bot\main.py"
```
