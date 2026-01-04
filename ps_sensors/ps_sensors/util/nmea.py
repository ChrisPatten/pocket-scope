"""NMEA sentence parsing utilities."""

import logging
import re
from typing import Any

logger = logging.getLogger(__name__)


def _ddmm_to_decimal(ddmm_str: str, direction: str) -> float:
    """
    Convert NMEA ddmm.mmmm or dddmm.mmmm format to decimal degrees.

    Args:
        ddmm_str: Latitude (ddmm.mmmm) or longitude (dddmm.mmmm) string
        direction: 'N', 'S', 'E', or 'W'

    Returns:
        Decimal degrees (negative for S/W)
    """
    if not ddmm_str or not direction:
        raise ValueError("Empty coordinate or direction")

    # Determine if latitude (2 digit degrees) or longitude (3 digit degrees)
    # Latitude: ddmm.mmmm, Longitude: dddmm.mmmm
    dot_pos = ddmm_str.find(".")
    if dot_pos == -1:
        raise ValueError(f"No decimal point in coordinate: {ddmm_str}")

    # Count digits before decimal to determine format
    int_part = ddmm_str[:dot_pos]
    if len(int_part) <= 4:  # ddmm format (latitude)
        degrees = int(int_part[:-2])
        minutes = float(int_part[-2:] + ddmm_str[dot_pos:])
    else:  # dddmm format (longitude)
        degrees = int(int_part[:-2])
        minutes = float(int_part[-2:] + ddmm_str[dot_pos:])

    decimal = degrees + minutes / 60.0

    if direction in ("S", "W"):
        decimal = -decimal

    return decimal


def parse_rmc(sentence: str) -> dict[str, Any]:
    """
    Parse $GPRMC or $GNRMC sentence.

    Example: $GPRMC,123519,A,4807.038,N,01131.000,E,022.4,084.4,230394,003.1,W*6A

    Returns:
        Dict with keys: time_utc, lat_deg, lon_deg, status, valid
    """
    parts = sentence.split(",")
    if len(parts) < 10:
        logger.debug(f"RMC too short: {sentence}")
        return {"valid": False}

    try:
        time_utc = parts[1]  # HHMMSS[.ss]
        status = parts[2]  # A=active, V=void
        lat_raw = parts[3]
        lat_dir = parts[4]
        lon_raw = parts[5]
        lon_dir = parts[6]

        # Format time as HH:MM:SS
        time_formatted = None
        if len(time_utc) >= 6:
            time_formatted = f"{time_utc[0:2]}:{time_utc[2:4]}:{time_utc[4:6]}"

        lat_deg = None
        lon_deg = None
        if lat_raw and lat_dir and lon_raw and lon_dir:
            try:
                lat_deg = _ddmm_to_decimal(lat_raw, lat_dir)
                lon_deg = _ddmm_to_decimal(lon_raw, lon_dir)
            except ValueError as e:
                logger.debug(f"RMC coord parse error: {e}")

        return {
            "valid": True,
            "time_utc": time_formatted,
            "lat_deg": lat_deg,
            "lon_deg": lon_deg,
            "status": status,
            "fix_type": f"RMC:{status}",
        }
    except (IndexError, ValueError) as e:
        logger.debug(f"RMC parse error: {e} in {sentence}")
        return {"valid": False}


def parse_gga(sentence: str) -> dict[str, Any]:
    """
    Parse $GPGGA or $GNGGA sentence.

    Example: $GPGGA,123519,4807.038,N,01131.000,E,1,08,0.9,545.4,M,46.9,M,,*47

    Returns:
        Dict with keys: time_utc, lat_deg, lon_deg, fix_quality, num_sats, valid
    """
    parts = sentence.split(",")
    if len(parts) < 10:
        logger.debug(f"GGA too short: {sentence}")
        return {"valid": False}

    try:
        time_utc = parts[1]  # HHMMSS[.ss]
        lat_raw = parts[2]
        lat_dir = parts[3]
        lon_raw = parts[4]
        lon_dir = parts[5]
        fix_quality = parts[6]  # 0=invalid, 1=GPS, 2=DGPS, etc.

        # Format time as HH:MM:SS
        time_formatted = None
        if len(time_utc) >= 6:
            time_formatted = f"{time_utc[0:2]}:{time_utc[2:4]}:{time_utc[4:6]}"

        lat_deg = None
        lon_deg = None
        if lat_raw and lat_dir and lon_raw and lon_dir:
            try:
                lat_deg = _ddmm_to_decimal(lat_raw, lat_dir)
                lon_deg = _ddmm_to_decimal(lon_raw, lon_dir)
            except ValueError as e:
                logger.debug(f"GGA coord parse error: {e}")

        return {
            "valid": True,
            "time_utc": time_formatted,
            "lat_deg": lat_deg,
            "lon_deg": lon_deg,
            "fix_quality": fix_quality,
            "fix_type": f"GGA:{fix_quality}",
        }
    except (IndexError, ValueError) as e:
        logger.debug(f"GGA parse error: {e} in {sentence}")
        return {"valid": False}


def is_nmea_sentence(line: str) -> bool:
    """Check if line looks like a valid NMEA sentence."""
    return line.startswith("$") and "*" in line and len(line) > 10

