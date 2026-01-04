# ps_sensors

Standardized Python package for GPS, IMU, and magnetometer access on Raspberry Pi.

Provides reusable interfaces to:
- **GPS**: UART NMEA parsing (GP/GN talkers) with optional PPS capture
- **ICM-20948 IMU**: Accelerometer and gyroscope over SPI
- **AK09916 Magnetometer**: Embedded in ICM-20948, accessed via I2C master

Designed for Raspberry Pi OS (Bookworm) with externally-managed Python environment.

## Features

- **Thread-safe**: All sensor interfaces use proper locking
- **PPS-aligned sampling**: Synchronize readings to GPS 1 PPS for logging/display
- **Background polling**: Magnetometer runs in background thread with cached latest sample
- **Calibration support**: Hard-iron offset correction for magnetometer
- **Live display**: Curses-based single-line display updating at 1 Hz on PPS
- **Graceful degradation**: Optional PPS, works without GPIO on development machines

## Hardware Requirements

### GPS Module (e.g., GY-GPS6MV2)
- **UART**: 9600 baud NMEA output
- **PPS**: 1 Hz pulse output (optional but recommended)
- **Wiring**:
  - TX → Raspberry Pi RX (typically GPIO 15)
  - PPS → Raspberry Pi GPIO 18 (configurable)

### ICM-20948 9-Axis IMU
- **Interface**: SPI (mode 0, 1 MHz)
- **Magnetometer**: AK09916 embedded, accessed via ICM's I2C master
- **Wiring** (SPI0):
  - MOSI → GPIO 10
  - MISO → GPIO 9
  - SCLK → GPIO 11
  - CS → GPIO 8 (CE0)
  - 3V3 and GND

## Installation

### System Dependencies

Install required Raspberry Pi OS packages:

```bash
sudo apt update
sudo apt install -y python3-serial python3-rpi.gpio python3-spidev
```

### Package Installation

#### Option 1: Editable Install (Development)

```bash
cd ps_sensors
python3 -m venv venv --system-site-packages
source venv/bin/activate
pip install -e .
```

#### Option 2: Standard Install

```bash
pip install .
```

## Configuration

### Enable UART and SPI

Edit `/boot/firmware/config.txt`:

```ini
# Enable UART (disable Bluetooth to use hardware UART)
enable_uart=1
dtoverlay=disable-bt

# Enable SPI
dtparam=spi=on
```

Reboot after changes:

```bash
sudo reboot
```

### Verify Devices

```bash
# UART (should show /dev/serial0)
ls -l /dev/serial0

# SPI (should show spidev0.0)
ls -l /dev/spidev0.0
```

## Usage

### CLI Live Display

The `ps-sensors-live` command provides a real-time single-line display updated on each PPS tick:

```bash
# Default configuration (serial0, GPIO 18 for PPS, SPI 0.0)
ps-sensors-live

# Custom configuration
ps-sensors-live \
  --gps-port /dev/ttyAMA0 \
  --pps-gpio 18 \
  --spi-bus 0 \
  --spi-dev 0 \
  --spi-speed 1000000 \
  --mag-mode 0x02
```

**Display format**:
```
Tick | Time     Lat       Lon       Fix    Age  | Accel          | Gyro              | Mag             
-----+---------------------------------------+----------------+-------------------+-----------------
#  42 | 12:34:56  42.3601 -71.0589 RMC:A  0.1s | ACC  0.001 -0.005  1.001g | GYR    0.12   -0.34    0.56°/s | MAG   45.23  -12.34   32.10µT DOR:   5
```

Press `q` to quit.

### Python API

#### Basic GPS Reading

```python
from ps_sensors.gps import GpsUartReader

# Create and start GPS reader
gps = GpsUartReader(port="/dev/serial0", baudrate=9600)
gps.start()

# Get latest fix
fix = gps.latest()
print(f"Position: {fix.lat_deg}, {fix.lon_deg}")
print(f"Fix type: {fix.fix_type}, age: {fix.age_s}s")

gps.stop()
```

#### IMU Reading

```python
from ps_sensors.icm20948 import ICM20948

# Create and initialize IMU
icm = ICM20948(bus=0, device=0, max_speed_hz=1_000_000)
icm.open()
icm.initialize()

# Read accelerometer and gyroscope
sample = icm.read_imu()
print(f"Accel: {sample.ax_g:.3f}, {sample.ay_g:.3f}, {sample.az_g:.3f} g")
print(f"Gyro: {sample.gx_dps:.2f}, {sample.gy_dps:.2f}, {sample.gz_dps:.2f} °/s")

icm.close()
```

#### Magnetometer Reading

```python
from ps_sensors.icm20948 import ICM20948, AK09916Stream
from ps_sensors.util import MagCalibration

# Setup ICM first
icm = ICM20948(bus=0, device=0)
icm.open()
icm.initialize()

# Create calibration with measured offsets
calib = MagCalibration(
    offset_x=-332,
    offset_y=-121,
    offset_z=-238,
)

# Start magnetometer stream
mag = AK09916Stream(
    icm=icm,
    mag_mode=0x02,  # 10 Hz continuous
    poll_hz=100.0,   # Poll at 100 Hz
    calibration=calib,
)
mag.start()

# Wait a bit for first sample
import time
time.sleep(0.5)

# Get latest reading
sample = mag.latest()
if sample:
    print(f"Mag: {sample.mx_uT:.2f}, {sample.my_uT:.2f}, {sample.mz_uT:.2f} µT")
    print(f"DOR total: {sample.dor_total}, OFL: {sample.ofl}")

mag.stop()
icm.close()
```

#### PPS-Aligned Sampling

```python
from ps_sensors.gps import GpsUartReader, PpsListener
from ps_sensors.icm20948 import ICM20948, AK09916Stream
from ps_sensors.sync import PpsSampler
from ps_sensors.util import MagCalibration

# Setup all components
gps = GpsUartReader(port="/dev/serial0")
gps.start()

pps = PpsListener(gpio_pin=18)
pps.start()

icm = ICM20948(bus=0, device=0)
icm.open()
icm.initialize()

mag = AK09916Stream(icm=icm, calibration=MagCalibration())
mag.start()

# Create sampler
sampler = PpsSampler(gps=gps, icm=icm, mag=mag, pps=pps)

# Wait for PPS and capture synchronized readings
for i in range(10):
    sample = sampler.wait_and_sample(timeout=2.0)
    if sample:
        print(f"Tick {sample.tick.tick_count}:")
        print(f"  GPS: {sample.gps.lat_deg}, {sample.gps.lon_deg}")
        print(f"  IMU: ax={sample.imu.ax_g:.3f}g")
        if sample.mag:
            print(f"  Mag: {sample.mag.mx_uT:.2f}µT")

# Cleanup
mag.stop()
pps.stop()
gps.stop()
icm.close()
```

## Calibration

### Magnetometer Hard-Iron Offset

The default calibration uses measured offsets:
- `MAG_OFFSET_X = -332`
- `MAG_OFFSET_Y = -121`
- `MAG_OFFSET_Z = -238`

To measure your own offsets:

1. Collect magnetometer data while rotating the sensor in all directions
2. Find min/max values for each axis
3. Calculate offset as `-(max + min) / 2` for each axis
4. Update `MagCalibration` parameters

```python
from ps_sensors.util import MagCalibration

calib = MagCalibration(
    offset_x=-332,  # Your measured X offset
    offset_y=-121,  # Your measured Y offset
    offset_z=-238,  # Your measured Z offset
)
```

## Troubleshooting

### GPS Not Receiving Data

```bash
# Check if GPS is connected
ls -l /dev/serial0

# Monitor raw NMEA sentences
sudo cat /dev/serial0

# Verify UART is enabled
grep enable_uart /boot/firmware/config.txt
```

### SPI Communication Errors

```bash
# Check SPI devices
ls -l /dev/spidev*

# Verify SPI is enabled
lsmod | grep spi

# Check permissions
sudo usermod -a -G spi,gpio $USER
```

### ICM-20948 WHO_AM_I Mismatch

- Check wiring (especially CS, MOSI, MISO, SCLK)
- Verify 3.3V power supply
- Try lower SPI speed (e.g., 500 kHz)
- Check for loose connections

### Magnetometer Not Working

- Ensure ICM-20948 is working first
- Check that AK09916 WIA2 reads correctly
- Verify I2C master is enabled
- Increase poll rate or mag ODR
- DOR (data overrun) is expected occasionally and tracked but not fatal

### PPS Not Detected

- Verify PPS output is connected to correct GPIO
- Check PPS signal with oscilloscope or logic analyzer
- Ensure `RPi.GPIO` is installed
- PPS is optional; system degrades gracefully without it

## Package Structure

```
ps_sensors/
├── __init__.py          # Package exports
├── errors.py            # Exception types
├── util/                # Utilities
│   ├── timing.py        # Timestamp helpers
│   ├── nmea.py          # NMEA parser
│   ├── calib.py         # Calibration types
│   └── ring.py          # Ring buffer
├── gps/                 # GPS module
│   ├── model.py         # GpsFix, PpsTick types
│   ├── uart.py          # NMEA reader
│   └── pps.py           # PPS capture
├── icm20948/            # IMU module
│   ├── model.py         # ImuSample, MagSample types
│   ├── regs.py          # Register definitions
│   ├── spi.py           # ICM-20948 SPI interface
│   └── mag_ak09916.py   # Magnetometer via I2C master
├── sync/                # Synchronization
│   └── pps_sampler.py   # PPS-aligned sampling
└── cli/                 # CLI tools
    └── live_line.py     # Live display command
```

## API Reference

### GPS Types

- **`GpsFix`**: GPS position with timestamp, fix type, and age
- **`PpsTick`**: PPS pulse event with nanosecond timestamp
- **`GpsUartReader`**: Background NMEA parser (GP/GN RMC+GGA)
- **`PpsListener`**: GPIO-based PPS capture

### IMU Types

- **`ImuSample`**: Accelerometer and gyroscope reading
- **`MagSample`**: Magnetometer reading with status flags
- **`ICM20948`**: Thread-safe SPI interface with bank switching
- **`AK09916Stream`**: Background magnetometer poller

### Synchronization

- **`TickSample`**: Combined GPS+IMU+Mag snapshot
- **`PpsSampler`**: Coordinator for PPS-aligned sampling

## Development

### Running Tests

```bash
# Install dev dependencies
pip install -e ".[dev]"

# Run tests
pytest

# With coverage
pytest --cov=ps_sensors
```

### Code Quality

```bash
# Format code
black ps_sensors/

# Check linting
ruff check ps_sensors/

# Type checking
mypy ps_sensors/
```

## Known Issues

- **Data Overrun (DOR)**: Magnetometer may report DOR occasionally, especially at higher ODRs. This is tracked but not fatal.
- **Bank switching delay**: 2ms delay required for reliable ICM-20948 bank switching on Pi.
- **GPIO permissions**: User must be in `gpio` and `spi` groups.

## Future Enhancements

- Sensor fusion (Madgwick/Mahony filter)
- FIFO support for high-rate IMU capture
- Soft-iron calibration matrix
- Automatic declination correction
- Data logging to file

## License

MIT License - see package metadata for details.

## Credits

Developed for the PocketScope project.

Hardware datasheets:
- ICM-20948: https://invensense.tdk.com/products/motion-tracking/9-axis/icm-20948/
- AK09916: Embedded magnetometer in ICM-20948
- NMEA 0183: GPS sentence format standard

