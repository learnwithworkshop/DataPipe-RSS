"""
connectors/__init__.py
======================
Output connector interface and registry for DataPipe-RSS.

Every connector must:
  1. Inherit from BaseConnector.
  2. Implement the `send(article)` method.
  3. Implement the `is_configured()` method.
  4. Use utils/security.py for credential validation.
  5. NOT touch core/database.py — the pipeline (main.py) handles that.

Adding a new connector (e.g., Telegram):
  - Create connectors/telegram.py
  - Implement BaseConnector
  - Add the instance to the connector list in main.py

No changes are needed in this file or main.py's core logic.
"""

from abc import ABC, abstractmethod
from typing import List

from core.database import ArticleRecord
from utils.logger import get_logger

log = get_logger(__name__)


class BaseConnector(ABC):
    """
    Abstract base class that every output connector must implement.

    Guarantees a consistent interface so main.py can call
    `connector.send(article)` regardless of the destination.
    """

    @abstractmethod
    def is_configured(self) -> bool:
        """
        Return True if this connector has valid credentials and is
        enabled in feature flags. Called before any send() attempt.
        """
        ...

    @abstractmethod
    def send(self, article: ArticleRecord) -> bool:
        """
        Dispatch a single article to the output destination.

        Args:
            article: The ArticleRecord to send.

        Returns:
            True on success, False on failure (should NOT raise).
        """
        ...

    def send_batch(self, articles: List[ArticleRecord]) -> int:
        """
        Send multiple articles. Returns the count of successful sends.
        Subclasses may override this for bulk API endpoints.

        Args:
            articles: List of ArticleRecord objects to send.

        Returns:
            Number of articles successfully sent.
        """
        success_count = 0
        for article in articles:
            if self.send(article):
                success_count += 1
        return success_count

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(configured={self.is_configured()})"


__all__ = ["BaseConnector"]
