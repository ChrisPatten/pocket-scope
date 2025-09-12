"""Data loaders and fixtures for PocketScope.

Currently includes a minimal airports loader. More directories/sources will
be added in future iterations.
"""

from .airports import Airport, load_airports_json, nearest_airports

__all__ = [
    "Airport",
    "load_airports_json",
    "nearest_airports",
]
