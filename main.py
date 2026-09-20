"""
Main entry point for Telegram Multi-Quality Video Encoder Bot.
"""

import asyncio
import sys
from pathlib import Path

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).parent.resolve()))

from pyrogram import idle
from app.bot import VideoEncoderBot
from app.utils.logging import logger


async def main() -> None:
    bot = VideoEncoderBot()

    try:
        await bot.start()
        logger.info("Bot is running and listening for messages. Press Ctrl+C to stop.")
        await idle()
    except (KeyboardInterrupt, SystemExit):
        logger.info("Shutdown signal received.")
    except Exception as e:
        logger.error(f"Fatal error in main event loop: {e}", exc_info=True)
    finally:
        await bot.stop()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        pass
