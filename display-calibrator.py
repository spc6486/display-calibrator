#!/usr/bin/env python3
"""
Display Calibrator — System Tray Application

Sits in the panel tray providing quick access to:
  - Display settings (resolution, scale, rotation, borders)
  - Touchscreen calibration and fine-tuning
  - System diagnostics

Requires: python3-gi, gir1.2-gtk-3.0, gir1.2-ayatanaappindicator3-0.1, libinput-tools, wlr-randr, kanshi
"""

import os, sys, re, subprocess, threading, time, shutil

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, Gdk, GLib, GdkPixbuf

# AppIndicator — try Ayatana first (Debian/RPi OS), then legacy
try:
    gi.require_version("AyatanaAppIndicator3", "0.1")
    from gi.repository import AyatanaAppIndicator3 as AppIndicator3
except (ValueError, ImportError):
    try:
        gi.require_version("AppIndicator3", "0.1")
        from gi.repository import AppIndicator3
    except (ValueError, ImportError):
        AppIndicator3 = None

# Our modules (installed alongside this file)
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)
import devices, config, matrix, touch_capture

ICON_SVG = os.path.join(SCRIPT_DIR, "display-calibrator.svg")
ICON_NAME = "display-calibrator"  # theme name (no extension)
APP_ID = "display-calibrator"


# ─── Margin ↔ rotation mapping ───────────────────────────
# Kernel video= margins are applied to the physical panel BEFORE
# compositor rotation.  With 180° rotation (upside-down mount),
# kernel "left" crops what the user sees as "right", etc.

def _margin_kernel_to_user(margins, rotation_str):
    """Map kernel-space margins to user-visible edges, given rotation."""
    m = margins
    r = str(rotation_str).strip().lower()
    if r in ("180", "flipped-180"):
        return {"left": m["right"], "right": m["left"],
                "top": m["bottom"], "bottom": m["top"]}
    if r in ("90", "flipped-90"):
        return {"left": m["top"], "right": m["bottom"],
                "top": m["right"], "bottom": m["left"]}
    if r in ("270", "flipped-270"):
        return {"left": m["bottom"], "right": m["top"],
                "top": m["left"], "bottom": m["right"]}
    return dict(m)  # normal / 0 — no mapping needed


def _margin_user_to_kernel(margins, rotation_str):
    """Map user-visible margins to kernel-space, given rotation."""
    m = margins
    r = str(rotation_str).strip().lower()
    if r in ("180", "flipped-180"):
        # 180° is self-inverse
        return {"left": m["right"], "right": m["left"],
                "top": m["bottom"], "bottom": m["top"]}
    if r in ("90", "flipped-90"):
        # inverse of kernel→user for 90°
        return {"left": m["bottom"], "right": m["top"],
                "top": m["left"], "bottom": m["right"]}
    if r in ("270", "flipped-270"):
        # inverse of kernel→user for 270°
        return {"left": m["top"], "right": m["bottom"],
                "top": m["right"], "bottom": m["left"]}
    return dict(m)


# ═══════════════════════════════════════════════════════════════
# Tray Application
# ═══════════════════════════════════════════════════════════════

class CalibratorTray:
    def __init__(self):
        self._open_windows = set()  # Track open settings windows
        self._build_indicator()
        self._build_menu()
        # Run preflight check after GTK main loop starts
        GLib.idle_add(self._startup_preflight)

    def _register_window(self, win):
        """Track an open settings window for z-order management."""
        self._open_windows.add(win)
        win.connect("destroy", lambda w: self._open_windows.discard(w))

    def _hide_all_windows(self):
        """Hide all open settings windows (before fullscreen overlay)."""
        for w in self._open_windows:
            w.hide()

    def _show_all_windows(self):
        """Restore all hidden settings windows (after fullscreen overlay)."""
        for w in self._open_windows:
            w.show()
            w.present()

    def _startup_preflight(self):
        """Check for conflicting configs and show existing settings on first launch."""
        # Check for conflicts first
        conflicts = config.preflight_scan()
        if conflicts:
            serious = [c for c in conflicts if c["severity"] == "conflict"]
            if serious:
                self._show_conflicts_dialog(conflicts, startup=True)
                return False  # Don't also show settings — conflicts take priority

        # Show existing settings only on first launch
        flag_dir = os.path.expanduser("~/.local/share/display-calibrator")
        flag_file = os.path.join(flag_dir, "startup-shown")
        if os.path.isfile(flag_file):
            return False  # Already shown

        existing = config.discover_existing_settings()
        if existing:
            self._show_existing_settings(existing)

        # Mark as shown
        try:
            os.makedirs(flag_dir, exist_ok=True)
            with open(flag_file, "w") as f:
                f.write("1\n")
        except OSError:
            pass

        return False  # Don't repeat

    def _show_existing_settings(self, settings):
        """Show what existing manual settings were detected."""
        # Filter out default RPi DSI touch mappings (these are system defaults, not user config)
        filtered = [s for s in settings if not (
            "DSI-" in s.get("value", "") or
            "ft5x06" in s.get("setting", "") or
            "Goodix" in s.get("setting", "") or
            "XTEST" in s.get("setting", "")
        )]
        if not filtered:
            return  # Only defaults found, nothing to show

        dlg = Gtk.MessageDialog(
            parent=None, flags=0,
            message_type=Gtk.MessageType.INFO,
            buttons=Gtk.ButtonsType.OK,
            text="Existing Settings Detected",
        )
        lines = ["The following display/touch settings were found\n"
                 "from previous manual configuration:\n"]
        for s in filtered:
            lines.append(f"  {s['source']}: {s['setting']}")
        lines.append("\nThese settings are loaded into the app automatically.\n"
                     "You can view and modify them through the tray menu.")
        dlg.format_secondary_text("\n".join(lines))
        dlg.run()
        dlg.destroy()

    # ── Indicator / tray icon ────────────────────────────────

    def _build_indicator(self):
        if AppIndicator3:
            self.indicator = AppIndicator3.Indicator.new(
                APP_ID,
                ICON_NAME,  # looked up in hicolor theme
                AppIndicator3.IndicatorCategory.HARDWARE,
            )
            # Also set the icon theme search path so it finds our SVG
            # even if the hicolor cache hasn't been rebuilt yet
            self.indicator.set_icon_theme_path(SCRIPT_DIR)
            self.indicator.set_status(AppIndicator3.IndicatorStatus.ACTIVE)
            self.indicator.set_title("Display Calibrator")
        else:
            # Fallback to StatusIcon (deprecated but works on Bookworm X11)
            self.indicator = None
            self.status_icon = Gtk.StatusIcon.new_from_file(ICON_SVG)
            self.status_icon.set_tooltip_text("Display Calibrator")
            self.status_icon.connect("popup-menu", self._on_status_popup)
            self.status_icon.connect("activate", self._on_status_activate)

    def _on_status_popup(self, icon, button, time):
        self.menu.popup(None, None, Gtk.StatusIcon.position_menu,
                        icon, button, time)

    def _on_status_activate(self, icon):
        self.menu.popup(None, None, None, None, 0, Gtk.get_current_event_time())

    # ── Menu ─────────────────────────────────────────────────

    def _build_menu(self):
        self.menu = Gtk.Menu()

        self._add_item("Display Calibrator…", self._on_settings)

        self.menu.append(Gtk.SeparatorMenuItem())

        self._add_item("Quit", self._on_quit)

        self.menu.show_all()

        if AppIndicator3 and self.indicator:
            self.indicator.set_menu(self.menu)

    def _add_item(self, label, callback):
        item = Gtk.MenuItem(label=label)
        # Use a short timeout to let the menu fully close before opening windows.
        # Under labwc, opening a window while the menu is still active causes the
        # menu to stay "grabbed", blocking all future menu interactions.
        def _safe_cb(widget, cb=callback):
            def _run():
                try:
                    cb(widget)
                except Exception as e:
                    import traceback
                    traceback.print_exc()
                return False
            GLib.timeout_add(200, _run)
        item.connect("activate", _safe_cb)
        self.menu.append(item)
        return item

    # ════════════════════════════════════════════════════════
    # CONFLICT DETECTION & CLEANUP
    # ════════════════════════════════════════════════════════

    def _on_settings(self, _=None):
        """Open the unified settings window with tabs."""
        win = Gtk.Window(title="Display Calibrator", default_width=440)
        win.set_position(Gtk.WindowPosition.CENTER)
        win.set_type_hint(Gdk.WindowTypeHint.DIALOG)
        win.set_size_request(440, 520)  # minimum height for comfortable spacing
        self._register_window(win)
        self._settings_win = win
        self._display_apply = None
        self._touch_apply = None

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        outer.set_margin_start(8)
        outer.set_margin_end(8)
        outer.set_margin_top(6)
        outer.set_margin_bottom(6)
        win.add(outer)

        # Header
        header = Gtk.Label()
        header.set_markup(
            f"<big><b>Display Calibrator</b></big>  "
            f"<small>v{config.VERSION}</small>")
        header.set_xalign(0)
        outer.pack_start(header, False, False, 0)

        # Notebook
        notebook = Gtk.Notebook()
        outer.pack_start(notebook, True, True, 4)

        # Tab 1: Display
        display_page = self._build_display_tab(win)
        notebook.append_page(display_page, Gtk.Label(label="Display"))

        # Tab 2: Touchscreen
        touch_page = self._build_touchscreen_tab(win)
        notebook.append_page(touch_page, Gtk.Label(label="Touchscreen"))

        # Tab 3: Tools
        tools_page = self._build_tools_tab(win)
        notebook.append_page(tools_page, Gtk.Label(label="Tools"))

        # ── Global bottom bar ──
        bottom = Gtk.Box(spacing=8)
        bottom.set_margin_top(4)

        # Left: tray toggle
        tray_check = Gtk.CheckButton(label="Tray icon")
        tray_check.set_active(config.is_tray_enabled())
        tray_check.set_tooltip_text(
            "Show system tray icon on login")
        def on_tray_toggle(chk):
            config.set_tray_enabled(chk.get_active())
        tray_check.connect("toggled", on_tray_toggle)
        bottom.pack_start(tray_check, False, False, 0)

        # Right: Close + Apply
        btn_close = Gtk.Button(label="Close")
        btn_apply = Gtk.Button(label="Apply")
        btn_apply.get_style_context().add_class("suggested-action")

        def on_global_apply(_):
            page = notebook.get_current_page()
            if page == 0 and self._display_apply:
                self._display_apply(None)
            elif page == 1 and self._touch_apply:
                self._touch_apply(None)

        def on_global_close(_):
            win.destroy()

        btn_close.connect("clicked", on_global_close)
        btn_apply.connect("clicked", on_global_apply)
        bottom.pack_end(btn_apply, False, False, 0)
        bottom.pack_end(btn_close, False, False, 0)

        outer.pack_end(bottom, False, False, 0)

        win.show_all()

    def _build_tools_tab(self, win):
        """Build the Tools tab: conflicts, history, status, uninstall, tray toggle."""
        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        vbox.set_margin_start(12)
        vbox.set_margin_end(12)
        vbox.set_margin_top(14)
        vbox.set_margin_bottom(8)

        # Conflicts
        btn_conflicts = Gtk.Button(label="Check for Conflicts…")
        btn_conflicts.set_tooltip_text(
            "Scan for old manual calibration files\n"
            "that could interfere with this app.")
        btn_conflicts.connect("clicked", self._on_check_conflicts)
        vbox.pack_start(btn_conflicts, False, False, 0)

        # History
        btn_history = Gtk.Button(label="Settings History…")
        btn_history.set_tooltip_text(
            "Browse and restore timestamped backups\n"
            "of all configuration changes.")
        btn_history.connect("clicked", self._on_backup_history)
        vbox.pack_start(btn_history, False, False, 0)

        # System status
        btn_cli = Gtk.Button(label="Show System Status…")
        btn_cli.set_tooltip_text("Display detected hardware, current\n"
                                 "settings, and diagnostic information.")
        def on_status(_):
            swin = Gtk.Window(title="Display Calibrator — Status",
                              default_width=500, default_height=400)
            swin.set_position(Gtk.WindowPosition.CENTER)
            swin.set_type_hint(Gdk.WindowTypeHint.DIALOG)
            svbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
            svbox.set_margin_start(8)
            svbox.set_margin_end(8)
            svbox.set_margin_top(8)
            svbox.set_margin_bottom(8)
            swin.add(svbox)
            scroll = Gtk.ScrolledWindow()
            scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
            textview = Gtk.TextView()
            textview.set_editable(False)
            textview.set_monospace(True)
            textview.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
            buf = textview.get_buffer()
            buf.set_text(self._gather_status())
            scroll.add(textview)
            svbox.pack_start(scroll, True, True, 0)
            btn_r = Gtk.Button(label="Refresh")
            btn_r.connect("clicked", lambda _: buf.set_text(self._gather_status()))
            btn_c = Gtk.Button(label="Close")
            btn_c.connect("clicked", lambda _: swin.destroy())
            hb = Gtk.Box(spacing=8)
            hb.set_halign(Gtk.Align.END)
            hb.pack_start(btn_r, False, False, 0)
            hb.pack_start(btn_c, False, False, 0)
            svbox.pack_end(hb, False, False, 4)
            swin.show_all()
        btn_cli.connect("clicked", on_status)
        vbox.pack_start(btn_cli, False, False, 0)

        vbox.pack_start(Gtk.Separator(), False, False, 4)

        # Uninstall
        btn_uninstall = Gtk.Button(label="Uninstall…")
        btn_uninstall.get_style_context().add_class("destructive-action")
        btn_uninstall.connect("clicked", self._on_uninstall)
        vbox.pack_start(btn_uninstall, False, False, 0)

        # Bottom spacer
        vbox.pack_start(Gtk.Box(), True, True, 0)

        return vbox

    def _on_check_conflicts(self, _):
        conflicts = config.preflight_scan()
        if not conflicts:
            _info_dialog(None, "No Conflicts Found",
                "No conflicting configurations detected.\n\n"
                "The system is clean for this app to manage\n"
                "display margins and touchscreen calibration.")
            return
        self._show_conflicts_dialog(conflicts, startup=False)

    def _show_conflicts_dialog(self, conflicts, startup=False):
        """Show a dialog listing found conflicts with option to clean up."""
        win = Gtk.Window(
            title="Configuration Conflicts Detected",
            default_width=560, default_height=400,
        )
        win.set_position(Gtk.WindowPosition.CENTER)
        win.set_type_hint(Gdk.WindowTypeHint.DIALOG)
        win.set_keep_above(True)

        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        vbox.set_margin_start(14)
        vbox.set_margin_end(14)
        vbox.set_margin_top(10)
        vbox.set_margin_bottom(10)
        win.add(vbox)

        if startup:
            header = ("Existing configurations were found that may conflict\n"
                       "with this application. Review and clean up if needed.")
        else:
            header = "The following configurations were found on your system:"

        lbl = Gtk.Label(label=header)
        lbl.set_line_wrap(True)
        lbl.set_xalign(0)
        vbox.pack_start(lbl, False, False, 0)

        # Scrollable list of conflicts with checkboxes
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        listbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        listbox.set_margin_top(8)

        checks = []  # (checkbox, path)
        for c in conflicts:
            row = Gtk.Box(spacing=8)

            # Severity indicator
            sev_colors = {"conflict": "#DC2626", "warning": "#F59E0B", "info": "#6B7280"}
            sev_labels = {"conflict": "CONFLICT", "warning": "WARNING", "info": "INFO"}
            sev = c["severity"]

            badge = Gtk.Label()
            badge.set_markup(
                f'<span foreground="{sev_colors.get(sev, "#6B7280")}"'
                f' weight="bold" size="small">{sev_labels.get(sev, "INFO")}</span>'
            )
            badge.set_size_request(70, -1)
            badge.set_xalign(0)
            row.pack_start(badge, False, False, 0)

            detail = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
            path_lbl = Gtk.Label()
            path_lbl.set_markup(f'<tt><small>{c["path"]}</small></tt>')
            path_lbl.set_xalign(0)
            detail.pack_start(path_lbl, False, False, 0)

            desc_lbl = Gtk.Label(label=c["description"])
            desc_lbl.set_xalign(0)
            desc_lbl.set_line_wrap(True)
            detail.pack_start(desc_lbl, False, False, 0)

            if c.get("content_preview"):
                prev_lbl = Gtk.Label()
                preview_text = c["content_preview"][:80].replace("&", "&amp;").replace("<", "&lt;")
                prev_lbl.set_markup(f'<tt><small>{preview_text}</small></tt>')
                prev_lbl.set_xalign(0)
                detail.pack_start(prev_lbl, False, False, 0)

            row.pack_start(detail, True, True, 0)

            # Checkbox for removable files (not cmdline.txt or config.txt or wayfire.ini)
            removable = c["path"] not in (config.CMDLINE, config.CONFIG_TXT, config.WAYFIRE,
                                          config.KANSHI, config.LABWC_RC)
            if removable:
                chk = Gtk.CheckButton()
                chk.set_active(sev == "conflict")  # Pre-check conflicts
                row.pack_end(chk, False, False, 0)
                checks.append((chk, c["path"]))

            listbox.pack_start(row, False, False, 0)
            listbox.pack_start(Gtk.Separator(), False, False, 0)

        scroll.add(listbox)
        vbox.pack_start(scroll, True, True, 0)

        # Buttons
        hbox = Gtk.Box(spacing=8)
        hbox.set_halign(Gtk.Align.END)

        btn_close = Gtk.Button(label="Close")
        btn_close.connect("clicked", lambda _: win.destroy())
        hbox.pack_start(btn_close, False, False, 0)

        # Only show Remove Selected if there are removable items
        if checks:
            note = Gtk.Label()
            note.set_markup(
                "<small>Checked items can be removed (backups are created).\n"
                "Items in cmdline.txt, config.txt, and compositor configs\n"
                "must be edited through the app's other dialogs.</small>"
            )
            note.set_xalign(0)
            vbox.pack_start(note, False, False, 4)

            btn_clean = Gtk.Button(label="Remove Selected")
            btn_clean.get_style_context().add_class("destructive-action")

            def on_clean(_):
                to_remove = [path for chk, path in checks if chk.get_active()]
                if not to_remove:
                    _info_dialog(win, "Nothing Selected", "No files selected for removal.")
                    return
                if not _confirm_dialog(win, "Confirm Removal",
                        f"Remove {len(to_remove)} file(s)?\n\n"
                        + "\n".join(f"  {p}" for p in to_remove) +
                        "\n\nTimestamped backups will be created."):
                    return
                results = config.clean_conflicts(to_remove)
                msgs = []
                for path, bak, ok in results:
                    status = "removed" if ok else "FAILED"
                    msgs.append(f"  {status}: {path}")
                    if bak:
                        msgs.append(f"    backup: {bak}")
                _info_dialog(win, "Cleanup Complete", "\n".join(msgs))
                win.destroy()

            btn_clean.connect("clicked", on_clean)
            hbox.pack_start(btn_clean, False, False, 0)
        else:
            note = Gtk.Label()
            note.set_markup(
                "<small>No removable files found. Items shown are\n"
                "informational or must be edited through the app.</small>"
            )
            note.set_xalign(0)
            vbox.pack_start(note, False, False, 4)
        vbox.pack_end(hbox, False, False, 0)

        win.show_all()

    def _pre_write_check(self, action_description):
        """
        Run before any write operation. If conflicts exist, warn the user.
        Returns True if safe to proceed, False if user cancelled.
        """
        conflicts = config.preflight_scan()
        # Only warn about actual conflicts, not info-level items
        serious = [c for c in conflicts if c["severity"] in ("conflict", "warning")]

        # Skip wayfire.ini warnings when running labwc (and vice versa)
        compositor = config.detect_compositor()
        if compositor == "labwc":
            serious = [c for c in serious if c["path"] != config.WAYFIRE]
        elif compositor == "wayfire":
            serious = [c for c in serious
                       if c["path"] not in (config.KANSHI, config.LABWC_RC)]

        if not serious:
            return True

        msg_lines = [f"Found {len(serious)} issue(s) that may interfere:\n"]
        for c in serious[:5]:  # Show up to 5
            msg_lines.append(f"  [{c['severity'].upper()}] {c['path']}")
            msg_lines.append(f"    {c['description']}\n")
        if len(serious) > 5:
            msg_lines.append(f"  ... and {len(serious) - 5} more\n")
        msg_lines.append(f"Proceed with {action_description} anyway?\n"
                         "(Use 'Check for Conflicts' from the tray menu to clean up)")

        return _confirm_dialog(None, "Conflicts Detected", "\n".join(msg_lines))

    # ════════════════════════════════════════════════════════
    # DISPLAY SETTINGS WINDOW (unified)
    # ════════════════════════════════════════════════════════

    def _build_display_tab(self, win):
        """Build the Display tab content."""
        outputs = devices.get_connected_outputs()
        if not outputs:
            lbl = Gtk.Label(label="No connected display outputs found.")
            return lbl

        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        vbox.set_margin_start(12)
        vbox.set_margin_end(12)
        vbox.set_margin_top(14)
        vbox.set_margin_bottom(8)

        # ── Output selector ──
        hbox_out = Gtk.Box(spacing=8)
        hbox_out.pack_start(Gtk.Label(label="Output:"), False, False, 0)
        combo_out = Gtk.ComboBoxText()
        for o in outputs:
            res = o.get("resolution", "unknown")
            combo_out.append_text(f'{o["name"]} \u2014 {res}')
        combo_out.set_active(0)
        hbox_out.pack_start(combo_out, True, True, 0)
        vbox.pack_start(hbox_out, False, False, 0)

        # ── State tracking ──
        conn_name = outputs[0]["name"]
        rotation = config.read_rotation(conn_name) or "normal"
        scale_val = config.read_scale(conn_name) or 1.0
        wlr_info = config.get_wlr_randr_output(conn_name) or {}
        modes = wlr_info.get("modes", [])

        # Current resolution
        cur_mode = None
        for m in modes:
            if m.get("current"):
                cur_mode = m["resolution"]
                break
        if not cur_mode:
            cur_mode = outputs[0].get("resolution", "2048x1536")

        # Panel dimensions for fullscreen editor
        pw, ph = 2048, 1536
        m_res = re.match(r"(\d+)x(\d+)", cur_mode)
        if m_res:
            pw, ph = int(m_res.group(1)), int(m_res.group(2))

        # Read margins in user-space (rotation-aware)
        kern_margins = config.read_margins(conn_name) or \
            {"left": 0, "right": 0, "top": 0, "bottom": 0}
        user_margins = _margin_kernel_to_user(kern_margins, rotation)
        saved_margins = user_margins.copy()

        # Touch device mapped to this output
        def _touch_for_output(conn):
            for td in devices.get_touch_devices():
                mapping = config.read_touch_mapping(td["name"])
                if mapping == conn:
                    return td["name"]
            return None

        # ── Info card ──
        info_frame = Gtk.Frame()
        info_label = Gtk.Label()
        info_label.set_xalign(0)
        info_label.set_margin_start(8)
        info_label.set_margin_end(8)
        info_label.set_margin_top(4)
        info_label.set_margin_bottom(4)
        info_label.set_line_wrap(True)
        info_frame.add(info_label)
        vbox.pack_start(info_frame, False, False, 0)

        def _update_info():
            touch = _touch_for_output(conn_name) or "none"
            ml = margin_spins["left"].get_value_as_int()
            mr = margin_spins["right"].get_value_as_int()
            mt = margin_spins["top"].get_value_as_int()
            mb = margin_spins["bottom"].get_value_as_int()
            r_str = combo_rot_label(rotation)
            s_str = f"{scale_val:.10g}x"
            # Calculate logical res
            m2 = re.match(r"(\d+)x(\d+)", cur_mode)
            if m2 and scale_val > 0:
                lw = int(int(m2.group(1)) / scale_val)
                lh = int(int(m2.group(2)) / scale_val)
                s_str += f" ({lw}\u00d7{lh} logical)"
            info_label.set_markup(
                f"<small>"
                f"<b>Resolution:</b> {cur_mode}    "
                f"<b>Scale:</b> {s_str}\n"
                f"<b>Rotation:</b> {r_str}    "
                f"<b>Touch:</b> {touch}\n"
                f"<b>Borders:</b> L:{ml} R:{mr} T:{mt} B:{mb}"
                f"</small>"
            )

        # ── Resolution ──
        hbox_res_scale = Gtk.Box(spacing=12)

        box_res = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        box_res.pack_start(Gtk.Label(label="Resolution:", xalign=0), False, False, 0)
        combo_res = Gtk.ComboBoxText()
        active_res_idx = 0
        seen_res = []
        for i, m in enumerate(modes):
            res = m["resolution"]
            if res in seen_res:
                continue
            seen_res.append(res)
            suffix = ""
            if m.get("preferred"):
                suffix = " (preferred)"
            combo_res.append_text(f"{res}{suffix}")
            if m.get("current"):
                active_res_idx = len(seen_res) - 1
        if not seen_res:
            combo_res.append_text(cur_mode)
        combo_res.set_active(active_res_idx)
        box_res.pack_start(combo_res, False, False, 0)
        hbox_res_scale.pack_start(box_res, True, True, 0)

        # ── Scale ──
        box_scale = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        box_scale.pack_start(Gtk.Label(label="Scale:", xalign=0), False, False, 0)
        combo_scale = Gtk.ComboBoxText()
        scale_options = [1.0, 1.25, 1.5, 1.75, 2.0]
        active_scale_idx = 0
        for i, sv in enumerate(scale_options):
            combo_scale.append_text(f"{sv:.10g}x")
            if abs(sv - scale_val) < 0.01:
                active_scale_idx = i
        combo_scale.set_active(active_scale_idx)
        box_scale.pack_start(combo_scale, False, False, 0)
        hbox_res_scale.pack_start(box_scale, True, True, 0)

        vbox.pack_start(hbox_res_scale, False, False, 0)

        # ── Rotation ──
        def combo_rot_label(r):
            return {"normal": "0\u00b0", "90": "90\u00b0", "180": "180\u00b0",
                    "270": "270\u00b0"}.get(r, r)

        vbox.pack_start(Gtk.Label(label="Rotation:", xalign=0), False, False, 0)
        rot_box = Gtk.Box(spacing=6)
        rot_buttons = {}
        rot_values = ["normal", "90", "180", "270"]
        rot_labels = ["0\u00b0", "90\u00b0", "180\u00b0", "270\u00b0"]

        first_rb = None
        for rv, rl in zip(rot_values, rot_labels):
            if first_rb is None:
                rb = Gtk.RadioButton.new_with_label(None, rl)
                first_rb = rb
            else:
                rb = Gtk.RadioButton.new_with_label_from_widget(first_rb, rl)
            rb._rot_value = rv
            if rv == rotation:
                rb.set_active(True)
            rot_buttons[rv] = rb
            rot_box.pack_start(rb, True, True, 0)
        vbox.pack_start(rot_box, False, False, 0)

        # ── Hidden margin spins (for fullscreen editor integration) ──
        margin_spins = {}
        for edge in ("left", "right", "top", "bottom"):
            adj = Gtk.Adjustment(value=user_margins.get(edge, 0),
                                 lower=0, upper=999,
                                 step_increment=5, page_increment=20)
            spin = Gtk.SpinButton(adjustment=adj, numeric=True)
            margin_spins[edge] = spin
            # Update info card when margins change
            spin.connect("value-changed", lambda *a: _update_info())

        # ── Display borders ──
        border_lbl = Gtk.Label()
        border_lbl.set_markup("<small>Crop edges to match display bezel</small>")
        border_lbl.set_xalign(0)
        vbox.pack_start(border_lbl, False, False, 0)

        btn_borders = Gtk.Button(label="Adjust display borders\u2026")
        def on_borders_click(_):
            nonlocal scale_val, saved_margins
            si = combo_scale.get_active()
            if si >= 0 and si < len(scale_options):
                scale_val = scale_options[si]
            # Get current rotation selection
            cur_rot_sel = "normal"
            for rv, rb in rot_buttons.items():
                if rb.get_active():
                    cur_rot_sel = rv
                    break

            def _do_save_borders():
                """Save borders from the editor directly."""
                nonlocal saved_margins
                new_m = {e: margin_spins[e].get_value_as_int()
                         for e in ("left", "right", "top", "bottom")}
                if new_m == saved_margins:
                    return True  # Nothing changed
                kern_m = _margin_user_to_kernel(new_m, cur_rot_sel)
                ok, bak, msg = config.write_margins(
                    conn_name, kern_m["left"], kern_m["right"],
                    kern_m["top"], kern_m["bottom"])
                if not ok:
                    _error_dialog(win, msg)
                    return False
                saved_margins = dict(new_m)
                resp = _reboot_or_cancel_dialog(win, "Borders Saved",
                    "Border changes require a reboot to take effect.\n\n"
                    "If the screen is unusable after reboot, mount\n"
                    "the SD card and edit cmdline.txt.")
                if resp == "reboot":
                    subprocess.Popen(["sudo", "reboot"])
                elif resp == "cancel":
                    if bak:
                        config.restore_backup(bak, config.CMDLINE)
                        saved_margins = config.read_margins(conn_name) or saved_margins
                        for e in ("left", "right", "top", "bottom"):
                            margin_spins[e].set_value(saved_margins.get(e, 0))
                return True

            self._fullscreen_margin_editor(
                margin_spins, pw, ph, scale_val, win,
                on_save=_do_save_borders)
        btn_borders.connect("clicked", on_borders_click)
        vbox.pack_start(btn_borders, False, False, 0)

        def on_apply(_):
            if not self._pre_write_check("applying display settings"):
                return
            name = conn_name

            # Get selected values
            ri = combo_res.get_active()
            new_mode = seen_res[ri] if ri >= 0 and ri < len(seen_res) else cur_mode
            si = combo_scale.get_active()
            new_scale = scale_options[si] if si >= 0 and si < len(scale_options) else scale_val
            new_rot = "normal"
            for rv, rb in rot_buttons.items():
                if rb.get_active():
                    new_rot = rv
                    break

            # Get margin values
            new_margins = {e: margin_spins[e].get_value_as_int()
                           for e in ("left", "right", "top", "bottom")}

            # Auto-rescale margins if resolution changed
            res_changed = new_mode != cur_mode
            if res_changed and any(v > 0 for v in new_margins.values()):
                old_m = re.match(r"(\d+)x(\d+)", cur_mode)
                new_m = re.match(r"(\d+)x(\d+)", new_mode)
                if old_m and new_m:
                    old_w, old_h = int(old_m.group(1)), int(old_m.group(2))
                    new_w, new_h = int(new_m.group(1)), int(new_m.group(2))
                    if old_w != new_w or old_h != new_h:
                        nl, nr, nt, nb = matrix.rescale_margins(
                            old_w, old_h, new_w, new_h,
                            new_margins["left"], new_margins["right"],
                            new_margins["top"], new_margins["bottom"])
                        if _confirm_dialog(win, "Rescale Borders?",
                                f"Resolution changed from {cur_mode} to {new_mode}.\n\n"
                                f"Current borders: L:{new_margins['left']} R:{new_margins['right']} "
                                f"T:{new_margins['top']} B:{new_margins['bottom']}\n"
                                f"Rescaled borders: L:{nl} R:{nr} T:{nt} B:{nb}\n\n"
                                f"Rescale borders proportionally?\n\n"
                                f"A reboot is needed for border changes\n"
                                f"to take effect."):
                            new_margins = {"left": nl, "right": nr, "top": nt, "bottom": nb}
                            for e in ("left", "right", "top", "bottom"):
                                margin_spins[e].set_value(new_margins[e])

            margins_changed = new_margins != saved_margins
            display_changed = (new_mode != cur_mode or
                               abs(new_scale - scale_val) > 0.01 or
                               new_rot != rotation)

            # Write compositor config (rotation, scale, mode)
            comp = config.detect_compositor()
            bak_comp = None       # backup path for revert
            bak_comp_dest = ""    # original file to restore to
            old_rot_val = rotation
            old_sc_val = scale_val
            if comp == "labwc":
                old_profile = config.read_kanshi_profile(name)
                if old_profile:
                    old_rot_val = old_profile.get("transform", rotation)
                    old_sc_val = old_profile.get("scale", scale_val)
                ok, bak_comp = config.write_kanshi_profile(
                    name, mode=new_mode, transform=new_rot, scale=new_scale)
                bak_comp_dest = config.KANSHI
                if not ok:
                    _error_dialog(win, "Failed to write kanshi config.")
                    return
            elif comp == "wayfire":
                old_section = config.read_wayfire_section(f"output:{name}")
                if old_section:
                    old_rot_val = old_section.get("transform", rotation)
                    try:
                        old_sc_val = float(old_section.get("scale", scale_val))
                    except (ValueError, TypeError):
                        pass
                kv = {"transform": new_rot}
                if new_mode:
                    kv["mode"] = new_mode
                if new_scale:
                    kv["scale"] = str(new_scale)
                ok, bak_comp = config.write_wayfire_section(f"output:{name}", kv)
                bak_comp_dest = config.WAYFIRE

            # Apply rotation/scale/mode live via wlr-randr
            if display_changed:
                ok_live, msg_live = config.apply_display_live(
                    name, transform=new_rot, scale=new_scale, mode=new_mode)

                if ok_live:
                    # Hide settings window so countdown dialog is fully visible
                    self._hide_all_windows()
                    # Force GTK to process the hide before showing dialog
                    while Gtk.events_pending():
                        Gtk.main_iteration()
                    # Countdown confirm — if screen went blank, auto-reverts
                    kept = _countdown_confirm(win,
                        "Confirm Display Settings",
                        "Can you see this dialog?\n\n"
                        "If the screen is unreadable, settings will\n"
                        "automatically revert.",
                        timeout=15)
                    self._show_all_windows()
                    if not kept:
                        # Revert: restore config and apply old settings live
                        if bak_comp:
                            config.restore_backup(bak_comp, bak_comp_dest)
                        config.apply_display_live(
                            name, transform=old_rot_val,
                            scale=old_sc_val, mode=cur_mode)
                        _info_dialog(win, "Reverted",
                            "Display settings reverted to previous values.")
                        return
                else:
                    _error_dialog(win,
                        f"Failed to apply display settings:\n{msg_live}")
                    if bak_comp:
                        config.restore_backup(bak_comp, bak_comp_dest)
                    return

            # Write margins if changed
            bak_margins = None
            if margins_changed:
                kern_m = _margin_user_to_kernel(new_margins, new_rot)
                ok_m, bak_margins, msg_m = config.write_margins(
                    name, kern_m["left"], kern_m["right"],
                    kern_m["top"], kern_m["bottom"])
                if not ok_m:
                    _error_dialog(win, msg_m)
                    return

            # Show result
            if margins_changed:
                resp = _reboot_or_cancel_dialog(win, "Settings Applied",
                    f"Display settings applied for {name}.\n\n"
                    f"Border changes require a reboot to take effect.\n\n"
                    f"If the screen is unusable after reboot, mount\n"
                    f"the SD card and edit cmdline.txt.")
                if resp == "reboot":
                    subprocess.Popen(["sudo", "reboot"])
                elif resp == "cancel":
                    if bak_margins:
                        config.restore_backup(bak_margins, config.CMDLINE)
                    for e in ("left", "right", "top", "bottom"):
                        margin_spins[e].set_value(saved_margins.get(e, 0))
            elif display_changed:
                _info_dialog(win, "Applied",
                    f"Display settings applied for {name}.")
            else:
                _info_dialog(win, "No Changes",
                    "No settings were changed.")

        self._display_apply = on_apply

        # ── Output change handler ──
        def on_output_changed(_):
            nonlocal conn_name, rotation, scale_val, cur_mode
            nonlocal modes, seen_res, wlr_info, pw, ph
            nonlocal kern_margins, user_margins, saved_margins

            idx = combo_out.get_active()
            if idx < 0:
                return
            conn_name = outputs[idx]["name"]

            # Refresh everything for this output
            rotation = config.read_rotation(conn_name) or "normal"
            scale_val = config.read_scale(conn_name) or 1.0
            wlr_info = config.get_wlr_randr_output(conn_name) or {}
            modes = wlr_info.get("modes", [])

            # Update resolution combo
            combo_res.remove_all()
            seen_res = []
            active_res_idx = 0
            cur_mode = outputs[idx].get("resolution") or "unknown"
            for i, md in enumerate(modes):
                res = md["resolution"]
                if res in seen_res:
                    continue
                seen_res.append(res)
                suffix = " (preferred)" if md.get("preferred") else ""
                combo_res.append_text(f"{res}{suffix}")
                if md.get("current"):
                    active_res_idx = len(seen_res) - 1
                    cur_mode = res
            if not seen_res:
                seen_res = [cur_mode]
                combo_res.append_text(cur_mode)
            combo_res.set_active(active_res_idx)

            m2 = re.match(r"(\d+)x(\d+)", cur_mode)
            if m2:
                pw, ph = int(m2.group(1)), int(m2.group(2))

            # Update scale combo
            active_scale_idx = 0
            for i, sv in enumerate(scale_options):
                if abs(sv - scale_val) < 0.01:
                    active_scale_idx = i
            combo_scale.set_active(active_scale_idx)

            # Update rotation
            for rv, rb in rot_buttons.items():
                rb.set_active(rv == rotation)

            # Update margins
            kern_margins = config.read_margins(conn_name) or \
                {"left": 0, "right": 0, "top": 0, "bottom": 0}
            user_margins = _margin_kernel_to_user(kern_margins, rotation)
            saved_margins = user_margins.copy()
            for edge in ("left", "right", "top", "bottom"):
                margin_spins[edge].set_value(user_margins.get(edge, 0))

            _update_info()

        combo_out.connect("changed", on_output_changed)

        # Initial info card update
        _update_info()

        return vbox


    def _fullscreen_margin_editor(self, spins, panel_w, panel_h, scale, parent_win=None, on_save=None):
        """
        Fullscreen 1:1 margin editor.
        Uses a GTK Overlay: DrawingArea for edge bars + green bounding rect,
        real GTK SpinButtons in a centered panel matching the margins dialog.
        """
        # Snapshot active margins (what kernel is currently applying)
        active = {e: spins[e].get_value_as_int() for e in ("left", "right", "top", "bottom")}

        # Hide all settings windows so they can't pop over the fullscreen editor
        self._hide_all_windows()

        fwin = Gtk.Window(type=Gtk.WindowType.TOPLEVEL)
        fwin.set_title("Margin Editor")
        fwin.set_decorated(False)
        fwin.set_can_focus(True)
        fwin.set_keep_above(True)

        display = Gdk.Display.get_default()
        monitor = display.get_primary_monitor() or display.get_monitor(0)
        if monitor:
            geom = monitor.get_geometry()
            fwin.set_default_size(geom.width, geom.height)
        else:
            fwin.set_default_size(1024, 768)
        fwin.fullscreen()

        overlay = Gtk.Overlay()
        fwin.add(overlay)

        # ── Background: DrawingArea for edge bars and bounding rect ──
        da = Gtk.DrawingArea()
        overlay.add(da)

        def draw_editor(widget, cr):
            sw = widget.get_allocated_width()
            sh = widget.get_allocated_height()

            cr.set_source_rgba(0, 0, 0, 0.6)
            cr.paint()

            ml = spins["left"].get_value_as_int()
            mr = spins["right"].get_value_as_int()
            mt = spins["top"].get_value_as_int()
            mb = spins["bottom"].get_value_as_int()

            cr.set_line_width(3)
            for edge, new_val in [("left", ml), ("right", mr), ("top", mt), ("bottom", mb)]:
                delta = new_val - active.get(edge, 0)
                offset = abs(delta) / scale

                if delta > 0:
                    cr.set_source_rgba(1, 0.15, 0.1, 0.5)
                    if edge == "left":    cr.rectangle(0, 0, offset, sh)
                    elif edge == "right": cr.rectangle(sw - offset, 0, offset, sh)
                    elif edge == "top":   cr.rectangle(0, 0, sw, offset)
                    elif edge == "bottom":cr.rectangle(0, sh - offset, sw, offset)
                    cr.fill()
                    cr.set_source_rgb(1, 0.3, 0.2)
                    if edge == "left":    cr.move_to(offset, 0); cr.line_to(offset, sh)
                    elif edge == "right": cr.move_to(sw - offset, 0); cr.line_to(sw - offset, sh)
                    elif edge == "top":   cr.move_to(0, offset); cr.line_to(sw, offset)
                    elif edge == "bottom":cr.move_to(0, sh - offset); cr.line_to(sw, sh - offset)
                    cr.stroke()
                elif delta < 0:
                    cr.set_source_rgb(0.3, 0.6, 1.0)
                    cr.set_line_width(4)
                    if edge == "left":    cr.move_to(0, 0); cr.line_to(0, sh)
                    elif edge == "right": cr.move_to(sw - 1, 0); cr.line_to(sw - 1, sh)
                    elif edge == "top":   cr.move_to(0, 0); cr.line_to(sw, 0)
                    elif edge == "bottom":cr.move_to(0, sh - 1); cr.line_to(sw, sh - 1)
                    cr.stroke()
                    cr.set_line_width(3)

            # Green bounding rectangle
            bound_l = max(0, ml - active.get("left", 0)) / scale
            bound_t = max(0, mt - active.get("top", 0)) / scale
            bound_r = max(0, mr - active.get("right", 0)) / scale
            bound_b = max(0, mb - active.get("bottom", 0)) / scale
            bound_w = sw - bound_l - bound_r
            bound_h = sh - bound_t - bound_b
            if bound_w > 0 and bound_h > 0:
                cr.set_source_rgba(0.2, 1.0, 0.4, 0.8)
                cr.set_line_width(2)
                cr.rectangle(bound_l, bound_t, bound_w, bound_h)
                cr.stroke()
                mark = 18
                cr.set_line_width(3)
                cr.move_to(bound_l, bound_t + mark); cr.line_to(bound_l, bound_t); cr.line_to(bound_l + mark, bound_t)
                cr.stroke()
                cr.move_to(bound_l + bound_w - mark, bound_t); cr.line_to(bound_l + bound_w, bound_t); cr.line_to(bound_l + bound_w, bound_t + mark)
                cr.stroke()
                cr.move_to(bound_l, bound_t + bound_h - mark); cr.line_to(bound_l, bound_t + bound_h); cr.line_to(bound_l + mark, bound_t + bound_h)
                cr.stroke()
                cr.move_to(bound_l + bound_w - mark, bound_t + bound_h); cr.line_to(bound_l + bound_w, bound_t + bound_h); cr.line_to(bound_l + bound_w, bound_t + bound_h - mark)
                cr.stroke()

        da.connect("draw", draw_editor)

        # ── Centered widget panel (matches margins dialog style) ──
        panel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        panel.set_halign(Gtk.Align.CENTER)
        panel.set_valign(Gtk.Align.CENTER)

        # Apply dark theme styling via CSS — force text colors for all child widgets
        css = Gtk.CssProvider()
        css.load_from_data(b"""
            .fs-panel {
                background-color: rgba(20, 20, 20, 0.92);
                border: 1px solid #555;
                border-radius: 10px;
                padding: 16px 20px;
                color: #eee;
            }
            .fs-panel label {
                color: #eee;
            }
            .fs-panel .subtitle-label {
                font-size: 90%;
                color: #bbb;
            }
            .fs-panel spinbutton {
                background: #333;
                color: #fff;
            }
            .fs-panel spinbutton entry {
                background: #333;
                color: #fff;
            }
            .fs-panel button {
                min-height: 28px;
                min-width: 60px;
                background: #444;
                color: #fff;
            }
            .fs-panel button:hover {
                background: #555;
            }
        """)
        Gtk.StyleContext.add_provider_for_screen(
            Gdk.Screen.get_default(), css,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        panel.get_style_context().add_class("fs-panel")

        # Title
        title_lbl = Gtk.Label()
        title_lbl.set_markup("<b>Adjust Borders</b>")
        title_lbl.get_style_context().add_class("title-label")
        panel.pack_start(title_lbl, False, False, 0)

        sub_lbl = Gtk.Label()
        sub_lbl.set_markup(
            f"<small>{panel_w}\u00d7{panel_h} panel, {scale:.0f}x scale</small>")
        sub_lbl.get_style_context().add_class("subtitle-label")
        panel.pack_start(sub_lbl, False, False, 0)

        # Spin buttons in a grid — same layout as margins dialog
        grid = Gtk.Grid(column_spacing=8, row_spacing=6)
        grid.set_halign(Gtk.Align.CENTER)
        grid.set_margin_top(4)
        grid.set_margin_bottom(4)

        fs_spins = {}  # local spins linked to the parent dialog spins
        for r, (edge, label_text) in enumerate([
            ("top", "Top:"), ("bottom", "Bottom:"),
            ("left", "Left:"), ("right", "Right:")
        ]):
            lbl = Gtk.Label(label=label_text)
            lbl.set_halign(Gtk.Align.END)
            adj = Gtk.Adjustment(
                value=spins[edge].get_value_as_int(),
                lower=0, upper=999,
                step_increment=5, page_increment=20)
            spin = Gtk.SpinButton(adjustment=adj, numeric=True, width_chars=5)
            fs_spins[edge] = spin
            grid.attach(lbl, 0, r, 1, 1)
            grid.attach(spin, 1, r, 1, 1)

        panel.pack_start(grid, False, False, 0)

        # Keep parent dialog spins in sync with fullscreen spins
        def _sync_to_parent(edge):
            def _cb(spin):
                spins[edge].set_value(spin.get_value_as_int())
                da.queue_draw()
            return _cb

        for edge, spin in fs_spins.items():
            spin.connect("value-changed", _sync_to_parent(edge))

        # Action buttons
        btn_box = Gtk.Box(spacing=10)
        btn_box.set_halign(Gtk.Align.CENTER)
        btn_box.set_margin_top(4)

        btn_reset = Gtk.Button(label="Reset")
        btn_clear = Gtk.Button(label="Clear All")
        btn_clear.get_style_context().add_class("destructive-action")
        btn_save = Gtk.Button(label="Save")
        btn_cancel = Gtk.Button(label="Cancel")

        def on_reset(_):
            for e in ("left", "right", "top", "bottom"):
                fs_spins[e].set_value(active[e])

        def on_clear(_):
            for e in ("left", "right", "top", "bottom"):
                fs_spins[e].set_value(0)

        def on_save_click(_):
            fwin.destroy()  # close fullscreen first so dialogs aren't behind it
            if on_save:
                on_save()

        def on_cancel(_):
            # Restore original values
            for e in ("left", "right", "top", "bottom"):
                spins[e].set_value(active[e])
            fwin.destroy()

        btn_reset.connect("clicked", on_reset)
        btn_clear.connect("clicked", on_clear)
        btn_save.connect("clicked", on_save_click)
        btn_cancel.connect("clicked", on_cancel)
        btn_save.set_can_focus(True)
        btn_box.pack_start(btn_reset, False, False, 0)
        btn_box.pack_start(btn_clear, False, False, 0)
        btn_box.pack_start(btn_cancel, False, False, 0)
        btn_box.pack_start(btn_save, False, False, 0)
        panel.pack_start(btn_box, False, False, 0)

        # Legend — colored text
        legend = Gtk.Label()
        legend.set_markup(
            '<small>'
            '<span foreground="#ff5a4d">Red</span> = hidden   '
            '<span foreground="#66aaff">Blue</span> = expands   '
            '<span foreground="#4dff80">Green</span> = visible area'
            '</small>')
        legend.set_margin_top(2)
        panel.pack_start(legend, False, False, 0)

        overlay.add_overlay(panel)

        def on_key(widget, event):
            if event.keyval == Gdk.KEY_Escape:
                on_cancel(None)
                return True
            elif event.keyval == Gdk.KEY_Return:
                on_save_click(None)
                return True

        fwin.connect("key-press-event", on_key)

        # Restore all settings windows when fullscreen editor closes
        def on_destroy(widget):
            self._show_all_windows()
        fwin.connect("destroy", on_destroy)

        fwin.show_all()
        btn_save.grab_focus()

    def _build_touchscreen_tab(self, win):
        """Build the Touchscreen tab content."""
        touch_devs = devices.get_touch_devices()
        outputs = devices.get_connected_outputs()

        if not touch_devs:
            lbl = Gtk.Label(label="No touch devices detected.\n\n"
                            "Connect a touchscreen and reopen settings.")
            lbl.set_line_wrap(True)
            return lbl
        if not outputs:
            lbl = Gtk.Label(label="No connected display outputs.")
            return lbl

        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        vbox.set_margin_start(12)
        vbox.set_margin_end(12)
        vbox.set_margin_top(14)
        vbox.set_margin_bottom(8)

        # ── Device selectors ──
        grid_sel = Gtk.Grid(column_spacing=8, row_spacing=4)
        grid_sel.attach(Gtk.Label(label="Touch device:", xalign=0), 0, 0, 1, 1)
        combo_touch = Gtk.ComboBoxText()
        for d in touch_devs:
            combo_touch.append_text(d["name"])
        combo_touch.set_active(0)
        grid_sel.attach(combo_touch, 1, 0, 1, 1)

        grid_sel.attach(Gtk.Label(label="Map to display:", xalign=0), 0, 1, 1, 1)
        combo_output = Gtk.ComboBoxText()
        for o in outputs:
            res = o.get("resolution", "?")
            rot = config.read_rotation(o["name"]) or "normal"
            rot_lbl = {"normal": "0\u00b0", "90": "90\u00b0", "180": "180\u00b0",
                       "270": "270\u00b0"}.get(rot, rot)
            sc = config.read_scale(o["name"]) or 1.0
            combo_output.append_text(
                f'{o["name"]} \u2014 {res} @ {sc:.10g}x, {rot_lbl}')
        combo_output.set_active(0)
        grid_sel.attach(combo_output, 1, 1, 1, 1)
        vbox.pack_start(grid_sel, False, False, 0)

        # ── State tracking ──
        cur_dev = touch_devs[0]
        cur_conn = outputs[0]["name"]
        cur_rot_str = config.read_rotation(cur_conn) or "normal"
        cur_rot = int(cur_rot_str) if cur_rot_str.isdigit() else 0

        def _get_screen_res(conn):
            for o in outputs:
                if o["name"] == conn and o.get("resolution"):
                    m = re.match(r"(\d+)x(\d+)", o["resolution"])
                    if m:
                        return int(m.group(1)), int(m.group(2))
            return 2048, 1536

        screen_w, screen_h = _get_screen_res(cur_conn)

        cal = config.read_calibration()
        cal_name = cal[0] if cal else None
        cur_matrix = cal[1] if cal else None

        # ── Status line ──
        status_label = Gtk.Label()
        status_label.set_xalign(0)
        status_label.set_line_wrap(True)
        vbox.pack_start(status_label, False, False, 2)

        def _update_info():
            mapping = config.read_touch_mapping(cur_dev["name"])
            map_str = mapping or "not mapped"
            if cal and cal[0] == cur_dev["name"]:
                st = '<span foreground="#4caf50">Calibrated</span>'
            else:
                st = '<span foreground="#ff9800">Not calibrated</span>'
            status_label.set_markup(
                f"<small><b>Mapping:</b> {map_str}  |  {st}</small>")

        # ── Calibrate button ──
        btn_cal = Gtk.Button(label="Calibrate touchscreen")
        vbox.pack_start(btn_cal, False, False, 2)

        def on_calibrate(_):
            path = cur_dev.get("kernel")
            if not path:
                _error_dialog(win, "Cannot determine device path.")
                return
            if not os.access(path, os.R_OK) and os.getuid() != 0:
                _error_dialog(win,
                    f"Cannot read {path}.\n\n"
                    "Add your user to the 'input' group:\n"
                    "  sudo usermod -aG input $USER\n"
                    "Then log out and back in.")
                return
            self._hide_all_windows()

            def _on_cal_done():
                nonlocal cal, cal_name, cur_matrix
                cal = config.read_calibration()
                cal_name = cal[0] if cal else None
                cur_matrix = cal[1] if cal else None
                _update_info()
                _update_fine_tune()
                self._show_all_windows()

            self._run_calibration_overlay(
                cur_dev, cur_conn, cur_rot, on_done=_on_cal_done)

        btn_cal.connect("clicked", on_calibrate)

        # ── Fine-tune ──
        ft_frame = Gtk.Frame(label=" Fine-tune ")
        ft_grid = Gtk.Grid(column_spacing=6, row_spacing=3)
        ft_grid.set_margin_start(8)
        ft_grid.set_margin_end(8)
        ft_grid.set_margin_top(4)
        ft_grid.set_margin_bottom(4)

        def _matrix_to_pixel():
            if cur_matrix:
                return (cur_matrix[2] * screen_w, cur_matrix[5] * screen_h,
                        abs(cur_matrix[0]) * 100, abs(cur_matrix[4]) * 100)
            return (0, 0, 100, 100)

        px_x, px_y, sc_x, sc_y = _matrix_to_pixel()

        ft_labels = ["H shift:", "V shift:", "H scale:", "V scale:"]
        ft_values = [px_x, px_y, sc_x, sc_y]
        ft_ranges = [(-500, 2500, 1, 5), (-500, 2500, 1, 5),
                     (50, 200, 0.5, 2), (50, 200, 0.5, 2)]
        ft_units = ["px", "px", "%", "%"]
        ft_nudge = [5, 5, 0.5, 0.5]
        ft_spins = []

        for i, (label, val, (lo, hi, step, page), unit, nudge) in enumerate(
                zip(ft_labels, ft_values, ft_ranges, ft_units, ft_nudge)):
            ft_grid.attach(Gtk.Label(label=label, xalign=1), 0, i, 1, 1)
            adj = Gtk.Adjustment(value=val, lower=lo, upper=hi,
                                 step_increment=step, page_increment=page)
            sp = Gtk.SpinButton(adjustment=adj, digits=1, width_chars=7)
            sp.set_numeric(True)
            ft_spins.append(sp)
            ft_grid.attach(sp, 1, i, 1, 1)
            ft_grid.attach(Gtk.Label(label=unit), 2, i, 1, 1)

            def make_nudge(spin, delta):
                def cb(_):
                    spin.set_value(spin.get_value() + delta)
                return cb
            bm = Gtk.Button(label=f"\u2212{nudge:g}")
            bp = Gtk.Button(label=f"+{nudge:g}")
            bm.connect("clicked", make_nudge(sp, -nudge))
            bp.connect("clicked", make_nudge(sp, nudge))
            nb = Gtk.Box(spacing=2)
            nb.pack_start(bm, False, False, 0)
            nb.pack_start(bp, False, False, 0)
            ft_grid.attach(nb, 3, i, 1, 1)

        spin_px_x, spin_px_y, spin_sx, spin_sy = ft_spins
        ft_frame.add(ft_grid)
        vbox.pack_start(ft_frame, False, False, 2)

        def _update_fine_tune():
            nonlocal px_x, px_y, sc_x, sc_y
            px_x, px_y, sc_x, sc_y = _matrix_to_pixel()
            spin_px_x.set_value(px_x)
            spin_px_y.set_value(px_y)
            spin_sx.set_value(sc_x)
            spin_sy.set_value(sc_y)

        def _build_matrix():
            new_sx = spin_sx.get_value() / 100.0
            new_sy = spin_sy.get_value() / 100.0
            if cur_matrix:
                if cur_matrix[0] < 0:
                    new_sx = -new_sx
                if cur_matrix[4] < 0:
                    new_sy = -new_sy
            new_tx = spin_px_x.get_value() / screen_w
            new_ty = spin_px_y.get_value() / screen_h
            return [new_sx, 0.0, new_tx, 0.0, new_sy, new_ty, 0.0, 0.0, 1.0]

        # ── Clear calibration ──
        btn_clear = Gtk.Button(label="Clear Calibration")
        btn_clear.get_style_context().add_class("destructive-action")

        def on_clear(_):
            if _confirm_dialog(win, "Clear Calibration?",
                    "This will delete the udev calibration rule.\n"
                    "Touch input will revert to raw uncalibrated mapping."):
                config.remove_calibration()
                nonlocal cal, cal_name, cur_matrix
                cal = None
                cal_name = None
                cur_matrix = None
                _update_info()
                _update_fine_tune()

        btn_clear.connect("clicked", on_clear)
        vbox.pack_start(btn_clear, False, False, 0)

        def on_apply_ts(_):
            nonlocal cal, cal_name, cur_matrix
            if not self._pre_write_check("applying touchscreen settings"):
                return
            # Save touch-to-output mapping
            config.write_touch_mapping(cur_dev["name"], cur_conn)
            # Only write calibration if we have a matrix
            if not cur_matrix:
                _info_dialog(win, "Mapping Saved",
                    f"Touch device mapped to {cur_conn}.\n\n"
                    f"Run 'Calibrate touchscreen' to set up\n"
                    f"the calibration matrix.")
                return
            mat = _build_matrix()
            ok, bak, msg = config.write_calibration(cur_dev["name"], mat)
            if not ok:
                _error_dialog(win, msg)
                return
            event_name = devices.get_event_name(cur_dev)
            if event_name:
                ok2, msg2 = config.apply_calibration_live(event_name)
            else:
                ok2, msg2 = False, "Cannot determine event name"
            cal = config.read_calibration()
            cal_name = cal[0] if cal else None
            cur_matrix = cal[1] if cal else None
            _update_info()
            if ok2:
                _info_dialog(win, "Applied",
                    f"Touchscreen calibration applied.\n{msg2}")
            else:
                _info_dialog(win, "Saved",
                    f"Calibration saved but live apply failed:\n{msg2}\n\n"
                    f"A reboot may be needed.")

        self._touch_apply = on_apply_ts

        # ── Combo change handlers ──
        def on_touch_changed(_):
            nonlocal cur_dev, cal, cal_name, cur_matrix
            idx = combo_touch.get_active()
            if idx >= 0:
                cur_dev = touch_devs[idx]
                cal = config.read_calibration()
                cal_name = cal[0] if cal else None
                cur_matrix = cal[1] if cal else None
                _update_info()
                _update_fine_tune()

        def on_output_changed(_):
            nonlocal cur_conn, cur_rot_str, cur_rot, screen_w, screen_h
            idx = combo_output.get_active()
            if idx >= 0:
                cur_conn = outputs[idx]["name"]
                cur_rot_str = config.read_rotation(cur_conn) or "normal"
                cur_rot = int(cur_rot_str) if cur_rot_str.isdigit() else 0
                screen_w, screen_h = _get_screen_res(cur_conn)
                _update_info()
                _update_fine_tune()

        combo_touch.connect("changed", on_touch_changed)
        combo_output.connect("changed", on_output_changed)

        _update_info()

        return vbox


    def _run_calibration_overlay(self, dev, conn, rotation, on_done=None):
        """Fullscreen overlay with crosshair targets."""
        self._calibration_on_done = on_done
        win = Gtk.Window(type=Gtk.WindowType.TOPLEVEL)
        win.set_title("Calibration")
        win.set_decorated(False)
        win.set_app_paintable(True)
        win.set_can_focus(True)
        win.set_keep_above(True)

        # Size to full screen — works on both labwc and Wayfire
        display = Gdk.Display.get_default()
        monitor = display.get_primary_monitor() or display.get_monitor(0)
        if monitor:
            geom = monitor.get_geometry()
            win.set_default_size(geom.width, geom.height)
        else:
            win.set_default_size(1024, 768)
        win.fullscreen()

        da = Gtk.DrawingArea()
        win.add(da)

        INSET = 30  # pixels from screen edge (small for scale=2 screens)
        points = []          # captured (norm_x, norm_y)
        state = {"idx": 0, "screen_w": 1024, "screen_h": 768, "cancelled": False}

        def get_targets(w, h):
            return [
                (INSET, INSET, "Top-Left"),
                (w - INSET, INSET, "Top-Right"),
                (w - INSET, h - INSET, "Bottom-Right"),
                (INSET, h - INSET, "Bottom-Left"),
            ]

        def draw(widget, cr):
            w = widget.get_allocated_width()
            h = widget.get_allocated_height()
            state["screen_w"] = w
            state["screen_h"] = h
            # Black background
            cr.set_source_rgb(0, 0, 0)
            cr.paint()

            targets = get_targets(w, h)
            idx = state["idx"]

            if idx >= len(targets):
                cr.set_source_rgb(0.3, 1, 0.3)
                cr.select_font_face("Sans", 0, 1)
                cr.set_font_size(24)
                cr.move_to(w / 2 - 120, h / 2)
                cr.show_text("Calibration Complete!")
                return

            tx, ty, label = targets[idx]

            # Crosshair
            cr.set_source_rgb(1, 0.3, 0.2)
            cr.set_line_width(2)
            cr.move_to(tx - 20, ty); cr.line_to(tx + 20, ty); cr.stroke()
            cr.move_to(tx, ty - 20); cr.line_to(tx, ty + 20); cr.stroke()
            cr.arc(tx, ty, 8, 0, 6.3)
            cr.stroke()
            cr.arc(tx, ty, 2, 0, 6.3)
            cr.fill()

            # Instructions
            cr.set_source_rgb(1, 1, 1)
            cr.select_font_face("Sans", 0, 0)
            cr.set_font_size(16)
            msg = f"Touch the {label} crosshair  ({idx + 1}/4)"
            cr.move_to(w / 2 - 150, h / 2)
            cr.show_text(msg)

            cr.set_source_rgb(0.5, 0.5, 0.5)
            cr.set_font_size(12)
            cr.move_to(w / 2 - 80, h - 20)
            cr.show_text("Press Escape to cancel")

        da.connect("draw", draw)

        def on_key(widget, event):
            if event.keyval == Gdk.KEY_Escape:
                state["cancelled"] = True
                win.destroy()
                return True

        win.connect("key-press-event", on_key)

        # Fire on_done callback when overlay closes (escape, completion, or error)
        def _on_overlay_destroy(widget):
            cb = getattr(self, '_calibration_on_done', None)
            if cb:
                self._calibration_on_done = None
                GLib.idle_add(cb)
        win.connect("destroy", _on_overlay_destroy)

        win.show_all()

        # Background capture thread
        def capture():
            try:
                path = dev["kernel"]
                for idx, nx, ny in touch_capture.capture_points(path, 4, timeout=30):
                    if state["cancelled"]:
                        return
                    points.append((nx, ny))
                    state["idx"] = idx + 1
                    GLib.idle_add(da.queue_draw)

                if state["cancelled"]:
                    return

                # Deduplicate points — some touch controllers report each touch twice
                deduped = []
                for p in points:
                    if not deduped or abs(p[0] - deduped[-1][0]) > 0.005 or abs(p[1] - deduped[-1][1]) > 0.005:
                        deduped.append(p)

                if len(deduped) < 4:
                    GLib.idle_add(lambda: _error_dialog(None,
                        f"Only {len(deduped)} unique points captured (need 4).\n"
                        f"Raw points: {len(points)}"))
                    return

                # Compute target positions in normalized screen coordinates
                sw = state["screen_w"]
                sh = state["screen_h"]
                target_tl = (INSET / sw, INSET / sh)
                target_br = (1.0 - INSET / sw, 1.0 - INSET / sh)

                GLib.idle_add(lambda: self._finish_calibration(
                    win, dev, conn, rotation, deduped, target_tl, target_br))
            except Exception as e:
                GLib.idle_add(lambda: (_error_dialog(None, str(e)), win.destroy()))

        threading.Thread(target=capture, daemon=True).start()

    def _finish_calibration(self, overlay, dev, conn, rotation, pts, target_tl, target_br):
        overlay.destroy()
        if len(pts) < 4:
            _error_dialog(None, "Not enough points captured.")
            return

        # Use TL (index 0) and BR (index 2) raw touch coordinates
        raw_tl = pts[0]
        raw_br = pts[2]

        try:
            mat = matrix.from_4point(raw_tl, raw_br, target_tl, target_br, rotation)
        except ValueError as e:
            _error_dialog(None, str(e))
            return

        desc = matrix.describe(mat)
        if _confirm_dialog(None, "Apply Calibration?",
                f"Computed matrix:\n{matrix.fmt(mat, 4)}\n\n{desc}\n\n"
                f"Raw TL=({raw_tl[0]:.3f},{raw_tl[1]:.3f}) "
                f"BR=({raw_br[0]:.3f},{raw_br[1]:.3f})\n"
                f"Targets TL=({target_tl[0]:.3f},{target_tl[1]:.3f}) "
                f"BR=({target_br[0]:.3f},{target_br[1]:.3f})"):
            self._write_and_apply(dev, conn, mat)

    def _write_and_apply(self, dev, conn, mat):
        if not self._pre_write_check("applying touchscreen calibration"):
            return
        ok, bak, msg = config.write_calibration(dev["name"], mat)
        if not ok:
            _error_dialog(None, msg)
            return

        # Save reference margins (what was active during calibration)
        active_m = config.read_active_margins(conn)
        if active_m:
            config.save_calibration_reference(conn, active_m)

        # Map device to output via compositor-aware function
        config.write_touch_mapping(dev["name"], conn)

        # Offer restart options
        dlg = Gtk.Dialog(title="Calibration Saved", parent=None, flags=0)
        dlg.set_default_size(380, 160)
        dlg.set_position(Gtk.WindowPosition.CENTER)
        dlg.set_keep_above(True)

        box = dlg.get_content_area()
        box.set_spacing(8)
        box.set_margin_start(16)
        box.set_margin_end(16)
        box.set_margin_top(12)

        lbl = Gtk.Label()
        lbl.set_markup(
            "Calibration rule saved.\n\n"
            "A <b>reboot</b> is required for the\n"
            "new calibration to take effect."
        )
        lbl.set_line_wrap(True)
        lbl.set_xalign(0)
        box.pack_start(lbl, False, False, 0)

        btn_later = dlg.add_button("Reboot Later", Gtk.ResponseType.CANCEL)
        btn_now = dlg.add_button("Reboot Now", Gtk.ResponseType.OK)
        btn_now.get_style_context().add_class("suggested-action")

        dlg.show_all()
        resp = dlg.run()
        dlg.destroy()

        if resp == Gtk.ResponseType.OK:
            subprocess.Popen(["sudo", "reboot"])

    def _gather_status(self):
        compositor = config.detect_compositor()
        lines = [f"═══ Compositor ═══"]
        lines.append(f"  Active: {compositor}")
        wl = config.detect_wayland_display()
        lines.append(f"  WAYLAND_DISPLAY: {wl or '(not found)'}")

        lines.append("\n═══ Config.txt Validation ═══")
        for sev, msg in config.validate_config_txt():
            lines.append(f"  [{sev.upper():7s}] {msg}")

        lines.append("\n═══ Display Outputs ═══")
        for o in devices.get_drm_outputs():
            st = "CONNECTED" if o["connected"] else "disconnected"
            lines.append(f"  {o['name']:20s} {st:12s} {o.get('resolution','?')}")
        # Show wlr-randr info for connected outputs
        for o in devices.get_connected_outputs():
            info = config.get_wlr_randr_output(o["name"])
            if info:
                parts = []
                if info.get("current_mode"):
                    parts.append(f"mode={info['current_mode']}")
                if info.get("transform"):
                    parts.append(f"transform={info['transform']}")
                if info.get("scale"):
                    parts.append(f"scale={info['scale']}")
                if parts:
                    lines.append(f"    wlr-randr: {' '.join(parts)}")

        lines.append("\n═══ Current Margins ═══")
        for o in devices.get_connected_outputs():
            m = config.read_margins(o["name"])
            if m:
                lines.append(f"  {o['name']}: L={m['left']} R={m['right']} T={m['top']} B={m['bottom']}")
            else:
                lines.append(f"  {o['name']}: No margins")

        lines.append("\n═══ Touch Devices ═══")
        for d in devices.get_touch_devices():
            lines.append(f"  {d['name']}")
            lines.append(f"    Path: {d.get('kernel','?')}")
            lines.append(f"    Calibration: {d.get('calibration','not reported')}")

        lines.append("\n═══ Calibration Rule ═══")
        cal = config.read_calibration()
        if cal:
            name, mat = cal
            lines.append(f"  Device: {name}")
            lines.append(f"  Matrix: {matrix.fmt(mat, 4)}")
            lines.append(f"  {matrix.describe(mat)}")
        else:
            lines.append("  No rule found")

        lines.append("\n═══ Kernel Cmdline ═══")
        try:
            cmd = config._read_file("/proc/cmdline").strip()
            vm = re.search(r"video=\S+", cmd)
            lines.append(f"  {vm.group(0)}" if vm else "  No video= parameter")
        except OSError:
            lines.append("  Cannot read /proc/cmdline")

        # Show compositor-specific configs
        if compositor == "labwc" or os.path.isfile(config.KANSHI):
            lines.append("\n═══ Kanshi Config (labwc) ═══")
            if os.path.isfile(config.KANSHI):
                content = config._read_file(config.KANSHI).strip()
                if content:
                    for l in content.splitlines():
                        lines.append(f"  {l}")
                else:
                    lines.append("  (empty)")
            else:
                lines.append("  (not found)")

        if compositor == "labwc" or os.path.isfile(config.LABWC_RC):
            lines.append("\n═══ labwc rc.xml Touch Mappings ═══")
            if os.path.isfile(config.LABWC_RC):
                content = config._read_file(config.LABWC_RC)
                for m in re.finditer(r'<touch\s+[^>]*deviceName="([^"]+)"[^>]*mapToOutput="([^"]+)"', content):
                    lines.append(f"  {m.group(1)} → {m.group(2)}")
                if not re.search(r'<touch\s', content):
                    lines.append("  (no touch mappings)")
            else:
                lines.append("  (not found)")

        if compositor == "wayfire" or os.path.isfile(config.WAYFIRE):
            lines.append("\n═══ Wayfire Config ═══")
            for section_prefix in ("output:", "input-device:"):
                for o in devices.get_connected_outputs():
                    sec = f"{section_prefix}{o['name']}"
                    vals = config.read_wayfire_section(sec)
                    if vals:
                        lines.append(f"  [{sec}]")
                        for k, v in vals.items():
                            lines.append(f"    {k} = {v}")
                for d in devices.get_touch_devices():
                    sec = f"input-device:{d['name']}"
                    vals = config.read_wayfire_section(sec)
                    if vals:
                        lines.append(f"  [{sec}]")
                        for k, v in vals.items():
                            lines.append(f"    {k} = {v}")

        lines.append("\n═══ Conflict Scan ═══")
        conflicts = config.preflight_scan()
        if conflicts:
            for c in conflicts:
                sev = c["severity"].upper()
                lines.append(f"  [{sev:8s}] {c['path']}")
                lines.append(f"             {c['description']}")
                if c.get("content_preview"):
                    preview = c["content_preview"][:60]
                    lines.append(f"             → {preview}")
        else:
            lines.append("  No conflicts detected")

        return "\n".join(lines)

    # ── Backup History ────────────────────────────────────

    def _on_backup_history(self, _):
        """Show timestamped backups with restore option."""
        backups = config.list_backups()

        win = Gtk.Window(title="Settings History",
                         default_width=540, default_height=400)
        win.set_position(Gtk.WindowPosition.CENTER)
        win.set_type_hint(Gdk.WindowTypeHint.DIALOG)

        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        vbox.set_margin_start(14)
        vbox.set_margin_end(14)
        vbox.set_margin_top(10)
        vbox.set_margin_bottom(10)
        win.add(vbox)

        if not backups:
            vbox.pack_start(Gtk.Label(
                label="No backups found.\n\n"
                "Backups are created automatically each time\n"
                "you save margins, rotation, or calibration."
            ), True, True, 0)
            btn_close = Gtk.Button(label="Close")
            btn_close.connect("clicked", lambda _: win.destroy())
            vbox.pack_end(btn_close, False, False, 0)
            win.show_all()
            return

        lbl = Gtk.Label()
        lbl.set_markup(
            f"<b>{len(backups)} backup(s) found</b>  — "
            "select one and click Restore to revert to that snapshot"
        )
        lbl.set_xalign(0)
        lbl.set_line_wrap(True)
        vbox.pack_start(lbl, False, False, 0)

        # Scrollable list
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)

        store = Gtk.ListStore(str, str, str, str, str)  # label, timestamp, age, path, original
        for b in backups:
            # Format timestamp as "2026-03-25 01:40:06" for readability
            ts_display = b["ts_str"]
            if len(ts_display) == 14:
                ts_display = (f"{ts_display[:4]}-{ts_display[4:6]}-{ts_display[6:8]} "
                              f"{ts_display[8:10]}:{ts_display[10:12]}:{ts_display[12:14]}")
            store.append([
                b["label"],
                ts_display,
                b["age_str"],
                b["path"],
                b["original"],
            ])

        tree = Gtk.TreeView(model=store)
        tree.set_headers_visible(True)

        for i, title in enumerate(["File", "Timestamp", "Age"]):
            renderer = Gtk.CellRendererText()
            col = Gtk.TreeViewColumn(title, renderer, text=i)
            if i == 0:
                col.set_min_width(140)
            tree.append_column(col)

        tree.get_selection().set_mode(Gtk.SelectionMode.SINGLE)
        scroll.add(tree)
        vbox.pack_start(scroll, True, True, 0)

        # Preview of selected backup
        preview_frame = Gtk.Frame(label=" Preview ")
        preview_label = Gtk.Label(label="(select a backup above)")
        preview_label.set_selectable(True)
        preview_label.set_xalign(0)
        preview_label.set_line_wrap(True)
        preview_sw = Gtk.ScrolledWindow()
        preview_sw.set_min_content_height(80)
        preview_sw.set_max_content_height(80)
        preview_sw.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        preview_sw.add(preview_label)
        preview_frame.add(preview_sw)
        vbox.pack_start(preview_frame, False, False, 0)

        def on_selection_changed(sel):
            model, it = sel.get_selected()
            if it:
                path = model[it][3]
                try:
                    content = config._read_file(path)[:500]
                    if len(content) >= 500:
                        content += "\n…(truncated)"
                    preview_label.set_text(content)
                except OSError:
                    preview_label.set_text("(cannot read file)")
            else:
                preview_label.set_text("(select a backup above)")

        tree.get_selection().connect("changed", on_selection_changed)

        # Buttons
        hbox = Gtk.Box(spacing=8)
        hbox.set_halign(Gtk.Align.END)

        btn_restore = Gtk.Button(label="Restore Selected")
        btn_restore.get_style_context().add_class("suggested-action")
        btn_close = Gtk.Button(label="Close")

        def on_restore(_):
            model, it = tree.get_selection().get_selected()
            if not it:
                _info_dialog(win, "No Selection", "Select a backup to restore.")
                return

            bak_path = model[it][3]
            original = model[it][4]
            ts = model[it][1]
            label = model[it][0]

            if not _confirm_dialog(win, "Confirm Restore",
                    f"Restore {label} from {ts}?\n\n"
                    f"Backup: {bak_path}\n"
                    f"Target: {original}\n\n"
                    "A new backup of the current file will be\n"
                    "created before restoring."):
                return

            # Backup current before restoring
            if os.path.isfile(original):
                if original in (config.WAYFIRE, config.KANSHI, config.LABWC_RC):
                    shutil.copy2(original,
                        f"{original}.bak.{config._stamp()}")
                else:
                    config._sudo_backup(original)

            ok = config.restore_backup(bak_path, original)
            if ok:
                # Determine if live-apply is possible
                msgs = [f"Restored {label} from {ts}."]
                if original == config.UDEV_RULE:
                    # Re-trigger udev for immediate effect
                    touch_devs = devices.get_touch_devices()
                    if touch_devs:
                        ev = devices.get_event_name(touch_devs[0])
                        if ev:
                            config.apply_calibration_live(ev)
                            msgs.append("Calibration applied live.")
                elif original == config.CMDLINE:
                    msgs.append("Reboot required for margin changes.")
                elif original in (config.WAYFIRE, config.KANSHI, config.LABWC_RC):
                    msgs.append("Session restart may be required.")
                _info_dialog(win, "Restored", "\n".join(msgs))
            else:
                _error_dialog(win, f"Failed to restore {bak_path}")

        btn_restore.connect("clicked", on_restore)
        btn_close.connect("clicked", lambda _: win.destroy())
        hbox.pack_start(btn_close, False, False, 0)
        hbox.pack_start(btn_restore, False, False, 0)
        vbox.pack_end(hbox, False, False, 0)

        win.show_all()

    # ── Quit ─────────────────────────────────────────────

    def _on_uninstall(self, _):
        dlg = Gtk.Dialog(title="Uninstall Display Calibrator?", parent=None, flags=0)
        dlg.set_position(Gtk.WindowPosition.CENTER)
        dlg.set_keep_above(True)
        dlg.set_default_size(400, -1)

        box = dlg.get_content_area()
        box.set_spacing(8)
        box.set_margin_start(16)
        box.set_margin_end(16)
        box.set_margin_top(12)
        box.set_margin_bottom(8)

        lbl = Gtk.Label()
        lbl.set_markup(
            "This will remove the application, tray icon,\n"
            "menu entry, and autostart.\n")
        lbl.set_xalign(0)
        lbl.set_line_wrap(True)
        box.pack_start(lbl, False, False, 0)

        # Cleanup options
        has_cal = os.path.isfile(config.UDEV_RULE)
        has_margins = False
        try:
            cmdline = config._read_file(config.CMDLINE)
            has_margins = "margin_" in cmdline
        except Exception:
            pass

        chk_cal = None
        chk_margins = None

        if has_cal or has_margins:
            sep = Gtk.Separator()
            box.pack_start(sep, False, False, 4)

            opt_lbl = Gtk.Label()
            opt_lbl.set_markup("<b>Also remove settings:</b>")
            opt_lbl.set_xalign(0)
            box.pack_start(opt_lbl, False, False, 0)

            if has_cal:
                chk_cal = Gtk.CheckButton(label="Touchscreen calibration")
                box.pack_start(chk_cal, False, False, 0)

            if has_margins:
                chk_margins = Gtk.CheckButton(label="Display border margins")
                box.pack_start(chk_margins, False, False, 0)

        dlg.add_button("Cancel", Gtk.ResponseType.CANCEL)
        dlg.add_button("Uninstall", Gtk.ResponseType.OK)

        dlg.show_all()
        resp = dlg.run()
        remove_cal = chk_cal and chk_cal.get_active() if chk_cal else False
        remove_margins = chk_margins and chk_margins.get_active() if chk_margins else False
        dlg.destroy()

        if resp != Gtk.ResponseType.OK:
            return

        install_script = os.path.join(SCRIPT_DIR, "install.sh")
        if not os.path.isfile(install_script):
            install_script = "/opt/display-calibrator/install.sh"
        if not os.path.isfile(install_script):
            _error_dialog(None, "Cannot find install.sh for uninstall.")
            return

        # Run base uninstall
        try:
            subprocess.run(
                ["sudo", install_script, "--uninstall"],
                stdin=subprocess.DEVNULL, timeout=15)
        except (OSError, subprocess.TimeoutExpired) as e:
            _error_dialog(None, f"Uninstall failed: {e}")
            return

        # Remove optional settings
        if remove_cal:
            subprocess.run(["sudo", "rm", "-f", config.UDEV_RULE],
                           capture_output=True)
            subprocess.run(["sudo", "rm", "-f", config.CAL_REF],
                           capture_output=True)

        if remove_margins:
            for f in ["/boot/firmware/cmdline.txt", "/boot/cmdline.txt"]:
                if os.path.isfile(f):
                    subprocess.run(
                        ["sudo", "sed", "-i", "s/ *video=[^ ]*//", f],
                        capture_output=True)

        Gtk.main_quit()

    def _on_quit(self, _):
        if _confirm_dialog(None, "Quit Display Calibrator?",
                "To relaunch, open the application menu:\n"
                "Preferences → Display Calibrator\n\n"
                "The tray also restarts automatically on next login."):
            Gtk.main_quit()


# ═══════════════════════════════════════════════════════════════
# Dialog helpers
# ═══════════════════════════════════════════════════════════════

def _info_dialog(parent, title, msg=""):
    dlg = Gtk.MessageDialog(parent=parent, flags=0,
            message_type=Gtk.MessageType.INFO,
            buttons=Gtk.ButtonsType.OK, text=title)
    if msg:
        dlg.format_secondary_text(msg)
    dlg.run()
    dlg.destroy()


def _error_dialog(parent, msg):
    dlg = Gtk.MessageDialog(parent=parent, flags=0,
            message_type=Gtk.MessageType.ERROR,
            buttons=Gtk.ButtonsType.OK, text="Error")
    dlg.format_secondary_text(msg)
    dlg.run()
    dlg.destroy()


def _confirm_dialog(parent, title, msg):
    dlg = Gtk.MessageDialog(parent=parent, flags=0,
            message_type=Gtk.MessageType.QUESTION,
            buttons=Gtk.ButtonsType.YES_NO, text=title)
    dlg.format_secondary_text(msg)
    resp = dlg.run()
    dlg.destroy()
    return resp == Gtk.ResponseType.YES


def _reboot_dialog(parent, title, msg):
    """Show a dialog with Reboot Now / Reboot Later. Returns 'reboot' or 'later'."""
    dlg = Gtk.Dialog(title=title, parent=parent, flags=0)
    dlg.set_default_size(340, 140)
    dlg.set_position(Gtk.WindowPosition.CENTER)
    dlg.set_keep_above(True)

    box = dlg.get_content_area()
    box.set_spacing(8)
    box.set_margin_start(16)
    box.set_margin_end(16)
    box.set_margin_top(12)

    lbl = Gtk.Label(label=msg)
    lbl.set_line_wrap(True)
    lbl.set_xalign(0)
    box.pack_start(lbl, False, False, 0)

    dlg.add_button("Reboot Later", Gtk.ResponseType.CANCEL)
    btn = dlg.add_button("Reboot Now", Gtk.ResponseType.OK)
    btn.get_style_context().add_class("suggested-action")

    dlg.show_all()
    resp = dlg.run()
    dlg.destroy()
    return "reboot" if resp == Gtk.ResponseType.OK else "later"


def _reboot_or_cancel_dialog(parent, title, msg):
    """Show a dialog with Reboot Now / Reboot Later / Cancel.
    Returns 'reboot', 'later', or 'cancel'."""
    dlg = Gtk.Dialog(title=title, parent=parent, flags=0)
    dlg.set_default_size(380, 160)
    dlg.set_position(Gtk.WindowPosition.CENTER)
    dlg.set_keep_above(True)

    box = dlg.get_content_area()
    box.set_spacing(8)
    box.set_margin_start(16)
    box.set_margin_end(16)
    box.set_margin_top(12)

    lbl = Gtk.Label(label=msg)
    lbl.set_line_wrap(True)
    lbl.set_xalign(0)
    box.pack_start(lbl, False, False, 0)

    btn_cancel = dlg.add_button("Cancel", Gtk.ResponseType.REJECT)
    btn_cancel.get_style_context().add_class("destructive-action")
    dlg.add_button("Reboot Later", Gtk.ResponseType.CANCEL)
    btn_reboot = dlg.add_button("Reboot Now", Gtk.ResponseType.OK)
    btn_reboot.get_style_context().add_class("suggested-action")

    dlg.show_all()
    resp = dlg.run()
    dlg.destroy()
    if resp == Gtk.ResponseType.OK:
        return "reboot"
    elif resp == Gtk.ResponseType.REJECT:
        return "cancel"
    return "later"


def _countdown_confirm(parent, title, msg, timeout=15):
    """
    Show a countdown dialog. Returns True if user confirms (Keep),
    False if timer expires or user clicks Revert.

    Used after potentially dangerous writes (margins, rotation) to
    give the user a chance to see if the screen is still visible.
    If the screen went blank, the timer auto-reverts.
    """
    dlg = Gtk.Dialog(title=title, parent=parent, flags=0)
    dlg.set_default_size(400, 160)
    dlg.set_position(Gtk.WindowPosition.CENTER)
    dlg.set_keep_above(True)

    btn_revert = dlg.add_button("Revert Now", Gtk.ResponseType.CANCEL)
    btn_revert.get_style_context().add_class("destructive-action")
    btn_keep = dlg.add_button("Keep Changes", Gtk.ResponseType.OK)
    btn_keep.get_style_context().add_class("suggested-action")

    box = dlg.get_content_area()
    box.set_spacing(8)
    box.set_margin_start(16)
    box.set_margin_end(16)
    box.set_margin_top(12)

    label = Gtk.Label(label=msg)
    label.set_line_wrap(True)
    label.set_xalign(0)
    box.pack_start(label, False, False, 0)

    countdown_label = Gtk.Label()
    countdown_label.set_markup(
        f'<span size="large" weight="bold">'
        f'Auto-reverting in {timeout} seconds…</span>'
    )
    box.pack_start(countdown_label, False, False, 4)

    progress = Gtk.ProgressBar()
    progress.set_fraction(1.0)
    box.pack_start(progress, False, False, 0)

    dlg.show_all()

    remaining = [timeout]
    confirmed = [False]

    def tick():
        remaining[0] -= 1
        if remaining[0] <= 0:
            dlg.response(Gtk.ResponseType.CANCEL)
            return False
        frac = remaining[0] / timeout
        progress.set_fraction(frac)
        countdown_label.set_markup(
            f'<span size="large" weight="bold">'
            f'Auto-reverting in {remaining[0]} seconds…</span>'
        )
        return True

    timer_id = GLib.timeout_add(1000, tick)

    resp = dlg.run()
    GLib.source_remove(timer_id)
    dlg.destroy()

    return resp == Gtk.ResponseType.OK


# ═══════════════════════════════════════════════════════════════
# CLI diagnostics mode
# ═══════════════════════════════════════════════════════════════

def cli_status():
    comp = config.detect_compositor()
    print(f"Display Calibrator — Status  (compositor: {comp})\n")
    for sev, msg in config.validate_config_txt():
        print(f"  [{sev.upper():7s}] {msg}")
    print()
    for o in devices.get_drm_outputs():
        st = "CONNECTED" if o["connected"] else "disconnected"
        print(f"  {o['name']:20s} {st:12s} {o.get('resolution','?')}")
    print()
    for d in devices.get_touch_devices():
        print(f"  Touch: {d['name']}  ({d.get('kernel','?')})  cal={d.get('calibration','?')}")
    print()
    cal = config.read_calibration()
    if cal:
        name, mat = cal
        print(f"  Rule: {name}")
        print(f"  Matrix: {matrix.fmt(mat, 4)}")
    else:
        print("  No calibration rule")
    print()
    for o in devices.get_connected_outputs():
        m = config.read_margins(o["name"])
        if m:
            print(f"  {o['name']}: L={m['left']} R={m['right']} T={m['top']} B={m['bottom']}")
        else:
            print(f"  {o['name']}: No margins set")


# ═══════════════════════════════════════════════════════════════
# Entry point
# ═══════════════════════════════════════════════════════════════

def main():
    if "--version" in sys.argv or "-V" in sys.argv:
        print(f"display-calibrator {config.VERSION}")
        return

    if "--cli" in sys.argv or "--status" in sys.argv:
        cli_status()
        return

    if "--help" in sys.argv or "-h" in sys.argv:
        print(f"display-calibrator {config.VERSION}")
        print("Usage: display-calibrator [OPTIONS]")
        print("  (none)      Open settings window")
        print("  --tray      Run as system tray icon")
        print("  --cli       Print status to terminal")
        print("  --version   Show version")
        return

    app = CalibratorTray()

    if "--tray" in sys.argv:
        # Tray-only mode: stay alive, open window on click
        pass
    else:
        # Window mode: open settings directly, quit on close
        if AppIndicator3 and app.indicator:
            app.indicator.set_status(AppIndicator3.IndicatorStatus.PASSIVE)
        app._on_settings()
        # Find the settings window and connect destroy to quit
        for w in app._open_windows:
            w.connect("destroy", lambda _: Gtk.main_quit())
            break

    Gtk.main()


if __name__ == "__main__":
    main()
