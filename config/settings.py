"""
config/settings.py
==================
Central configuration module for DataPipe-RSS.

Loads all settings from the .env file and exposes them as typed
constants. This is the ONLY place in the codebase that reads
environment variables — all other modules import from here.

Security Rule: Never import os.environ directly in other modules.
               Always use `from config.settings import SETTINGS`.
"""

import os
import logging
from dataclasses import dataclass, field
from pathlib import Path
from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# Bootstrap: locate and load .env from the project root
# ---------------------------------------------------------------------------
# __file__ = DataPipe-RSS/config/settings.py
# PROJECT_ROOT = DataPipe-RSS/
PROJECT_ROOT: Path = Path(__file__).resolve().parent.parent

_env_path = PROJECT_ROOT / ".env"
load_dotenv(dotenv_path=_env_path)


# ---------------------------------------------------------------------------
# Feature Flags — toggle connectors without touching code
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class FeatureFlags:
    """
    Boolean switches for each output connector.
    Set to True in .env or directly here to activate a connector.
    All connectors are OFF by default for safety.
    """
    enable_google_sheets: bool = field(
        default_factory=lambda: os.getenv("ENABLE_GOOGLE_SHEETS", "false").lower() == "true"
    )
    enable_excel_online: bool = field(
        default_factory=lambda: os.getenv("ENABLE_EXCEL_ONLINE", "false").lower() == "true"
    )
    enable_telegram: bool = field(
        default_factory=lambda: os.getenv("ENABLE_TELEGRAM", "false").lower() == "true"
    )
    enable_notion: bool = field(
        default_factory=lambda: os.getenv("ENABLE_NOTION", "false").lower() == "true"
    )
    enable_ai_processor: bool = field(
        default_factory=lambda: os.getenv("ENABLE_AI_PROCESSOR", "false").lower() == "true"
    )


# ---------------------------------------------------------------------------
# Main Settings Dataclass
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class AppSettings:
    """
    Immutable, type-hinted container for all application settings.
    Instantiated once at module load time as the `SETTINGS` singleton.
    """

    # ── Project Paths ──────────────────────────────────────────────────────
    project_root: Path = PROJECT_ROOT
    db_path: Path = field(
        default_factory=lambda: PROJECT_ROOT / os.getenv("DB_PATH", "data/datapipe.db")
    )
    feeds_config_path: Path = field(
        default_factory=lambda: PROJECT_ROOT / "config" / "feeds.json"
    )
    log_dir: Path = field(
        default_factory=lambda: PROJECT_ROOT / "logs"
    )

    # ── App Behaviour ──────────────────────────────────────────────────────
    log_level: str = field(
        default_factory=lambda: os.getenv("LOG_LEVEL", "INFO").upper()
    )
    fetch_interval_seconds: int = field(
        default_factory=lambda: int(os.getenv("FETCH_INTERVAL_SECONDS", "3600"))
    )
    request_timeout_seconds: int = field(
        default_factory=lambda: int(os.getenv("REQUEST_TIMEOUT_SECONDS", "30"))
    )

    # ── Google Sheets Connector ────────────────────────────────────────────
    google_sheets_webhook_url: str = field(
        default_factory=lambda: os.getenv("GOOGLE_SHEETS_WEBHOOK_URL", "")
    )

    # ── Excel Online / Power Automate Connector ────────────────────────────
    excel_webhook_url: str = field(
        default_factory=lambda: os.getenv("EXCEL_WEBHOOK_URL", "")
    )

    # ── Telegram Connector (Future) ────────────────────────────────────────
    telegram_bot_token: str = field(
        default_factory=lambda: os.getenv("TELEGRAM_BOT_TOKEN", "")
    )
    telegram_chat_id: str = field(
        default_factory=lambda: os.getenv("TELEGRAM_CHAT_ID", "")
    )

    # ── Notion Connector (Future) ──────────────────────────────────────────
    notion_api_key: str = field(
        default_factory=lambda: os.getenv("NOTION_API_KEY", "")
    )
    notion_database_id: str = field(
        default_factory=lambda: os.getenv("NOTION_DATABASE_ID", "")
    )

    # ── AI Processor (Future) ──────────────────────────────────────────────
    openai_api_key: str = field(
        default_factory=lambda: os.getenv("OPENAI_API_KEY", "")
    )

    # ── Feature Flags ──────────────────────────────────────────────────────
    features: FeatureFlags = field(default_factory=FeatureFlags)

    def validate(self) -> None:
        """
        Runtime validation: warn if an enabled connector has no credentials.
        Called once at startup in main.py before the pipeline runs.
        """
        logger = logging.getLogger(__name__)

        if self.features.enable_google_sheets and not self.google_sheets_webhook_url:
            logger.warning(
                "ENABLE_GOOGLE_SHEETS=true but GOOGLE_SHEETS_WEBHOOK_URL is not set. "
                "Google Sheets connector will be skipped."
            )
        if self.features.enable_excel_online and not self.excel_webhook_url:
            logger.warning(
                "ENABLE_EXCEL_ONLINE=true but EXCEL_WEBHOOK_URL is not set. "
                "Excel Online connector will be skipped."
            )
        if self.features.enable_telegram and not self.telegram_bot_token:
            logger.warning(
                "ENABLE_TELEGRAM=true but TELEGRAM_BOT_TOKEN is not set."
            )
        if self.features.enable_ai_processor and not self.openai_api_key:
            logger.warning(
                "ENABLE_AI_PROCESSOR=true but OPENAI_API_KEY is not set."
            )

        # Ensure the data directory for SQLite exists
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.log_dir.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Singleton — import this everywhere
# ---------------------------------------------------------------------------
SETTINGS = AppSettings()
