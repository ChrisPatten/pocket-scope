"""Unit tests for RenderPipeline."""

from __future__ import annotations

from PIL import Image

from pocketscope.render.canvas import Canvas, Color
from pocketscope.render.pipeline import Layer, RenderPipeline


class SimpleTestLayer(Layer):
    """Simple test layer that draws a colored rectangle."""

    def __init__(self, color: Color = (255, 0, 0, 255)) -> None:
        self.color = color
        self.update_called = False

    def update(self, dt: float) -> None:
        self.update_called = True

    def draw(self, canvas: Canvas) -> None:
        # Draw a simple shape
        canvas.filled_circle((10, 10), 5, self.color)


def test_pipeline_initialization() -> None:
    """Test pipeline creates with correct dimensions."""
    pipeline = RenderPipeline(320, 480)
    assert pipeline.size() == (320, 480)
    assert pipeline.layer_count() == 0


def test_pipeline_add_remove_layers() -> None:
    """Test adding and removing layers."""
    pipeline = RenderPipeline(320, 480)
    layer1 = SimpleTestLayer((255, 0, 0, 255))
    layer2 = SimpleTestLayer((0, 255, 0, 255))

    # Add layers
    pipeline.add_layer(layer1)
    assert pipeline.layer_count() == 1

    pipeline.add_layer(layer2)
    assert pipeline.layer_count() == 2

    # Remove a layer
    pipeline.remove_layer(layer1)
    assert pipeline.layer_count() == 1

    # Remove non-existent layer (should be no-op)
    pipeline.remove_layer(layer1)
    assert pipeline.layer_count() == 1

    # Clear all layers
    pipeline.clear_layers()
    assert pipeline.layer_count() == 0


def test_pipeline_render_returns_rgba_image() -> None:
    """Test that render returns an RGBA image."""
    pipeline = RenderPipeline(320, 480, clear_color=(0, 0, 0, 255))
    layer = SimpleTestLayer((255, 255, 255, 255))
    pipeline.add_layer(layer)

    frame = pipeline.render(0.016)  # 60 FPS delta

    # Check frame properties
    assert isinstance(frame, Image.Image)
    assert frame.mode == "RGBA"
    assert frame.size == (320, 480)

    # Check that layer was updated
    assert layer.update_called


def test_pipeline_render_layer_order() -> None:
    """Test that layers render in bottom-to-top order."""
    pipeline = RenderPipeline(100, 100, clear_color=(0, 0, 0, 255))

    # Create two layers with different colors
    red = SimpleTestLayer((255, 0, 0, 255))  # Red, drawn first (bottom)
    blue = SimpleTestLayer((0, 0, 255, 255))  # Blue, drawn second (top)

    pipeline.add_layer(red)
    pipeline.add_layer(blue)

    frame = pipeline.render(0.016)

    # The blue layer should be on top, so pixels where both drew should be blue
    # Check a pixel at (10, 10) where both layers drew
    pixel = frame.getpixel((10, 10))
    # Should be blue (last layer wins)
    assert pixel[2] > 0  # Blue channel


def test_pipeline_clear_color() -> None:
    """Test that clear color is applied."""
    clear_color: Color = (128, 64, 32, 255)
    pipeline = RenderPipeline(100, 100, clear_color=clear_color)

    frame = pipeline.render(0.016)

    # Check background pixel (where no layer drew)
    pixel = frame.getpixel((50, 50))
    assert pixel == clear_color


def test_pipeline_rgba_transparency() -> None:
    """Test that RGBA transparency is preserved."""
    pipeline = RenderPipeline(100, 100, clear_color=(0, 0, 0, 255))

    # Create a layer that draws with transparency
    class TransparentLayer(Layer):
        def update(self, dt: float) -> None:
            pass

        def draw(self, canvas: Canvas) -> None:
            # Draw a semi-transparent circle
            canvas.filled_circle((50, 50), 10, (255, 255, 255, 128))

    pipeline.add_layer(TransparentLayer())
    frame = pipeline.render(0.016)

    # Check that frame is still RGBA (supports alpha channel)
    assert frame.mode == "RGBA"


def test_pipeline_get_last_frame() -> None:
    """Test retrieving the last rendered frame."""
    pipeline = RenderPipeline(100, 100)

    # No frame yet
    assert pipeline.get_last_frame() is None

    # After render
    frame = pipeline.render(0.016)
    assert pipeline.get_last_frame() is frame


def test_pipeline_render_error_resilience() -> None:
    """Test that pipeline handles layer errors gracefully."""

    class BrokenLayer(Layer):
        def update(self, dt: float) -> None:
            raise ValueError("Update error")

        def draw(self, canvas: Canvas) -> None:
            raise ValueError("Draw error")

    pipeline = RenderPipeline(100, 100)
    pipeline.add_layer(BrokenLayer())

    # Should not raise, just log warnings
    frame = pipeline.render(0.016)
    assert frame is not None


def test_pipeline_multiple_renders() -> None:
    """Test rendering multiple frames."""
    pipeline = RenderPipeline(100, 100)
    layer = SimpleTestLayer((255, 255, 255, 255))
    pipeline.add_layer(layer)

    # Render multiple times
    for i in range(5):
        frame = pipeline.render(0.016)
        assert isinstance(frame, Image.Image)
        assert frame.size == (100, 100)

    # Each render should have created a new frame object
    assert pipeline.get_last_frame() is not None


if __name__ == "__main__":
    # Run tests
    test_pipeline_initialization()
    test_pipeline_add_remove_layers()
    test_pipeline_render_returns_rgba_image()
    test_pipeline_render_layer_order()
    test_pipeline_clear_color()
    test_pipeline_rgba_transparency()
    test_pipeline_get_last_frame()
    test_pipeline_render_error_resilience()
    test_pipeline_multiple_renders()
    print("All pipeline tests passed!")
