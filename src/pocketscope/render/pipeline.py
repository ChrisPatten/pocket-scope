"""
Rendering pipeline: composites layers into RGBA frames for display backends.

The pipeline owns frame composition—layering, transparency, blending—and
produces pre-composited RGBA frames that backends consume. This decouples
scene composition from hardware-specific presentation (pygame, TFT, web, etc).

Architecture:
    1. Layers update with delta time
    2. Pipeline creates fresh RGBA canvas
    3. Layers draw in bottom-to-top order (with full transparency support)
    4. Pipeline returns final RGBA frame to backend for presentation

Layer Protocol:
    Each layer implements update(dt: float) and draw(canvas: Canvas).
    Layers may subscribe to event bus topics and maintain internal state.

Frame Composition:
    - All drawing uses full RGBA color space with alpha blending
    - Transparency is resolved during Pillow drawing (in-software)
    - Only the final flattened RGBA is returned to backend
    - Backend handles color space conversion (RGBA → RGB565, etc)
"""

from __future__ import annotations

import logging
from typing import Iterable, Protocol, Tuple

from PIL import Image

from pocketscope.render.canvas import Canvas, Color


class Layer(Protocol):
    """Protocol for renderable layers."""

    def update(self, dt: float) -> None:
        """Update layer state (called before draw each frame).

        Args:
            dt: Delta time in seconds since last frame.
        """
        ...

    def draw(self, canvas: Canvas) -> None:
        """Render layer content to canvas.

        Args:
            canvas: Canvas instance with full RGBA support.
        """
        ...


class RenderPipeline:
    """
    Composites layers into RGBA frames with proper alpha blending.

    The pipeline creates a fresh RGBA canvas each frame, updates layers,
    renders them bottom-to-top, and returns the final composited frame
    for backend consumption.
    """

    def __init__(
        self,
        width: int,
        height: int,
        clear_color: Color = (0, 0, 0, 255),
    ) -> None:
        """Initialize rendering pipeline.

        Args:
            width: Canvas width in pixels.
            height: Canvas height in pixels.
            clear_color: Background color (RGBA) for each frame.
        """
        self._w = int(width)
        self._h = int(height)
        self._clear_color = clear_color
        self._layers: list[Layer] = []
        self._frame: Image.Image | None = None
        self._logger = logging.getLogger(__name__)

    def add_layer(self, layer: Layer) -> None:
        """Add a layer to the render stack (bottom-to-top order).

        Args:
            layer: Layer implementing update/draw protocol.
        """
        if layer not in self._layers:
            self._layers.append(layer)

    def remove_layer(self, layer: Layer) -> None:
        """Remove a layer from the render stack.

        Args:
            layer: Layer to remove.
        """
        if layer in self._layers:
            self._layers.remove(layer)

    def clear_layers(self) -> None:
        """Remove all layers from the pipeline."""
        self._layers.clear()

    def render(self, dt: float) -> Image.Image:
        """
        Composite all layers into a final RGBA frame.

        This method:
        1. Creates a fresh RGBA canvas
        2. Updates all layers with delta time
        3. Draws layers bottom-to-top
        4. Returns the composited RGBA frame

        Args:
            dt: Delta time in seconds since last frame.

        Returns:
            Pillow RGBA image ready for backend consumption.
        """
        # Import here to avoid circular dependency
        from pocketscope.platform.display.pillow_canvas import FontCache, PillowCanvas

        # Create fresh RGBA canvas
        frame = Image.new("RGBA", (self._w, self._h), self._clear_color)

        # Wrap in canvas abstraction
        fonts = FontCache()
        canvas = PillowCanvas(frame, fonts)

        # Update and draw each layer bottom-to-top
        try:
            for layer in self._layers:
                try:
                    layer.update(dt)
                except Exception as e:
                    self._logger.warning(f"Layer {layer.__class__.__name__} update raised: {e}")

            for layer in self._layers:
                try:
                    layer.draw(canvas)
                except Exception as e:
                    self._logger.warning(f"Layer {layer.__class__.__name__} draw raised: {e}")
        except Exception as e:
            self._logger.error(f"Pipeline render error: {e}")

        self._frame = frame
        return frame

    def get_last_frame(self) -> Image.Image | None:
        """Return most recently rendered frame (for recovery/caching).

        Returns:
            RGBA frame or None if render() has not been called yet.
        """
        return self._frame

    def size(self) -> Tuple[int, int]:
        """Return pipeline canvas dimensions.

        Returns:
            (width, height) in pixels.
        """
        return (self._w, self._h)

    def layer_count(self) -> int:
        """Return number of registered layers.

        Returns:
            Layer count.
        """
        return len(self._layers)

    def layers(self) -> Iterable[Layer]:
        """Return iterable of registered layers (bottom-to-top order).

        Returns:
            Iterable of layers.
        """
        return iter(self._layers)
