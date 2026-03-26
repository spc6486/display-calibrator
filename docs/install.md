# Install Details

## What the installer does

The `install.sh` script performs five steps:

### 1. Install packages

```
python3-gi  python3-gi-cairo  gir1.2-gtk-3.0
gir1.2-ayatanaappindicator3-0.1
libinput-tools  wlr-randr  kanshi
```

### 2. Set up permissions

- Adds your user to the `input` group (required to read touch device events)
- Creates `/etc/sudoers.d/display-calibrator` with passwordless sudo rules for:
  - Writing calibration rules and cmdline.txt
  - Backing up and removing config files
  - Reloading udev and rebooting

### 3. Install application files

Copies to `/opt/display-calibrator/`:

| File | Purpose |
|------|---------|
| `display-calibrator.py` | Main application |
| `config.py` | Config read/write for all compositors |
| `devices.py` | Display and touch device discovery |
| `matrix.py` | Calibration matrix computation |
| `touch_capture.py` | Raw touch event capture |
| `display-calibrator.svg` | Tray icon |
| `install.sh` | Installer (also used for uninstall) |

### 4. Create launcher and menu entry

- `/usr/local/bin/display-calibrator` — CLI/GUI launcher script
- `/usr/share/applications/display-calibrator.desktop` — desktop menu entry under Preferences
- Icon installed to `/usr/share/icons/hicolor/scalable/apps/`

### 5. Set up autostart

- `/etc/xdg/autostart/display-calibrator.desktop` — starts the tray app automatically on login (with a 3-second delay to wait for the panel)

## File locations

### Application

| Path | Contents |
|------|----------|
| `/opt/display-calibrator/` | Application files |
| `/usr/local/bin/display-calibrator` | Launcher script |
| `/usr/share/applications/display-calibrator.desktop` | Menu entry |
| `/etc/xdg/autostart/display-calibrator.desktop` | Autostart entry |
| `/etc/sudoers.d/display-calibrator` | Sudo rules |

### Configuration (written by the app)

| Path | Contents | Needs reboot |
|------|----------|:------------:|
| `/boot/firmware/cmdline.txt` (or `/boot/cmdline.txt`) | Display border margins | Yes |
| `/etc/udev/rules.d/99-touchscreen-calibration.rules` | Calibration matrix | No* |
| `~/.config/kanshi/config` | Display settings (labwc) | No |
| `~/.config/labwc/rc.xml` | Touch mapping (labwc) | No |
| `~/.config/wayfire.ini` | Display + touch settings (Wayfire) | No |

\* Calibration is applied live via `udevadm trigger` after writing.

### Backups

Every config change creates a timestamped backup alongside the original file:

```
/boot/firmware/cmdline.txt.bak.20241015143022
~/.config/kanshi/config.bak.20241015143022
```

A maximum of 10 backups per file are kept. Use **Settings History** from the tray menu to browse and restore.

## Uninstall

Three ways to uninstall:

**From the tray menu:** Click the tray icon → **Uninstall**. A dialog confirms removal and offers checkboxes to also remove touchscreen calibration and display border margins.

**From the application menu:** Right-click "Display Calibrator" in Preferences → **Uninstall Display Calibrator**.

**From the terminal:**

```bash
sudo /opt/display-calibrator/install.sh --uninstall
```

When run from a terminal, the uninstaller asks whether to also remove:

- **Touchscreen calibration** — the udev rule with your calibration matrix
- **Display border margins** — the `video=` parameter in cmdline.txt

All three methods offer the same cleanup options. If you decline, settings are preserved. Compositor configs (kanshi, rc.xml, wayfire.ini) are always preserved.

## Updating

```bash
pkill -f display-calibrator
cd ~/display-calibrator
git pull   # or extract new tarball
./install.sh
display-calibrator
```

The installer overwrites application files but preserves all user configuration.
