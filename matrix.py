"""Calibration matrix computation for touchscreen alignment."""


def from_4point(raw_tl, raw_br, target_tl, target_br, rotation=0):
    """
    Compute calibration matrix from 4-point capture results.

    raw_tl: (x, y) normalized raw touch coords when user touched the TL target
    raw_br: (x, y) normalized raw touch coords when user touched the BR target
    target_tl: (x, y) normalized screen position of the TL crosshair
    target_br: (x, y) normalized screen position of the BR crosshair
    rotation: display rotation in degrees (0, 90, 180, 270)

    The calibration matrix maps raw device coords to OUTPUT space (pre-rotation).
    The compositor then applies the rotation transform to get screen coords.
    So the targets must be transformed from screen space to pre-rotation space.
    """
    # Transform screen targets to pre-rotation output space
    if rotation == 180:
        target_tl = (1 - target_tl[0], 1 - target_tl[1])
        target_br = (1 - target_br[0], 1 - target_br[1])
    elif rotation == 90:
        target_tl = (target_tl[1], 1 - target_tl[0])
        target_br = (target_br[1], 1 - target_br[0])
    elif rotation == 270:
        target_tl = (1 - target_tl[1], target_tl[0])
        target_br = (1 - target_br[1], target_br[0])
    # rotation == 0: no transform needed

    raw_w = raw_br[0] - raw_tl[0]
    raw_h = raw_br[1] - raw_tl[1]
    if abs(raw_w) < 0.01 or abs(raw_h) < 0.01:
        raise ValueError(f"Touch span too small: w={raw_w:.3f} h={raw_h:.3f}")

    screen_w = target_br[0] - target_tl[0]
    screen_h = target_br[1] - target_tl[1]

    sx = screen_w / raw_w
    tx = target_tl[0] - sx * raw_tl[0]
    sy = screen_h / raw_h
    ty = target_tl[1] - sy * raw_tl[1]

    return [sx, 0.0, tx, 0.0, sy, ty, 0.0, 0.0, 1.0]


def from_corners(tl_x, tl_y, br_x, br_y, rotation=0):
    """Compute 3x3 calibration matrix from measured normalized corner coordinates.
    Used by from_margins (Quick Calibrate) where corners are in panel-space
    and rotation must be explicitly applied."""
    w = br_x - tl_x
    h = br_y - tl_y
    if abs(w) < 0.01 or abs(h) < 0.01:
        raise ValueError(f"Touch span too small: w={w:.3f} h={h:.3f}")
    sx, sy = 1.0 / w, 1.0 / h
    tx, ty = -tl_x / w, -tl_y / h
    if rotation == 0:
        return [sx, 0, tx, 0, sy, ty, 0, 0, 1]
    elif rotation == 180:
        return [-sx, 0, 1 + tx, 0, -sy, 1 + ty, 0, 0, 1]
    elif rotation == 90:
        return [0, sx, tx, -sy, 0, 1 + ty, 0, 0, 1]
    elif rotation == 270:
        return [0, -sx, 1 + tx, sy, 0, ty, 0, 0, 1]
    raise ValueError(f"Unsupported rotation: {rotation}")


def from_margins(W, H, left, right, top, bottom, rotation=0):
    """Compute matrix from known margin pixel values.
    NOTE: This assumes touch panel active area == LCD panel area.
    For better results with a known touch offset, use adjust_for_margins()
    with an existing 4-point calibration."""
    tl_x, tl_y = left / W, top / H
    br_x, br_y = 1 - right / W, 1 - bottom / H
    return from_corners(tl_x, tl_y, br_x, br_y, rotation)


def adjust_for_margins(existing_matrix, W, H,
                       old_left, old_right, old_top, old_bottom,
                       new_left, new_right, new_top, new_bottom):
    """Adjust an existing calibration matrix for changed margins.

    The existing matrix already encodes the true touch-panel-to-LCD mapping
    (from a 4-point calibration). This function rescales it for new margins
    without losing the touch panel boundary information.

    All margin values are in physical panel pixels (kernel-space).
    W, H = physical panel resolution.
    """
    old_vw = W - old_left - old_right
    old_vh = H - old_top - old_bottom
    new_vw = W - new_left - new_right
    new_vh = H - new_top - new_bottom

    if new_vw <= 0 or new_vh <= 0 or old_vw <= 0 or old_vh <= 0:
        return list(existing_matrix)  # safety: return unchanged

    sx, _, tx, _, sy, ty = existing_matrix[:6]
    ratio_w = old_vw / new_vw
    ratio_h = old_vh / new_vh

    new_sx = sx * ratio_w
    new_tx = tx * ratio_w + (old_left - new_left) / new_vw
    new_sy = sy * ratio_h
    new_ty = ty * ratio_h + (old_top - new_top) / new_vh

    return [new_sx, 0.0, new_tx, 0.0, new_sy, new_ty, 0.0, 0.0, 1.0]


def rescale_margins(old_W, old_H, new_W, new_H, left, right, top, bottom):
    """Rescale margin pixel values proportionally for a resolution change.
    Returns (new_left, new_right, new_top, new_bottom) as ints."""
    if old_W <= 0 or old_H <= 0:
        return left, right, top, bottom
    return (
        round(left * new_W / old_W),
        round(right * new_W / old_W),
        round(top * new_H / old_H),
        round(bottom * new_H / old_H),
    )


def identity():
    return [1, 0, 0, 0, 1, 0, 0, 0, 1]


def fmt(m, prec=6):
    """Format as space-separated string for udev."""
    return " ".join(f"{v:.{prec}f}" for v in m)


def describe(m):
    """Human-readable description of the matrix."""
    sx, _, tx, _, sy, ty = m[:6]
    lines = []
    if sx < 0 and sy < 0:
        lines.append("180° rotation (both axes inverted)")
    elif sx < 0:
        lines.append("X-axis inverted")
    elif sy < 0:
        lines.append("Y-axis inverted")
    for label, val in [("X scale", abs(sx)), ("Y scale", abs(sy))]:
        pct = (val - 1) * 100
        lines.append(f"{label}: {val:.4f} ({pct:+.1f}%)")
    if abs(tx) > 0.001:
        lines.append(f"X offset: {tx:+.4f}")
    if abs(ty) > 0.001:
        lines.append(f"Y offset: {ty:+.4f}")
    return "\n".join(lines)
