"""Application package for PocketScope.

This package contains the main application entrypoint modules. The
previous example location `pocketscope.examples.live_view` is kept as a
compatibility shim that re-exports the same module.
"""

from . import live_view  # re-export the main application module

__all__ = ["live_view"]
