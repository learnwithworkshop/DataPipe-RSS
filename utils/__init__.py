# utils/__init__.py
# Makes 'utils' a proper Python package.
# Import shared utilities from here for convenience.

from utils.logger import get_logger

__all__ = ["get_logger"]
