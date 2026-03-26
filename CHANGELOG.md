# Changelog

All notable changes to this project will be documented in this file.

## [2.0.0] — 2025-03-26

### Added
- Unified **Display Settings** window (resolution, scale, rotation, borders)
- Unified **Touchscreen Settings** window (mapping, calibration, fine-tune)
- Fullscreen border editor with visual red/blue/green indicators
- Rotation-aware margin mapping (kernel ↔ user-visible edges)
- Auto-rescale borders when resolution changes
- 15-second countdown auto-revert for resolution/scale/rotation changes
- kanshi multi-profile preservation (only modifies the target connector)
- kanshi reload after config write (`kanshictl reload` with fallback)
- Auto-detect `cmdline.txt` path (Bookworm `/boot/firmware/` vs legacy `/boot/`)
- Safe file reading via `_read_file()` wrapper throughout
- Version number (`config.VERSION`) and `--version` CLI flag
- Uninstall from tray menu, desktop right-click action, and terminal
- Uninstall cleanup prompts for calibration and border settings
- Post-install warning about manual config conflicts
- Settings History with one-click restore (max 10 backups per file)
- Conflict detection for libinput quirks, hwdb, xorg.conf, wayfire.ini
- Startup preflight scan for existing manual settings
- MIT license, README, full documentation suite

### Removed
- Quick calibrate (inaccurate — always use 4-point calibration)
- Separate margins, rotation, calibration, fine-tune, status windows (consolidated)
- `python3-evdev` dependency (touch capture uses raw `/dev/input`)
- `libdrm-tests` dependency (unused)

## [1.0.0] — 2024-10-26

Initial release with separate windows for each function.
