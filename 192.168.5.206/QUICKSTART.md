# ps_sensors Quick Start

## 1. Install System Dependencies

```bash
sudo apt update
sudo apt install -y python3-serial python3-rpi.gpio python3-spidev
```

## 2. Enable UART and SPI

Edit `/boot/firmware/config.txt`:

```ini
enable_uart=1
dtoverlay=disable-bt
dtparam=spi=on
```

Reboot:

```bash
sudo reboot
```

## 3. Install Package

```bash
cd ps_sensors
python3 -m venv venv --system-site-packages
source venv/bin/activate
pip install -e .
```

## 4. Verify Hardware

```bash
# Check UART
ls -l /dev/serial0

# Check SPI
ls -l /dev/spidev0.0

# Add user to groups
sudo usermod -a -G spi,gpio $USER
# Log out and back in for group changes
```

## 5. Run Live Display

```bash
source venv/bin/activate
ps-sensors-live
```

Press `q` to quit.

## 6. Test in Python

```python
from ps_sensors.gps import GpsUartReader
from ps_sensors.icm20948 import ICM20948

# GPS
gps = GpsUartReader(port="/dev/serial0")
gps.start()
fix = gps.latest()
print(f"GPS: {fix.lat_deg}, {fix.lon_deg}")
gps.stop()

# IMU
icm = ICM20948(bus=0, device=0)
icm.open()
icm.initialize()
sample = icm.read_imu()
print(f"Accel: {sample.ax_g:.3f}g")
icm.close()
```

## Wiring Reference

### GPS Module (GY-GPS6MV2)
- TX → Pi RX (GPIO 15)
- RX → Pi TX (GPIO 14) [optional]
- PPS → Pi GPIO 18
- VCC → 3.3V
- GND → GND

### ICM-20948 (SPI)
- VCC → 3.3V
- GND → GND
- SCL → GPIO 11 (SCLK)
- SDA → GPIO 10 (MOSI)
- AD0 → GPIO 9 (MISO)
- CS → GPIO 8 (CE0)

## Troubleshooting

**No GPS data**: Check `sudo cat /dev/serial0` for NMEA sentences

**SPI error**: Verify wiring and `ls -l /dev/spidev0.0`

**Permission denied**: Add user to `spi` and `gpio` groups

**WHO_AM_I error**: Check ICM-20948 wiring and power

See README.md for detailed documentation.

