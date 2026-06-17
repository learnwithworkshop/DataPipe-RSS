"""
core/database.py
================
SQLite persistence layer for DataPipe-RSS.

This module is the **gatekeeper** of data integrity. Before any article
reaches a connector (Google Sheets, Telegram, etc.), it MUST pass through
`DuplicateChecker.is_new()`. This prevents re-sending old articles on
every fetch cycle.

Components:
  - ArticleRecord  : Typed dataclass representing a stored article.
  - DatabaseManager: Thin wrapper around sqlite3 (connection + migration).
  - DuplicateChecker: High-level API used by main.py and connectors.

Design decisions:
  - Uses sqlite3 from stdlib — no ORM dependency.
  - Thread-safe via `check_same_thread=False` + a threading.Lock.
  - Deduplication key = SHA-256 hash of the article URL (not the full URL
    string, to keep the index compact and consistent across redirects).
  - Cross-platform paths via pathlib.Path (Linux + Windows compatible).
"""

import hashlib
import sqlite3
import threading
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from utils.logger import get_logger

log = get_logger(__name__)

# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class ArticleRecord:
    """
    Represents a single RSS article as stored in the database.

    Attributes:
        url:          The canonical article URL (used as the dedup key).
        title:        Article headline.
        summary:      Short description / first paragraph.
        published_at: Publication datetime string (ISO 8601).
        source_name:  Human-readable feed name (e.g., "BBC Technology").
        category:     Feed category label from feeds.json.
        url_hash:     SHA-256 hex of url — computed automatically if not set.
        sent_at:      UTC datetime when this record was first inserted.
        id:           Auto-assigned SQLite rowid (None before insertion).
    """
    url: str
    title: str
    summary: str
    published_at: str
    source_name: str
    category: str
    url_hash: str = field(default="")
    sent_at: str = field(default="")
    id: Optional[int] = field(default=None)

    def __post_init__(self) -> None:
        if not self.url_hash:
            self.url_hash = hashlib.sha256(self.url.encode("utf-8")).hexdigest()
        if not self.sent_at:
            self.sent_at = datetime.utcnow().isoformat()


# ---------------------------------------------------------------------------
# Low-level DB manager
# ---------------------------------------------------------------------------

class DatabaseManager:
    """
    Manages the SQLite connection and schema migration.

    Usage:
        db = DatabaseManager(Path("data/datapipe.db"))
        conn = db.get_connection()
    """

    _CREATE_TABLE_SQL = """
        CREATE TABLE IF NOT EXISTS articles (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            url_hash     TEXT    NOT NULL UNIQUE,
            url          TEXT    NOT NULL,
            title        TEXT    NOT NULL,
            summary      TEXT,
            published_at TEXT,
            source_name  TEXT,
            category     TEXT,
            sent_at      TEXT    NOT NULL
        );
    """

    _CREATE_INDEX_SQL = """
        CREATE INDEX IF NOT EXISTS idx_articles_url_hash
        ON articles (url_hash);
    """

    def __init__(self, db_path: Path) -> None:
        """
        Args:
            db_path: Full path to the .db file. Parent directory is
            created automatically if it doesn't exist.
        """
        self._db_path = db_path
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._connection: Optional[sqlite3.Connection] = None
        self._lock = threading.Lock()
        log.debug("DatabaseManager initialised. DB path: %s", self._db_path)

    def get_connection(self) -> sqlite3.Connection:
        """
        Return a shared, thread-safe SQLite connection.
        Creates and migrates the schema on the first call.

        Returns:
            An open sqlite3.Connection object.
        """
        if self._connection is None:
            with self._lock:
                if self._connection is None:  # Double-checked locking
                    self._connection = sqlite3.connect(
                        str(self._db_path),
                        check_same_thread=False,
                    )
                    self._connection.row_factory = sqlite3.Row
                    self._migrate()
                    log.info("SQLite database connected: %s", self._db_path.name)
        return self._connection

    def _migrate(self) -> None:
        """Apply schema migrations (idempotent — safe to run on every start)."""
        conn = self._connection
        try:
            conn.execute(self._CREATE_TABLE_SQL)
            conn.execute(self._CREATE_INDEX_SQL)
            conn.commit()
            log.debug("Database schema verified/migrated successfully.")
        except sqlite3.Error as exc:
            log.error("Schema migration failed: %s", exc)
            raise

    def close(self) -> None:
        """Close the database connection gracefully."""
        if self._connection:
            self._connection.close()
            self._connection = None
            log.debug("SQLite connection closed.")


# ---------------------------------------------------------------------------
# High-level duplicate checker (used by main.py)
# ---------------------------------------------------------------------------

class DuplicateChecker:
    """
    The central data-integrity guardian.

    Every article fetched from an RSS feed MUST be tested with
    `is_new()` before being dispatched to any connector. If `is_new()`
    returns True, call `mark_as_sent()` immediately after a successful
    dispatch to prevent re-sending.

    Example flow in main.py:
        checker = DuplicateChecker()
        for article in collector.fetch():
            if checker.is_new(article):
                connector.send(article)
                checker.mark_as_sent(article)
    """

    def __init__(self, db_manager: Optional[DatabaseManager] = None) -> None:
        """
        Args:
            db_manager: Inject a DatabaseManager instance (useful for testing).
                        If None, creates one from config.settings.SETTINGS.db_path.
        """
        if db_manager is None:
            from config.settings import SETTINGS
            db_manager = DatabaseManager(SETTINGS.db_path)
        self._db = db_manager
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Core API
    # ------------------------------------------------------------------

    def is_new(self, article: ArticleRecord) -> bool:
        """
        Check whether an article has NOT been sent before.
        """
        conn = self._db.get_connection()
        try:
            with self._lock:
                # Context manager added to auto-close cursor and prevent DB locks
                with conn.execute(
                    "SELECT 1 FROM articles WHERE url_hash = ? LIMIT 1;",
                    (article.url_hash,),
                ) as cursor:
                    exists = cursor.fetchone() is not None
                
                log.debug(
                    "Duplicate check [%s]: %s",
                    "DUPLICATE" if exists else "NEW",
                    article.title[:60],
                )
                return not exists
        except sqlite3.Error as exc:
            log.error("Duplicate check failed for '%s': %s", article.title[:60], exc)
            # Fail-safe: treat as duplicate to avoid re-sending on DB errors
            return False

    def mark_as_sent(self, article: ArticleRecord) -> bool:
        """
        Persist an article to the database after a successful send.

        Args:
            article: The ArticleRecord that was just dispatched.

        Returns:
            True on success, False on failure.
        """
        conn = self._db.get_connection()
        try:
            with self._lock:
                conn.execute(
                    """
                    INSERT INTO articles
                        (url_hash, url, title, summary,
                         published_at, source_name, category, sent_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?);
                    """,
                    (
                        article.url_hash,
                        article.url,
                        article.title,
                        article.summary,
                        article.published_at,
                        article.source_name,
                        article.category,
                        article.sent_at,
                    ),
                )
                conn.commit()
                log.debug("Article marked as sent: %s", article.title[:60])
                return True
        except sqlite3.IntegrityError:
            # Race condition — another thread already inserted this hash
            log.debug("Article already marked (race condition): %s", article.title[:60])
            return False
        except sqlite3.Error as exc:
            log.error("Failed to mark article as sent: %s | Error: %s", article.title[:60], exc)
            return False

    # ------------------------------------------------------------------
    # Utility / reporting
    # ------------------------------------------------------------------

    def get_total_count(self) -> int:
        """Return the total number of articles stored in the database."""
        conn = self._db.get_connection()
        try:
            cursor = conn.execute("SELECT COUNT(*) FROM articles;")
            row = cursor.fetchone()
            return row[0] if row else 0
        except sqlite3.Error as exc:
            log.error("Failed to count articles: %s", exc)
            return -1

    def get_recent(self, limit: int = 10) -> List[ArticleRecord]:
        """
        Retrieve the most recently sent articles.

        Args:
            limit: Maximum number of records to return.

        Returns:
            List of ArticleRecord objects, newest first.
        """
        conn = self._db.get_connection()
        try:
            cursor = conn.execute(
                """
                SELECT id, url_hash, url, title, summary,
                       published_at, source_name, category, sent_at
                FROM articles
                ORDER BY id DESC
                LIMIT ?;
                """,
                (limit,),
            )
            rows = cursor.fetchall()
            return [
                ArticleRecord(
                    id=row["id"],
                    url_hash=row["url_hash"],
                    url=row["url"],
                    title=row["title"],
                    summary=row["summary"] or "",
                    published_at=row["published_at"] or "",
                    source_name=row["source_name"] or "",
                    category=row["category"] or "",
                    sent_at=row["sent_at"],
                )
                for row in rows
            ]
        except sqlite3.Error as exc:
            log.error("Failed to fetch recent articles: %s", exc)
            return []

    def close(self) -> None:
        """Close the underlying database connection."""
        self._db.close()
