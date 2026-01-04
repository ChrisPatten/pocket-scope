"""Live single-line sensor display with PPS updates."""

import argparse
import curses
import logging
import sys
import time

from ..gps.pps import PpsListener
from ..gps.uart import GpsUartReader
from ..icm20948.mag_ak09916 import AK09916Stream
from ..icm20948.spi import ICM20948
from ..sync.pps_sampler import PpsSampler
from ..util.calib import MagCalibration

logger = logging.getLogger(__name__)


def format_sample_line(sampler: PpsSampler) -> str:
    """
    Format current sensor readings as single line.

    Returns:
        Formatted string with GPS, IMU, and mag data
    """
    sample = sampler.sample_now()
    if not sample:
        return "Waiting for PPS..."

    gps = sample.gps
    imu = sample.imu
    mag = sample.mag

    # GPS section
    time_str = gps.time_utc_hms or "??:??:??"
    lat_str = f"{gps.lat_deg:8.4f}" if gps.lat_deg is not None else "  n/a   "
    lon_str = f"{gps.lon_deg:9.4f}" if gps.lon_deg is not None else "   n/a   "
    fix_str = f"{gps.fix_type:6s}"
    age_str = f"{gps.age_s:4.1f}s"

    # IMU section
    acc_str = f"ACC {imu.ax_g:6.3f} {imu.ay_g:6.3f} {imu.az_g:6.3f}g"
    gyr_str = f"GYR {imu.gx_dps:7.2f} {imu.gy_dps:7.2f} {imu.gz_dps:7.2f}°/s"

    # Mag section
    if mag:
        mag_str = f"MAG {mag.mx_uT:7.2f} {mag.my_uT:7.2f} {mag.mz_uT:7.2f}µT"
        dor_str = f"DOR:{mag.dor_total:4d}"
    else:
        mag_str = "MAG     n/a"
        dor_str = "DOR:  n/a"

    tick_str = f"#{sample.tick.tick_count:4d}"

    line = (
        f"{tick_str} | "
        f"{time_str} {lat_str} {lon_str} {fix_str} {age_str} | "
        f"{acc_str} | {gyr_str} | {mag_str} {dor_str}"
    )

    return line


def live_display_curses(stdscr: "curses._CursesWindow", sampler: PpsSampler) -> None:
    """
    Display live sensor data in single line using curses.

    Updates on each PPS tick. Press 'q' to quit.

    Args:
        stdscr: Curses window
        sampler: PPS sampler instance
    """
    curses.curs_set(0)  # Hide cursor
    stdscr.nodelay(True)  # Non-blocking input
    stdscr.clear()

    # Header
    stdscr.addstr(0, 0, "ps_sensors live display (press 'q' to quit)")
    stdscr.addstr(
        1,
        0,
        "Tick | Time     Lat       Lon       Fix    Age  | Accel          | Gyro              | Mag             ",
    )
    stdscr.addstr(
        2,
        0,
        "-----+---------------------------------------+----------------+-------------------+-----------------",
    )

    running = True
    while running:
        # Wait for next tick (with timeout)
        sample = sampler.wait_and_sample(timeout=2.0)

        if sample:
            line = format_sample_line(sampler)
            stdscr.addstr(3, 0, line)
            stdscr.clrtoeol()
            stdscr.refresh()

        # Check for quit key
        try:
            key = stdscr.getkey()
            if key.lower() == "q":
                running = False
        except curses.error:
            pass  # No key pressed


def run_live_display(
    gps_port: str,
    pps_gpio: int,
    spi_bus: int,
    spi_dev: int,
    spi_speed_hz: int,
    mag_mode: int,
) -> int:
    """
    Main live display entry point.

    Returns:
        Exit code (0 = success, 1 = error)
    """
    # Setup logging
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    logger.info("Starting ps_sensors live display")

    # Create calibration
    calib = MagCalibration()

    # Initialize components
    try:
        # GPS
        logger.info(f"Opening GPS on {gps_port}")
        gps = GpsUartReader(port=gps_port)
        gps.start()

        # PPS
        logger.info(f"Setting up PPS on GPIO {pps_gpio}")
        pps = PpsListener(gpio_pin=pps_gpio, required=False)
        pps.start()

        # ICM-20948
        logger.info(f"Opening ICM-20948 on SPI {spi_bus}.{spi_dev}")
        icm = ICM20948(bus=spi_bus, device=spi_dev, max_speed_hz=spi_speed_hz)
        icm.open()
        icm.initialize()

        # AK09916 magnetometer
        logger.info("Starting AK09916 magnetometer")
        mag = AK09916Stream(
            icm=icm,
            mag_mode=mag_mode,
            poll_hz=100.0,
            calibration=calib,
        )
        mag.start()

        # Give magnetometer time to start producing data
        time.sleep(0.5)

        # Create sampler
        sampler = PpsSampler(gps=gps, icm=icm, mag=mag, pps=pps)

        logger.info("All sensors initialized, starting display")

        # Run curses display
        curses.wrapper(live_display_curses, sampler)

        logger.info("Display stopped, cleaning up")

        # Cleanup
        mag.stop()
        pps.stop()
        gps.stop()
        icm.close()

        return 0

    except KeyboardInterrupt:
        logger.info("Interrupted by user")
        return 0
    except Exception as e:
        logger.error(f"Error: {e}", exc_info=True)
        return 1


def main() -> None:
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Live sensor display with PPS-aligned updates"
    )
    parser.add_argument(
        "--gps-port",
        default="/dev/serial0",
        help="GPS serial port (default: /dev/serial0)",
    )
    parser.add_argument(
        "--pps-gpio",
        type=int,
        default=18,
        help="PPS GPIO pin (BCM numbering, default: 18)",
    )
    parser.add_argument(
        "--spi-bus",
        type=int,
        default=0,
        help="SPI bus number (default: 0)",
    )
    parser.add_argument(
        "--spi-dev",
        type=int,
        default=0,
        help="SPI device number (default: 0)",
    )
    parser.add_argument(
        "--spi-speed",
        type=int,
        default=1_000_000,
        help="SPI speed in Hz (default: 1000000)",
    )
    parser.add_argument(
        "--mag-mode",
        type=lambda x: int(x, 0),
        default=0x02,
        help="Magnetometer mode (default: 0x02 = 10Hz continuous)",
    )

    args = parser.parse_args()

    exit_code = run_live_display(
        gps_port=args.gps_port,
        pps_gpio=args.pps_gpio,
        spi_bus=args.spi_bus,
        spi_dev=args.spi_dev,
        spi_speed_hz=args.spi_speed,
        mag_mode=args.mag_mode,
    )

    sys.exit(exit_code)


if __name__ == "__main__":
    main()

