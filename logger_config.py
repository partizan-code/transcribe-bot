"""Configure Loguru once at application startup."""

import sys

from loguru import logger

import config

LOG_FORMAT = (
    "<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | "
    "<level>{level: <8}</level> | "
    "<cyan>{module}:{function}:{line}</cyan> | <level>{message}</level>"
)


def setup_logging() -> None:
    """Send INFO+ to stderr and ERROR+ to error.log beside this module."""
    logger.remove()
    logger.add(
        sys.stderr,
        level="INFO",
        format=LOG_FORMAT,
        backtrace=False,
        diagnose=False,
    )
    logger.add(
        config.ERROR_LOG_PATH,
        level="ERROR",
        format=LOG_FORMAT,
        colorize=False,
        encoding="utf-8",
        delay=True,
        backtrace=False,
        diagnose=False,
    )
