"""Web bridge for rendering the offscreen pygame surface in a browser.

This module provides `WebDisplayBackend`, a thin wrapper around the
existing `PygameDisplayBackend` that captures the offscreen PNG after
each frame and exposes a tiny HTTP server serving a simple HTML page and
`/frame.png`. The server runs in a background thread so the host process
can continue its asyncio event loop.

Usage:
    In `live_view.py` replace the import of `PygameDisplayBackend` with::

        from pocketscope.platform.display.web_backend import WebDisplayBackend

    and construct `WebDisplayBackend(...)` the same way as `PygameDisplayBackend`.

The server prints the URL on startup. The HTML will poll `/frame.png` to
refresh the displayed image.
"""

from __future__ import annotations

import os
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Optional, Tuple

from pocketscope.platform.display.pygame_backend import PygameDisplayBackend
from pocketscope.render.canvas import Canvas


class _FrameHandler(BaseHTTPRequestHandler):
    # Will be set by the server thread during startup
    backend: "WebDisplayBackend" = None  # type: ignore

    def do_GET(self) -> None:  # pragma: no cover - simple I/O handler
        path = self.path.split("?", 1)[0]
        if path == "/" or path == "/index.html":
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            html = (
                "<html><head><meta charset='utf-8'><title>PocketScope Web UI</title>"
                "<style>body{margin:0;background:#000;color:#fff;display:flex;align-items:center;justify-content:center;height:100vh}img{max-width:100%;height:auto}</style>"
                "</head><body>"
                "<img id=frame src='/frame.png?t=0' alt='frame'/>"
                "<script>setInterval(()=>{document.getElementById('frame').src='/frame.png?t='+Date.now()},1000);</script>"
                "</body></html>"
            )
            self.wfile.write(html.encode("utf-8"))
            return

        if path == "/frame.png":
            # Serve the most recent frame bytes
            backend = _FrameHandler.backend
            if backend is None:
                self.send_error(503, "backend not ready")
                return
            data = backend.get_frame_bytes()
            if not data:
                # Empty/placeholder 1x1 transparent PNG
                self.send_response(204)
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.send_header("Content-Length", str(len(data)))
            # Prevent caching so browsers always fetch the latest frame
            self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
            self.end_headers()
            self.wfile.write(data)
            return

        self.send_error(404, "not found")


class WebDisplayBackend(PygameDisplayBackend):
    """Wrapper display backend that serves the offscreen surface over HTTP.

    It delegates actual drawing to an inner `PygameDisplayBackend` and,
    on each `end_frame`, saves a PNG to an in-memory buffer which is then
    returned by `get_frame_bytes()` and served by the internal HTTP server.
    """

    # Ensure mypy knows about this attribute's type
    _frame_bytes: Optional[bytes] | None = None

    def __init__(
        self,
        size: Tuple[int, int] = (320, 480),
        *,
        create_window: bool = False,
        host: str = "127.0.0.1",
        port: int = 8000,
    ) -> None:
        # Initialize underlying PygameDisplayBackend (we subclass it so
        # callers that expect a PygameDisplayBackend still accept us).
        # Force headless SDL so no real window is required for web mode.
        os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
        os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
        super().__init__(size=size, create_window=create_window)
        self._frame_lock = threading.Lock()
        # Last rendered PNG frame bytes; None until first frame is rendered.
        self._frame_bytes: Optional[bytes] = None

        # Start HTTP server in background thread
        self._host = host
        self._port = int(os.environ.get("POCKETSCOPE_WEB_PORT", str(port)))
        self._server_thread = threading.Thread(target=self._run_server, daemon=True)
        self._server_thread.start()

    def size(self) -> Tuple[int, int]:
        return super().size()

    def begin_frame(self) -> Canvas:
        return super().begin_frame()

    def end_frame(self) -> None:
        # Let inner backend handle any window blitting, then capture PNG
        super().end_frame()
        # Use a temporary file because the inner backend exposes save_png(file_path)
        # (delegating to pygame.image.save) and that is the simplest portable
        # approach without adding a dependency.
        tmp = None
        try:
            fd, tmp = tempfile.mkstemp(suffix=".png")
            os.close(fd)
            super().save_png(tmp)
            with open(tmp, "rb") as f:
                data = f.read()
            with self._frame_lock:
                self._frame_bytes = data
        finally:
            if tmp and os.path.exists(tmp):
                try:
                    os.remove(tmp)
                except Exception:
                    pass

    def save_png(self, path: str) -> None:
        # Delegate to base backend for compatibility
        super().save_png(path)

    def get_frame_bytes(self) -> Optional[bytes]:
        with self._frame_lock:
            return self._frame_bytes

    # --- internal server ---
    def _run_server(self) -> None:
        try:
            addr = (self._host, self._port)
            server = ThreadingHTTPServer(addr, _FrameHandler)
            _FrameHandler.backend = self
            url = f"http://{self._host}:{self._port}/"
            print(f"[WebDisplayBackend] serving UI at: {url}")
            server.serve_forever()
        except Exception as e:  # pragma: no cover - runtime environmental
            print(f"[WebDisplayBackend] server failed: {e}")
