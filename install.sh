#!/usr/bin/env bash
# ────────────────────────────────────────────────────────────
# Display Calibrator — One-Click Installer
#
#   curl -sL <url>/install.sh | bash
#   -- or --
#   chmod +x install.sh && ./install.sh
#
# Installs to /opt/display-calibrator with:
#   • System tray application (GTK3 + AppIndicator)
#   • Panel autostart on login
#   • CLI access via 'display-calibrator'
#   • Desktop menu entry under Preferences
#
# Uninstall:  sudo /opt/display-calibrator/install.sh --uninstall
# ────────────────────────────────────────────────────────────
set -euo pipefail

INSTALL_DIR="/opt/display-calibrator"
LAUNCHER="/usr/local/bin/display-calibrator"
ICON_DIR="/usr/share/icons/hicolor/scalable/apps"
ICON_NAME="display-calibrator.svg"
DESKTOP_FILE="/usr/share/applications/display-calibrator.desktop"
AUTOSTART_SYS="/etc/xdg/autostart/display-calibrator.desktop"
SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
USER_REAL="${SUDO_USER:-$USER}"

# ── Uninstall ────────────────────────────────────────────

if [ "${1:-}" = "--uninstall" ] || [ "${1:-}" = "remove" ]; then
    echo "Removing Display Calibrator..."
    sudo rm -rf "$INSTALL_DIR"
    sudo rm -f  "$LAUNCHER"
    sudo rm -f  "$DESKTOP_FILE"
    sudo rm -f  "$AUTOSTART_SYS"
    sudo rm -f  "$ICON_DIR/$ICON_NAME"
    sudo rm -f  "/etc/sudoers.d/display-calibrator"
    echo "Application removed."
    echo

    # Ask about config cleanup if running interactively
    if [ -t 0 ]; then
        REMOVE_CAL="n"
        REMOVE_MARGINS="n"
        if [ -f /etc/udev/rules.d/99-touchscreen-calibration.rules ]; then
            printf "Remove touchscreen calibration? (y/N) "
            read -r REMOVE_CAL
        fi
        if grep -q "margin_" /boot/firmware/cmdline.txt 2>/dev/null || \
           grep -q "margin_" /boot/cmdline.txt 2>/dev/null; then
            printf "Remove display border margins from cmdline.txt? (y/N) "
            read -r REMOVE_MARGINS
        fi
        if [ "$REMOVE_CAL" = "y" ] || [ "$REMOVE_CAL" = "Y" ]; then
            sudo rm -f /etc/udev/rules.d/99-touchscreen-calibration.rules
            sudo rm -f /etc/udev/rules.d/99-touchscreen-calibration.ref
            echo "Calibration removed."
        fi
        if [ "$REMOVE_MARGINS" = "y" ] || [ "$REMOVE_MARGINS" = "Y" ]; then
            for f in /boot/firmware/cmdline.txt /boot/cmdline.txt; do
                if [ -f "$f" ]; then
                    sudo sed -i 's/ *video=[^ ]*//' "$f"
                    echo "Margins removed from $f."
                fi
            done
        fi
    fi

    if [ -f /etc/udev/rules.d/99-touchscreen-calibration.rules ]; then
        echo "Calibration rule preserved: /etc/udev/rules.d/99-touchscreen-calibration.rules"
    fi
    echo "Display border margins preserved in cmdline.txt (if any)."
    exit 0
fi

echo "╔══════════════════════════════════════════════╗"
echo "║  Display Calibrator — Installer      ║"
echo "╚══════════════════════════════════════════════╝"
echo

# ── 1. Dependencies ──────────────────────────────────────

echo "[1/5] Installing packages..."
sudo apt-get update -qq 2>/dev/null
sudo apt-get install -y -qq \
    python3-gi \
    python3-gi-cairo \
    gir1.2-gtk-3.0 \
    gir1.2-ayatanaappindicator3-0.1 \
    libinput-tools \
    wlr-randr \
    kanshi \
    2>/dev/null || true

# Verify critical deps
python3 -c "import gi; gi.require_version('Gtk','3.0'); from gi.repository import Gtk" 2>/dev/null || {
    echo "ERROR: python3-gi / GTK3 not available. Install manually:"
    echo "  sudo apt install python3-gi gir1.2-gtk-3.0"
    exit 1
}
echo "  Dependencies OK."

# ── 2. Permissions ───────────────────────────────────────

echo
echo "[2/5] Setting up permissions..."
if ! groups "$USER_REAL" 2>/dev/null | grep -q '\binput\b'; then
    sudo usermod -aG input "$USER_REAL"
    echo "  Added $USER_REAL to 'input' group."
    echo "  ⚠  Log out and back in for this to take effect."
else
    echo "  $USER_REAL already in 'input' group."
fi

# Allow the tray app to write udev rules and trigger without password
SUDOERS="/etc/sudoers.d/display-calibrator"
sudo tee "$SUDOERS" > /dev/null <<SUDOEOF
# Display Calibrator - passwordless operations
# Core: write calibration rule and reload udev
$USER_REAL ALL=(root) NOPASSWD: /usr/bin/tee /etc/udev/rules.d/99-touchscreen-calibration.rules
$USER_REAL ALL=(root) NOPASSWD: /usr/bin/tee /etc/udev/rules.d/99-touchscreen-calibration.ref
$USER_REAL ALL=(root) NOPASSWD: /usr/bin/udevadm control --reload
$USER_REAL ALL=(root) NOPASSWD: /usr/bin/udevadm trigger --subsystem-match=input *
# Core: write/backup kernel cmdline
$USER_REAL ALL=(root) NOPASSWD: /usr/bin/tee /boot/firmware/cmdline.txt
$USER_REAL ALL=(root) NOPASSWD: /usr/bin/tee /boot/cmdline.txt
$USER_REAL ALL=(root) NOPASSWD: /bin/cp -a /boot/firmware/cmdline.txt *
$USER_REAL ALL=(root) NOPASSWD: /bin/cp -a /boot/cmdline.txt *
# Core: backup/remove udev rule
$USER_REAL ALL=(root) NOPASSWD: /bin/cp -a /etc/udev/rules.d/99-touchscreen-calibration.rules *
$USER_REAL ALL=(root) NOPASSWD: /bin/rm -f /etc/udev/rules.d/99-touchscreen-calibration.rules
# Core: libinput device listing
$USER_REAL ALL=(root) NOPASSWD: /usr/bin/libinput list-devices
# Conflict cleanup: backup and remove known conflict files
$USER_REAL ALL=(root) NOPASSWD: /bin/cp -a /etc/libinput/local-overrides.quirks *
$USER_REAL ALL=(root) NOPASSWD: /bin/rm -f /etc/libinput/local-overrides.quirks
$USER_REAL ALL=(root) NOPASSWD: /bin/cp -a /usr/share/libinput/local-overrides.quirks *
$USER_REAL ALL=(root) NOPASSWD: /bin/rm -f /usr/share/libinput/local-overrides.quirks
$USER_REAL ALL=(root) NOPASSWD: /bin/cp -a /usr/share/libinput/50-touchscreen-overrides.quirks *
$USER_REAL ALL=(root) NOPASSWD: /bin/rm -f /usr/share/libinput/50-touchscreen-overrides.quirks
$USER_REAL ALL=(root) NOPASSWD: /bin/cp -a /etc/udev/hwdb.d/90-touchscreen-calibration.hwdb *
$USER_REAL ALL=(root) NOPASSWD: /bin/rm -f /etc/udev/hwdb.d/90-touchscreen-calibration.hwdb
$USER_REAL ALL=(root) NOPASSWD: /bin/cp -a /etc/X11/xorg.conf.d/40-touchscreen-calibration.conf *
$USER_REAL ALL=(root) NOPASSWD: /bin/rm -f /etc/X11/xorg.conf.d/40-touchscreen-calibration.conf
$USER_REAL ALL=(root) NOPASSWD: /bin/cp -a /etc/X11/xorg.conf.d/99-calibration.conf *
$USER_REAL ALL=(root) NOPASSWD: /bin/rm -f /etc/X11/xorg.conf.d/99-calibration.conf
$USER_REAL ALL=(root) NOPASSWD: /usr/bin/systemd-hwdb update
# Session restart for calibration apply
$USER_REAL ALL=(root) NOPASSWD: /usr/bin/systemctl restart lightdm
# Reboot for margin changes
$USER_REAL ALL=(root) NOPASSWD: /usr/sbin/reboot
# Tray icon toggle (rename autostart file)
$USER_REAL ALL=(root) NOPASSWD: /bin/mv /etc/xdg/autostart/display-calibrator.desktop /etc/xdg/autostart/display-calibrator.desktop.disabled
$USER_REAL ALL=(root) NOPASSWD: /bin/mv /etc/xdg/autostart/display-calibrator.desktop.disabled /etc/xdg/autostart/display-calibrator.desktop
SUDOEOF
sudo chmod 440 "$SUDOERS"
echo "  Sudoers rules installed for passwordless calibration."

# ── 3. Install files ────────────────────────────────────

echo
echo "[3/5] Installing to $INSTALL_DIR..."
sudo mkdir -p "$INSTALL_DIR"
for f in display-calibrator.py display-calibrator.svg \
         devices.py config.py matrix.py touch_capture.py install.sh; do
    if [ -f "$SRC/$f" ]; then
        sudo cp "$SRC/$f" "$INSTALL_DIR/"
    fi
done
sudo chmod 755 "$INSTALL_DIR/display-calibrator.py"
sudo chmod 755 "$INSTALL_DIR/install.sh"

# Install icon
sudo mkdir -p "$ICON_DIR"
sudo cp "$SRC/display-calibrator.svg" "$ICON_DIR/$ICON_NAME"

echo "  Files installed."

# ── 4. Launcher + desktop entries ────────────────────────

echo
echo "[4/5] Creating launcher and menu entry..."

# CLI/GUI launcher
sudo tee "$LAUNCHER" > /dev/null <<EOF
#!/bin/sh
# Wait for panel to be ready on autostart (no-op if already running)
if [ -z "\$DISPLAY" ] && [ -z "\$WAYLAND_DISPLAY" ]; then
    sleep 3
fi
cd "$INSTALL_DIR"
exec python3 display-calibrator.py "\$@"
EOF
sudo chmod 755 "$LAUNCHER"

# Update icon cache so wf-panel-pi / GTK can find the icon by name
if command -v gtk-update-icon-cache >/dev/null 2>&1; then
    sudo gtk-update-icon-cache -f /usr/share/icons/hicolor/ 2>/dev/null || true
fi

# Application menu entry (Settings category)
sudo tee "$DESKTOP_FILE" > /dev/null <<EOF
[Desktop Entry]
Type=Application
Name=Display Calibrator
Comment=Configure display settings and touchscreen calibration
Exec=$LAUNCHER
Icon=display-calibrator
Terminal=false
Categories=Settings;HardwareSettings;
Keywords=display;touchscreen;calibration;margins;rotation;
Actions=Uninstall;

[Desktop Action Uninstall]
Name=Uninstall Display Calibrator
Exec=sh -c 'pkill -f display-calibrator; sudo $INSTALL_DIR/install.sh --uninstall'
EOF

echo "  Launcher: $LAUNCHER"
echo "  Menu entry: $DESKTOP_FILE"

# ── 5. Autostart (system tray on login) ─────────────────

echo
echo "[5/5] Setting up panel autostart..."

sudo tee "$AUTOSTART_SYS" > /dev/null <<EOF
[Desktop Entry]
Type=Application
Name=Display Calibrator Tray
Comment=System tray for display and touchscreen calibration
Exec=sh -c 'sleep 3 && $LAUNCHER --tray'
Icon=display-calibrator
X-GNOME-Autostart-enabled=true
NoDisplay=true
EOF

echo "  Autostart: $AUTOSTART_SYS"

# ── Done ─────────────────────────────────────────────────

echo
echo "╔══════════════════════════════════════════════╗"
echo "║  Installation Complete!                      ║"
echo "╚══════════════════════════════════════════════╝"
echo
echo "  Launch now:     display-calibrator"
echo "  CLI status:     display-calibrator --cli"
echo "  Menu:           Preferences → Display Calibrator"
echo "  Tray:           Starts automatically on login"
echo
echo "  Uninstall:      sudo $INSTALL_DIR/install.sh --uninstall"
echo
if ! groups "$USER_REAL" 2>/dev/null | grep -q '\binput\b'; then
    echo "  ⚠  IMPORTANT: Log out and back in so the 'input'"
    echo "     group takes effect (needed for touch calibration)."
    echo
fi
echo "  ⚠  NOTE: Use this app for all display and touchscreen"
echo "     configuration. Manually editing libinput quirks,"
echo "     udev hwdb entries, or xorg.conf calibration files"
echo "     will conflict. Use 'Check for Conflicts' in the"
echo "     tray menu to detect and clean up such files."
echo
