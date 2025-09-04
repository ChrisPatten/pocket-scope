"""Headless input smoke test for the pygame backend."""

import os


def test_input_smoke() -> None:
    # Ensure pygame loads in dummy mode and import backend/input lazily
    os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
    from pocketscope.platform.display.pygame_backend import PygameDisplayBackend
    from pocketscope.platform.input.pygame_input import PygameInputBackend

    backend = PygameDisplayBackend(size=(100, 100))
    inp = PygameInputBackend()

    # Synthesize a mouse click at center using pygame event API
    import pygame

    w, h = backend.size()
    center = (w // 2, h // 2)
    pygame.event.post(pygame.event.Event(pygame.MOUSEBUTTONDOWN, {"pos": center}))
    pygame.event.post(pygame.event.Event(pygame.MOUSEBUTTONUP, {"pos": center}))

    events = list(inp.pump())
    # Expect at least a tap event
    assert any(
        e.type == "tap" and abs(e.x - center[0]) <= 1 and abs(e.y - center[1]) <= 1
        for e in events
    )
