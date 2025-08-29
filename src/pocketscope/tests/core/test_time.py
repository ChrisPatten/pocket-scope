"""Tests for time abstraction module."""

import asyncio
from unittest.mock import patch

import pytest

from pocketscope.core.time import RealTimeSource, SimTimeSource, TimeSource


class TestTimeSource:
    """Test TimeSource protocol compliance."""

    def test_real_time_source_implements_protocol(self) -> None:
        """Test that RealTimeSource implements TimeSource protocol."""
        ts: TimeSource = RealTimeSource()
        assert hasattr(ts, "monotonic")
        assert hasattr(ts, "wall_time")
        assert hasattr(ts, "sleep")

    def test_sim_time_source_implements_protocol(self) -> None:
        """Test that SimTimeSource implements TimeSource protocol."""
        ts: TimeSource = SimTimeSource()
        assert hasattr(ts, "monotonic")
        assert hasattr(ts, "wall_time")
        assert hasattr(ts, "sleep")


class TestRealTimeSource:
    """Test RealTimeSource functionality."""

    def test_monotonic_returns_float(self) -> None:
        """Test that monotonic() returns a float."""
        ts = RealTimeSource()
        result = ts.monotonic()
        assert isinstance(result, float)
        assert result > 0

    def test_wall_time_returns_float(self) -> None:
        """Test that wall_time() returns a float."""
        ts = RealTimeSource()
        result = ts.wall_time()
        assert isinstance(result, float)
        assert result > 0

    @pytest.mark.asyncio
    async def test_sleep_waits_approximately_correct_time(self) -> None:
        """Test that sleep() waits approximately the correct time."""
        ts = RealTimeSource()
        start = ts.monotonic()
        await ts.sleep(0.01)  # Small sleep to avoid test slowness
        elapsed = ts.monotonic() - start

        # Should be close to 0.01 seconds (allow some tolerance)
        assert 0.005 <= elapsed <= 0.05


class TestSimTimeSource:
    """Test SimTimeSource functionality."""

    def test_init_with_default_start(self) -> None:
        """Test initialization with default start time."""
        ts = SimTimeSource()
        assert ts.monotonic() == 0.0

    def test_init_with_custom_start(self) -> None:
        """Test initialization with custom start time."""
        ts = SimTimeSource(start=100.0)
        assert ts.monotonic() == 100.0

    def test_wall_time_based_on_monotonic(self) -> None:
        """Test that wall_time is based on monotonic time."""
        with patch("time.time", return_value=1000000.0):
            ts = SimTimeSource(start=50.0)
            wall_time = ts.wall_time()
            # Should be creation time + monotonic time
            assert wall_time == 1000000.0 + 50.0

    def test_set_time_forward(self) -> None:
        """Test setting time forward."""
        ts = SimTimeSource(start=10.0)
        ts.set_time(20.0)
        assert ts.monotonic() == 20.0

    def test_set_time_backward_raises_error(self) -> None:
        """Test that setting time backward raises ValueError."""
        ts = SimTimeSource(start=10.0)
        with pytest.raises(ValueError, match="Cannot set time backwards"):
            ts.set_time(5.0)

    def test_advance_positive(self) -> None:
        """Test advancing time by positive amount."""
        ts = SimTimeSource(start=10.0)
        ts.advance(5.0)
        assert ts.monotonic() == 15.0

    def test_advance_zero(self) -> None:
        """Test advancing time by zero."""
        ts = SimTimeSource(start=10.0)
        ts.advance(0.0)
        assert ts.monotonic() == 10.0

    def test_advance_negative_raises_error(self) -> None:
        """Test that advancing by negative amount raises ValueError."""
        ts = SimTimeSource(start=10.0)
        with pytest.raises(ValueError, match="Cannot advance time backwards"):
            ts.advance(-1.0)

    @pytest.mark.asyncio
    async def test_sleep_zero_yields_control(self) -> None:
        """Test that sleep(0) yields control to event loop."""
        ts = SimTimeSource()

        # This should complete immediately without hanging
        start_time = ts.monotonic()
        await ts.sleep(0.0)
        assert ts.monotonic() == start_time

    @pytest.mark.asyncio
    async def test_sleep_negative_raises_error(self) -> None:
        """Test that negative sleep raises ValueError."""
        ts = SimTimeSource()
        with pytest.raises(ValueError, match="Sleep duration must be non-negative"):
            await ts.sleep(-1.0)

    @pytest.mark.asyncio
    async def test_sim_time_advance_and_sleep(self) -> None:
        """Test that sleep waits until time is advanced."""
        ts = SimTimeSource(start=0.0)

        # Start a sleep task
        sleep_task = asyncio.create_task(ts.sleep(1.5))

        # Give the task a chance to start
        await asyncio.sleep(0)

        # Sleep should not be complete yet
        assert not sleep_task.done()
        assert ts.monotonic() == 0.0

        # Advance time partially - sleep should still not complete
        ts.advance(1.0)
        await asyncio.sleep(0)  # Allow task to run
        assert not sleep_task.done()
        assert ts.monotonic() == 1.0

        # Advance time to exactly the sleep duration
        ts.advance(0.5)
        await asyncio.sleep(0)  # Allow task to run
        assert sleep_task.done()
        assert ts.monotonic() == 1.5

        # Task should complete without error
        await sleep_task

    @pytest.mark.asyncio
    async def test_multiple_sleepers_wake_in_order(self) -> None:
        """Test that multiple sleepers wake up in the correct order."""
        ts = SimTimeSource(start=0.0)

        # Start multiple sleep tasks
        sleep1 = asyncio.create_task(ts.sleep(1.0))
        sleep2 = asyncio.create_task(ts.sleep(0.5))
        sleep3 = asyncio.create_task(ts.sleep(1.5))

        await asyncio.sleep(0)  # Allow tasks to start

        # None should be done initially
        assert not sleep1.done()
        assert not sleep2.done()
        assert not sleep3.done()

        # Advance to 0.5 - only sleep2 should complete
        ts.advance(0.5)
        await asyncio.sleep(0)
        assert not sleep1.done()
        assert sleep2.done()
        assert not sleep3.done()

        # Advance to 1.0 - sleep1 should complete
        ts.advance(0.5)
        await asyncio.sleep(0)
        assert sleep1.done()
        assert sleep2.done()
        assert not sleep3.done()

        # Advance to 1.5 - sleep3 should complete
        ts.advance(0.5)
        await asyncio.sleep(0)
        assert sleep1.done()
        assert sleep2.done()
        assert sleep3.done()

        # All tasks should complete without error
        await asyncio.gather(sleep1, sleep2, sleep3)

    @pytest.mark.asyncio
    async def test_set_time_wakes_sleepers(self) -> None:
        """Test that set_time() wakes up sleepers whose time has come."""
        ts = SimTimeSource(start=0.0)

        # Start sleep tasks
        sleep1 = asyncio.create_task(ts.sleep(1.0))
        sleep2 = asyncio.create_task(ts.sleep(2.0))

        await asyncio.sleep(0)  # Allow tasks to start

        # Jump directly to time 1.5 - first sleep should complete
        ts.set_time(1.5)
        await asyncio.sleep(0)
        assert sleep1.done()
        assert not sleep2.done()

        # Jump to time 2.0 - second sleep should complete
        ts.set_time(2.0)
        await asyncio.sleep(0)
        assert sleep2.done()

        await asyncio.gather(sleep1, sleep2)

    def test_next_due_monotonic_no_sleepers(self) -> None:
        """Test next_due_monotonic when no sleepers are pending."""
        ts = SimTimeSource()
        assert ts.next_due_monotonic() is None

    @pytest.mark.asyncio
    async def test_next_due_monotonic_with_sleepers(self) -> None:
        """Test next_due_monotonic returns the earliest due time."""
        ts = SimTimeSource(start=0.0)

        # Start multiple sleep tasks
        sleep1 = asyncio.create_task(ts.sleep(2.0))
        sleep2 = asyncio.create_task(ts.sleep(1.0))
        sleep3 = asyncio.create_task(ts.sleep(3.0))

        await asyncio.sleep(0)  # Allow tasks to start

        # Should return the earliest due time (1.0)
        assert ts.next_due_monotonic() == 1.0

        # Advance past first sleeper
        ts.advance(1.5)
        await asyncio.sleep(0)

        # Should now return the next earliest (2.0)
        assert ts.next_due_monotonic() == 2.0

        # Advance past second sleeper
        ts.advance(1.0)
        await asyncio.sleep(0)

        # Should now return the last one (3.0)
        assert ts.next_due_monotonic() == 3.0

        # Advance past the final sleeper to complete all tasks
        ts.advance(0.5)  # Time is now 3.0, which completes sleep3
        await asyncio.sleep(0)

        # No more sleepers should be pending
        assert ts.next_due_monotonic() is None

        # Clean up - all tasks should now be completed
        await asyncio.gather(sleep1, sleep2, sleep3, return_exceptions=True)

    @pytest.mark.asyncio
    async def test_cancelled_sleep_does_not_interfere(self) -> None:
        """Test that cancelled sleep tasks don't interfere with others."""
        ts = SimTimeSource(start=0.0)

        # Start sleep tasks
        sleep1 = asyncio.create_task(ts.sleep(1.0))
        sleep2 = asyncio.create_task(ts.sleep(2.0))

        await asyncio.sleep(0)  # Allow tasks to start

        # Cancel first task
        sleep1.cancel()

        # Advance past both times
        ts.advance(2.5)
        await asyncio.sleep(0)

        # Second task should complete normally
        assert sleep2.done()
        await sleep2

        # First task should be cancelled
        with pytest.raises(asyncio.CancelledError):
            await sleep1
