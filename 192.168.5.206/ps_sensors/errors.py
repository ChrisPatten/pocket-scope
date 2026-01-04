"""Custom exception types for ps_sensors."""


class DeviceNotFoundError(Exception):
    """Raised when a hardware device cannot be found or opened."""

    pass


class UnexpectedWhoAmIError(Exception):
    """Raised when ICM-20948 WHO_AM_I register does not match expected value."""

    pass


class MagnetometerIdError(Exception):
    """Raised when AK09916 WIA2 register does not match expected value."""

    pass


class GpsPortError(Exception):
    """Raised when GPS serial port cannot be opened or configured."""

    pass


class PpsNotAvailableError(Exception):
    """Raised when PPS is explicitly required but not available."""

    pass

