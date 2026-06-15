"""
Unit tests for ps_sensors GPS source adapter.

Tests GPS fix validation, conversion, and event bus publishing.
"""

import asyncio
from datetime import datetime, timezone
from unittest.mock import MagicMock, Mock, patch

import pytest

from pocketscope.core.events import EventBus, unpack
from pocketscope.core.models import GpsFix


class MockPsGpsFix:
    """Mock ps_sensors GpsFix for testing."""

    def __init__(
        self,
        lat_deg: float | None = None,
        lon_deg: float | None = None,
        fix_type: str = "UNKNOWN",
        age_s: float = 0.0,
        time_utc_hms: str | None = None,
        timestamp_ns: int = 0,
    ):
        self.lat_deg = lat_deg
        self.lon_deg = lon_deg
        self.fix_type = fix_type
        self.age_s = age_s
        self.time_utc_hms = time_utc_hms
        self.timestamp_ns = timestamp_ns
        self.raw_last = None


@pytest.mark.asyncio
async def test_gps_source_valid_fix():
    """Test GPS source publishes valid fix to event bus."""
    from pocketscope.ingest.gps.ps_sensors_source import PsSensorsGpsSource

    bus = EventBus()
    
    # Mock ps_sensors GPS reader
    mock_reader = Mock()
    mock_fix = MockPsGpsFix(
        lat_deg=42.3601,
        lon_deg=-71.0589,
        fix_type="RMC:A",
        age_s=1.0,
        timestamp_ns=int(datetime.now(timezone.utc).timestamp() * 1e9),
    )
    mock_reader.latest.return_value = mock_fix
    mock_reader.start = Mock()
    mock_reader.stop = Mock()
    
    # Patch ps_sensors import
    with patch("pocketscope.ingest.gps.ps_sensors_source.GpsUartReader", return_value=mock_reader):
        source = PsSensorsGpsSource(bus=bus, poll_hz=10.0)
        
        # Subscribe to GPS events
        sub = bus.subscribe("gps.position")
        assert sub is not None
        
        # Start source in background
        task = asyncio.create_task(source.run())
        
        # Wait for at least one GPS event
        try:
            envelope = await asyncio.wait_for(sub.__anext__(), timeout=1.0)
            
            # Verify event data
            data = unpack(envelope.payload)
            gps_fix = GpsFix.model_validate(data)
            
            assert gps_fix.lat == pytest.approx(42.3601, abs=0.0001)
            assert gps_fix.lon == pytest.approx(-71.0589, abs=0.0001)
            
        finally:
            await source.stop()
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass


@pytest.mark.asyncio
async def test_gps_source_invalid_fix_not_published():
    """Test GPS source does not publish invalid fix."""
    from pocketscope.ingest.gps.ps_sensors_source import PsSensorsGpsSource

    bus = EventBus()
    
    # Mock ps_sensors GPS reader with invalid fix
    mock_reader = Mock()
    mock_fix = MockPsGpsFix(
        lat_deg=None,  # Invalid: missing coordinates
        lon_deg=None,
        fix_type="RMC:V",  # Void fix
        age_s=1.0,
    )
    mock_reader.latest.return_value = mock_fix
    mock_reader.start = Mock()
    mock_reader.stop = Mock()
    
    # Patch ps_sensors import
    with patch("pocketscope.ingest.gps.ps_sensors_source.GpsUartReader", return_value=mock_reader):
        source = PsSensorsGpsSource(bus=bus, poll_hz=10.0)
        
        # Subscribe to GPS events
        sub = bus.subscribe("gps.position")
        assert sub is not None
        
        # Start source in background
        task = asyncio.create_task(source.run())
        
        # Wait briefly - should not receive any events
        try:
            await asyncio.wait_for(sub.__anext__(), timeout=0.2)
            pytest.fail("Should not have received GPS event for invalid fix")
        except asyncio.TimeoutError:
            # Expected - no event should be published
            pass
        finally:
            await source.stop()
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass


@pytest.mark.asyncio
async def test_gps_source_stale_fix_not_published():
    """Test GPS source does not publish stale fix (age > 10s)."""
    from pocketscope.ingest.gps.ps_sensors_source import PsSensorsGpsSource

    bus = EventBus()
    
    # Mock ps_sensors GPS reader with stale fix
    mock_reader = Mock()
    mock_fix = MockPsGpsFix(
        lat_deg=42.3601,
        lon_deg=-71.0589,
        fix_type="RMC:A",
        age_s=15.0,  # Stale: > 10 seconds old
        timestamp_ns=int(datetime.now(timezone.utc).timestamp() * 1e9),
    )
    mock_reader.latest.return_value = mock_fix
    mock_reader.start = Mock()
    mock_reader.stop = Mock()
    
    # Patch ps_sensors import
    with patch("pocketscope.ingest.gps.ps_sensors_source.GpsUartReader", return_value=mock_reader):
        source = PsSensorsGpsSource(bus=bus, poll_hz=10.0)
        
        # Subscribe to GPS events
        sub = bus.subscribe("gps.position")
        assert sub is not None
        
        # Start source in background
        task = asyncio.create_task(source.run())
        
        # Wait briefly - should not receive any events
        try:
            await asyncio.wait_for(sub.__anext__(), timeout=0.2)
            pytest.fail("Should not have received GPS event for stale fix")
        except asyncio.TimeoutError:
            # Expected - no event should be published
            pass
        finally:
            await source.stop()
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass


def test_gps_fix_validation():
    """Test GPS fix validation logic."""
    from pocketscope.ingest.gps.ps_sensors_source import PsSensorsGpsSource

    bus = EventBus()
    source = PsSensorsGpsSource(bus=bus)
    
    # Valid RMC:A fix
    valid_fix = MockPsGpsFix(
        lat_deg=42.3601,
        lon_deg=-71.0589,
        fix_type="RMC:A",
        age_s=5.0,
    )
    assert source._is_valid_fix(valid_fix) is True
    
    # Valid GGA:1 fix
    valid_gga = MockPsGpsFix(
        lat_deg=42.3601,
        lon_deg=-71.0589,
        fix_type="GGA:1",
        age_s=5.0,
    )
    assert source._is_valid_fix(valid_gga) is True
    
    # Invalid: RMC:V (void)
    invalid_void = MockPsGpsFix(
        lat_deg=42.3601,
        lon_deg=-71.0589,
        fix_type="RMC:V",
        age_s=5.0,
    )
    assert source._is_valid_fix(invalid_void) is False
    
    # Invalid: missing coordinates
    invalid_no_coords = MockPsGpsFix(
        lat_deg=None,
        lon_deg=None,
        fix_type="RMC:A",
        age_s=5.0,
    )
    assert source._is_valid_fix(invalid_no_coords) is False
    
    # Invalid: stale (> 10s)
    invalid_stale = MockPsGpsFix(
        lat_deg=42.3601,
        lon_deg=-71.0589,
        fix_type="RMC:A",
        age_s=15.0,
    )
    assert source._is_valid_fix(invalid_stale) is False
    
    # Invalid: coordinates out of range
    invalid_coords = MockPsGpsFix(
        lat_deg=999.0,  # Invalid latitude
        lon_deg=-71.0589,
        fix_type="RMC:A",
        age_s=5.0,
    )
    assert source._is_valid_fix(invalid_coords) is False


def test_gps_fix_conversion():
    """Test conversion from ps_sensors GpsFix to PocketScope GpsFix."""
    from pocketscope.ingest.gps.ps_sensors_source import PsSensorsGpsSource

    bus = EventBus()
    source = PsSensorsGpsSource(bus=bus)
    
    # Create mock ps_sensors fix
    ps_fix = MockPsGpsFix(
        lat_deg=42.3601,
        lon_deg=-71.0589,
        fix_type="RMC:A",
        age_s=2.0,
        timestamp_ns=int(datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc).timestamp() * 1e9),
    )
    
    # Convert to PocketScope GpsFix
    pocketscope_fix = source._convert_fix(ps_fix)
    
    assert pocketscope_fix is not None
    assert pocketscope_fix.lat == pytest.approx(42.3601, abs=0.0001)
    assert pocketscope_fix.lon == pytest.approx(-71.0589, abs=0.0001)
    assert pocketscope_fix.ts.year == 2024
    assert pocketscope_fix.ts.month == 1
    assert pocketscope_fix.ts.day == 1
    # Optional fields should be None (ps_sensors doesn't provide them)
    assert pocketscope_fix.alt_m is None
    assert pocketscope_fix.speed_mps is None
    assert pocketscope_fix.track_deg is None
    assert pocketscope_fix.hdop is None

