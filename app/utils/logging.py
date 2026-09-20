"""
Logging configuration with secret sanitization.
"""

from __future__ import annotations

import logging
import re
import sys
from typing import Any


class SecretFilter(logging.Filter):
    """Filter that sanitizes tokens, hashes, and sensitive patterns from log records."""

    def __init__(self, secrets: list[str] | None = None) -> None:
        super().__init__()
        self.secrets = [s for s in (secrets or []) if s and len(s) > 4]
        # Regex to catch typical bot tokens like 123456789:ABCdefGHIjklMNOpqrsTUVwxyz
        self.token_pattern = re.compile(r"\b\d{8,12}:[A-Za-z0-9_-]{30,45}\b")

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = self._sanitize(record.msg)
        if record.args:
            if isinstance(record.args, dict):
                record.args = {k: self._sanitize(str(v)) for k, v in record.args.items()}
            elif isinstance(record.args, tuple):
                record.args = tuple(self._sanitize(str(v)) for v in record.args)
        return True

    def _sanitize(self, text: str) -> str:
        text = self.token_pattern.sub("[REDACTED_BOT_TOKEN]", text)
        for secret in self.secrets:
            text = text.replace(secret, "[REDACTED_SECRET]")
        return text


def setup_logger(name: str = "encoder_bot", level: int = logging.INFO) -> logging.Logger:
    """Configures and returns a sanitized logger."""
    logger = logging.getLogger(name)
    logger.setLevel(level)

    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setLevel(level)
        formatter = logging.Formatter(
            fmt="%(asctime)s | %(levelname)-8s | %(name)s:%(lineno)d - %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        handler.setFormatter(formatter)
        logger.addHandler(handler)

    # Attach secret filter
    from app.config import config
    secrets = [config.bot.bot_token, config.bot.api_hash]
    logger.addFilter(SecretFilter(secrets))
    return logger


logger = setup_logger()
