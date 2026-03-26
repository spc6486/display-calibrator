# Technical Reference

## Display borders

Borders are set via the kernel `video=` parameter in `/boot/firmware/cmdline.txt` (or `/boot/cmdline.txt` on older Pi OS):

```
video=DSI-1:margin_left=40,margin_right=40,margin_top=90,margin_bottom=120
```

These margins are in **physical panel pixels** and applied by the kernel **before** compositor rotation.

### Rotation mapping

With 180° rotation, the kernel's "left" margin crops what the user sees as the "right" edge. The app handles this automatically:

| Kernel margin | User sees (0°) | User sees (180°) | User sees (90°) | User sees (270°) |
|---------------|----------------|-------------------|------------------|-------------------|
| margin_left | Left | Right | Bottom | Top |
| margin_right | Right | Left | Top | Bottom |
| margin_top | Top | Bottom | Right | Left |
| margin_bottom | Bottom | Top | Left | Right |

The functions `_margin_kernel_to_user()` and `_margin_user_to_kernel()` in `display-calibrator.py` perform this mapping.

### Resolution scaling

Margins are pixel-absolute. Changing resolution from 2048×1536 to 1024×768 without adjusting margins doubles the proportional crop. The app detects resolution changes and offers proportional rescaling via `matrix.rescale_margins()`.

## Touchscreen calibration

### Calibration matrix

The calibration matrix is a 3×3 affine transform stored as a `LIBINPUT_CALIBRATION_MATRIX` in a udev rule:

```
SUBSYSTEM=="input", KERNEL=="event*", ATTRS{name}=="DeviceName", ENV{LIBINPUT_CALIBRATION_MATRIX}="sx 0 tx 0 sy ty 0 0 1"
```

Where:
- `sx`, `sy` — scale factors (negative = axis inverted, used for 180° rotation)
- `tx`, `ty` — translation offsets (normalized 0.0–1.0)

### 4-point calibration

The app captures raw normalized touch coordinates at four screen corners (TL, TR, BR, BL) and computes the matrix using `matrix.from_4point()`:

1. Screen targets are transformed from screen space to pre-rotation output space
2. Scale factors are computed from the ratio of screen span to raw touch span
3. Translation is derived from the offset between target position and scaled touch position

### Reference margins

When calibration is performed, the app saves the active kernel margins to `/etc/udev/rules.d/99-touchscreen-calibration.ref`. This allows future margin adjustments to be applied to the existing calibration matrix without re-running 4-point calibration.

## Compositor integration

The app auto-detects whether labwc or Wayfire is running. labwc is the primary development target; Wayfire support is maintained but Pi OS deprecated Wayfire in favor of labwc as of October 2024.

### labwc

| Config | File | Format |
|--------|------|--------|
| Display settings | `~/.config/kanshi/config` | `profile { output CONN enable mode RES position X,Y transform ROT scale S }` |
| Touch mapping | `~/.config/labwc/rc.xml` | `<touch deviceName="NAME" mapToOutput="CONN" mouseEmulation="yes"/>` |
| Live apply | `wlr-randr` | `wlr-randr --output CONN --transform ROT --scale S --mode RES` |
| Config reload | `kanshictl reload` | Falls back to `pkill -HUP kanshi` |

The app preserves existing kanshi profiles for other connectors when writing — it only modifies the output line for the connector being configured.

### Wayfire

| Config | File | Format |
|--------|------|--------|
| Display settings | `~/.config/wayfire.ini` | `[output:CONN]` section with `mode`, `transform`, `scale` keys |
| Touch mapping | `~/.config/wayfire.ini` | `[input-device:NAME]` section with `output` key |
| Live apply | `wlr-randr` | Same as labwc |

### Auto-detection

The app detects the running compositor by checking for `labwc` or `wayfire` processes via `pgrep`. The Wayland display socket is found by checking `$WAYLAND_DISPLAY` or scanning `/run/user/$UID/`.

## Device discovery

### Display outputs

Enumerated from `/sys/class/drm/card*-*`:
- Connection status from `status` file
- Resolution from `modes` file (first listed = preferred)
- Physical size from `width`/`height` files
- Connector type classified from name prefix (HDMI, DSI, DP, etc.)

Enriched with `wlr-randr` for current mode, transform, scale, and full mode list.

### Touch devices

Primary source: `sudo libinput list-devices` — provides device name, kernel path, and current calibration.

Enriched with `udevadm info` for USB VID:PID, bus type, and stable `/dev/input/by-id/` symlink.

Trackpads are filtered out by checking for `pointer+touch` capability combination or name keywords.

## Conflict detection

The app scans for files that were commonly created during manual calibration attempts and could interfere with the udev-rule approach:

| Path | Risk | Reason |
|------|------|--------|
| `/etc/libinput/local-overrides.quirks` | High | Produces "Unknown match key" errors on Pi OS |
| `/usr/share/libinput/*.quirks` | High | May override udev calibration |
| `/etc/udev/hwdb.d/90-touchscreen-calibration.hwdb` | High | Ignored by some libinput builds |
| `/etc/X11/xorg.conf.d/*calibration*.conf` | High | X11-only, conflicts under Wayland |
| `disable_fw_kms_setup=1` in config.txt | Medium | May cause display detection issues |
| `calibration_matrix` in wayfire.ini | Medium | Conflicts with udev-based calibration |
| `hdmi_group`/`hdmi_mode` in config.txt | Low | May force resolution, usually harmless |
