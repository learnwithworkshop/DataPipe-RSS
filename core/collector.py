"""
core/collector.py
=================
RSS Feed Collector — the data ingestion engine of DataPipe-RSS.

Responsibilities:
  1. Load the list of active feeds from config/feeds.json.
  2. Fetch each RSS/Atom feed using `feedparser`.
  3. Parse each entry into a typed `ArticleRecord`.
  4. Yield only valid, non-empty records (schema validation).
  5. Handle per-feed errors gracefully — one bad feed must NOT
     stop the rest of the pipeline.

This module produces data; it does NOT check for duplicates.
That responsibility belongs exclusively to core/database.py.

Usage in main.py:
    from core.collector import FeedCollector
    collector = FeedCollector()
    articles = collector.collect_all()
"""

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Generator, List, Optional

import feedparser
from bs4 import BeautifulSoup

from core.database import ArticleRecord
from utils.logger import get_logger

log = get_logger(__name__)


# ---------------------------------------------------------------------------
# Feed config model
# ---------------------------------------------------------------------------

class FeedConfig:
    """
    Represents a single RSS feed entry from config/feeds.json.

    Attributes:
        id:       Unique identifier string (e.g., "bbc_tech").
        name:     Human-readable label (e.g., "BBC Technology").
        url:      The RSS/Atom feed URL.
        category: Topic category label stored with each article.
        active:   If False, this feed is skipped during collection.
    """
    __slots__ = ("id", "name", "url", "category", "active")

    def __init__(
        self,
        id: str,
        name: str,
        url: str,
        category: str = "Uncategorised",
        active: bool = True,
    ) -> None:
        self.id = id
        self.name = name
        self.url = url
        self.category = category
        self.active = active

    def __repr__(self) -> str:
        return f"FeedConfig(id={self.id!r}, active={self.active})"


# ---------------------------------------------------------------------------
# Collector
# ---------------------------------------------------------------------------

class FeedCollector:
    """
    Loads feed configurations and fetches articles from all active feeds.

    Args:
        feeds_config_path: Override the default path to feeds.json.
        request_timeout:   HTTP timeout in seconds per feed request.
    """

    def __init__(
        self,
        feeds_config_path: Optional[Path] = None,
        request_timeout: Optional[int] = None,
    ) -> None:
        from config.settings import SETTINGS

        self._feeds_path: Path = feeds_config_path or SETTINGS.feeds_config_path
        self._timeout: int = request_timeout or SETTINGS.request_timeout_seconds
        self._feeds: List[FeedConfig] = self._load_feeds()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def collect_all(self) -> List[ArticleRecord]:
        """
        Fetch all active RSS feeds and return every parsed article.

        Returns:
            A flat list of ArticleRecord objects from all feeds.
            May be empty if all feeds fail or are empty.
        """
        active_feeds = [f for f in self._feeds if f.active]
        log.info(
            "Starting collection. Active feeds: %d / %d",
            len(active_feeds),
            len(self._feeds),
        )

        all_articles: List[ArticleRecord] = []
        for feed_config in active_feeds:
            articles = list(self._fetch_feed(feed_config))
            log.info(
                "Feed [%s] → %d articles fetched.", feed_config.name, len(articles)
            )
            all_articles.extend(articles)

        log.info("Collection complete. Total articles fetched: %d", len(all_articles))
        return all_articles

    def collect_feed_by_id(self, feed_id: str) -> List[ArticleRecord]:
        """
        Fetch a single feed by its config id.

        Args:
            feed_id: The 'id' field from feeds.json (e.g., "bbc_tech").

        Returns:
            List of ArticleRecord objects, or empty list if not found.
        """
        feed = next((f for f in self._feeds if f.id == feed_id), None)
        if not feed:
            log.warning("Feed id '%s' not found in feeds.json.", feed_id)
            return []
        return list(self._fetch_feed(feed))

    # ------------------------------------------------------------------
    # Internal: loading feeds config
    # ------------------------------------------------------------------

    def _load_feeds(self) -> List[FeedConfig]:
        """
        Parse config/feeds.json into a list of FeedConfig objects.

        Returns:
            List of FeedConfig; empty list if file is missing or malformed.
        """
        if not self._feeds_path.exists():
            log.error(
                "feeds.json not found at: %s — no feeds will be collected.",
                self._feeds_path,
            )
            return []

        try:
            with self._feeds_path.open(encoding="utf-8") as fh:
                raw: list = json.load(fh)

            feeds: List[FeedConfig] = []
            for item in raw:
                if not isinstance(item, dict):
                    continue
                feeds.append(
                    FeedConfig(
                        id=item.get("id", "unknown"),
                        name=item.get("name", "Unknown Feed"),
                        url=item.get("url", ""),
                        category=item.get("category", "Uncategorised"),
                        active=bool(item.get("active", True)),
                    )
                )
            log.info("Loaded %d feed(s) from %s.", len(feeds), self._feeds_path.name)
            return feeds

        except (json.JSONDecodeError, OSError) as exc:
            log.error("Failed to load feeds.json: %s", exc)
            return []

    # ------------------------------------------------------------------
    # Internal: fetching and parsing a single feed
    # ------------------------------------------------------------------

    def _fetch_feed(
        self, feed_config: FeedConfig
    ) -> Generator[ArticleRecord, None, None]:
        """
        Fetch a single RSS/Atom feed and yield ArticleRecord objects.

        Uses feedparser's built-in HTTP handling with a custom User-Agent.
        Errors are caught and logged; the generator simply stops on failure.

        Args:
            feed_config: The FeedConfig entry to fetch.

        Yields:
            ArticleRecord objects, one per valid feed entry.
        """
        if not feed_config.url:
            log.warning("Feed '%s' has no URL — skipping.", feed_config.id)
            return

        log.debug("Fetching: %s", feed_config.url)
        try:
            parsed = feedparser.parse(
                feed_config.url,
                agent="DataPipe-RSS/1.0 (+https://github.com/your-org/datapipe-rss)",
                request_headers={"Connection": "close"},
            )

            # feedparser sets bozo=True for malformed XML (still usable)
            if parsed.bozo:
                log.warning(
                    "Feed '%s' has parse warnings (bozo): %s",
                    feed_config.name,
                    str(parsed.bozo_exception)[:100],
                )

            if not parsed.entries:
                log.info("Feed '%s' returned 0 entries.", feed_config.name)
                return

            for entry in parsed.entries:
                article = self._parse_entry(entry, feed_config)
                if article:
                    yield article

        except Exception as exc:
            log.error(
                "Unexpected error fetching feed '%s': %s", feed_config.name, exc
            )

    def _parse_entry(
        self, entry: feedparser.FeedParserDict, feed_config: FeedConfig
    ) -> Optional[ArticleRecord]:
        """
        Convert a raw feedparser entry dict into a typed ArticleRecord.

        Args:
            entry:       A single feedparser entry.
            feed_config: Parent feed config (for name/category).

        Returns:
            An ArticleRecord, or None if the entry is missing critical fields.
        """
        # ── URL (required) ─────────────────────────────────────────────────
        url: str = entry.get("link", "").strip()
        if not url:
            log.debug("Skipping entry with no URL from feed '%s'.", feed_config.name)
            return None

        # ── Title (required) ───────────────────────────────────────────────
        title: str = self._clean_text(entry.get("title", "")).strip()
        if not title:
            log.debug("Skipping entry with no title: %s", url[:80])
            return None

        # ── Summary (optional) ─────────────────────────────────────────────
        # Try 'summary', then 'content', then empty string
        raw_summary = entry.get("summary", "")
        if not raw_summary and entry.get("content"):
            raw_summary = entry["content"][0].get("value", "")
        summary: str = self._strip_html(raw_summary)[:500]  # Truncate to 500 chars

        # ── Published date ─────────────────────────────────────────────────
        published_at: str = self._parse_date(entry)

        return ArticleRecord(
            url=url,
            title=title,
            summary=summary,
            published_at=published_at,
            source_name=feed_config.name,
            category=feed_config.category,
        )

    # ------------------------------------------------------------------
    # Internal: text cleaning helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _clean_text(text: str) -> str:
        """Remove excessive whitespace and newlines from a string."""
        return " ".join(text.split())

    @staticmethod
    def _strip_html(html_text: str) -> str:
        """
        Remove HTML tags from a string using BeautifulSoup.
        Falls back to standard html.parser if lxml is missing, or regex on total failure.
        """
        if not html_text:
            return ""
        try:
            # Fallback guard for environments missing lxml
            parser = "lxml"
            try:
                import lxml
            except ImportError:
                parser = "html.parser"
                
            soup = BeautifulSoup(html_text, parser)
            return " ".join(soup.get_text(separator=" ").split())
        except Exception:
            # Fallback: crude tag stripping without bs4
            import re
            return re.sub(r"<[^>]+>", "", html_text)
    @staticmethod
    def _parse_date(entry: feedparser.FeedParserDict) -> str:
        """
        Extract a publication date from a feedparser entry.

        Tries `published_parsed` (struct_time) first, then `updated_parsed`,
        then the raw `published` string, falling back to UTC now.

        Returns:
            ISO 8601 datetime string, e.g. "2024-03-15T10:30:00".
        """
        for attr in ("published_parsed", "updated_parsed"):
            time_struct = entry.get(attr)
            if time_struct:
                try:
                    return datetime(*time_struct[:6]).isoformat()
                except (TypeError, ValueError):
                    continue

        # Try the raw string value
        raw = entry.get("published", "") or entry.get("updated", "")
        if raw:
            return str(raw)

        # Last resort
        return datetime.utcnow().isoformat()
