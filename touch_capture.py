"""Raw touch event capture for calibration point collection."""

import os, struct, select, time, fcntl, array

EVENT_FMT = "LLHHi"
EVENT_SZ = struct.calcsize(EVENT_FMT)

EV_ABS, EV_KEY, EV_SYN = 0x03, 0x01, 0x00
ABS_MT_POSITION_X, ABS_MT_POSITION_Y = 0x35, 0x36
ABS_X, ABS_Y = 0x00, 0x01
BTN_TOUCH = 0x14A


def _abs_range(fd, axis):
    EVIOCGABS = (2 << 30) | (24 << 16) | (ord("E") << 8) | (0x40 + axis)
    buf = array.array("i", [0] * 6)
    fcntl.ioctl(fd, EVIOCGABS, buf)
    return buf[1], buf[2]  # min, max


def get_abs_info(path):
    """Get X/Y min/max for a touch device."""
    try:
        fd = os.open(path, os.O_RDONLY)
        try:
            xmin, xmax = _abs_range(fd, ABS_MT_POSITION_X)
            ymin, ymax = _abs_range(fd, ABS_MT_POSITION_Y)
        except OSError:
            xmin, xmax = _abs_range(fd, ABS_X)
            ymin, ymax = _abs_range(fd, ABS_Y)
        os.close(fd)
        return xmin, xmax, ymin, ymax
    except OSError:
        return None


ABS_MT_TRACKING_ID = 0x39


def capture_points(device_path, num_points=2, timeout=30):
    """
    Capture touch-down coordinates. Yields (index, norm_x, norm_y).
    Captures on touch-down (first contact), not release.
    Waits for finger-up between points to avoid double-counting.
    Requires read access to the device (root or 'input' group).
    """
    info = get_abs_info(device_path)
    if not info:
        raise RuntimeError(f"Cannot read ABS info from {device_path}")
    xmin, xmax, ymin, ymax = info
    xr, yr = xmax - xmin, ymax - ymin
    if xr <= 0 or yr <= 0:
        raise RuntimeError(f"Invalid ABS ranges: X={xmin}..{xmax} Y={ymin}..{ymax}")

    fd = os.open(device_path, os.O_RDONLY | os.O_NONBLOCK)
    try:
        for pi in range(num_points):
            cx = cy = None
            is_down = False
            was_captured = False
            deadline = time.monotonic() + timeout

            # Drain any buffered events
            while select.select([fd], [], [], 0)[0]:
                os.read(fd, EVENT_SZ * 64)

            # Wait for finger-down, capture position, then wait for finger-up
            while time.monotonic() < deadline:
                if not select.select([fd], [], [], 0.1)[0]:
                    continue
                data = os.read(fd, EVENT_SZ * 64)
                off = 0
                done = False

                while off + EVENT_SZ <= len(data):
                    _, _, typ, code, val = struct.unpack_from(EVENT_FMT, data, off)
                    off += EVENT_SZ

                    if typ == EV_ABS:
                        if code in (ABS_MT_POSITION_X, ABS_X):
                            cx = val
                        elif code in (ABS_MT_POSITION_Y, ABS_Y):
                            cy = val
                        elif code == ABS_MT_TRACKING_ID:
                            if val >= 0:
                                is_down = True
                            elif val == -1 and was_captured:
                                # Finger lifted after capture — move to next
                                done = True
                                break

                    elif typ == EV_KEY and code == BTN_TOUCH:
                        if val == 1:
                            is_down = True
                        elif val == 0 and was_captured:
                            done = True
                            break

                    elif typ == EV_SYN and is_down and not was_captured:
                        if cx is not None and cy is not None:
                            # First SYN after touch-down with valid coords
                            nx = (cx - xmin) / xr
                            ny = (cy - ymin) / yr
                            yield (pi, nx, ny)
                            was_captured = True

                if done:
                    break
            else:
                raise TimeoutError(f"Timeout on point {pi + 1}/{num_points}")
    finally:
        os.close(fd)
