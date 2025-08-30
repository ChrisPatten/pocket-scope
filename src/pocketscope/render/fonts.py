"""Font utilities for monospaced rendering.

Provides a simple helper to obtain a monospaced font handle from pygame
with a fallback chain across common system fonts. Rendering itself is done
via the abstract Canvas.text API, so this module is primarily useful for
backends that need an explicit font handle.
"""

from __future__ import annotations


class FontHandle:
    """Lightweight wrapper for a backend font object.

    The concrete type depends on the active backend (e.g., pygame Font).
    This class exists to avoid importing pygame at call sites.
    """

    def __init__(self, obj: object) -> None:  # pragma: no cover - trivial wrapper
        self.obj = obj


def get_mono(size_px: int) -> FontHandle:
    """Return a monospaced font handle at the given pixel size.

    Attempts a preferred list of monospaced faces and falls back to the
    default monospace if necessary. Uses pygame (or pygame-ce) when available.

    Fallback chain: ["DejaVu Sans Mono","Menlo","Consolas","Courier New","monospace"].

    Notes
    -----
    - Callers are not required to use the returned handle with Canvas.text,
      since Canvas abstracts font selection. This helper is provided for
      backends that accept font objects explicitly.
    """

    try:
        import pygame

        pygame.font.init()
        names = [
            "DejaVu Sans Mono",
            "Menlo",
            "Consolas",
            "Courier New",
            "monospace",
        ]
        # Try system font matching first
        for name in names:
            try:
                f = pygame.font.SysFont(name, size_px)
                if f is not None:
                    return FontHandle(f)
            except Exception:
                continue
        # Fall back to default font
        return FontHandle(pygame.font.Font(None, size_px))
    except Exception:
        # If pygame isn't available in the test environment, return a stub.
        # Rendering will still proceed via Canvas.text which doesn't require
        # this handle.
        return FontHandle(object())
