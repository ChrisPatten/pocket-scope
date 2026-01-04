"""ICM-20948 and AK09916 register definitions."""

# ICM-20948 register addresses
# Bank selection
REG_BANK_SEL = 0x7F

# Bank 0 registers
WHO_AM_I = 0x00  # Expected: 0xEA
USER_CTRL = 0x03
PWR_MGMT_1 = 0x06
PWR_MGMT_2 = 0x07
INT_PIN_CFG = 0x0F
INT_ENABLE = 0x10
INT_ENABLE_1 = 0x11
INT_ENABLE_2 = 0x12
INT_ENABLE_3 = 0x13

ACCEL_XOUT_H = 0x2D
ACCEL_XOUT_L = 0x2E
ACCEL_YOUT_H = 0x2F
ACCEL_YOUT_L = 0x30
ACCEL_ZOUT_H = 0x31
ACCEL_ZOUT_L = 0x32
GYRO_XOUT_H = 0x33
GYRO_XOUT_L = 0x34
GYRO_YOUT_H = 0x35
GYRO_YOUT_L = 0x36
GYRO_ZOUT_H = 0x37
GYRO_ZOUT_L = 0x38

# External sensor data (magnetometer via I2C master)
EXT_SLV_SENS_DATA_00 = 0x3B
EXT_SLV_SENS_DATA_01 = 0x3C
EXT_SLV_SENS_DATA_02 = 0x3D
EXT_SLV_SENS_DATA_03 = 0x3E
EXT_SLV_SENS_DATA_04 = 0x3F
EXT_SLV_SENS_DATA_05 = 0x40
EXT_SLV_SENS_DATA_06 = 0x41
EXT_SLV_SENS_DATA_07 = 0x42
EXT_SLV_SENS_DATA_08 = 0x43

# Bank 2 registers
GYRO_SMPLRT_DIV = 0x00
GYRO_CONFIG_1 = 0x01
GYRO_CONFIG_2 = 0x02
ACCEL_SMPLRT_DIV_1 = 0x10
ACCEL_SMPLRT_DIV_2 = 0x11
ACCEL_CONFIG = 0x14

# Bank 3 registers (I2C master control)
I2C_MST_CTRL = 0x01
I2C_SLV0_ADDR = 0x03
I2C_SLV0_REG = 0x04
I2C_SLV0_CTRL = 0x05
I2C_SLV0_DO = 0x06

# AK09916 magnetometer registers (accessed via I2C master)
AK_I2C_ADDR = 0x0C  # 7-bit address
AK_WIA2 = 0x01  # Expected: 0x09
AK_ST1 = 0x10  # Status 1
AK_HXL = 0x11  # X-axis low byte
AK_HXH = 0x12  # X-axis high byte
AK_HYL = 0x13  # Y-axis low byte
AK_HYH = 0x14  # Y-axis high byte
AK_HZL = 0x15  # Z-axis low byte
AK_HZH = 0x16  # Z-axis high byte
AK_ST2 = 0x18  # Status 2
AK_CNTL2 = 0x31  # Control 2 (mode)
AK_CNTL3 = 0x32  # Control 3 (reset)

# AK09916 modes
MAG_MODE_POWER_DOWN = 0x00
MAG_MODE_SINGLE = 0x01
MAG_MODE_CONT_10HZ = 0x02
MAG_MODE_CONT_20HZ = 0x04
MAG_MODE_CONT_50HZ = 0x06
MAG_MODE_CONT_100HZ = 0x08

# Scale factors
ACCEL_SCALE_2G = 16384.0  # LSB/g for ±2g range
GYRO_SCALE_250DPS = 131.0  # LSB/(deg/s) for ±250 dps range
MAG_SCALE_UT = 0.15  # uT/LSB for AK09916

# Expected WHO_AM_I values
ICM20948_WHO_AM_I_VALUE = 0xEA
AK09916_WIA2_VALUE = 0x09

