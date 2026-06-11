"""
connectors/excel_online.py
===========================
Microsoft Excel Online Output Connector for DataPipe-RSS.

This connector sends article data to an Excel Online spreadsheet via a
Microsoft Power Automate HTTP trigger (or any MS Graph-compatible webhook).

How it works:
  1. You create a Power Automate flow with an HTTP Request trigger.
  2. The flow parses the JSON body and uses "Add a row into a table"
     to write data to an Excel Online table.
  3. Copy the flow's HTTP trigger URL into .env.

Power Automate Setup:
  - Create flow: Instant → HTTP Request trigger
  - Request Body JSON Schema (paste into flow):
    {
      "type": "object",
      "properties": {
        "title":        {"type": "string"},
        "source":       {"type": "string"},
        "category":     {"type": "string"},
        "summary":      {"type": "string"},
        "url":          {"type": "string"},
        "published_at": {"type": "string"},
        "timestamp":    {"type": "string"}
      }
    }
  - Add step: Excel Online (Business) → Add a row into a table
  - Map each JSON field to the corresponding Excel column.

Setup (.env):
    ENABLE_EXCEL_ONLINE=true
    EXCEL_WEBHOOK_URL=https://prod-xx.eastus.logic.azure.com/workflows/.../triggers/...
"""

import json
from typing import Optional

import requests

from connectors import BaseConnector
from core.database import ArticleRecord
from utils.logger import get_logger
from utils.security import CredentialError, mask_url, validate_url

log = get_logger(__name__)


class ExcelOnlineConnector(BaseConnector):
    """
    Sends ArticleRecord data to Excel Online via a Power Automate webhook.

    Attributes:
        _webhook_url: Power Automate HTTP trigger URL.
        _timeout:     HTTP request timeout in seconds.
        _max_retries: Retry count on transient failures.
    """

    def __init__(
        self,
        webhook_url: Optional[str] = None,
        timeout: Optional[int] = None,
        max_retries: int = 2,
    ) -> None:
        from config.settings import SETTINGS

        self._webhook_url: str = webhook_url or SETTINGS.excel_webhook_url
        self._timeout: int = timeout or SETTINGS.request_timeout_seconds
        self._max_retries: int = max_retries
        self._enabled: bool = SETTINGS.features.enable_excel_online

        if self._enabled:
            log.info(
                "ExcelOnlineConnector initialised. Endpoint: %s",
                mask_url(self._webhook_url),
            )

    # ------------------------------------------------------------------
    # BaseConnector interface
    # ------------------------------------------------------------------

    def is_configured(self) -> bool:
        """
        Return True only if the feature flag is on AND a webhook URL is set.
        """
        if not self._enabled:
            log.debug("ExcelOnlineConnector: feature flag is OFF.")
            return False
        if not self._webhook_url:
            log.warning(
                "ExcelOnlineConnector: ENABLE_EXCEL_ONLINE=true but "
                "EXCEL_WEBHOOK_URL is not set."
            )
            return False
        return True

    def send(self, article: ArticleRecord) -> bool:
        """
        POST a single article as JSON to the Power Automate webhook.

        Power Automate expects a 202 Accepted on async flows or
        200 OK on sync flows. Both are treated as success.

        Args:
            article: The ArticleRecord to dispatch.

        Returns:
            True on success (HTTP 200 or 202), False on any error.
        """
        if not self.is_configured():
            return False

        payload = self._build_payload(article)

        for attempt in range(1, self._max_retries + 2):
            try:
                validate_url(self._webhook_url, "EXCEL_WEBHOOK_URL")

                response = requests.post(
                    url=self._webhook_url,
                    json=payload,
                    headers={
                        "Content-Type": "application/json",
                        "Accept":       "application/json",
                    },
                    timeout=self._timeout,
                )

                # Power Automate async flows return 202; sync return 200
                if response.status_code in (200, 202):
                    log.info(
                        "✅ Excel Online ← '%s' [HTTP %d, attempt %d]",
                        article.title[:60],
                        response.status_code,
                        attempt,
                    )
                    return True
                elif response.status_code == 429:
                    # Rate limited — back off briefly before retry
                    log.warning(
                        "Excel Online rate-limited (429). Attempt %d/%d.",
                        attempt,
                        self._max_retries + 1,
                    )
                    import time
                    time.sleep(5 * attempt)  # Progressive back-off
                else:
                    log.warning(
                        "Excel Online returned HTTP %d for '%s'. Body: %s",
                        response.status_code,
                        article.title[:50],
                        response.text[:200],
                    )

            except CredentialError as exc:
                log.error("ExcelOnlineConnector credential error: %s", exc)
                return False

            except requests.exceptions.Timeout:
                log.warning(
                    "Excel Online request timed out (attempt %d/%d).", attempt, self._max_retries + 1
                )

            except requests.exceptions.ConnectionError as exc:
                log.error("Excel Online connection error: %s", exc)

            except requests.exceptions.RequestException as exc:
                log.error("Excel Online unexpected error: %s", exc)
                return False

        log.error(
            "Excel Online: all %d attempts failed for '%s'.",
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
        Build the JSON payload matching the Power Automate flow schema.

        Returns:
            Dictionary with string values for all article fields.
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
