"""
connectors/google_sheets.py
============================
Google Sheets Output Connector for DataPipe-RSS.

This connector sends article data to a Google Sheet via a
Google Apps Script Web App (deployed as a webhook endpoint).

How it works:
  1. You deploy a Google Apps Script in your spreadsheet that
     accepts POST requests with JSON data and appends rows.
  2. This connector POSTs each ArticleRecord as a JSON payload
     to that script's URL.
  3. The script appends a new row to the sheet.

Google Apps Script (deploy in your Sheet — Tools → Script Editor):
  Paste the script from docs/google_apps_script.js into the editor,
  then: Deploy → New deployment → Web App → Execute as: Me →
  Who has access: Anyone → Deploy → copy the URL to .env.

Setup:
    ENABLE_GOOGLE_SHEETS=true
    GOOGLE_SHEETS_WEBHOOK_URL=https://script.google.com/macros/s/.../exec
"""

import json
from typing import Optional

import requests

from connectors import BaseConnector
from core.database import ArticleRecord
from utils.logger import get_logger
from utils.security import CredentialError, mask_url, validate_url

log = get_logger(__name__)


class GoogleSheetsConnector(BaseConnector):
    """
    Sends ArticleRecord data to Google Sheets via Apps Script webhook.

    Attributes:
        _webhook_url:   The Google Apps Script Web App URL.
        _timeout:       HTTP request timeout in seconds.
        _max_retries:   Number of retry attempts on transient failure.
    """

    def __init__(
        self,
        webhook_url: Optional[str] = None,
        timeout: Optional[int] = None,
        max_retries: int = 2,
    ) -> None:
        from config.settings import SETTINGS

        self._webhook_url: str = webhook_url or SETTINGS.google_sheets_webhook_url
        self._timeout: int = timeout or SETTINGS.request_timeout_seconds
        self._max_retries: int = max_retries
        self._enabled: bool = SETTINGS.features.enable_google_sheets

        if self._enabled:
            log.info(
                "GoogleSheetsConnector initialised. Endpoint: %s",
                mask_url(self._webhook_url),
            )

    # ------------------------------------------------------------------
    # BaseConnector interface
    # ------------------------------------------------------------------

    def is_configured(self) -> bool:
        """
        Return True only if the feature flag is on AND a webhook URL is set.
        Called by the pipeline before any send attempt.
        """
        if not self._enabled:
            log.debug("GoogleSheetsConnector: feature flag is OFF.")
            return False
        if not self._webhook_url:
            log.warning(
                "GoogleSheetsConnector: ENABLE_GOOGLE_SHEETS=true but "
                "GOOGLE_SHEETS_WEBHOOK_URL is not set."
            )
            return False
        return True

    def send(self, article: ArticleRecord) -> bool:
        """
        POST a single article as JSON to the Apps Script webhook.

        Args:
            article: The ArticleRecord to dispatch.

        Returns:
            True on HTTP 200 success, False on any error.
        """
        if not self.is_configured():
            return False

        payload = self._build_payload(article)

        for attempt in range(1, self._max_retries + 2):
            try:
                validate_url(self._webhook_url, "GOOGLE_SHEETS_WEBHOOK_URL")

                response = requests.post(
                    url=self._webhook_url,
                    data=json.dumps(payload),   # Apps Script expects raw JSON string
                    headers={"Content-Type": "application/json"},
                    timeout=self._timeout,
                )

                if response.status_code == 200:
                    log.info(
                        "✅ Google Sheets ← '%s' [attempt %d]",
                        article.title[:60],
                        attempt,
                    )
                    return True
                else:
                    log.warning(
                        "Google Sheets returned HTTP %d for '%s'. Body: %s",
                        response.status_code,
                        article.title[:50],
                        response.text[:200],
                    )

            except CredentialError as exc:
                log.error("GoogleSheetsConnector credential error: %s", exc)
                return False  # No point retrying a bad URL

            except requests.exceptions.Timeout:
                log.warning(
                    "Google Sheets request timed out (attempt %d/%d) for '%s'.",
                    attempt,
                    self._max_retries + 1,
                    article.title[:50],
                )

            except requests.exceptions.ConnectionError as exc:
                log.error(
                    "Google Sheets connection error on attempt %d: %s", attempt, exc
                )

            except requests.exceptions.RequestException as exc:
                log.error("Google Sheets unexpected request error: %s", exc)
                return False  # Non-retriable

        log.error(
            "Google Sheets: all %d attempts failed for '%s'.",
            self._max_retries + 1,
            article.title[:60],
        )
        return False

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _build_payload(article: ArticleRecord) -> dict:
        """
        Convert an ArticleRecord into the JSON payload the Apps Script expects.

        The Apps Script should read these keys to append a spreadsheet row:
            row = [timestamp, title, source, category, summary, url]

        Returns:
            Dictionary ready for json.dumps().
        """
        return {
            "timestamp":    article.sent_at,
            "title":        article.title,
            "source":       article.source_name,
            "category":     article.category,
            "summary":      article.summary,
            "url":          article.url,
            "published_at": article.published_at,
        }
