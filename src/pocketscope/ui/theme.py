"""Backward compatibility shim.

The theme system has moved to ``pocketscope.theme`` to avoid circular
imports with other ``ui`` submodules. This module re-exports the public
symbols. New code should import from ``pocketscope.theme`` directly.
"""

from pocketscope.theme import (
    REQUIRED_KEYS,
    THEMES,
    Color,
    ColorTuple,
    Theme,
    ThemeManager,
    TrackSpeedScale,
    hex_color,
    to_rgb565,
)

__all__ = [
    "Color",
    "ColorTuple",
    "Theme",
    "TrackSpeedScale",
    "ThemeManager",
    "hex_color",
    "to_rgb565",
    "REQUIRED_KEYS",
    "THEMES",
]
