"""
Device discovery for display outputs and touch input devices.

Display detection:
  - Reads /sys/class/drm/ for all connectors
  - Tags each with type (hdmi, dsi, dp) for filtering
  - Only HDMI/DP outputs support kernel video= margins

Touch detection:
  - Primary: libinput list-devices (name, kernel path, calibration)
  - Enriched with udevadm for stable IDs (USB VID:PID, by-id path)
  - Fallback: /proc/bus/input/devices with ABS_MT bit check
  - Filters out trackpads (BTN_TOOL_FINGER / pointer+touch combo)
"""

import subprocess, re, os


# ── Display outputs ──────────────────────────────────────────────────

_CONNECTOR_TYPES = {
    "HDMI": "hdmi", "DP": "dp", "DSI": "dsi", "DPI": "dpi",
    "Composite": "composite", "TV": "composite", "VGA": "vga",
    "LVDS": "lvds", "eDP": "edp",
}


def _classify_connector(name):
    for prefix, ctype in _CONNECTOR_TYPES.items():
        if name.startswith(prefix):
            return ctype
    return "unknown"


def get_drm_outputs():
    """
    Enumerate all DRM display connectors.
    Returns list of dicts with: name, connected, resolution,
    connector_type, physical_size, supports_margins
    """
    outputs = []
    base = "/sys/class/drm"
    if not os.path.isdir(base):
        return outputs

    for entry in sorted(os.listdir(base)):
        m = re.match(r"card(\d+)-(.+)", entry)
        if not m:
            continue
        name = m.group(2)
        spath = os.path.join(base, entry)

        try:
            status = open(os.path.join(spath, "status")).read().strip()
        except OSError:
            status = "unknown"

        connected = status == "connected"
        ctype = _classify_connector(name)

        resolution = None
        phys_size = None
        if connected:
            try:
                modes = open(os.path.join(spath, "modes")).read().strip().splitlines()
                if modes:
                    resolution = modes[0]
            except OSError:
                pass
            try:
                w = int(open(os.path.join(spath, "width")).read().strip() or "0")
                h = int(open(os.path.join(spath, "height")).read().strip() or "0")
                if w > 0 and h > 0:
                    import math
                    diag = math.sqrt(w * w + h * h) / 25.4
                    phys_size = f'{w}x{h}mm ({diag:.1f}")'
            except (OSError, ValueError):
                pass

        outputs.append({
            "name": name,
            "connected": connected,
            "resolution": resolution,
            "connector_type": ctype,
            "physical_size": phys_size,
            "supports_margins": ctype in ("hdmi", "dp"),
        })
    return outputs


def get_connected_outputs():
    return [o for o in get_drm_outputs() if o["connected"]]


def get_margin_capable_outputs():
    """Only connected HDMI/DP outputs (kernel margins don't apply to DSI)."""
    return [o for o in get_drm_outputs() if o["connected"] and o["supports_margins"]]


# ── Touch input devices ─────────────────────────────────────────────

def _enrich_with_udevadm(dev):
    """Add USB VID:PID and stable by-id path from udevadm."""
    kernel_path = dev.get("kernel", "")
    if not kernel_path:
        return
    try:
        r = subprocess.run(
            ["udevadm", "info", "-q", "all", "-n", kernel_path],
            capture_output=True, text=True, timeout=5,
        )
        if r.returncode != 0:
            return
        for line in r.stdout.splitlines():
            if "ID_VENDOR_ID=" in line:
                dev["usb_vid"] = line.split("=", 1)[1].strip()
            elif "ID_MODEL_ID=" in line:
                dev["usb_pid"] = line.split("=", 1)[1].strip()
            elif "ID_BUS=" in line:
                dev["bus_type"] = line.split("=", 1)[1].strip()
            elif "ID_PATH=" in line:
                dev["id_path"] = line.split("=", 1)[1].strip()
        vid = dev.get("usb_vid", "")
        pid = dev.get("usb_pid", "")
        if vid and pid:
            dev["usb_id"] = f"{vid}:{pid}"
    except Exception:
        pass

    # Find stable /dev/input/by-id symlink
    by_id = "/dev/input/by-id"
    if os.path.isdir(by_id):
        try:
            real = os.path.realpath(kernel_path)
            for link in os.listdir(by_id):
                if os.path.realpath(os.path.join(by_id, link)) == real:
                    dev["by_id_path"] = os.path.join(by_id, link)
                    break
        except OSError:
            pass


def _is_trackpad(dev):
    """Reject trackpads that report touch capability."""
    caps = dev.get("capabilities", "")
    if "pointer" in caps and "touch" in caps:
        return True
    name = dev.get("name", "").lower()
    return any(kw in name for kw in ("trackpad", "touchpad", "synaptics", "clickpad"))


def get_touch_devices():
    """
    Return touch-capable input devices enriched with stable IDs.
    Each dict: name, kernel, capabilities, calibration,
               usb_id, by_id_path, bus_type (when available)
    """
    devices = []

    for cmd in [
        ["libinput", "list-devices"],
        ["sudo", "-n", "libinput", "list-devices"],
    ]:
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
            if r.returncode != 0:
                continue

            current = {}
            for line in r.stdout.splitlines():
                line = line.strip()
                if line.startswith("Device:"):
                    if current.get("is_touch") and not _is_trackpad(current):
                        _enrich_with_udevadm(current)
                        devices.append(current)
                    current = {"name": line.split(":", 1)[1].strip()}
                elif line.startswith("Kernel:"):
                    current["kernel"] = line.split(":", 1)[1].strip()
                elif line.startswith("Capabilities:"):
                    caps = line.split(":", 1)[1].strip()
                    current["capabilities"] = caps
                    current["is_touch"] = "touch" in caps
                elif line.startswith("Calibration:"):
                    current["calibration"] = line.split(":", 1)[1].strip()

            if current.get("is_touch") and not _is_trackpad(current):
                _enrich_with_udevadm(current)
                devices.append(current)

            if devices:
                return devices
        except Exception:
            continue

    # Fallback: /proc/bus/input/devices with ABS_MT bit check
    try:
        content = open("/proc/bus/input/devices").read()
        for block in content.split("\n\n"):
            abs_m = re.search(r"B: ABS=([0-9a-f]+)", block, re.I)
            if not abs_m:
                continue
            # Check ABS_MT_POSITION_X = bit 53
            if not (int(abs_m.group(1), 16) & (1 << 53)):
                continue
            # Reject trackpads: BTN_TOOL_FINGER = bit 325
            key_m = re.search(r"B: KEY=([0-9a-f ]+)", block, re.I)
            if key_m:
                try:
                    if int(key_m.group(1).replace(" ", ""), 16) & (1 << 325):
                        continue
                except ValueError:
                    pass
            nm = re.search(r'N: Name="(.+)"', block)
            hm = re.search(r"H: Handlers=.*?(event\d+)", block)
            if nm and hm:
                dev = {
                    "name": nm.group(1),
                    "kernel": f"/dev/input/{hm.group(1)}",
                    "is_touch": True,
                    "calibration": "unknown",
                }
                _enrich_with_udevadm(dev)
                devices.append(dev)
    except OSError:
        pass

    return devices


def get_event_name(dev):
    """Get sysname (e.g. 'event5') for udevadm trigger. Returns str or None."""
    k = dev.get("kernel", "")
    return os.path.basename(k) if k else None


# ── Formatting helpers for dropdowns ─────────────────────────────────

def format_output(o):
    """Human-readable output name for dropdown."""
    parts = [o["name"]]
    if o.get("resolution"):
        parts.append(f'({o["resolution"]})')
    if o.get("connector_type", "unknown") != "unknown":
        parts.append(f'[{o["connector_type"].upper()}]')
    return " ".join(parts)


def format_touch(d):
    """Human-readable touch device name for dropdown."""
    parts = [d["name"]]
    if d.get("usb_id"):
        parts.append(f'[{d["usb_id"]}]')
    if d.get("kernel"):
        parts.append(f'({os.path.basename(d["kernel"])})')
    return " ".join(parts)
