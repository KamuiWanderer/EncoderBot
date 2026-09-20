"""
Async retry utilities and Telegram FloodWait management.
"""

from __future__ import annotations

import asyncio
import functools
from typing import Any, Callable, TypeVar
from app.utils.logging import logger

T = TypeVar("T")


def async_retry(
    max_retries: int = 3,
    base_delay: float = 2.0,
    backoff_factor: float = 2.0,
    exceptions: tuple[type[Exception], ...] = (Exception,),
) -> Callable:
    """Decorator to retry async functions with exponential backoff and FloodWait awareness."""
    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        @functools.wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            attempt = 0
            delay = base_delay
            while attempt < max_retries:
                try:
                    return await func(*args, **kwargs)
                except exceptions as e:
                    attempt += 1
                    # Check for Pyrogram FloodWait
                    error_name = type(e).__name__
                    if "FloodWait" in error_name or hasattr(e, "value"):
                        wait_seconds = getattr(e, "value", 10)
                        logger.warning(
                            f"Telegram FloodWait hit in {func.__name__}. Sleeping for {wait_seconds}s (Attempt {attempt}/{max_retries})"
                        )
                        await asyncio.sleep(float(wait_seconds) + 1.0)
                        continue

                    if attempt >= max_retries:
                        logger.error(f"Function {func.__name__} failed permanently after {attempt} attempts: {e}")
                        raise e

                    logger.warning(
                        f"Retryable error in {func.__name__} ({error_name}: {e}). Retrying in {delay:.1f}s (Attempt {attempt}/{max_retries})"
                    )
                    await asyncio.sleep(delay)
                    delay *= backoff_factor
            return None
        return wrapper
    return decorator
