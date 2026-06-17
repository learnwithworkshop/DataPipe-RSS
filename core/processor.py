"""
core/processor.py
=================
Text Processing Pipeline for DataPipe-RSS.

This module sits between the Collector (data ingestion) and the
Connectors (data dispatch). It transforms raw ArticleRecords into
clean, enriched records ready for output.

Current processors (always active):
  - TextCleaner   : Normalises whitespace, truncates long fields.
  - SummaryTrimmer: Enforces max character limits per connector type.

Future plug-in processors (activated via feature flags):
  - AIProcessor   : Uses OpenAI to summarise/tag/translate articles.
                    Enabled when ENABLE_AI_PROCESSOR=true in .env.

Design: The Pipeline class accepts a list of processor objects. Adding
a new processor requires zero changes to main.py — just instantiate it
and append it to the pipeline.

Usage in main.py:
    from core.processor import Pipeline, TextCleaner, AIProcessor
    pipeline = Pipeline([TextCleaner(), AIProcessor()])
    clean_articles = pipeline.run(raw_articles)
"""

import re
from abc import ABC, abstractmethod
from typing import List, Optional

from core.database import ArticleRecord
from utils.logger import get_logger

log = get_logger(__name__)


# ---------------------------------------------------------------------------
# Base Processor Interface
# ---------------------------------------------------------------------------

class BaseProcessor(ABC):
    """
    Abstract base class for all processors.

    Each processor receives a list of ArticleRecord objects and returns
    a (potentially modified or filtered) list. Processors are composable
    and stateless by convention.
    """

    @abstractmethod
    def process(self, articles: List[ArticleRecord]) -> List[ArticleRecord]:
        """
        Transform a batch of articles.

        Args:
            articles: Input list of ArticleRecord objects.

        Returns:
            Processed list. May be smaller (if filtering) or same size.
        """
        ...

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}()"


# ---------------------------------------------------------------------------
# Processor 1: TextCleaner (always active)
# ---------------------------------------------------------------------------

class TextCleaner(BaseProcessor):
    """
    Normalises text fields in every ArticleRecord:
      - Collapses multiple whitespace/newlines.
      - Removes non-printable / control characters.
      - Truncates title and summary to safe limits.

    Args:
        max_title_length:   Max characters for title (default: 200).
        max_summary_length: Max characters for summary (default: 1000).
    """

    def __init__(
        self,
        max_title_length: int = 200,
        max_summary_length: int = 1000,
    ) -> None:
        self._max_title = max_title_length
        self._max_summary = max_summary_length

    def process(self, articles: List[ArticleRecord]) -> List[ArticleRecord]:
        cleaned: List[ArticleRecord] = []
        for article in articles:
            try:
                cleaned_article = ArticleRecord(
                    url=article.url.strip(),
                    title=self._clean(article.title, self._max_title),
                    summary=self._clean(article.summary, self._max_summary),
                    published_at=article.published_at,
                    source_name=article.source_name,
                    category=article.category,
                    url_hash=article.url_hash,  # Preserve pre-computed hash
                    sent_at=article.sent_at,
                    id=article.id,
                )
                cleaned.append(cleaned_article)
            except Exception as exc:
                log.warning("TextCleaner failed on article '%s': %s", article.title[:40], exc)
                cleaned.append(article)  # Keep original on failure
        log.debug("TextCleaner processed %d articles.", len(cleaned))
        return cleaned

    @staticmethod
    def _clean(text: str, max_length: int) -> str:
        """Remove control chars, normalise whitespace, and truncate."""
        if not text:
            return ""
        # Remove non-printable control characters (except newline/tab)
        text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", text)
        # Collapse whitespace
        text = " ".join(text.split())
        # Truncate with ellipsis
        if len(text) > max_length:
            text = text[: max_length - 3] + "..."
        return text


# ---------------------------------------------------------------------------
# Processor 2: CategoryFilter (optional, utility)
# ---------------------------------------------------------------------------

class CategoryFilter(BaseProcessor):
    """
    Filters articles to only include specific categories.

    Useful when you want to send only "Technology" articles to one
    connector and "Science" articles to another.

    Args:
        allowed_categories: List of category strings to keep.
                            If empty, all categories pass through.
    """

    def __init__(self, allowed_categories: Optional[List[str]] = None) -> None:
        self._allowed = set(allowed_categories or [])

    def process(self, articles: List[ArticleRecord]) -> List[ArticleRecord]:
        if not self._allowed:
            return articles
        filtered = [a for a in articles if a.category in self._allowed]
        log.debug(
            "CategoryFilter: %d → %d articles (allowed: %s).",
            len(articles),
            len(filtered),
            self._allowed,
        )
        return filtered


# ---------------------------------------------------------------------------
# Processor 3: AIProcessor (future — activated by ENABLE_AI_PROCESSOR=true)
# ---------------------------------------------------------------------------

class AIProcessor(BaseProcessor):
    """
    AI-powered article enrichment using the OpenAI API.

    When active, this processor:
      1. Generates a 2-sentence summary of the article.
      2. Adds 3 topic tags.
      3. Optionally translates the summary to a target language.

    Activation:
        Set ENABLE_AI_PROCESSOR=true and OPENAI_API_KEY=sk-... in .env.

    Note:
        This processor makes one API call per article. Use judiciously
        to manage costs. Consider adding a per-run limit.
    """

    DEFAULT_SYSTEM_PROMPT = (
        "You are a concise news analyst. Given an article title and raw summary, "
        "respond ONLY with a JSON object: "
        '{"summary": "<2 sentence summary>", "tags": ["tag1", "tag2", "tag3"]}. '
        "No markdown, no extra text."
    )

    def __init__(self, target_language: Optional[str] = None) -> None:
        """
        Args:
            target_language: If set (e.g., "Hindi"), the AI will translate
                             the summary into that language.
        """
        self._target_language = target_language
        self._client = None  # Lazy-initialised on first use

    def _get_client(self) -> "openai.OpenAI":
        """
        Lazily initialise and return the OpenAI client on first use.

        Returns:
            An openai.OpenAI instance configured with the API key from settings.

        Raises:
            ImportError: If the openai package is not installed.
        """
        if self._client is None:
            try:
                import openai
                from config.settings import SETTINGS
                self._client = openai.OpenAI(api_key=SETTINGS.openai_api_key)
            except ImportError:
                raise ImportError(
                    "openai package not installed. "
                    "Run: pip install openai"
                )
        return self._client

    def process(self, articles: List[ArticleRecord]) -> List[ArticleRecord]:
        from config.settings import SETTINGS
        if not SETTINGS.features.enable_ai_processor:
            log.debug("AIProcessor: feature flag is OFF — skipping.")
            return articles

        # Safe Guard: Validate openai package installation before processing loop
        try:
            self._get_client()
        except ImportError as exc:
            log.error("AIProcessor skipped: %s. Please install dependencies.", exc)
            return articles

        enriched: List[ArticleRecord] = []
        for article in articles:
            try:
                improved = self._enrich(article)
                enriched.append(improved)
            except Exception as exc:
                log.warning(
                    "AIProcessor failed for '%s': %s — keeping original.",
                    article.title[:50],
                    exc,
                )
                enriched.append(article)
        log.info("AIProcessor enriched %d/%d articles.", len(enriched), len(articles))
        return enriched
    def _enrich(self, article: ArticleRecord) -> ArticleRecord:
        """
        Call the OpenAI API to summarise and tag a single article.

        Sends a structured prompt containing the article title and raw summary,
        then parses the JSON response to extract a clean 2-sentence summary and
        up to 3 topic tags. The enriched summary replaces the original.

        Args:
            article: The ArticleRecord to enrich.

        Returns:
            A new ArticleRecord with an AI-generated summary and tag string.

        Raises:
            openai.OpenAIError: On API failure (caught by process()).
            json.JSONDecodeError: If the model returns malformed JSON (caught by process()).
        """
        import json as _json

        client = self._get_client()
        lang_instruction = (
            f" Translate the summary to {self._target_language}."
            if self._target_language
            else ""
        )
        user_prompt = (
            f"Title: {article.title}\n"
            f"Raw Summary: {article.summary[:600]}\n"
            f"{lang_instruction}"
        )

        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": self.DEFAULT_SYSTEM_PROMPT},
                {"role": "user",   "content": user_prompt},
            ],
            temperature=0.3,
            max_tokens=200,
        )

        raw_json = response.choices[0].message.content.strip()
        data: dict = _json.loads(raw_json)

        # Build tag string (appended to summary for downstream connectors)
        tags_str = ", ".join(data.get("tags", []))
        enriched_summary = (
            data.get("summary", article.summary)
            + (f"\n🏷️ Tags: {tags_str}" if tags_str else "")
        )

        # Return a new ArticleRecord with the enriched summary
        return ArticleRecord(
            url=article.url,
            title=article.title,
            summary=enriched_summary,
            published_at=article.published_at,
            source_name=article.source_name,
            category=article.category,
            url_hash=article.url_hash,
            sent_at=article.sent_at,
            id=article.id,
        )


# ---------------------------------------------------------------------------
# Pipeline: composes multiple processors
# ---------------------------------------------------------------------------

class Pipeline:
    """
    Runs a sequence of processors in order on a batch of articles.

    Each processor's output becomes the next processor's input.
    If a processor raises an exception, the pipeline logs the error
    and continues with the unmodified batch from that step.

    Usage:
        pipeline = Pipeline([TextCleaner(), CategoryFilter(["Technology"])])
        ready_articles = pipeline.run(raw_articles)
    """

    def __init__(self, processors: Optional[List[BaseProcessor]] = None) -> None:
        self._processors: List[BaseProcessor] = processors or [TextCleaner()]

    def run(self, articles: List[ArticleRecord]) -> List[ArticleRecord]:
        """
        Execute all processors in sequence.

        Args:
            articles: Raw ArticleRecord list from the Collector.

        Returns:
            Processed and (optionally) filtered list of ArticleRecord objects.
        """
        log.info(
            "Pipeline starting. Processors: %s | Input articles: %d",
            [str(p) for p in self._processors],
            len(articles),
        )
        current_batch = articles

        for processor in self._processors:
            try:
                current_batch = processor.process(current_batch)
                log.debug(
                    "After %s: %d articles.", processor.__class__.__name__, len(current_batch)
                )
            except Exception as exc:
                log.error(
                    "Processor %s failed: %s — skipping this step.",
                    processor.__class__.__name__,
                    exc,
                )

        log.info("Pipeline complete. Output articles: %d", len(current_batch))
        return current_batch
