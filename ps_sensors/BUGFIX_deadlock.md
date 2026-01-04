# Bug Fix: Deadlock in ICM-20948 SPI Interface

## Issue

The `ps-sensors-live` CLI would hang during ICM-20948 initialization with no error message. The program had to be killed with Ctrl+C.

**Symptoms**:
- Last log: "Opened ICM-20948 on SPI bus 0 device 0 at 1000000 Hz"
- Never reached: "ICM-20948 WHO_AM_I verified" or "ICM-20948 initialized"
- Process hung indefinitely

## Root Cause

**Deadlock in thread synchronization**

The `ICM20948` class used `threading.Lock()` for SPI access serialization. However, the code had nested lock acquisition:

```python
def read_reg(self, bank: int, reg: int) -> int:
    with self._lock:           # Acquires lock
        self.set_bank(bank)    # Tries to acquire lock again → DEADLOCK
        # ... SPI operations

def set_bank(self, bank: int) -> None:
    with self._lock:           # Tries to acquire lock (already held)
        # ... bank switching
```

Since Python's `threading.Lock` is **non-reentrant**, attempting to acquire it twice from the same thread causes a deadlock.

## Fix

Changed `threading.Lock()` to `threading.RLock()` (reentrant lock):

```python
# Before:
self._lock = threading.Lock()

# After:
self._lock = threading.RLock()  # Reentrant lock for nested calls
```

`RLock` allows the same thread to acquire the lock multiple times (with matching releases).

## Additional Improvements

Added detailed logging to `initialize()` method to aid future debugging:

```python
logger.info("Initializing ICM-20948...")
logger.debug("Setting power management...")
logger.debug("Reading WHO_AM_I register...")
# etc.
```

This provides visibility into initialization progress.

## Testing

After fix, initialization should complete successfully:

```
[INFO] Opening ICM-20948 on SPI 0.0
[INFO] Opened ICM-20948 on SPI bus 0 device 0 at 1000000 Hz
[INFO] Initializing ICM-20948...
[INFO] ICM-20948 WHO_AM_I verified: 0xEA
[INFO] ICM-20948 initialized: ±2g accel, ±250 dps gyro
[INFO] Starting AK09916 magnetometer
```

## Files Changed

- `ps_sensors/icm20948/spi.py`:
  - Line 75: Changed `Lock()` to `RLock()`
  - Lines 101-135: Added detailed logging to `initialize()`

## Impact

- **Critical fix**: Program unusable without this
- **Safe change**: RLock is drop-in replacement for Lock with same semantics
- **No API changes**: External interface unchanged

## Prevention

When using locks with method calls that might call each other:
1. Use `RLock` instead of `Lock` if nested acquisition is possible
2. Or refactor to avoid nested locking (extract unlocked internal methods)
3. Add logging to identify hang points during testing

## Version

Fixed in: v0.1.0 (2026-01-04)

