"""Read/write system config: cmdline.txt margins, udev rules, wayfire.ini."""

import os, re, shutil, subprocess
from datetime import datetime

VERSION = "2.0.0"

# Auto-detect cmdline.txt path (Bookworm vs older Pi OS)
_CMDLINE_CANDIDATES = ["/boot/firmware/cmdline.txt", "/boot/cmdline.txt"]
CMDLINE = next((p for p in _CMDLINE_CANDIDATES if os.path.isfile(p)),
               _CMDLINE_CANDIDATES[0])

UDEV_RULE = "/etc/udev/rules.d/99-touchscreen-calibration.rules"
WAYFIRE   = os.path.expanduser("~/.config/wayfire.ini")

_CONFIG_CANDIDATES = ["/boot/firmware/config.txt", "/boot/config.txt"]
CONFIG_TXT = next((p for p in _CONFIG_CANDIDATES if os.path.isfile(p)),
                  _CONFIG_CANDIDATES[0])

KANSHI    = os.path.expanduser("~/.config/kanshi/config")
LABWC_RC  = os.path.expanduser("~/.config/labwc/rc.xml")


def _read_file(path):
    """Read a file safely. Returns content string or empty string on error."""
    try:
        with open(path) as f:
            return f.read()
    except OSError:
        return ""


# ── Compositor detection ─────────────────────────────────────────────

def detect_compositor():
    """Detect which Wayland compositor is running. Returns 'labwc', 'wayfire', or 'unknown'."""
    for name in ("labwc", "wayfire"):
        try:
            r = subprocess.run(["pgrep", "-x", name], capture_output=True)
            if r.returncode == 0:
                return name
        except OSError:
            pass
    return "unknown"


def detect_wayland_display():
    """Find the active WAYLAND_DISPLAY socket. labwc uses wayland-0, Wayfire uses wayland-1."""
    # Check environment first
    env_val = os.environ.get("WAYLAND_DISPLAY")
    if env_val:
        return env_val
    # Probe sockets
    uid = os.getuid()
    runtime = f"/run/user/{uid}"
    for candidate in ("wayland-0", "wayland-1", "wayland-2"):
        if os.path.exists(os.path.join(runtime, candidate)):
            return candidate
    return None


def get_wlr_randr_output(connector=None):
    """
    Query wlr-randr for output info. Returns dict with:
      mode, transform, scale, preferred_mode, all_modes
    Returns None on failure.
    """
    wl = detect_wayland_display()
    if not wl:
        return None
    env = dict(os.environ, WAYLAND_DISPLAY=wl)
    try:
        r = subprocess.run(["wlr-randr"], capture_output=True, text=True, timeout=5, env=env)
        if r.returncode != 0:
            return None
    except (OSError, subprocess.TimeoutExpired):
        return None

    result = {}
    current_output = None
    for line in r.stdout.splitlines():
        line_s = line.strip()
        # Output header: "HDMI-A-1 ..."
        if not line.startswith(" ") and not line.startswith("\t"):
            parts = line_s.split()
            if parts:
                current_output = parts[0]
                if connector and current_output != connector:
                    current_output = None
                if current_output:
                    result = {"name": current_output, "modes": []}
        elif current_output:
            if line_s.startswith("Transform:"):
                result["transform"] = line_s.split(":", 1)[1].strip()
            elif line_s.startswith("Scale:"):
                try:
                    result["scale"] = float(line_s.split(":", 1)[1].strip())
                except ValueError:
                    pass
            elif "px," in line_s and "Hz" in line_s:
                # Mode line: "2048x1536 px, 59.994999 Hz (preferred, current)"
                mode_str = line_s
                is_preferred = "preferred" in mode_str
                is_current = "current" in mode_str
                m = re.match(r"(\d+x\d+)\s+px,\s+([\d.]+)\s+Hz", mode_str)
                if m:
                    mode_info = {"resolution": m.group(1), "refresh": m.group(2),
                                 "preferred": is_preferred, "current": is_current}
                    result.setdefault("modes", []).append(mode_info)
                    if is_preferred:
                        result["preferred_mode"] = m.group(1)
                    if is_current:
                        result["current_mode"] = m.group(1)

    return result if result else None


def _stamp():
    return datetime.now().strftime("%Y%m%d%H%M%S")


def _sudo_write(path, content):
    return subprocess.run(
        ["sudo", "tee", path], input=content,
        capture_output=True, text=True
    ).returncode == 0


def _sudo_backup(path):
    if not os.path.isfile(path):
        return None
    bak = f"{path}.bak.{_stamp()}"
    subprocess.run(["sudo", "cp", "-a", path, bak], check=False)
    # Trim old backups
    cleanup_old_backups()
    return bak


def restore_backup(backup_path, original_path):
    """Restore a file from its backup. Returns True on success."""
    if not backup_path or not os.path.isfile(backup_path):
        return False
    # Check if we own the file (wayfire.ini is user-owned, no sudo)
    if os.path.isfile(original_path) and os.access(original_path, os.W_OK):
        shutil.copy2(backup_path, original_path)
        return True
    # Root-owned files: use sudo tee (covered by sudoers rules)
    try:
        content = _read_file(backup_path)
        return _sudo_write(original_path, content)
    except OSError:
        return False


# ── Margins ──────────────────────────────────────────────────────────

def read_margins(connector):
    """Read margins from cmdline.txt. Returns dict or None."""
    if not os.path.isfile(CMDLINE):
        return None
    cmdline = _read_file(CMDLINE).strip()
    m = re.search(rf"video={re.escape(connector)}:([^\s]+)", cmdline)
    if not m:
        return None
    opts = m.group(1)
    margins = {}
    for edge in ("left", "right", "top", "bottom"):
        em = re.search(rf"margin_{edge}=(\d+)", opts)
        margins[edge] = int(em.group(1)) if em else 0
    return margins


def read_active_margins(connector):
    """Read margins from /proc/cmdline (what kernel is actually using now).
    Returns dict or None."""
    try:
        cmdline = _read_file("/proc/cmdline").strip()
    except OSError:
        return None
    m = re.search(rf"video={re.escape(connector)}:([^\s]+)", cmdline)
    if not m:
        return None
    opts = m.group(1)
    margins = {}
    for edge in ("left", "right", "top", "bottom"):
        em = re.search(rf"margin_{edge}=(\d+)", opts)
        margins[edge] = int(em.group(1)) if em else 0
    return margins


# Store reference margins alongside calibration so quick-calibrate can
# adjust the matrix when margins change.
CAL_REF = "/etc/udev/rules.d/99-touchscreen-calibration.ref"


def save_calibration_reference(connector, margins):
    """Save the margins that were active when calibration was performed."""
    content = f"connector={connector}\n"
    for edge in ("left", "right", "top", "bottom"):
        content += f"margin_{edge}={margins.get(edge, 0)}\n"
    _sudo_write(CAL_REF, content)


def read_calibration_reference():
    """Read the reference margins saved with calibration.
    Returns (connector, margins_dict) or (None, None)."""
    if not os.path.isfile(CAL_REF):
        return None, None
    content = _read_file(CAL_REF)
    cm = re.search(r"connector=(\S+)", content)
    if not cm:
        return None, None
    margins = {}
    for edge in ("left", "right", "top", "bottom"):
        em = re.search(rf"margin_{edge}=(\d+)", content)
        margins[edge] = int(em.group(1)) if em else 0
    return cm.group(1), margins


def write_margins(connector, left, right, top, bottom):
    """Write margins to cmdline.txt. Returns (ok, backup, msg)."""
    bak = _sudo_backup(CMDLINE)
    cmdline = _read_file(CMDLINE).strip()
    cmdline = re.sub(rf"\s*video={re.escape(connector)}:[^\s]*", "", cmdline)

    parts = []
    for edge, val in [("left", left), ("right", right), ("top", top), ("bottom", bottom)]:
        if val > 0:
            parts.append(f"margin_{edge}={val}")
    if parts:
        cmdline += f" video={connector}:{','.join(parts)}"

    ok = _sudo_write(CMDLINE, cmdline.strip() + "\n")
    return ok, bak, "Margins saved. Reboot required." if ok else "Write failed."


def remove_margins(connector):
    bak = _sudo_backup(CMDLINE)
    cmdline = _read_file(CMDLINE).strip()
    cmdline = re.sub(rf"\s*video={re.escape(connector)}:[^\s]*", "", cmdline)
    return _sudo_write(CMDLINE, cmdline.strip() + "\n"), bak


# ── Calibration udev rule ────────────────────────────────────────────

def read_calibration():
    """Read calibration matrix from udev rule. Returns (device, [9 floats]) or None."""
    if not os.path.isfile(UDEV_RULE):
        return None
    content = _read_file(UDEV_RULE)
    nm = re.search(r'ATTRS\{name\}=="([^"]+)"', content)
    mm = re.search(r'LIBINPUT_CALIBRATION_MATRIX\}?="([^"]+)"', content)
    if not nm or not mm:
        return None
    try:
        vals = [float(x) for x in mm.group(1).split()]
        if len(vals) != 9:
            return None
    except ValueError:
        return None
    return nm.group(1), vals


def write_calibration(device_name, matrix_9):
    """Write udev calibration rule. Returns (ok, backup, msg)."""
    bak = _sudo_backup(UDEV_RULE) if os.path.isfile(UDEV_RULE) else None
    mstr = " ".join(f"{v:.6f}" for v in matrix_9)
    line = (
        f'SUBSYSTEM=="input", KERNEL=="event*", '
        f'ATTRS{{name}}=="{device_name}", '
        f'ENV{{LIBINPUT_CALIBRATION_MATRIX}}="{mstr}"'
    )
    ok = _sudo_write(UDEV_RULE, line + "\n")
    return ok, bak, "Calibration rule written." if ok else "Write failed."


def remove_calibration():
    bak = _sudo_backup(UDEV_RULE) if os.path.isfile(UDEV_RULE) else None
    subprocess.run(["sudo", "rm", "-f", UDEV_RULE], check=False)
    return True, bak


def apply_calibration_live(event_name):
    """Reload udev and trigger device."""
    for cmd in [
        ["sudo", "udevadm", "control", "--reload"],
        ["sudo", "udevadm", "trigger", "--subsystem-match=input",
         f"--sysname-match={event_name}"],
    ]:
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0:
            return False, f"Failed: {' '.join(cmd)}"
    return True, "Applied live."


# ── Wayfire ──────────────────────────────────────────────────────────

def read_wayfire_section(section):
    """Read a section from wayfire.ini. Returns dict of key=value."""
    if not os.path.isfile(WAYFIRE):
        return {}
    result = {}
    in_section = False
    for line in _read_file(WAYFIRE).splitlines():
        s = line.strip()
        if s == f"[{section}]":
            in_section = True
        elif s.startswith("["):
            if in_section:
                break
            in_section = False
        elif in_section and "=" in s and not s.startswith("#"):
            k, _, v = s.partition("=")
            result[k.strip()] = v.strip()
    return result


def write_wayfire_section(section, kv):
    """Add or update a section in wayfire.ini. Returns (ok, backup_path)."""
    os.makedirs(os.path.dirname(WAYFIRE), exist_ok=True)

    if not os.path.isfile(WAYFIRE):
        with open(WAYFIRE, "w") as f:
            f.write(f"[{section}]\n")
            for k, v in kv.items():
                f.write(f"{k}={v}\n")
            f.write("\n")
        return True, None

    bak = f"{WAYFIRE}.bak.{_stamp()}"
    shutil.copy2(WAYFIRE, bak)
    lines = _read_file(WAYFIRE).splitlines(True)

    header = f"[{section}]"
    start = end = None
    for i, line in enumerate(lines):
        if line.strip() == header:
            start = i
        elif start is not None and line.strip().startswith("["):
            end = i
            break
    if start is not None and end is None:
        end = len(lines)

    if start is not None:
        existing = {}
        for line in lines[start + 1:end]:
            if "=" in line and not line.strip().startswith("#"):
                k, _, v = line.strip().partition("=")
                existing[k.strip()] = v.strip()
        existing.update(kv)
        block = [f"{header}\n"] + [f"{k}={v}\n" for k, v in existing.items()] + ["\n"]
        lines[start:end] = block
    else:
        lines.append(f"\n{header}\n")
        for k, v in kv.items():
            lines.append(f"{k}={v}\n")
        lines.append("\n")

    with open(WAYFIRE, "w") as f:
        f.writelines(lines)
    return True, bak


# ── Validation ───────────────────────────────────────────────────────

# ── Kanshi (labwc rotation persistence) ──────────────────────────────

def read_kanshi_profile(connector):
    """Read kanshi config for a given output. Returns dict with transform, mode, scale, position."""
    if not os.path.isfile(KANSHI):
        return {}
    content = _read_file(KANSHI)
    # Find profile block containing this connector
    # Format: profile { output HDMI-A-1 enable mode ... transform 180 scale 2 }
    result = {}
    for m in re.finditer(r"output\s+" + re.escape(connector) + r"\s+([^}]+)", content):
        opts = m.group(1).strip()
        tm = re.search(r"transform\s+(\S+)", opts)
        if tm:
            result["transform"] = tm.group(1)
        sm = re.search(r"scale\s+([\d.]+)", opts)
        if sm:
            result["scale"] = float(sm.group(1))
        mm = re.search(r"mode\s+([\dx@.]+Hz)", opts)
        if mm:
            result["mode"] = mm.group(1)
        pm = re.search(r"position\s+([\d,]+)", opts)
        if pm:
            result["position"] = pm.group(1)
    return result


def write_kanshi_profile(connector, mode=None, transform=None, scale=None, position="0,0"):
    """Write/update kanshi config for one output, preserving other profiles."""
    os.makedirs(os.path.dirname(KANSHI), exist_ok=True)

    bak = None
    if os.path.isfile(KANSHI):
        bak = f"{KANSHI}.bak.{_stamp()}"
        shutil.copy2(KANSHI, bak)
        cleanup_old_backups()

    # Build the new output line
    parts = [f"output {connector} enable"]
    if mode:
        parts.append(f"mode {mode}")
    if position:
        parts.append(f"position {position}")
    if transform and transform != "normal":
        parts.append(f"transform {transform}")
    if scale and scale != 1.0:
        parts.append(f"scale {scale}")
    new_line = " ".join(parts)

    # Read existing config and update/add this connector's profile
    if os.path.isfile(KANSHI):
        with open(KANSHI) as f:
            existing = f.read()
    else:
        existing = ""

    # Replace existing output line for this connector within any profile block
    pattern = r"(output\s+" + re.escape(connector) + r"\s+)[^\n}]+"
    if re.search(pattern, existing):
        updated = re.sub(pattern, new_line, existing)
    else:
        # No existing profile for this connector — add a new profile block
        updated = existing.rstrip() + f"\n\nprofile {{\n\t{new_line}\n}}\n"

    with open(KANSHI, "w") as f:
        f.write(updated)
    _reload_kanshi()
    return True, bak


def _reload_kanshi():
    """Signal kanshi to reload its config, or restart it."""
    try:
        # kanshictl reload is the preferred method
        subprocess.run(["kanshictl", "reload"],
                       capture_output=True, timeout=3)
    except (OSError, subprocess.TimeoutExpired):
        # Fallback: send SIGHUP
        subprocess.run(["pkill", "-HUP", "kanshi"],
                       capture_output=True, timeout=3)


# ── labwc rc.xml touch mapping ───────────────────────────────────────

def read_labwc_touch_mapping(device_name):
    """Read touch mapToOutput from labwc rc.xml. Returns connector name or None."""
    for rcpath in (LABWC_RC, "/etc/xdg/labwc/rc.xml"):
        if not os.path.isfile(rcpath):
            continue
        content = _read_file(rcpath)
        # Look for: <touch deviceName="..." mapToOutput="HDMI-A-1" .../>
        pattern = r'<touch\s+[^>]*deviceName="' + re.escape(device_name) + r'"[^>]*mapToOutput="([^"]+)"'
        m = re.search(pattern, content)
        if m:
            return m.group(1)
    return None


def write_labwc_touch_mapping(device_name, connector):
    """Add or update touch mapping in labwc user rc.xml. Returns (ok, backup_path)."""
    os.makedirs(os.path.dirname(LABWC_RC), exist_ok=True)

    bak = None
    if os.path.isfile(LABWC_RC):
        bak = f"{LABWC_RC}.bak.{_stamp()}"
        shutil.copy2(LABWC_RC, bak)
        content = _read_file(LABWC_RC)
    else:
        content = '<?xml version="1.0"?>\n<openbox_config xmlns="http://openbox.org/3.4/rc">\n</openbox_config>\n'

    new_entry = f'<touch deviceName="{device_name}" mapToOutput="{connector}" mouseEmulation="yes"/>'

    # Check if entry already exists for this device
    pattern = r'<touch\s+[^>]*deviceName="' + re.escape(device_name) + r'"[^>]*/>'
    if re.search(pattern, content):
        # Replace existing entry
        content = re.sub(pattern, new_entry, content)
    else:
        # Insert before closing </openbox_config> tag
        content = content.replace("</openbox_config>", new_entry + "\n</openbox_config>")

    with open(LABWC_RC, "w") as f:
        f.write(content)
    return True, bak


# ── Universal rotation and touch mapping ─────────────────────────────

def read_rotation(connector):
    """Read current rotation from the active compositor's config. Returns transform string."""
    comp = detect_compositor()
    if comp == "labwc":
        profile = read_kanshi_profile(connector)
        return profile.get("transform", "normal")
    elif comp == "wayfire":
        section = read_wayfire_section(f"output:{connector}")
        return section.get("transform", "normal")
    # Fallback: try wlr-randr
    info = get_wlr_randr_output(connector)
    if info:
        return info.get("transform", "normal")
    return "normal"


def read_scale(connector):
    """Read current scale factor. Returns float."""
    comp = detect_compositor()
    if comp == "labwc":
        profile = read_kanshi_profile(connector)
        return profile.get("scale", 1.0)
    elif comp == "wayfire":
        section = read_wayfire_section(f"output:{connector}")
        try:
            return float(section.get("scale", "1"))
        except ValueError:
            return 1.0
    info = get_wlr_randr_output(connector)
    if info:
        return info.get("scale", 1.0)
    return 1.0


def write_rotation(connector, transform, mode=None, scale=None):
    """
    Write rotation to the correct compositor config.
    Returns (ok, backup_path, msg).
    """
    comp = detect_compositor()

    if comp == "labwc":
        # Read existing kanshi values to preserve them
        existing = read_kanshi_profile(connector)
        if mode is None:
            mode = existing.get("mode")
        if scale is None:
            scale = existing.get("scale")
        ok, bak = write_kanshi_profile(connector, mode=mode, transform=transform,
                                        scale=scale)
        msg = "Rotation saved to kanshi config." if ok else "Failed to write kanshi config."
        return ok, bak, msg

    elif comp == "wayfire":
        kv = {"transform": transform}
        if mode:
            kv["mode"] = mode
        if scale is not None:
            kv["scale"] = str(int(scale) if scale == int(scale) else scale)
        ok, bak = write_wayfire_section(f"output:{connector}", kv)
        msg = "Rotation saved to wayfire.ini." if ok else "Failed to write wayfire.ini."
        return ok, bak, msg

    return False, None, f"Unknown compositor: {comp}"


def read_touch_mapping(device_name):
    """Read touch-to-output mapping from the active compositor's config.
    Returns connector name or None."""
    comp = detect_compositor()
    if comp == "labwc":
        return read_labwc_touch_mapping(device_name)
    elif comp == "wayfire":
        section = read_wayfire_section(f"input-device:{device_name}")
        return section.get("output") if section else None
    return None


def write_touch_mapping(device_name, connector):
    """
    Write touch-to-output mapping for the active compositor.
    Returns (ok, backup_path).
    """
    comp = detect_compositor()
    if comp == "labwc":
        return write_labwc_touch_mapping(device_name, connector)
    elif comp == "wayfire":
        return write_wayfire_section(
            f"input-device:{device_name}", {"output": connector}
        )
    return False, None


def apply_rotation_live(connector, transform):
    """Apply rotation immediately via wlr-randr (works on both compositors)."""
    wl = detect_wayland_display()
    if not wl:
        return False, "No WAYLAND_DISPLAY found"
    env = dict(os.environ, WAYLAND_DISPLAY=wl)
    cmd = ["wlr-randr", "--output", connector, "--transform", transform]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=5, env=env)
        if r.returncode == 0:
            return True, "Rotation applied live."
        return False, f"wlr-randr failed: {r.stderr.strip()}"
    except (OSError, subprocess.TimeoutExpired) as e:
        return False, str(e)


def apply_display_live(connector, transform=None, scale=None, mode=None):
    """Apply display settings immediately via wlr-randr. Returns (ok, msg)."""
    wl = detect_wayland_display()
    if not wl:
        return False, "No WAYLAND_DISPLAY found"
    env = dict(os.environ, WAYLAND_DISPLAY=wl)
    cmd = ["wlr-randr", "--output", connector]
    if transform:
        cmd += ["--transform", transform]
    if scale:
        cmd += ["--scale", str(scale)]
    if mode:
        cmd += ["--mode", mode]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=5, env=env)
        if r.returncode == 0:
            return True, "Display settings applied."
        return False, f"wlr-randr failed: {r.stderr.strip()}"
    except (OSError, subprocess.TimeoutExpired) as e:
        return False, str(e)

def validate_config_txt():
    """Check config.txt for common Pi 5 issues. Returns [(severity, msg)]."""
    issues = []
    if not os.path.isfile(CONFIG_TXT):
        return [("error", "config.txt not found")]
    content = _read_file(CONFIG_TXT)
    if re.search(r"^dtoverlay=vc4-kms-v3d\s*$", content, re.M):
        issues.append(("ok", "vc4-kms-v3d enabled"))
    else:
        issues.append(("error", "vc4-kms-v3d not active"))
    if re.search(r"^dtoverlay=vc4-fkms-v3d\s*$", content, re.M):
        issues.append(("error", "vc4-fkms-v3d NOT supported on Pi 5"))
    if re.search(r"^disable_fw_kms_setup=1\s*$", content, re.M):
        issues.append(("warning", "disable_fw_kms_setup=1 may cause issues"))
    return issues


# ── Preflight: detect conflicting manual configurations ──────────────

# Every file path that was tried during manual troubleshooting and
# could conflict with the udev-rule approach this app uses.
CONFLICTING_PATHS = [
    # libinput quirks files (these were tried and don't work on RPi OS Bookworm,
    # but if present they produce "Unknown match key" errors in libinput)
    ("/etc/libinput/local-overrides.quirks",
     "libinput quirks override (causes 'Unknown match key' warnings)"),
    ("/usr/share/libinput/local-overrides.quirks",
     "libinput quirks override (may conflict with udev rule)"),
    ("/usr/share/libinput/50-touchscreen-overrides.quirks",
     "libinput quirks override (may conflict with udev rule)"),

    # hwdb calibration entries (loaded by udev but ignored by some libinput builds)
    ("/etc/udev/hwdb.d/90-touchscreen-calibration.hwdb",
     "hwdb touch calibration (may conflict with udev rule)"),

    # Xorg calibration config (only applies in X11 sessions, conflicts with
    # the udev-based approach under Wayland)
    ("/etc/X11/xorg.conf.d/40-touchscreen-calibration.conf",
     "Xorg touch calibration (conflicts under Wayland)"),
    ("/etc/X11/xorg.conf.d/99-calibration.conf",
     "Xorg calibration config (conflicts under Wayland)"),
]


def preflight_scan():
    """
    Scan the system for pre-existing manual configurations that could
    conflict with this app's approach.

    Returns list of dicts:
        {path, description, severity, content_preview}

    severity: "conflict" = will actively interfere
              "warning"  = may cause confusion
              "info"     = exists but probably harmless
    """
    found = []

    # Check for conflicting files
    for path, desc in CONFLICTING_PATHS:
        if os.path.isfile(path):
            try:
                preview = _read_file(path)[:200].strip()
            except OSError:
                preview = "(unreadable)"
            found.append({
                "path": path,
                "description": desc,
                "severity": "conflict",
                "content_preview": preview,
            })

    # Check for an existing udev rule NOT written by this app
    if os.path.isfile(UDEV_RULE):
        content = _read_file(UDEV_RULE).strip()
        # Our app writes exactly one line starting with SUBSYSTEM==
        if content and not content.startswith('SUBSYSTEM=="input"'):
            found.append({
                "path": UDEV_RULE,
                "description": "Existing udev calibration rule (non-standard format)",
                "severity": "warning",
                "content_preview": content[:200],
            })

    # Check for video= with resolution forcing (not just margins)
    if os.path.isfile(CMDLINE):
        cmdline = _read_file(CMDLINE).strip()
        video_match = re.search(r"video=(\S+)", cmdline)
        if video_match:
            video_str = video_match.group(1)
            # Check for resolution/mode flags that aren't just margins
            has_resolution = bool(re.search(r"\d+x\d+[MDe@]", video_str))
            has_margins = "margin_" in video_str
            if has_resolution and has_margins:
                found.append({
                    "path": CMDLINE,
                    "description": "video= has both resolution forcing and margins "
                                   "(resolution may override EDID preferred mode)",
                    "severity": "warning",
                    "content_preview": f"video={video_str}",
                })
            elif has_resolution and not has_margins:
                found.append({
                    "path": CMDLINE,
                    "description": "video= forces a resolution without margins",
                    "severity": "info",
                    "content_preview": f"video={video_str}",
                })

    # Check config.txt for problematic entries
    if os.path.isfile(CONFIG_TXT):
        content = _read_file(CONFIG_TXT)
        if re.search(r"^dtoverlay=vc4-fkms-v3d\s*$", content, re.M):
            found.append({
                "path": CONFIG_TXT,
                "description": "vc4-fkms-v3d enabled (NOT supported on Pi 5, will break display)",
                "severity": "conflict",
                "content_preview": "dtoverlay=vc4-fkms-v3d",
            })
        if re.search(r"^disable_fw_kms_setup=1\s*$", content, re.M):
            found.append({
                "path": CONFIG_TXT,
                "description": "disable_fw_kms_setup=1 (can cause bad video modes on boot)",
                "severity": "warning",
                "content_preview": "disable_fw_kms_setup=1",
            })
        # Check for hdmi_group/hdmi_mode which force resolution and can
        # conflict with EDID-preferred mode + margins
        if re.search(r"^hdmi_group=\d", content, re.M) and re.search(r"^hdmi_mode=\d", content, re.M):
            found.append({
                "path": CONFIG_TXT,
                "description": "hdmi_group/hdmi_mode set (forces resolution, may conflict with margins)",
                "severity": "info",
                "content_preview": "hdmi_group + hdmi_mode present",
            })

    # Check wayfire.ini for calibration_matrix or touch-transform keys
    # (these were tried during troubleshooting but don't work on RPi Wayfire)
    if os.path.isfile(WAYFIRE):
        wf_content = _read_file(WAYFIRE)
        if "calibration_matrix" in wf_content:
            found.append({
                "path": WAYFIRE,
                "description": "wayfire.ini has calibration_matrix (not honored by RPi Wayfire build)",
                "severity": "warning",
                "content_preview": next(
                    (l.strip() for l in wf_content.splitlines() if "calibration_matrix" in l), ""
                ),
            })
        if "touch-transform" in wf_content:
            found.append({
                "path": WAYFIRE,
                "description": "wayfire.ini has touch-transform (not honored by RPi Wayfire build)",
                "severity": "warning",
                "content_preview": next(
                    (l.strip() for l in wf_content.splitlines() if "touch-transform" in l), ""
                ),
            })

    # Detect orphaned config from the other compositor
    comp = detect_compositor()
    if comp == "labwc" and os.path.isfile(WAYFIRE):
        wf_content = _read_file(WAYFIRE)
        if re.search(r"\[output:", wf_content) or re.search(r"\[input-device:", wf_content):
            found.append({
                "path": WAYFIRE,
                "description": (
                    "wayfire.ini has display/touch settings but labwc is running. "
                    "These settings are ignored. Re-apply in Display Settings "
                    "and Touchscreen Settings to save for labwc."),
                "severity": "info",
                "content_preview": "",
            })
    elif comp == "wayfire" and os.path.isfile(KANSHI):
        kanshi_content = _read_file(KANSHI)
        if "output " in kanshi_content:
            found.append({
                "path": KANSHI,
                "description": (
                    "kanshi config has display settings but Wayfire is running. "
                    "These settings are ignored. Re-apply in Display Settings "
                    "to save for Wayfire."),
                "severity": "info",
                "content_preview": "",
            })

    return found


def clean_conflicts(paths_to_remove):
    """
    Remove conflicting files. Creates backups first.
    paths_to_remove: list of file paths to clean up.
    Returns list of (path, backup_path, success) tuples.
    """
    results = []
    for path in paths_to_remove:
        if not os.path.isfile(path):
            results.append((path, None, True))
            continue

        bak = _sudo_backup(path)
        r = subprocess.run(["sudo", "rm", "-f", path], capture_output=True)
        results.append((path, bak, r.returncode == 0))

    # Rebuild hwdb if we removed any hwdb files
    if any("hwdb" in p for p in paths_to_remove):
        subprocess.run(["sudo", "systemd-hwdb", "update"], capture_output=True)

    return results


# ── Existing settings discovery ──────────────────────────────────────

def discover_existing_settings():
    """
    Scan for all existing display/touch settings.
    Called on first launch to show what manual config was found.
    Returns list of dicts: {source, setting, value, file}
    """
    found = []

    # Check cmdline.txt for any video= margins
    if os.path.isfile(CMDLINE):
        cmdline = _read_file(CMDLINE).strip()
        for m in re.finditer(r"video=(\S+)", cmdline):
            video_str = m.group(1)
            if "margin_" in video_str:
                connector = video_str.split(":")[0] if ":" in video_str else "?"
                margins = {}
                for edge in ("left", "right", "top", "bottom"):
                    em = re.search(rf"margin_{edge}=(\d+)", video_str)
                    if em:
                        margins[edge] = em.group(1)
                found.append({
                    "source": "Display Margins",
                    "setting": f"{connector}: " + ", ".join(
                        f"{e}={v}" for e, v in margins.items()),
                    "value": video_str,
                    "file": CMDLINE,
                })

    # Check udev calibration rule
    if os.path.isfile(UDEV_RULE):
        cal = read_calibration()
        if cal:
            name, mat = cal
            found.append({
                "source": "Touch Calibration",
                "setting": f"Device: {name}",
                "value": " ".join(f"{v:.4f}" for v in mat),
                "file": UDEV_RULE,
            })

    # Check wayfire.ini for output transforms and input mappings
    if os.path.isfile(WAYFIRE):
        content = _read_file(WAYFIRE)
        for m in re.finditer(r"\[output:([^\]]+)\]", content):
            section = read_wayfire_section(f"output:{m.group(1)}")
            if section.get("transform") and section["transform"] != "normal":
                found.append({
                    "source": "Display Rotation (Wayfire)",
                    "setting": f"{m.group(1)}: {section['transform']}",
                    "value": section["transform"],
                    "file": WAYFIRE,
                })
        for m in re.finditer(r"\[input-device:([^\]]+)\]", content):
            section = read_wayfire_section(f"input-device:{m.group(1)}")
            if section.get("output"):
                found.append({
                    "source": "Touch Mapping (Wayfire)",
                    "setting": f"{m.group(1)} → {section['output']}",
                    "value": section["output"],
                    "file": WAYFIRE,
                })

    # Check kanshi config for rotation (labwc)
    if os.path.isfile(KANSHI):
        content = _read_file(KANSHI).strip()
        if content:  # Skip empty kanshi configs (labwc-pi creates these)
            for m in re.finditer(r"output\s+(\S+)\s+[^}]*transform\s+(\S+)", content):
                found.append({
                    "source": "Display Rotation (kanshi/labwc)",
                    "setting": f"{m.group(1)}: {m.group(2)}",
                    "value": m.group(2),
                    "file": KANSHI,
                })

    # Check labwc rc.xml for touch mappings
    if os.path.isfile(LABWC_RC):
        content = _read_file(LABWC_RC)
        for m in re.finditer(r'<touch\s+[^>]*deviceName="([^"]+)"[^>]*mapToOutput="([^"]+)"', content):
            found.append({
                "source": "Touch Mapping (labwc)",
                "setting": f"{m.group(1)} → {m.group(2)}",
                "value": m.group(2),
                "file": LABWC_RC,
            })

    return found


# ── Backup management ────────────────────────────────────────────────

def list_backups():
    """
    Find all timestamped backups for the three config files.
    Returns list of dicts sorted newest first:
      {path, original, timestamp, size, age_str}
    """
    import glob
    from datetime import datetime

    backups = []
    patterns = [
        (CMDLINE, f"{CMDLINE}.bak.*"),
        (UDEV_RULE, f"{UDEV_RULE}.bak.*"),
        (WAYFIRE, f"{WAYFIRE}.bak.*"),
        (KANSHI, f"{KANSHI}.bak.*"),
        (LABWC_RC, f"{LABWC_RC}.bak.*"),
    ]

    for original, pattern in patterns:
        for bak_path in glob.glob(pattern):
            # Extract timestamp from filename: .bak.YYYYMMDDHHMMSS
            m = re.search(r"\.bak\.(\d{14})$", bak_path)
            if not m:
                continue
            ts_str = m.group(1)
            try:
                ts = datetime.strptime(ts_str, "%Y%m%d%H%M%S")
                age = datetime.now() - ts
                if age.days > 0:
                    age_str = f"{age.days}d ago"
                elif age.seconds > 3600:
                    age_str = f"{age.seconds // 3600}h ago"
                else:
                    age_str = f"{age.seconds // 60}m ago"
            except ValueError:
                ts = None
                age_str = "?"

            try:
                size = os.path.getsize(bak_path)
            except OSError:
                size = 0

            backups.append({
                "path": bak_path,
                "original": original,
                "timestamp": ts,
                "ts_str": ts_str,
                "size": size,
                "age_str": age_str,
                "label": _backup_label(original),
            })

    backups.sort(key=lambda b: b.get("timestamp") or datetime.min, reverse=True)
    return backups


# Friendly labels for backup files
_BACKUP_LABELS = {
    CMDLINE: "Display borders",
    UDEV_RULE: "Touch calibration",
    WAYFIRE: "Display settings (wayfire)",
    KANSHI: "Display settings (labwc)",
    LABWC_RC: "Touch mapping (labwc)",
}


def _backup_label(path):
    return _BACKUP_LABELS.get(path, os.path.basename(path))


MAX_BACKUPS_PER_FILE = 10


def cleanup_old_backups():
    """Remove oldest backups beyond MAX_BACKUPS_PER_FILE per original file."""
    import glob
    patterns = [
        (CMDLINE, f"{CMDLINE}.bak.*"),
        (UDEV_RULE, f"{UDEV_RULE}.bak.*"),
        (KANSHI, f"{KANSHI}.bak.*"),
        (LABWC_RC, f"{LABWC_RC}.bak.*"),
    ]
    if os.path.isfile(WAYFIRE):
        patterns.append((WAYFIRE, f"{WAYFIRE}.bak.*"))

    for original, pattern in patterns:
        baks = sorted(glob.glob(pattern))
        if len(baks) > MAX_BACKUPS_PER_FILE:
            for old in baks[:len(baks) - MAX_BACKUPS_PER_FILE]:
                try:
                    if os.access(old, os.W_OK):
                        os.remove(old)
                    else:
                        subprocess.run(["sudo", "rm", "-f", old], check=False)
                except OSError:
                    pass




# ── Tray icon management ─────────────────────────────────────────────

TRAY_AUTOSTART = "/etc/xdg/autostart/display-calibrator.desktop"


def is_tray_enabled():
    """Check if the tray icon autostart is active."""
    return os.path.isfile(TRAY_AUTOSTART)


def set_tray_enabled(enable):
    """Enable or disable the tray icon autostart by renaming the file."""
    disabled = TRAY_AUTOSTART + ".disabled"
    try:
        if enable and os.path.isfile(disabled):
            subprocess.run(["sudo", "-n", "mv", disabled, TRAY_AUTOSTART],
                           check=True, capture_output=True, timeout=10)
        elif not enable and os.path.isfile(TRAY_AUTOSTART):
            subprocess.run(["sudo", "-n", "mv", TRAY_AUTOSTART, disabled],
                           check=True, capture_output=True, timeout=10)
        return True
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return False
