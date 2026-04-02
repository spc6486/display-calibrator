# Display Calibrator

A system tray application for Raspberry Pi that adjusts the visible display area and maps touchscreen input to match.

If you're building a project where a physical bezel, enclosure, or frame covers part of the screen, this tool crops the GUI output to fit the visible area and recalibrates touch input so it stays accurate within those borders. Useful for retro computing builds, custom kiosks, emulator setups, in-wall displays, or any Raspberry Pi project with a screen mounted in a housing that obscures the edges.

**The problem it solves:** the Pi's display output fills the entire panel, but if a bezel hides 40px on each side, you get clipped UI elements and misaligned touch input. This app sets kernel-level display margins and computes a touchscreen calibration matrix so everything lines up with what's actually visible.

## Features

- **Display borders** — crop the GUI output per-edge to match the visible area behind a bezel or frame
- **Touchscreen calibration** — 4-point calibration maps touch input to the visible area with pixel-level fine-tuning
- **Display settings** — resolution, scale, and rotation in one window
- **Fullscreen border editor** — visual overlay showing exactly where edges will be cropped
- **Safety revert** — 15-second countdown auto-reverts display changes if the screen goes blank
- **Conflict detection** — finds old manual config files that could interfere
- **Settings history** — timestamped backups with one-click restore
- **Multi-display and multi-touch** — configure each pairing independently
- **labwc and Wayfire** — works on both compositors (primarily tested on labwc; Pi OS deprecated Wayfire in favor of labwc since October 2024)

## Compatibility

Works on Raspberry Pi 5, 4, 3, and Zero 2 W running Pi OS Bookworm (or later) with Wayland. Requires Python 3.9+ and GTK 3 (installed automatically).

Not compatible with Raspberry Pi Pico (microcontroller — no Linux desktop).

A touchscreen is not required. The app works as a display-border-only tool for projects with keyboard/mouse input.

## Install

```bash
git clone https://github.com/spc6486/display-calibrator.git
cd display-calibrator
./install.sh
```

Or from a tarball:

```bash
tar xzf display-calibrator.tar.gz
cd display-calibrator
./install.sh
```

**Log out and back in** after installation for touch permissions to take effect.

The installer handles dependencies (GTK3, AppIndicator, libinput, wlr-randr, kanshi), permissions, system tray autostart, and desktop menu entry. See [install details](docs/install.md) for specifics.

## Quick start

1. Click the tray icon → **Display Settings**
2. Set resolution, scale, and rotation → **Apply**
3. Click **Adjust display borders** → adjust edges to match your bezel → **Save** (prompts for reboot)
4. Click the tray icon → **Touchscreen Settings**
5. Click **Calibrate touchscreen** → tap the four crosshair targets
6. Fine-tune pointer offset if needed → **Apply**

See the full [User Manual](docs/manual.md) for detailed walkthrough.

## CLI

```bash
display-calibrator              # Launch tray application
display-calibrator --cli        # Print status to terminal
display-calibrator --version    # Show version
```

## Uninstall

From the tray menu → **Uninstall**, or from the terminal:

```bash
sudo /opt/display-calibrator/install.sh --uninstall
```

When run from a terminal, the uninstaller offers to also remove touchscreen calibration and display border settings.

## Documentation

- [User Manual](docs/manual.md) — setup workflow, display settings, touchscreen calibration
- [Install Details](docs/install.md) — what the installer does, file locations, permissions
- [Technical Reference](docs/technical.md) — margins, calibration matrices, compositor integration
- [Troubleshooting](docs/troubleshooting.md) — common problems and recovery

## Contributing

Contributions are welcome. Please open an issue before submitting large changes.

### Version control

Releases follow semantic versioning (`MAJOR.MINOR.PATCH`). The version is set in `config.py`:

```python
VERSION = "2.0.0"
```

Update this value before tagging a release. Tags should match the version:

```bash
git tag -a v2.0.1 -m "Fix margin rescaling edge case"
git push origin v2.0.1
```

The `--version` CLI flag reads from `config.VERSION`.

## License

MIT — see [LICENSE](LICENSE).
