"""
utils/tracker.py
================
Advanced File System Monitor & Git Diff Generator for DataPipe-RSS.

This module serves TWO purposes:

  1. **Audit Trail**: Whenever a Python source file in the project is
     modified, it appends a timestamped entry to logs/project_audit.md.
     This gives you a human-readable changelog of every code change.

  2. **Git Diff Snapshot**: Captures `git diff` output at any point and
     writes it to a dated file in logs/ for review.

Designed to be run as a background thread or called explicitly from
main.py / CI pipelines. It does NOT modify any source files.

Usage:
    from utils.tracker import FileSystemTracker
    tracker = FileSystemTracker()
    tracker.snapshot()          # One-time snapshot of current git diff
    tracker.start_watching()    # Background thread — watches for changes
    tracker.stop_watching()
"""

import os
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional, Set

from utils.logger import get_logger

log = get_logger(__name__)


class FileSystemTracker:
    """
    Monitors Python source files for modifications and maintains an
    audit log in logs/project_audit.md.

    Attributes:
        project_root:   Absolute path to the DataPipe-RSS root directory.
        audit_log_path: Path to logs/project_audit.md.
        poll_interval:  How often (seconds) to scan for file changes.
    """

    _WATCH_EXTENSIONS: Set[str] = {".py", ".json", ".env", ".txt", ".md"}

    def __init__(
        self,
        project_root: Optional[Path] = None,
        poll_interval: float = 10.0,
    ) -> None:
        from config.settings import SETTINGS

        self.project_root: Path = project_root or SETTINGS.project_root
        self.audit_log_path: Path = SETTINGS.log_dir / "project_audit.md"
        self.poll_interval: float = poll_interval

        # Map of file_path → last_modified_timestamp
        self._file_mtimes: Dict[str, float] = {}
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

        # Seed the initial state so we don't log everything on first run
        self._seed_initial_state()
        self._ensure_audit_log_header()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def snapshot(self) -> None:
        """
        Capture a one-time snapshot of the current git diff and write
        it to logs/git_snapshot_<timestamp>.diff.
        Also records the snapshot event in project_audit.md.
        """
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        diff_output = self._get_git_diff()

        if diff_output:
            diff_file = self.audit_log_path.parent / f"git_snapshot_{timestamp}.diff"
            try:
                diff_file.write_text(diff_output, encoding="utf-8")
                log.info("Git diff snapshot saved: %s", diff_file.name)
                self._append_audit_entry(
                    event_type="GIT_SNAPSHOT",
                    detail=f"Saved to: {diff_file.name}",
                )
            except OSError as exc:
                log.error("Failed to write git snapshot: %s", exc)
        else:
            log.info("Git snapshot: no changes detected (clean working tree).")

    def start_watching(self) -> None:
        """
        Start the background file-watcher thread.
        Calling this more than once is safe (no-op if already running).
        """
        if self._thread and self._thread.is_alive():
            log.debug("FileSystemTracker is already watching.")
            return

        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._watch_loop,
            name="FileSystemTracker",
            daemon=True,      # Dies with the main process
        )
        self._thread.start()
        log.info(
            "FileSystemTracker started (poll interval: %.1fs).", self.poll_interval
        )

    def stop_watching(self) -> None:
        """Signal the background thread to stop and wait for it to exit."""
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=self.poll_interval + 2)
            log.info("FileSystemTracker stopped.")

    # ------------------------------------------------------------------
    # Internal: file watching loop
    # ------------------------------------------------------------------

    def _watch_loop(self) -> None:
        """The main loop executed in the background thread."""
        while not self._stop_event.is_set():
            try:
                self._check_for_changes()
            except Exception as exc:
                log.error("FileSystemTracker error during scan: %s", exc)
            self._stop_event.wait(timeout=self.poll_interval)

    def _check_for_changes(self) -> None:
        """Scan project files and log any that have been modified."""
        for file_path in self._iter_watched_files():
            try:
                current_mtime = file_path.stat().st_mtime
            except OSError:
                continue  # File might have been deleted — skip

            path_str = str(file_path)
            last_mtime = self._file_mtimes.get(path_str)

            if last_mtime is None:
                # New file appeared after startup
                self._file_mtimes[path_str] = current_mtime
                self._append_audit_entry("FILE_CREATED", path_str)
                log.debug("New file detected: %s", file_path.name)

            elif current_mtime != last_mtime:
                # Existing file was modified
                self._file_mtimes[path_str] = current_mtime
                self._append_audit_entry("FILE_MODIFIED", path_str)
                log.info("File change detected: %s", file_path.name)

    # ------------------------------------------------------------------
    # Internal: helpers
    # ------------------------------------------------------------------

    def _seed_initial_state(self) -> None:
        """Record the mtime of every watched file at startup."""
        for file_path in self._iter_watched_files():
            try:
                self._file_mtimes[str(file_path)] = file_path.stat().st_mtime
            except OSError:
                pass

    def _iter_watched_files(self):
        """Yield all project files with watched extensions, skipping .git and venv."""
        skip_dirs = {".git", "__pycache__", "venv", ".venv", "env", "data", "logs"}
        for root, dirs, files in os.walk(self.project_root):
            # Prune directories in-place to stop os.walk from descending
            dirs[:] = [d for d in dirs if d not in skip_dirs]
            for filename in files:
                if Path(filename).suffix in self._WATCH_EXTENSIONS:
                    yield Path(root) / filename

    def _ensure_audit_log_header(self) -> None:
        """Create the audit log with a Markdown header if it doesn't exist."""
        if not self.audit_log_path.exists():
            self.audit_log_path.parent.mkdir(parents=True, exist_ok=True)
            header = (
                "# DataPipe-RSS — Project Audit Log\n\n"
                "Auto-generated by `utils/tracker.py`. "
                "Do **not** edit manually.\n\n"
                "| Timestamp | Event | File / Detail |\n"
                "|-----------|-------|---------------|\n"
            )
            self.audit_log_path.write_text(header, encoding="utf-8")

    def _append_audit_entry(self, event_type: str, detail: str) -> None:
        """Append a single row to the Markdown audit table."""
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        # Make the detail path relative to project root for readability
        try:
            display_detail = str(Path(detail).relative_to(self.project_root))
        except ValueError:
            display_detail = detail

        row = f"| {timestamp} | `{event_type}` | `{display_detail}` |\n"
        try:
            with self.audit_log_path.open("a", encoding="utf-8") as fh:
                fh.write(row)
        except OSError as exc:
            log.error("Failed to write audit entry: %s", exc)

    def _get_git_diff(self) -> str:
        """
        Run `git diff` in the project root and return the output as a string.
        Returns an empty string if git is unavailable or the repo is clean.
        """
        try:
            result = subprocess.run(
                ["git", "diff"],
                cwd=str(self.project_root),
                capture_output=True,
                text=True,
                timeout=15,
            )
            return result.stdout
        except FileNotFoundError:
            log.warning("git executable not found — skipping diff snapshot.")
            return ""
        except subprocess.TimeoutExpired:
            log.warning("git diff timed out.")
            return ""
        except Exception as exc:
            log.error("Unexpected error running git diff: %s", exc)
            return ""
