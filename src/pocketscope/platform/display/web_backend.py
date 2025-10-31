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
from io import BytesIO
from typing import Dict, Optional, Tuple
from urllib.parse import parse_qs, urlparse

from PIL import Image

from pocketscope.platform.display.pygame_backend import PygameDisplayBackend
from pocketscope.render.canvas import Canvas
from pocketscope.theme import ThemeManager


class _FrameHandler(BaseHTTPRequestHandler):
    # Will be set by the server thread during startup
    backend: "WebDisplayBackend" = None  # type: ignore

    def do_GET(self) -> None:  # pragma: no cover - simple I/O handler
        parsed = urlparse(self.path)
        path = parsed.path
        qs = parse_qs(parsed.query)
        if path == "/" or path == "/index.html":
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            # Resolve theme status background color (hex) if available
            try:
                c = ThemeManager.color("status.bg")
                bg = "#%02x%02x%02x" % (c[0], c[1], c[2])
            except Exception:
                bg = "#000"

            # Build srcset entries for available scales (1x..N)
            backend = _FrameHandler.backend
            max_scale = getattr(backend, "_pixel_ratio", 1) if backend is not None else 1
            srcset_entries = []
            for s in range(1, max_scale + 1):
                suffix = "" if s == 1 else f"@{s}x"
                srcset_entries.append(f"/frame{suffix}.png {s}x")
            srcset = ", ".join(srcset_entries)

            # Build a JS array of base image paths to allow dynamic srcset updates
            bases = ["/frame.png"]
            for s in range(2, max_scale + 1):
                bases.append(f"/frame@{s}x.png")
            # JSON-encode the bases for safe embedding in JS
            import json

            bases_js = json.dumps(bases)
            html = (
                '<html><head><meta name=viewport content="width=device-width,initial-scale=1">'
                "<meta charset='utf-8'><title>PocketScope Web UI</title>"
                f"<style>html,body{{height:100%;margin:0;background:{bg};color:#fff;overflow:hidden;padding-top:env(safe-area-inset-top)}}"
                "#frame{position:fixed;top:0;left:0;width:100vw;height:auto;display:block;object-fit:contain}</style>"
                "</head><body>"
                f"<img id=frame src='/frame.png?t=0' srcset=\"{srcset}\" sizes=\"100vw\" alt='frame'/>"
                "<script>"
                f"const _p_bases = {bases_js};"
                "function _getSafeInset(pxKey){const el=document.createElement('div');el.style.cssText='position:fixed;left:0;top:0;visibility:hidden;padding-top:env(safe-area-inset-top);padding-bottom:env(safe-area-inset-bottom);padding-left:env(safe-area-inset-left);padding-right:env(safe-area-inset-right)';document.body.appendChild(el);const cs=getComputedStyle(el);const top=parseFloat(cs.paddingTop)||0;const bottom=parseFloat(cs.paddingBottom)||0;document.body.removeChild(el);return {top:top,bottom:bottom};}"  # noqa: E501
                "function _update(){const t=Date.now();const dpr=window.devicePixelRatio||1;const wCSS=window.innerWidth;const hCSS=window.innerHeight;const insets=_getSafeInset();const usableH=hCSS-insets.top-insets.bottom;const pxW=Math.round(wCSS*dpr);const pxH=Math.round(usableH*dpr);let f=document.getElementById('frame');const q='?t='+t+'&w='+pxW+'&h='+pxH+'&pr='+Math.round(dpr);f.src=_p_bases[0]+q;let s=_p_bases.map((u,i)=>u+q+' '+(i+1)+'x').join(', ');f.srcset=s;}"  # noqa: E501
                "window.addEventListener('resize',_update);setInterval(_update,1000);_update();"
                "</script>"
                "</body></html>"
            )
            self.wfile.write(html.encode("utf-8"))
            return

        # Support requests for /frame.png and /frame@{n}x.png variants
        if path == "/frame.png" or (path.startswith("/frame@") and path.endswith(".png")):
            # Serve the most recent frame bytes
            backend = _FrameHandler.backend
            if backend is None:
                self.send_error(503, "backend not ready")
                return
            # Allow client to request a particular pixel size (w,h) and DPR (pr)
            try:
                qs = qs if isinstance(qs, dict) else {}
                w_vals = qs.get("w") or qs.get("width")
                h_vals = qs.get("h") or qs.get("height")
                pr_vals = qs.get("pr") or qs.get("pxratio")
                req_w = int(w_vals[0]) if w_vals else None
                req_h = int(h_vals[0]) if h_vals else None
                int(pr_vals[0]) if pr_vals else None
            except Exception:
                req_w = req_h = None

            # Choose the highest-resolution stored frame as source
            src_scale = getattr(backend, "_pixel_ratio", 1)
            src_data = backend.get_frame_bytes(scale=src_scale)
            if not src_data:
                self.send_response(204)
                self.end_headers()
                return
            try:
                src_img = Image.open(BytesIO(src_data)).convert("RGBA")
                out_img = src_img
                if req_w and req_h:
                    # Resize to exactly requested pixel dims
                    out_img = src_img.resize((int(req_w), int(req_h)), resample=Image.LANCZOS)
                buf = BytesIO()
                out_img.save(buf, format="PNG")
                out_bytes = buf.getvalue()
            except Exception:
                out_bytes = src_data

            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.send_header("Content-Length", str(len(out_bytes)))
            # Prevent caching so browsers always fetch the latest frame
            self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
            self.end_headers()
            self.wfile.write(out_bytes)
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
        size: Tuple[int, int] = (430, 932),
        *,
        create_window: bool = False,
        host: str = "0.0.0.0",
        port: int = 8000,
        pixel_ratio: int | None = None,
    ) -> None:
        # Initialize underlying PygameDisplayBackend (we subclass it so
        # callers that expect a PygameDisplayBackend still accept us).
        # Force headless SDL so no real window is required for web mode.
        os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
        os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
        super().__init__(size=size, create_window=create_window)
        self._frame_lock = threading.Lock()
        # Pixel ratio: default to env or provided or 3
        env_pr = os.environ.get("POCKETSCOPE_PIXEL_RATIO")
        if pixel_ratio is None:
            try:
                self._pixel_ratio = int(env_pr) if env_pr is not None else 3
            except Exception:
                self._pixel_ratio = 3
        else:
            self._pixel_ratio = int(pixel_ratio)

        # Last rendered PNG frame bytes for each integer scale 1.._pixel_ratio
        self._frame_bytes_by_scale: Dict[int, bytes] = {}

        # Start HTTP server in background thread
        # Allow environment override for host/port too (useful for containers)
        self._host = os.environ.get("POCKETSCOPE_WEB_HOST", host)
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
            # Open with PIL and generate scaled versions
            try:
                img = Image.open(BytesIO(data)).convert("RGBA")
                orig_w, orig_h = img.size
                scaled: Dict[int, bytes] = {}
                for s in range(1, max(1, self._pixel_ratio) + 1):
                    if s == 1:
                        # use original bytes
                        scaled[1] = data
                        continue
                    nw = orig_w * s
                    nh = orig_h * s
                    resized = img.resize((nw, nh), resample=Image.LANCZOS)
                    buf = BytesIO()
                    resized.save(buf, format="PNG")
                    scaled[s] = buf.getvalue()
                with self._frame_lock:
                    self._frame_bytes_by_scale = scaled
            except Exception:
                # If PIL processing fails, at least store 1x
                with self._frame_lock:
                    self._frame_bytes_by_scale = {1: data}
        finally:
            if tmp and os.path.exists(tmp):
                try:
                    os.remove(tmp)
                except Exception:
                    pass

    def save_png(self, path: str) -> None:
        # Delegate to base backend for compatibility
        super().save_png(path)

    def get_frame_bytes(self, scale: int = 1) -> Optional[bytes]:
        """Return PNG bytes for the requested integer scale (fallback to lower scales)."""
        if scale <= 0:
            scale = 1
        with self._frame_lock:
            if not self._frame_bytes_by_scale:
                return None
            # Exact match
            val = self._frame_bytes_by_scale.get(scale)
            if val is not None:
                return val
            # Fallback to the highest available scale <= requested
            for s in range(min(scale, max(self._frame_bytes_by_scale.keys())), 0, -1):
                v = self._frame_bytes_by_scale.get(s)
                if v is not None:
                    return v
            return None

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
