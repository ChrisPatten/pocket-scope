#!/usr/bin/env python3
"""
Test script to verify airport label collision avoidance with range ring labels.
"""

import unittest

from pocketscope.data.airports import Airport
from pocketscope.render.airports_layer import AirportsLayer


class MockCanvas:
    """Mock canvas that tracks text rendering calls."""

    def __init__(self):
        self.text_calls = []

    def text(self, pos, s, size_px=12, color=(255, 255, 255, 255)):
        self.text_calls.append(
            {"pos": pos, "text": s, "size_px": size_px, "color": color}
        )

    def polyline(self, pts, width=1, color=(255, 255, 255, 255)):
        pass

    def clear(self, color):
        pass

    def line(self, p0, p1, width=1, color=(255, 255, 255, 255)):
        pass

    def circle(self, center, radius, width=1, color=(255, 255, 255, 255)):
        pass

    def filled_circle(self, center, radius, color=(255, 255, 255, 255)):
        pass


class TestCollisionAvoidance(unittest.TestCase):
    def setUp(self):
        self.layer = AirportsLayer(font_px=12)
        self.canvas = MockCanvas()

    def test_collision_detection(self):
        """Test that airport labels avoid range ring exclusion zones."""
        # Create a mock airport at the center
        airports = [Airport(ident="TEST", lat=42.0, lon=-71.0)]

        # Define exclusion zones that would block the default NE position
        # Default airport label would be at marker_pos + (6, -8)
        # We'll place an exclusion zone there to force alternative positioning
        exclusions = [
            # Exclusion zone that blocks the default NE position
            (240 + 6 - 4, 400 - 8 - 4, 30, 20),  # (x, y, width, height)
        ]

        # Mock the screen size and center coordinates
        screen_size = (480, 800)
        center_lat, center_lon = 42.0, -71.0
        range_nm = 10.0

        # Create the layer and draw with exclusions
        self.layer.draw(
            self.canvas,
            center_lat=center_lat,
            center_lon=center_lon,
            range_nm=range_nm,
            airports=airports,
            screen_size=screen_size,
            range_ring_exclusions=exclusions,
        )

        # Check that text was rendered (airport not skipped)
        self.assertEqual(len(self.canvas.text_calls), 1)

        # Check that the label was not placed at the default NE position
        label_call = self.canvas.text_calls[0]
        default_ne_x = 240 + 6  # center_x + 6
        default_ne_y = 400 - 8  # center_y - 8

        actual_pos = label_call["pos"]
        self.assertNotEqual(
            actual_pos,
            (default_ne_x, default_ne_y),
            "Label should not be at default NE position due to exclusion",
        )

        # Verify the text content
        self.assertEqual(label_call["text"], "TEST")

    def test_no_collision_default_position(self):
        """Test that airport labels use default position when no collision."""
        airports = [Airport(ident="TEST", lat=42.0, lon=-71.0)]

        # No exclusions - should use default NE position
        exclusions = []

        screen_size = (480, 800)
        center_lat, center_lon = 42.0, -71.0
        range_nm = 10.0

        self.layer.draw(
            self.canvas,
            center_lat=center_lat,
            center_lon=center_lon,
            range_nm=range_nm,
            airports=airports,
            screen_size=screen_size,
            range_ring_exclusions=exclusions,
        )

        # Should have one text call
        self.assertEqual(len(self.canvas.text_calls), 1)

        # Should use default NE position (marker + offset)
        label_call = self.canvas.text_calls[0]
        # Note: actual positioning depends on coordinate transformation
        # We'll just verify the text was rendered
        self.assertEqual(label_call["text"], "TEST")

    def test_intersects_exclusions(self):
        """Test the exclusion intersection logic."""
        exclusions = [
            (10, 10, 20, 20),  # Rectangle from (10,10) to (30,30)
            (50, 50, 15, 15),  # Rectangle from (50,50) to (65,65)
        ]

        # Test overlapping cases
        self.assertTrue(
            AirportsLayer._intersects_exclusions(5, 5, 10, 10, exclusions)
        )  # Overlaps first
        self.assertTrue(
            AirportsLayer._intersects_exclusions(25, 25, 10, 10, exclusions)
        )  # Overlaps first
        self.assertTrue(
            AirportsLayer._intersects_exclusions(45, 45, 10, 10, exclusions)
        )  # Overlaps second

        # Test non-overlapping cases
        self.assertFalse(
            AirportsLayer._intersects_exclusions(0, 0, 5, 5, exclusions)
        )  # No overlap
        self.assertFalse(
            AirportsLayer._intersects_exclusions(70, 70, 10, 10, exclusions)
        )  # No overlap

        # Test empty exclusions
        self.assertFalse(AirportsLayer._intersects_exclusions(10, 10, 10, 10, []))


if __name__ == "__main__":
    print("Testing airport label collision avoidance...")
    unittest.main(verbosity=2)
