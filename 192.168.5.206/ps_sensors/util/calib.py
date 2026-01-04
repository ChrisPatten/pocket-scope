"""Magnetometer calibration utilities."""

from dataclasses import dataclass


@dataclass
class MagCalibration:
    """Magnetometer calibration parameters."""

    # Hard-iron offsets (in raw LSB units)
    offset_x: int = -332
    offset_y: int = -121
    offset_z: int = -238

    # Soft-iron correction matrix (future use)
    # For now, identity matrix (no correction)
    scale_matrix: tuple[tuple[float, float, float], ...] = (
        (1.0, 0.0, 0.0),
        (0.0, 1.0, 0.0),
        (0.0, 0.0, 1.0),
    )

    # Declination correction (future use, in degrees)
    declination_deg: float = 0.0

    def apply_hard_iron(self, x: int, y: int, z: int) -> tuple[int, int, int]:
        """Apply hard-iron offset correction."""
        return (
            x - self.offset_x,
            y - self.offset_y,
            z - self.offset_z,
        )

