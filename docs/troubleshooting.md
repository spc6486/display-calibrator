# Troubleshooting

## Display issues

### Screen goes blank after applying resolution/scale/rotation

The 15-second countdown dialog auto-reverts the change. If that fails (the dialog itself isn't visible):

1. Wait 15 seconds — the change should revert automatically
2. If it doesn't, reboot the Pi — kanshi will re-apply the previous saved config
3. If the saved config is also bad, boot from another PC:
   - Mount the SD card
   - Edit `~/.config/kanshi/config` on the SD card
   - Remove or fix the problematic `transform`, `scale`, or `mode` values

### Screen goes blank after saving borders

Borders take effect on reboot. If the screen is unusable after rebooting:

1. Connect via SSH: `ssh pi@your-pi-hostname`
2. Edit cmdline.txt: `sudo nano /boot/firmware/cmdline.txt`
3. Remove the `video=CONNECTOR:margin_...` section
4. Reboot: `sudo reboot`

Or boot from another PC and edit `/boot/firmware/cmdline.txt` on the SD card.

### Borders look wrong after changing resolution

Borders are in physical pixels. If you change from 2048×1536 to 1024×768, a 40px border now crops twice the proportional area. The app offers to rescale proportionally — accept the prompt. If you already rebooted with wrong borders, open Display Settings and adjust.

### Rotation doesn't apply

- Verify kanshi is running: `pgrep kanshi`
- If not running, start it: `kanshi &`
- Check kanshi config: `cat ~/.config/kanshi/config`
- Try applying manually: `wlr-randr --output DSI-1 --transform 180`

### Display borders don't appear in the fullscreen editor

The green bounding rectangle and red/blue bars show the difference between current (active) margins and your adjusted values. If margins haven't changed, there's nothing to show. Adjust a value using the spin buttons and the indicators will appear.

## Touch issues

### "Cannot read /dev/input/eventN"

Your user needs to be in the `input` group:

```bash
sudo usermod -aG input $USER
```

Log out and back in (or reboot) for the group change to take effect. Verify with:

```bash
groups | grep input
```

### Touch is inverted or mirrored

Set rotation correctly in **Display Settings** before calibrating. The calibration matrix includes axis inversion for 180° rotation. If you calibrate first and then change rotation, the calibration will be wrong — recalibrate.

### Touch pointer offset (pointer doesn't land where you touch)

Use **Touchscreen Settings → Fine-tune**:

| Symptom | Fix |
|---------|-----|
| Pointer lands right of finger | Decrease horizontal shift |
| Pointer lands left of finger | Increase horizontal shift |
| Pointer lands below finger | Decrease vertical shift |
| Pointer lands above finger | Increase vertical shift |
| Pointer drifts at screen edges | Increase scale % |
| Touch accurate in center, off at edges | Recalibrate with 4-point |

Each nudge is 5px (shift) or 0.5% (scale). Click **Apply** after each adjustment to test.

### Calibration has no effect

Check the udev rule was written:

```bash
cat /etc/udev/rules.d/99-touchscreen-calibration.rules
```

It should be a single line containing `LIBINPUT_CALIBRATION_MATRIX`. If it's missing or malformed, recalibrate.

Verify udev picked it up:

```bash
sudo udevadm control --reload
sudo udevadm trigger --subsystem-match=input
```

### Calibration shows identity matrix (1 0 0 0 1 0 0 0 1)

The raw touch coordinates matched the screen targets exactly — either the touch panel doesn't need calibration, or the capture didn't work. Try recalibrating, making sure you touch the center of each crosshair precisely.

## Application issues

### Tray icon doesn't appear

Check the AppIndicator package is installed:

```bash
dpkg -l | grep ayatanaappindicator
```

If missing:

```bash
sudo apt install gir1.2-ayatanaappindicator3-0.1
```

Run the app from a terminal to see error output:

```bash
WAYLAND_DISPLAY=wayland-0 display-calibrator 2>&1
```

### Check for Conflicts finds issues

The app detects files from previous manual calibration attempts. These can interfere:

| File | Safe to remove? | How |
|------|:-:|-----|
| `/etc/libinput/local-overrides.quirks` | Yes | Use the cleanup button in the conflicts dialog |
| `/usr/share/libinput/*.quirks` | Yes | Same |
| `/etc/udev/hwdb.d/90-touchscreen-calibration.hwdb` | Yes | Same |
| `/etc/X11/xorg.conf.d/*calibration*.conf` | Yes | Same |
| config.txt entries | Manual | Edit with `sudo nano /boot/firmware/config.txt` |

The conflicts dialog offers to remove files with automatic backups.

### Settings History shows many backups

The app keeps a maximum of 10 backups per config file and cleans up older ones automatically. If you see excessive backups, they may predate the cleanup feature — they'll be trimmed on the next config change.

## CLI diagnostics

For debugging, run the CLI status report:

```bash
display-calibrator --cli
```

This shows:

- Active compositor and Wayland display
- config.txt validation
- Connected display outputs with wlr-randr info
- Current margins
- Touch devices and calibration
- Kanshi/labwc/Wayfire config contents
- Conflict scan results

Include this output when reporting issues.

### Running on Wayfire instead of labwc

The app supports both compositors, but is primarily tested on labwc. If you're still using Wayfire:

- Display settings are saved to `~/.config/wayfire.ini` instead of kanshi
- Touch mappings use `[input-device:NAME]` sections in wayfire.ini
- Calibration (udev rules) works identically on both
- Pi OS deprecated Wayfire in October 2024 — consider switching to labwc via `sudo raspi-config` → Advanced Options → Wayland
