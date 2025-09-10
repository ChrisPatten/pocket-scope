"""Examples package for PocketScope.

Small shim to make example modules importable as
``pocketscope.examples.*``. Keeping this lightweight avoids adding
application logic here.
"""

from pocketscope.app import live_view as live_view

__all__ = ["live_view"]
