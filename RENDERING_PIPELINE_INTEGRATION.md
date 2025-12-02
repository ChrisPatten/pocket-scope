"""
Integration guide for RenderPipeline with existing rendering code.

The RenderPipeline is a formalized abstraction for scene composition that:
1. Owns all layer ordering and rendering
2. Composes all layers into a single RGBA frame each render() call
3. Passes the final frame to a DisplayBackend for presentation

This decouples scene composition from hardware-specific display logic.

Current State (Backward Compatible)
===================================
Existing code using begin_frame()/end_frame() continues to work unchanged.
Both ILI9341 and pygame backends now also support present(frame: Image.Image).

Migration Path
==============
Phase 1 (current): Backends support both APIs
- begin_frame() -> Canvas (legacy flow)
- end_frame() (legacy flow)
- present(frame: Image.Image) (new pipeline flow)

Phase 2 (future): Move to RenderPipeline
- Replace begin_frame()/end_frame() loop with RenderPipeline.render()
- Wrap existing render components as Layer instances
- Pipeline handles frame composition; backend only does presentation

Example: Using RenderPipeline (Future)
======================================

from pocketscope.render.pipeline import RenderPipeline
from pocketscope.render.layer_adapters import PpiViewLayer
from pocketscope.platform.display.ili9341_backend import ILI9341DisplayBackend

# Create pipeline
pipeline = RenderPipeline(240, 320, clear_color=(0, 0, 0, 255))

# Add layers (bottom-to-top order)
ppi_layer = PpiViewLayer(view)
pipeline.add_layer(ppi_layer)

# Get backend
backend = ILI9341DisplayBackend()

# Main render loop
while True:
    # Update layer scene data
    ppi_layer.set_scene(
        display_size=backend.size(),
        center_lat=...,
        center_lon=...,
        tracks=snapshots,
    )
    
    # Compose all layers into RGBA frame
    frame = pipeline.render(dt)
    
    # Display the frame
    backend.present(frame)
    
    await asyncio.sleep(1.0 / 60.0)

Key Benefits
============
1. Transparency works correctly: All drawing uses RGBA with natural blending
2. No frame reuse issues: Fresh canvas each frame, no trails or ghosting
3. Modular: Layers can be enabled/disabled dynamically
4. Testable: Pipeline produces deterministic RGBA frames for golden testing
5. Hardware agnostic: Same pipeline works for pygame, TFT, web, PNG export

Current Transparency Issue
==========================
The user reported opaque labels on the ILI9341. This occurs because:
1. begin_frame() reuses previous frame (for optimization)
2. Old labels remain on canvas when aircraft move
3. New labels drawn on top appear opaque

The pipeline fixes this because:
1. Each render() creates a fresh RGBA canvas
2. Layers draw with full alpha support
3. Pillow's ImageDraw handles alpha blending natively
4. Only the final blended frame is converted to RGB565

To fix transparency without the pipeline:
- Always start with a fresh frame: remove prev_frame copy in begin_frame()
- Draw label backgrounds: ensure opaque rect behind each label
- Or: Adopt the pipeline approach
"""
