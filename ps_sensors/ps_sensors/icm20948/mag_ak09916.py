"""AK09916 magnetometer access via ICM-20948 I2C master."""

import logging
import struct
import threading
import time

from ..errors import MagnetometerIdError
from ..util.calib import MagCalibration
from ..util.timing import monotonic_ns
from .model import MagSample
from .regs import (
    AK09916_WIA2_VALUE,
    AK_CNTL2,
    AK_CNTL3,
    AK_I2C_ADDR,
    AK_ST1,
    AK_WIA2,
    EXT_SLV_SENS_DATA_00,
    I2C_MST_CTRL,
    I2C_SLV0_ADDR,
    I2C_SLV0_CTRL,
    I2C_SLV0_DO,
    I2C_SLV0_REG,
    MAG_MODE_CONT_10HZ,
    MAG_SCALE_UT,
    PWR_MGMT_1,
    USER_CTRL,
)
from .spi import ICM20948

logger = logging.getLogger(__name__)


class AK09916Stream:
    """
    AK09916 magnetometer reader via ICM-20948 I2C master.

    Uses the ICM's I2C master to configure the AK09916 and
    continuously read magnetometer data into the EXT_SLV buffer.

    Background polling thread updates a cached latest sample.
    """

    def __init__(
        self,
        icm: ICM20948,
        mag_mode: int = MAG_MODE_CONT_10HZ,
        poll_hz: float = 100.0,
        calibration: MagCalibration | None = None,
        verify_wia2: bool = True,
    ) -> None:
        """
        Create AK09916 magnetometer stream.

        Args:
            icm: ICM20948 instance (must be opened and initialized)
            mag_mode: Magnetometer mode (0x02=10Hz, 0x04=20Hz, etc.)
            poll_hz: Rate to poll EXT buffer (should be > mag ODR)
            calibration: Magnetometer calibration parameters
            verify_wia2: Verify AK09916 WIA2 register on start

        Raises:
            MagnetometerIdError: If WIA2 does not match expected value
        """
        self.icm = icm
        self.mag_mode = mag_mode
        self.poll_hz = poll_hz
        self.calibration = calibration or MagCalibration()
        self.verify_wia2 = verify_wia2

        self._thread: threading.Thread | None = None
        self._running = False
        self._lock = threading.Lock()

        # Latest cached sample
        self._last_sample: MagSample | None = None
        self._dor_total = 0

    def start(self) -> None:
        """
        Initialize I2C master and start continuous magnetometer read.

        This is the proven sequence from the working prototype.
        """
        logger.info("Starting AK09916 magnetometer stream")

        # Step 1: Enable I2C master
        self._i2c_master_enable()
        time.sleep(0.01)

        # Step 2: Verify WIA2
        if self.verify_wia2:
            wia2 = self._read_ak_reg(AK_WIA2)
            if wia2 != AK09916_WIA2_VALUE:
                raise MagnetometerIdError(
                    f"Expected AK09916 WIA2 0x{AK09916_WIA2_VALUE:02X}, "
                    f"got 0x{wia2:02X}"
                )
            logger.info(f"AK09916 WIA2 verified: 0x{wia2:02X}")

        # Step 3: Reset AK09916
        self._write_ak_reg(AK_CNTL3, 0x01)
        time.sleep(0.01)

        # Step 4: Set continuous measurement mode
        self._write_ak_reg(AK_CNTL2, self.mag_mode)
        time.sleep(0.01)

        # Step 5: Setup continuous read of 9 bytes from ST1
        # ST1(1) + HXL(2) + HXH(2) + HYL(2) + HYH(2) + HZL(2) + HZH(2) + TMPS(1) + ST2(1) = 9 bytes
        self._setup_continuous_read(AK_ST1, 9)

        logger.info(
            f"AK09916 configured: mode=0x{self.mag_mode:02X}, "
            f"continuous read enabled"
        )

        # Start background polling thread
        self._running = True
        self._thread = threading.Thread(target=self._poll_loop, daemon=True)
        self._thread.start()
        logger.info(f"AK09916 polling started at {self.poll_hz} Hz")

    def stop(self) -> None:
        """Stop background polling thread."""
        if not self._running:
            return

        self._running = False
        if self._thread:
            self._thread.join(timeout=2.0)
        logger.info("AK09916 polling stopped")

    def latest(self) -> MagSample | None:
        """
        Get latest magnetometer sample.

        Returns:
            MagSample or None if no data available yet
        """
        with self._lock:
            return self._last_sample

    def poll_once(self) -> MagSample | None:
        """
        Poll magnetometer buffer once (blocking).

        Reads 9 bytes from EXT_SLV buffer and parses if DRDY set.

        Returns:
            MagSample or None if data not ready
        """
        # Read 9 bytes from EXT_SLV_SENS_DATA_00
        data = self.icm.read_bytes(0, EXT_SLV_SENS_DATA_00, 9)

        st1 = data[0]
        st2 = data[8]

        # Check DRDY (bit 0 of ST1)
        drdy = (st1 & 0x01) != 0
        if not drdy:
            return None

        # Parse magnetometer data (signed 16-bit little-endian)
        mx_raw, my_raw, mz_raw = struct.unpack("<hhh", data[1:7])

        # Apply hard-iron calibration
        mx_raw, my_raw, mz_raw = self.calibration.apply_hard_iron(mx_raw, my_raw, mz_raw)

        # Convert to uT
        mx_uT = mx_raw * MAG_SCALE_UT
        my_uT = my_raw * MAG_SCALE_UT
        mz_uT = mz_raw * MAG_SCALE_UT

        # Parse status flags
        dor = (st1 & 0x02) != 0  # Data overrun
        ofl = (st2 & 0x08) != 0  # Overflow

        if dor:
            self._dor_total += 1

        sample = MagSample(
            mx_uT=mx_uT,
            my_uT=my_uT,
            mz_uT=mz_uT,
            dor=dor,
            ofl=ofl,
            dor_total=self._dor_total,
            timestamp_ns=monotonic_ns(),
        )

        logger.debug(
            f"Mag: {mx_uT:7.2f} {my_uT:7.2f} {mz_uT:7.2f} uT "
            f"(DOR={dor} OFL={ofl} DOR_total={self._dor_total})"
        )

        return sample

    def _poll_loop(self) -> None:
        """Background thread: continuously poll magnetometer buffer."""
        poll_interval = 1.0 / self.poll_hz
        logger.debug(f"Mag poll loop started (interval={poll_interval:.4f}s)")

        while self._running:
            try:
                sample = self.poll_once()
                if sample:
                    with self._lock:
                        self._last_sample = sample
            except Exception as e:
                logger.error(f"Error polling magnetometer: {e}")

            time.sleep(poll_interval)

        logger.debug("Mag poll loop exited")

    def _i2c_master_enable(self) -> None:
        """Enable ICM-20948 I2C master interface."""
        # Set clock to auto-select
        self.icm.write_reg(0, PWR_MGMT_1, 0x01)
        time.sleep(0.01)

        # Enable I2C master and disable I2C slave interface
        self.icm.write_reg(0, USER_CTRL, 0x30)  # I2C_MST_EN | I2C_IF_DIS
        time.sleep(0.01)

        # Set I2C master clock to ~400 kHz
        self.icm.write_reg(3, I2C_MST_CTRL, 0x07)
        time.sleep(0.01)

        logger.debug("I2C master enabled")

    def _read_ak_reg(self, reg: int) -> int:
        """
        Read single AK09916 register via I2C master.

        Args:
            reg: AK09916 register address

        Returns:
            Register value
        """
        # Setup SLV0 for single-byte read
        self.icm.write_reg(3, I2C_SLV0_ADDR, AK_I2C_ADDR | 0x80)  # Read bit set
        self.icm.write_reg(3, I2C_SLV0_REG, reg)
        self.icm.write_reg(3, I2C_SLV0_CTRL, 0x81)  # Enable, 1 byte
        time.sleep(0.01)  # Wait for transaction

        # Read from EXT_SLV buffer
        value = self.icm.read_reg(0, EXT_SLV_SENS_DATA_00)
        return value

    def _write_ak_reg(self, reg: int, value: int) -> None:
        """
        Write single AK09916 register via I2C master.

        Args:
            reg: AK09916 register address
            value: Value to write
        """
        # Setup SLV0 for single-byte write
        self.icm.write_reg(3, I2C_SLV0_ADDR, AK_I2C_ADDR)  # Write (bit 7 clear)
        self.icm.write_reg(3, I2C_SLV0_REG, reg)
        self.icm.write_reg(3, I2C_SLV0_DO, value)  # Data out
        self.icm.write_reg(3, I2C_SLV0_CTRL, 0x81)  # Enable, 1 byte
        time.sleep(0.01)  # Wait for transaction

    def _setup_continuous_read(self, start_reg: int, num_bytes: int) -> None:
        """
        Setup continuous read from AK09916 into EXT_SLV buffer.

        Args:
            start_reg: Starting AK09916 register address
            num_bytes: Number of bytes to read continuously
        """
        # Configure SLV0 for continuous read
        self.icm.write_reg(3, I2C_SLV0_ADDR, AK_I2C_ADDR | 0x80)  # Read bit set
        self.icm.write_reg(3, I2C_SLV0_REG, start_reg)
        self.icm.write_reg(3, I2C_SLV0_CTRL, 0x80 | num_bytes)  # Enable, N bytes
        time.sleep(0.01)

        logger.debug(
            f"Continuous read setup: start=0x{start_reg:02X}, count={num_bytes}"
        )

