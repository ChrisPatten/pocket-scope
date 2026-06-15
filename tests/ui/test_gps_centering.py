"""
Unit tests for UI controller GPS centering logic.

Tests GPS-driven center coordinate updates with fallback cascade.
"""

import asyncio
import time
from datetime import datetime, timezone
from unittest.mock import Mock

import pytest

from pocketscope.core.events import EventBus, pack
from pocketscope.core.models import GpsFix
from pocketscope.core.time import RealTimeSource
from pocketscope.core.tracks import TrackService
from pocketscope.render.view_ppi import PpiView
from pocketscope.ui.controllers import UiConfig, UiController


@pytest.fixture
def mock_display():
    """Create mock display backend."""
    display = Mock()
    display.size.return_value = (480, 800)
    display.begin_frame.return_value = Mock()
    display.end_frame.return_value = None
    return display


@pytest.fixture
def ui_components(mock_display):
    """Create UI controller components."""
    bus = EventBus()
    ts = RealTimeSource()
    tracks = TrackService(bus, ts, expiry_s=300.0)
    view = PpiView()
    cfg = UiConfig(range_nm=10.0, overlay=True, target_fps=30.0)
    
    return {
        "bus": bus,
        "ts": ts,
        "tracks": tracks,
        "view": view,
        "cfg": cfg,
        "display": mock_display,
    }


@pytest.mark.asyncio
async def test_gps_updates_center_coordinates(ui_components):
    """Test that valid GPS fix updates center coordinates."""
    # Create UI controller with initial center
    ui = UiController(
        display=ui_components["display"],
        view=ui_components["view"],
        bus=ui_components["bus"],
        ts=ui_components["ts"],
        tracks=ui_components["tracks"],
        cfg=ui_components["cfg"],
        center_lat=42.0,
        center_lon=-71.0,
    )
    
    # Verify initial center
    assert ui._center_lat == pytest.approx(42.0, abs=0.0001)
    assert ui._center_lon == pytest.approx(-71.0, abs=0.0001)
    assert ui._gps_valid is False
    
    # Publish GPS fix
    gps_fix = GpsFix(
        ts=datetime.now(timezone.utc),
        lat=42.3601,
        lon=-71.0589,
    )
    fix_dict = gps_fix.model_dump()
    fix_dict["ts"] = gps_fix.ts.isoformat()
    await ui_components["bus"].publish("gps.position", pack(fix_dict))
    
    # Wait for GPS listener to process
    await asyncio.sleep(0.1)
    
    # Verify center updated to GPS position
    assert ui._center_lat == pytest.approx(42.3601, abs=0.0001)
    assert ui._center_lon == pytest.approx(-71.0589, abs=0.0001)
    assert ui._gps_valid is True
    
    # Cleanup
    if ui._gps_task:
        ui._gps_task.cancel()
        try:
            await ui._gps_task
        except asyncio.CancelledError:
            pass


@pytest.mark.asyncio
async def test_gps_fallback_to_cli_center(ui_components):
    """Test fallback to CLI center when GPS becomes stale."""
    # Create UI controller with CLI center
    ui = UiController(
        display=ui_components["display"],
        view=ui_components["view"],
        bus=ui_components["bus"],
        ts=ui_components["ts"],
        tracks=ui_components["tracks"],
        cfg=ui_components["cfg"],
        center_lat=42.0,
        center_lon=-71.0,
    )
    
    # Publish GPS fix
    gps_fix = GpsFix(
        ts=datetime.now(timezone.utc),
        lat=42.3601,
        lon=-71.0589,
    )
    fix_dict = gps_fix.model_dump()
    fix_dict["ts"] = gps_fix.ts.isoformat()
    await ui_components["bus"].publish("gps.position", pack(fix_dict))
    
    # Wait for GPS listener to process
    await asyncio.sleep(0.1)
    
    # Verify GPS position active
    assert ui._center_lat == pytest.approx(42.3601, abs=0.0001)
    assert ui._center_lon == pytest.approx(-71.0589, abs=0.0001)
    
    # Simulate GPS becoming stale by setting old timestamp
    ui._gps_last_update_ts = time.monotonic() - 65.0  # 65 seconds ago
    
    # Call update method to trigger fallback
    ui._update_center_from_gps()
    
    # Verify fallback to CLI center
    assert ui._center_lat == pytest.approx(42.0, abs=0.0001)
    assert ui._center_lon == pytest.approx(-71.0, abs=0.0001)
    assert ui._gps_valid is False
    
    # Cleanup
    if ui._gps_task:
        ui._gps_task.cancel()
        try:
            await ui._gps_task
        except asyncio.CancelledError:
            pass


@pytest.mark.asyncio
async def test_gps_last_known_position_fallback(ui_components):
    """Test fallback to last known GPS position when slightly stale."""
    # Create UI controller
    ui = UiController(
        display=ui_components["display"],
        view=ui_components["view"],
        bus=ui_components["bus"],
        ts=ui_components["ts"],
        tracks=ui_components["tracks"],
        cfg=ui_components["cfg"],
        center_lat=42.0,
        center_lon=-71.0,
    )
    
    # Publish GPS fix
    gps_fix = GpsFix(
        ts=datetime.now(timezone.utc),
        lat=42.3601,
        lon=-71.0589,
    )
    fix_dict = gps_fix.model_dump()
    fix_dict["ts"] = gps_fix.ts.isoformat()
    await ui_components["bus"].publish("gps.position", pack(fix_dict))
    
    # Wait for GPS listener to process
    await asyncio.sleep(0.1)
    
    # Simulate GPS becoming slightly stale (15 seconds, within 60s window)
    ui._gps_last_update_ts = time.monotonic() - 15.0
    ui._gps_valid = False  # Mark as not fresh
    
    # Call update method
    ui._update_center_from_gps()
    
    # Verify still using last known GPS position
    assert ui._center_lat == pytest.approx(42.3601, abs=0.0001)
    assert ui._center_lon == pytest.approx(-71.0589, abs=0.0001)
    # But marked as not valid (stale)
    assert ui._gps_valid is False
    
    # Cleanup
    if ui._gps_task:
        ui._gps_task.cancel()
        try:
            await ui._gps_task
        except asyncio.CancelledError:
            pass


def test_gps_coordinate_validation(ui_components):
    """Test GPS coordinate validation in listener."""
    # Create UI controller
    ui = UiController(
        display=ui_components["display"],
        view=ui_components["view"],
        bus=ui_components["bus"],
        ts=ui_components["ts"],
        tracks=ui_components["tracks"],
        cfg=ui_components["cfg"],
        center_lat=42.0,
        center_lon=-71.0,
    )
    
    # Test valid coordinates
    ui._gps_lat = 42.3601
    ui._gps_lon = -71.0589
    ui._gps_valid = True
    ui._gps_last_update_ts = time.monotonic()
    ui._update_center_from_gps()
    
    assert ui._center_lat == pytest.approx(42.3601, abs=0.0001)
    assert ui._center_lon == pytest.approx(-71.0589, abs=0.0001)
    
    # Cleanup
    if ui._gps_task:
        ui._gps_task.cancel()


@pytest.mark.asyncio
async def test_gps_status_flag_updates(ui_components):
    """Test GPS status flag updates correctly."""
    # Create UI controller
    ui = UiController(
        display=ui_components["display"],
        view=ui_components["view"],
        bus=ui_components["bus"],
        ts=ui_components["ts"],
        tracks=ui_components["tracks"],
        cfg=ui_components["cfg"],
        center_lat=42.0,
        center_lon=-71.0,
    )
    
    # Initially no GPS
    assert ui._gps_valid is False
    
    # Publish valid GPS fix
    gps_fix = GpsFix(
        ts=datetime.now(timezone.utc),
        lat=42.3601,
        lon=-71.0589,
    )
    fix_dict = gps_fix.model_dump()
    fix_dict["ts"] = gps_fix.ts.isoformat()
    await ui_components["bus"].publish("gps.position", pack(fix_dict))
    
    # Wait for processing
    await asyncio.sleep(0.1)
    
    # GPS should be valid
    assert ui._gps_valid is True
    
    # Simulate GPS becoming stale
    ui._gps_last_update_ts = time.monotonic() - 65.0
    ui._update_center_from_gps()
    
    # GPS should be invalid
    assert ui._gps_valid is False
    
    # Cleanup
    if ui._gps_task:
        ui._gps_task.cancel()
        try:
            await ui._gps_task
        except asyncio.CancelledError:
            pass

