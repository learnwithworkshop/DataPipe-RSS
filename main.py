"""
main.py
=======
DataPipe-RSS — Main Application Entry Point

This is the single orchestrator that wires together:
  1. Config & validation  (config/settings.py)
  2. RSS Collection       (core/collector.py)
  3. Text Processing      (core/processor.py)
  4. Duplicate Checking   (core/database.py)   ← MANDATORY gate
  5. Output Connectors    (connectors/)
  6. File System Tracking (utils/tracker.py)

Architecture guarantee:
  Every article passes through DuplicateChecker.is_new() before
  any connector receives it. No connector ever bypasses this gate.

Run modes:
  python main.py              → Run once and exit
  python main.py --schedule   → Run every FETCH_INTERVAL_SECONDS (scheduler loop)
  python main.py --stats      → Print DB statistics and exit
  python main.py --snapshot   → Save a git diff snapshot and exit
"""

import argparse
import sys
import time
from typing import List

import schedule

from config.settings import SETTINGS
from connectors import BaseConnector
from connectors.excel_online import ExcelOnlineConnector
from connectors.google_sheets import GoogleSheetsConnector
from core.collector import FeedCollector
from core.database import ArticleRecord, DuplicateChecker
from core.processor import CategoryFilter, Pipeline, TextCleaner
from utils.logger import get_logger
from utils.tracker import FileSystemTracker

log = get_logger(__name__)


# ---------------------------------------------------------------------------
# Pipeline run function
# ---------------------------------------------------------------------------

def run_pipeline() -> None:
    """
    Execute a single end-to-end RSS collection and dispatch cycle.

    Steps:
      1. Collect articles from all active feeds.
      2. Run them through the processor pipeline (clean, filter, AI).
      3. For each new (non-duplicate) article, dispatch to all connectors.
      4. Mark successfully sent articles as seen in the database.
    """
    log.info("=" * 60)
    log.info("DataPipe-RSS pipeline started.")
    log.info("=" * 60)

    # ── Step 1: Collect ────────────────────────────────────────────────────
    collector = FeedCollector()
    raw_articles: List[ArticleRecord] = collector.collect_all()

    if not raw_articles:
        log.warning("No articles collected from any feed. Ending cycle.")
        return

    # ── Step 2: Process ────────────────────────────────────────────────────
    # Build processor pipeline. Add processors here; order matters.
    processors = [
        TextCleaner(max_title_length=200, max_summary_length=800),
        # Uncomment to filter by category:
        # CategoryFilter(allowed_categories=["Technology", "Science"]),
        # Uncomment when ENABLE_AI_PROCESSOR=true and openai is installed:
        # AIProcessor(),
    ]
    pipeline = Pipeline(processors)
    processed_articles: List[ArticleRecord] = pipeline.run(raw_articles)

    # ── Step 3 & 4: Deduplicate → Dispatch → Mark ─────────────────────────
    checker = DuplicateChecker()
    connectors: List[BaseConnector] = _build_connectors()

    active_connectors = [c for c in connectors if c.is_configured()]
    if not active_connectors:
        log.warning(
            "No connectors are configured and enabled. "
            "Set ENABLE_GOOGLE_SHEETS=true or ENABLE_EXCEL_ONLINE=true in .env."
        )

    new_count = 0
    sent_count = 0

    for article in processed_articles:
        # MANDATORY: check for duplicates before touching any connector
        if not checker.is_new(article):
            log.debug("Duplicate skipped: %s", article.title[:60])
            continue

        new_count += 1
        dispatch_success = False

        for connector in active_connectors:
            try:
                success = connector.send(article)
                if success:
                    dispatch_success = True
            except Exception as exc:
                # Catch-all: one connector failure must not kill the loop
                log.error(
                    "Unhandled error in connector %s for '%s': %s",
                    type(connector).__name__,
                    article.title[:50],
                    exc,
                )

        # Mark as sent ONLY after at least one connector succeeded
        if dispatch_success or not active_connectors:
            checker.mark_as_sent(article)
            sent_count += 1

    checker.close()

    # ── Summary ────────────────────────────────────────────────────────────
    log.info(
        "Pipeline complete. "
        "Fetched: %d | New: %d | Sent: %d | Duplicates skipped: %d",
        len(processed_articles),
        new_count,
        sent_count,
        len(processed_articles) - new_count,
    )


def _build_connectors() -> List[BaseConnector]:
    """
    Instantiate all available connectors.

    To add a new connector (e.g., Telegram):
      1. Create connectors/telegram.py with a TelegramConnector class.
      2. Import it here and add it to the list — that's it.

    Returns:
        List of BaseConnector instances (may not all be active).
    """
    return [
        GoogleSheetsConnector(),
        ExcelOnlineConnector(),
        # TelegramConnector(),    # Future
        # NotionConnector(),      # Future
    ]


# ---------------------------------------------------------------------------
# CLI mode: stats
# ---------------------------------------------------------------------------

def print_stats() -> None:
    """Print database statistics to the console and exit."""
    checker = DuplicateChecker()
    total = checker.get_total_count()
    recent = checker.get_recent(limit=5)
    checker.close()

    print("\n📊 DataPipe-RSS Database Statistics")
    print(f"   Total articles stored : {total}")
    print("\n   Last 5 articles sent:")
    for i, article in enumerate(recent, 1):
        print(f"   {i}. [{article.source_name}] {article.title[:70]}")
    print()


# ---------------------------------------------------------------------------
# CLI mode: git snapshot
# ---------------------------------------------------------------------------

def take_snapshot() -> None:
    """Save a git diff snapshot and exit."""
    tracker = FileSystemTracker()
    tracker.snapshot()


# ---------------------------------------------------------------------------
# Scheduled runner
# ---------------------------------------------------------------------------

def run_scheduled() -> None:
    """
    Run the pipeline on a schedule (every FETCH_INTERVAL_SECONDS).
    Starts a file system tracker as a background thread.
    Blocks until Ctrl+C.
    """
    tracker = FileSystemTracker()
    tracker.start_watching()

    interval = SETTINGS.fetch_interval_seconds
    log.info("Scheduler mode: pipeline runs every %d seconds.", interval)

    # Run immediately on startup, then schedule
    run_pipeline()
    schedule.every(interval).seconds.do(run_pipeline)

    try:
        while True:
            schedule.run_pending()
            time.sleep(10)
    except KeyboardInterrupt:
        log.info("Shutdown signal received. Stopping scheduler.")
        tracker.stop_watching()
        sys.exit(0)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="DataPipe-RSS — Modular RSS to Spreadsheet Automation",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python main.py              # Run once\n"
            "  python main.py --schedule   # Run on a loop\n"
            "  python main.py --stats      # Show DB statistics\n"
            "  python main.py --snapshot   # Save git diff snapshot\n"
        ),
    )
    parser.add_argument(
        "--schedule",
        action="store_true",
        help=f"Run every {SETTINGS.fetch_interval_seconds}s (set FETCH_INTERVAL_SECONDS in .env)",
    )
    parser.add_argument(
        "--stats",
        action="store_true",
        help="Print database statistics and exit.",
    )
    parser.add_argument(
        "--snapshot",
        action="store_true",
        help="Save a git diff snapshot to logs/ and exit.",
    )

    args = parser.parse_args()

    # Validate settings before doing anything else
    SETTINGS.validate()

    log.info(
        "DataPipe-RSS starting. Version: 1.0.0 | Python: %s",
        sys.version.split()[0],
    )

    if args.stats:
        print_stats()
    elif args.snapshot:
        take_snapshot()
    elif args.schedule:
        run_scheduled()
    else:
        # Default: single run
        tracker = FileSystemTracker()
        tracker.start_watching()
        run_pipeline()
        tracker.stop_watching()


if __name__ == "__main__":
    main()
