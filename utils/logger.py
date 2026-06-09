"""
utils/logger.py
===============
Centralized logging factory for DataPipe-RSS.

Every module calls `get_logger(__name__)` to get its own named logger.
All loggers share the same handlers (console + rotating file) configured
once at the root level, so log format is consistent across the app.

Usage in any module:
    from utils.logger import get_logger
    log = get_logger(__name__)
    log.info("Starting collector...")
    log.error("Feed fetch failed", exc_info=True)
"""

import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# Optional: colored console output (degrades gracefully if not installed)
# ---------------------------------------------------------------------------
try:
    import colorlog
    _COLORLOG_AVAILABLE = True
except ImportError:
    _COLORLOG_AVAILABLE = False

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
_LOG_FORMAT = (
    "%(asctime)s | %(levelname)-8s | %(name)-30s | %(message)s"
)
_COLOR_FORMAT = (
    "%(log_color)s%(asctime)s | %(levelname)-8s%(reset)s | "
    "%(name)-30s | %(message)s"
)
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"
_MAX_BYTES = 5 * 1024 * 1024   # 5 MB per log file
_BACKUP_COUNT = 3               # Keep 3 rotated files

_root_configured = False        # Guard: configure root logger only once


def _configure_root_logger(log_level: str, log_dir: Path) -> None:
    """
    Configure the root logger with a rotating file handler and a
    (optionally colored) stream handler. Called once per process.

    Args:
        log_level: String level name, e.g. "INFO", "DEBUG".
        log_dir:   Path to the directory where log files are written.
    """
    global _root_configured
    if _root_configured:
        return

    numeric_level = getattr(logging, log_level.upper(), logging.INFO)
    root = logging.getLogger()
    root.setLevel(numeric_level)

    # ── Console Handler ────────────────────────────────────────────────────
    if _COLORLOG_AVAILABLE:
        console_formatter = colorlog.ColoredFormatter(
            _COLOR_FORMAT,
            datefmt=_DATE_FORMAT,
            log_colors={
                "DEBUG":    "cyan",
                "INFO":     "green",
                "WARNING":  "yellow",
                "ERROR":    "red",
                "CRITICAL": "bold_red",
            },
        )
    else:
        console_formatter = logging.Formatter(_LOG_FORMAT, datefmt=_DATE_FORMAT)

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(numeric_level)
    console_handler.setFormatter(console_formatter)
    root.addHandler(console_handler)

    # ── Rotating File Handler ──────────────────────────────────────────────
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file: Path = log_dir / "datapipe.log"

    file_formatter = logging.Formatter(_LOG_FORMAT, datefmt=_DATE_FORMAT)
    file_handler = RotatingFileHandler(
        filename=str(log_file),
        maxBytes=_MAX_BYTES,
        backupCount=_BACKUP_COUNT,
        encoding="utf-8",
    )
    file_handler.setLevel(numeric_level)
    file_handler.setFormatter(file_formatter)
    root.addHandler(file_handler)

    _root_configured = True
    root.debug("Root logger initialised. Log file: %s", log_file)


def get_logger(name: str, log_level: Optional[str] = None) -> logging.Logger:
    """
    Return a named logger. Bootstraps the root logger on first call
    using settings from config.settings (or the supplied log_level).

    Args:
        name:      Typically __name__ of the calling module.
        log_level: Override the log level from settings (optional).

    Returns:
        A configured logging.Logger instance.
    """
    # Lazy-import to avoid circular imports; settings imports nothing from utils
    from config.settings import SETTINGS

    effective_level = (log_level or SETTINGS.log_level).upper()
    _configure_root_logger(effective_level, SETTINGS.log_dir)

    return logging.getLogger(name)
