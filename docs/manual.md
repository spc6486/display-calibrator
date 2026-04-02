# User Manual

## Overview

Display Calibrator opens as a single window with three tabs: **Display**, **Touchscreen**, and **Tools**. It can also run as a system tray icon for quick access.

### Launching

- **Application menu:** Preferences → Display Calibrator (opens the settings window)
- **System tray:** Click the tray icon → Display Calibrator (opens the same window)
- **Terminal:** `display-calibrator` (window) or `display-calibrator --tray` (tray icon only)

### Tabs

| Tab | Contents |
|-----|----------|
| **Display** | Resolution, scale, rotation, display borders |
| **Touchscreen** | Device mapping, calibration, fine-tuning |
| **Tools** | Conflicts, settings history, system status, tray toggle, uninstall |

## Recommended setup workflow

| Step | Action | Details |
|------|--------|---------|
| 1 | **Install** | Run `./install.sh`, log out and back in |
| 2 | **Check for Conflicts** | Clean up any old manual calibration files |
| 3 | **Display Settings** | Set resolution, scale, rotation, and borders |
| 4 | **Reboot** | Border changes require a reboot |
| 5 | **Calibrate** | Run 4-point calibration, then fine-tune |

Resolution, scale, and rotation changes apply instantly. Border changes and calibration take effect after reboot.

## Display tab

Open the Display Calibrator window and select the **Display** tab.

### Output selector

Switch between connected displays. Each output stores its own resolution, scale, rotation, and borders independently. The dropdown shows connector name and resolution (e.g. `DSI-1 — 2048×1536`).

### Info card

A live summary below the output selector showing current resolution, scale, rotation, borders, and which touch device is mapped. Updates as you change controls.

### Resolution

Dropdown populated from the display's supported modes (queried via `wlr-randr`). The display's preferred mode is marked.

### Scale

Sets the logical desktop size. At 2048×1536 with scale 2x, the desktop is 1024×768. Available options: 1x, 1.25x, 1.5x, 1.75x, 2x.

### Rotation

Radio buttons for 0°, 90°, 180°, 270°. Select the orientation that matches how your screen is physically mounted.

### Display borders

Click **Adjust display borders** to open the fullscreen border editor. Borders crop the physical panel edges to match the display bezel — the area hidden behind the frame isn't wasted as a black bar.

### Apply

Writes resolution, scale, and rotation config and applies live. A **15-second countdown** confirms the changes — if the screen goes blank (unsupported mode), settings auto-revert. Border changes are saved directly from the fullscreen border editor via its **Save** button.

## Fullscreen border editor

Opens from Display Settings → **Adjust display borders**. The entire screen becomes the editor.

### Visual indicators

| Color | Meaning |
|-------|---------|
| **Red bars** | Area that will be hidden (margin increasing) |
| **Blue lines** | Area that would expand beyond current view (margin decreasing) |
| **Green rectangle** | The total visible area after reboot |

### Controls

The center panel has spin buttons for each edge (Top, Bottom, Left, Right) with values in pixels. The controls match the Display Settings window style.

| Button | Action |
|--------|--------|
| **Reset** | Restore borders to the values when the editor opened |
| **Clear All** | Set all four borders to zero |
| **Cancel** | Discard changes and return to Display Settings |
| **Save** | Write borders to cmdline.txt and prompt to reboot |

Keyboard: **Escape** = cancel, **Enter** = save.

### Rotation awareness

The editor automatically maps the physical kernel margins to user-visible edges. With 180° rotation, adjusting "Left" in the UI adjusts the correct physical edge — you don't need to think about which direction is "really" left.

### Resolution changes

When you change resolution in Display Settings with non-zero borders, the app offers to rescale borders proportionally. For example, borders of L:40 at 2048×1536 become L:20 at 1024×768.

## Touchscreen tab

Select the **Touchscreen** tab.

### Device selectors

**Touch device** — dropdown listing all detected touch input devices.

**Map to display** — dropdown listing all connected display outputs with their resolution, scale, and rotation shown alongside the name.

Each device-to-output pairing is configured independently.

### Status line

Shows the current mapping and calibration status at a glance. Green = calibrated (udev rule active). Orange = not calibrated.

### Calibrate touchscreen

Launches the 4-point calibration overlay:

1. The screen goes fullscreen black with a crosshair target at the top-left corner
2. Touch the center of each crosshair target — four points total (TL, TR, BR, BL)
3. After all four points, the app computes the calibration matrix
4. A confirmation dialog shows the computed matrix and asks to apply
5. If confirmed, the udev rule is written and applied immediately

Press **Escape** at any time to cancel.

### Fine-tune

After calibration, use the fine-tune controls to make small adjustments:

| Control | What it does | Nudge buttons |
|---------|-------------|---------------|
| **H shift** | Move pointer left/right (pixels) | ±5 px |
| **V shift** | Move pointer up/down (pixels) | ±5 px |
| **H scale** | Stretch/compress touch area horizontally (%) | ±0.5% |
| **V scale** | Stretch/compress touch area vertically (%) | ±0.5% |

**Adjustment guide:**

- Pointer lands **right** of your finger → decrease horizontal shift
- Pointer lands **below** your finger → decrease vertical shift
- Touch drifts at screen edges → increase scale %

### Apply

Writes the udev calibration rule, touch-to-output mapping, and applies live. Changes take effect immediately — no reboot needed.

### Clear Calibration

Removes the udev rule entirely, reverting to raw uncalibrated touch input. Located at the bottom-left of the window.

## Tools tab

### Check for Conflicts

Scans the system for manually created configuration files that could interfere with the app's udev-based calibration approach.

### Severity levels

| Level | Meaning | Examples |
|-------|---------|----------|
| **CONFLICT** | Will actively interfere — should be removed | libinput quirks overrides, hwdb calibration entries, Xorg calibration configs |
| **WARNING** | May cause confusion | `disable_fw_kms_setup=1`, calibration_matrix in wayfire.ini |
| **INFO** | Present but usually harmless | hdmi_group/hdmi_mode in config.txt |

The scan runs automatically on first launch and before every save operation. Detected files can be removed with backups created automatically.

### Settings History

Browse all timestamped backups created by the app. Each config change creates a backup before writing.

Backups are labeled by type:

| Label | What was backed up |
|-------|-------------------|
| Display borders | `/boot/firmware/cmdline.txt` |
| Touch calibration | udev calibration rule |
| Display settings (labwc) | kanshi config |
| Touch mapping (labwc) | labwc rc.xml |
| Display settings (wayfire) | wayfire.ini |

Select a backup and click **Restore** to revert to that snapshot. A maximum of 10 backups per file are kept — older ones are automatically cleaned up.

### Show System Status

Opens a window displaying detected hardware, current settings, and diagnostic information. Equivalent to `display-calibrator --cli` but in a GUI window with a Refresh button.

### Show system tray icon

Checkbox to enable or disable the system tray icon on login. When unchecked, the autostart entry is disabled and the tray icon won't appear after the next login. The app remains available from Preferences → Display Calibrator in the application menu.

### Uninstall

Removes the application with optional cleanup of touchscreen calibration and display border settings. See [install details](install.md) for what gets removed.
