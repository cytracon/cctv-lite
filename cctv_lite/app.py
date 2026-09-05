"""GTK4 / libadwaita multi-camera UI."""

from __future__ import annotations

import logging
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Gdk", "4.0")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk  # noqa: E402

from . import __app_id__, __version__
from .config import Config, ensure_config
from .player import CameraPlayer
from .settings import SettingsWindow

log = logging.getLogger("cctv_lite.app")

ICON_NAME = "com.bbachmann.cctv-lite"

CSS = """
window {
  background: #0b0d10;
}
.camera-cell {
  background: #11141a;
  border: 1px solid #1e2430;
  border-radius: 6px;
  margin: 2px;
}
.camera-cell:hover {
  border-color: #3d5a80;
}
.camera-cell.selected {
  border-color: #3d8bfd;
  box-shadow: 0 0 0 1px #3d8bfd;
}
.camera-label {
  background: rgba(0,0,0,0.55);
  color: #e8eef7;
  font-size: 11px;
  font-weight: 600;
  padding: 2px 8px;
  border-radius: 0 0 6px 0;
}
.camera-status {
  color: #9aa7b8;
  font-size: 10px;
  padding: 0 8px 4px 8px;
}
.camera-status.error {
  color: #ff7b72;
}
.camera-status.playing {
  color: #3fb950;
}
.toolbar-dark {
  background: #12151c;
}
.placeholder {
  color: #6b7785;
  font-size: 13px;
}
.detail-click {
  /* full-area click target for returning to grid */
}
"""


def _install_icon_search_paths() -> None:
    """Ensure user and app icon dirs are visible to the theme."""
    display = Gdk.Display.get_default()
    if display is None:
        return
    theme = Gtk.IconTheme.get_for_display(display)
    candidates = [
        Path.home() / ".local/share/icons",
        Path.home() / ".icons",
        Path.home() / ".local/share/cctv-lite/icons",
    ]
    for p in candidates:
        if p.is_dir():
            try:
                theme.add_search_path(str(p))
            except Exception:
                pass


class CameraCell(Gtk.Overlay):
    def __init__(self, player: CameraPlayer, show_labels: bool = True):
        super().__init__()
        self.player = player
        self.add_css_class("camera-cell")
        self.set_hexpand(True)
        self.set_vexpand(True)
        self.set_cursor(Gdk.Cursor.new_from_name("pointer"))

        self.player.picture.set_hexpand(True)
        self.player.picture.set_vexpand(True)
        self.set_child(self.player.picture)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        box.set_halign(Gtk.Align.START)
        box.set_valign(Gtk.Align.START)
        box.set_can_target(False)

        self.label = Gtk.Label(label=player.name, xalign=0)
        self.label.add_css_class("camera-label")
        self.status = Gtk.Label(label="idle", xalign=0)
        self.status.add_css_class("camera-status")

        if show_labels:
            box.append(self.label)
            box.append(self.status)
        self.add_overlay(box)

        click = Gtk.GestureClick()
        click.set_button(1)
        click.connect("released", self._on_click)
        self.add_controller(click)

        self._on_activate = None
        self._selected = False

    def set_on_activate(self, cb) -> None:
        self._on_activate = cb

    def set_selected(self, selected: bool) -> None:
        self._selected = selected
        if selected:
            self.add_css_class("selected")
        else:
            self.remove_css_class("selected")

    def _on_click(self, gesture: Gtk.GestureClick, n_press: int, x: float, y: float):
        # Single click opens large view
        if self._on_activate:
            self.set_selected(True)
            self._on_activate(self.player)

    def update_status(self, status: str) -> None:
        self.status.set_text(status)
        self.status.remove_css_class("error")
        self.status.remove_css_class("playing")
        if status.startswith("error") or status.startswith("reconnect"):
            self.status.add_css_class("error")
        elif status == "playing":
            self.status.add_css_class("playing")


class CctvWindow(Adw.ApplicationWindow):
    def __init__(self, app: "CctvApplication", config: Config):
        super().__init__(application=app, title="CCTV Lite")
        self.app = app
        self.config = config
        self.players: dict[str, CameraPlayer] = {}
        self.cells: dict[str, CameraCell] = {}
        self._grid: Gtk.Grid | None = None
        self._fullscreen_cam: str | None = None
        self._detail_player: CameraPlayer | None = None
        self._start_idle_ids: list[int] = []
        self._settings_win: SettingsWindow | None = None
        self._suppress_layout_signal = False

        try:
            self.set_icon_name(ICON_NAME)
        except Exception:
            try:
                self.set_icon_name("cctv-lite")
            except Exception:
                pass

        ui = config.ui()
        self.set_default_size(
            int(ui.get("window_width") or 1600),
            int(ui.get("window_height") or 900),
        )

        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.set_content(root)

        header = Adw.HeaderBar()
        header.add_css_class("toolbar-dark")
        # show title + icon in header / top bar
        title = Adw.WindowTitle(title="CCTV Lite", subtitle=f"v{__version__}")
        header.set_title_widget(title)
        root.append(header)

        self.layout_model = Gtk.StringList()
        for lay in config.layouts():
            self.layout_model.append(lay.name)
        self.layout_dropdown = Gtk.DropDown(model=self.layout_model)
        self.layout_dropdown.set_selected(int(config.data.get("active_layout") or 0))
        self.layout_dropdown.set_tooltip_text("Layout")
        self.layout_dropdown.connect("notify::selected", self._on_layout_changed)
        header.pack_start(self.layout_dropdown)

        btn_settings = Gtk.Button(
            icon_name="emblem-system-symbolic",
            tooltip_text="Settings",
        )
        btn_settings.connect("clicked", lambda *_: self.open_settings())
        header.pack_end(btn_settings)

        btn_reload = Gtk.Button(
            icon_name="view-refresh-symbolic", tooltip_text="Reconnect all"
        )
        btn_reload.connect("clicked", lambda *_: self.reconnect_all())
        header.pack_end(btn_reload)

        btn_fs = Gtk.Button(
            icon_name="view-fullscreen-symbolic", tooltip_text="Toggle fullscreen"
        )
        btn_fs.connect("clicked", lambda *_: self._toggle_window_fullscreen())
        header.pack_end(btn_fs)

        quality_box = Gtk.Box(spacing=4)
        self.quality_sub = Gtk.ToggleButton(label="Sub")
        self.quality_main = Gtk.ToggleButton(label="Main")
        self.quality_sub.set_group(self.quality_main)
        lay0 = config.active_layout()
        if lay0.quality == "main":
            self.quality_main.set_active(True)
        else:
            self.quality_sub.set_active(True)
        self.quality_sub.connect("toggled", self._on_quality_toggled)
        self.quality_main.connect("toggled", self._on_quality_toggled)
        quality_box.append(self.quality_sub)
        quality_box.append(self.quality_main)
        header.pack_end(quality_box)

        self.stack = Gtk.Stack()
        self.stack.set_hexpand(True)
        self.stack.set_vexpand(True)
        self.stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self.stack.set_transition_duration(120)
        root.append(self.stack)

        self.grid_host = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.grid_host.set_hexpand(True)
        self.grid_host.set_vexpand(True)
        self.stack.add_named(self.grid_host, "grid")

        # Detail view: full-area click returns to grid
        self.single_host = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.single_host.set_hexpand(True)
        self.single_host.set_vexpand(True)
        self.single_picture_box = Gtk.Box()
        self.single_picture_box.set_hexpand(True)
        self.single_picture_box.set_vexpand(True)
        self.single_picture_box.add_css_class("detail-click")
        self.single_picture_box.set_cursor(Gdk.Cursor.new_from_name("pointer"))
        self.single_host.append(self.single_picture_box)
        self.stack.add_named(self.single_host, "single")
        self.stack.set_visible_child_name("grid")

        detail_click = Gtk.GestureClick()
        detail_click.set_button(1)
        detail_click.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        detail_click.connect("released", self._on_detail_clicked)
        # CAPTURE on the whole detail host so video widgets don't swallow the click
        self.single_host.add_controller(detail_click)

        self._install_shortcuts()
        self.connect("map", lambda *_: self._on_map())
        self.connect("unmap", lambda *_: self._on_unmap())

        self.build_layout()
        # Name sync in a worker thread — never block the GTK main loop with curl probes
        GLib.timeout_add_seconds(3, self._auto_sync_names)

    def _auto_sync_names(self) -> bool:
        import threading
        from copy import deepcopy

        def worker():
            try:
                from .nvr import sync_names

                data = deepcopy(self.config.data)
                n, msg = sync_names(data)
                log.info("auto NVR name sync: %s", msg)

                def apply():
                    if n > 0:
                        self.config.data = data
                        try:
                            self.config.save()
                        except Exception:
                            log.exception("save names failed")
                        self.refresh_labels_from_config()
                    return False

                GLib.idle_add(apply)
            except Exception:
                log.exception("auto NVR name sync failed")

        threading.Thread(target=worker, name="cctv-nvr-names", daemon=True).start()
        return False  # one-shot

    def _install_shortcuts(self) -> None:
        controller = Gtk.ShortcutController()
        controller.set_scope(Gtk.ShortcutScope.LOCAL)

        def add(key, cb):
            trigger = Gtk.ShortcutTrigger.parse_string(key)
            action = Gtk.CallbackAction.new(lambda *_a: (cb() or True))
            controller.add_shortcut(Gtk.Shortcut.new(trigger, action))

        add("F11", self._toggle_window_fullscreen)
        add("Escape", self._on_escape)
        add("r", self.reconnect_all)
        add("space", self._toggle_pause_all)
        add("<primary>comma", self.open_settings)
        self.add_controller(controller)

    def open_settings(self) -> None:
        if self._settings_win is not None:
            self._settings_win.present()
            return

        def on_saved(cfg: Config, reconnect: bool):
            self.config = cfg
            self.app.config = cfg
            style = self.app.get_style_manager()
            if cfg.ui().get("dark", True):
                style.set_color_scheme(Adw.ColorScheme.FORCE_DARK)
            else:
                style.set_color_scheme(Adw.ColorScheme.FORCE_LIGHT)
            self._reload_layout_dropdown()
            if reconnect:
                self.build_layout()
            else:
                self.refresh_labels_from_config()

        def on_names_synced(cfg: Config):
            self.config = cfg
            self.app.config = cfg
            self.refresh_labels_from_config()

        try:
            win = SettingsWindow(
                self, self.config, on_saved=on_saved, on_names_synced=on_names_synced
            )
            win.connect("close-request", self._on_settings_closed)
            self._settings_win = win
            win.present()
        except Exception:
            log.exception("Settings failed to open")
            # Prefer a dialog over silent failure
            dlg = Adw.MessageDialog(
                transient_for=self,
                heading="Settings failed to open",
                body="See logs (journalctl / terminal). Run: python3 tests/smoke_settings.py",
            )
            dlg.add_response("ok", "OK")
            dlg.present()

    def _on_settings_closed(self, *_):
        self._settings_win = None
        return False

    def _reload_layout_dropdown(self) -> None:
        self._suppress_layout_signal = True
        self.layout_model = Gtk.StringList()
        for lay in self.config.layouts():
            self.layout_model.append(lay.name)
        self.layout_dropdown.set_model(self.layout_model)
        idx = int(self.config.data.get("active_layout") or 0)
        n = self.layout_model.get_n_items()
        if n:
            self.layout_dropdown.set_selected(max(0, min(idx, n - 1)))
        self._suppress_layout_signal = False

    def _on_detail_clicked(self, *_args):
        # Click anywhere on large view → back to overview
        if self._fullscreen_cam:
            self.show_grid()

    def _on_escape(self) -> None:
        if self._fullscreen_cam:
            self.show_grid()
        elif self.is_fullscreen():
            self.unfullscreen()

    def _toggle_window_fullscreen(self) -> None:
        if self.is_fullscreen():
            self.unfullscreen()
        else:
            self.fullscreen()

    def _toggle_pause_all(self) -> None:
        any_playing = any(p.status == "playing" for p in self.players.values())
        if any_playing:
            for p in self.players.values():
                p.pause()
        else:
            self._start_players_staggered()

    def _on_map(self) -> None:
        if self.config.ui().get("pause_when_unmapped", True):
            if self._fullscreen_cam and self._detail_player:
                if self._detail_player.status in ("paused", "stopped", "idle", "error"):
                    self._detail_player.hard_restart()
            elif not self._fullscreen_cam:
                # Fresh sessions after unmap — pause alone often breaks RTSP
                need = any(
                    p.status in ("paused", "stopped", "idle", "error")
                    or str(p.status).startswith("error")
                    for p in self.players.values()
                )
                if need:
                    self._start_players_staggered_hard()

    def _on_unmap(self) -> None:
        if self.config.ui().get("pause_when_unmapped", True):
            for p in self.players.values():
                p.stop()
            if self._detail_player:
                self._detail_player.stop()

    def _on_layout_changed(self, dropdown, *_):
        if self._suppress_layout_signal:
            return
        idx = int(dropdown.get_selected())
        self.config.set_active_layout(idx)
        try:
            self.config.save()
        except Exception:
            log.exception("save active layout failed")
        lay = self.config.active_layout()
        if lay.quality == "main":
            self.quality_main.set_active(True)
        else:
            self.quality_sub.set_active(True)
        self.build_layout()

    def _on_quality_toggled(self, btn: Gtk.ToggleButton):
        if not btn.get_active():
            return
        quality = "main" if btn is self.quality_main else "sub"
        self._apply_quality(quality)

    def _current_quality(self) -> str:
        return "main" if self.quality_main.get_active() else "sub"

    def _apply_quality(self, quality: str) -> None:
        cmap = self.config.camera_map()
        for cam_id, player in self.players.items():
            cam = cmap.get(cam_id)
            if not cam:
                continue
            url = self.config.url_for_camera(cam, quality)
            if url != player.url:
                player.set_url(url, autoplay=True)

    def _clear_start_timers(self) -> None:
        for i in self._start_idle_ids:
            try:
                GLib.source_remove(i)
            except Exception:
                pass
        self._start_idle_ids.clear()

    def _clear_detail(self) -> None:
        if self._detail_player is not None:
            self._detail_player.destroy()
            self._detail_player = None
        child = self.single_picture_box.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            self.single_picture_box.remove(child)
            child = nxt

    def teardown_players(self) -> None:
        self._clear_start_timers()
        self._clear_detail()
        self._fullscreen_cam = None
        for p in list(self.players.values()):
            p.destroy()
        self.players.clear()
        self.cells.clear()
        child = self.grid_host.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            self.grid_host.remove(child)
            child = nxt
        self._grid = None

    def build_layout(self) -> None:
        self.show_grid()
        self.teardown_players()

        layout = self.config.active_layout()
        quality = self._current_quality() or layout.quality
        show_labels = bool(self.config.ui().get("show_labels", True))
        cmap = self.config.camera_map()
        rtsp = self.config.rtsp()

        grid = Gtk.Grid()
        grid.set_column_homogeneous(True)
        grid.set_row_homogeneous(True)
        grid.set_column_spacing(2)
        grid.set_row_spacing(2)
        grid.set_hexpand(True)
        grid.set_vexpand(True)
        self._grid = grid
        self.grid_host.append(grid)

        cam_ids = list(layout.cameras)
        cols = layout.cols
        rows = layout.rows
        slots = cols * rows

        def on_status(cam_id: str, status: str):
            cell = self.cells.get(cam_id)
            if cell:
                GLib.idle_add(cell.update_status, status)

        for i in range(slots):
            r, c = i // cols, i % cols
            if i >= len(cam_ids):
                ph = Gtk.Label(label="")
                ph.add_css_class("placeholder")
                ph.set_hexpand(True)
                ph.set_vexpand(True)
                grid.attach(ph, c, r, 1, 1)
                continue
            cam_id = cam_ids[i]
            cam = cmap.get(cam_id)
            if cam is None or not cam.enabled:
                ph = Gtk.Label(label=f"Missing {cam_id}")
                ph.add_css_class("placeholder")
                grid.attach(ph, c, r, 1, 1)
                continue
            url = self.config.url_for_camera(cam, quality)
            player = CameraPlayer(
                cam_id=cam.id,
                name=cam.name,
                url=url,
                rtsp_opts=rtsp,
                on_status=on_status,
            )
            cell = CameraCell(player, show_labels=show_labels)
            cell.set_on_activate(self._open_single)
            self.players[cam.id] = player
            self.cells[cam.id] = cell
            grid.attach(cell, c, r, 1, 1)

        self._start_players_staggered()

    def _start_players_staggered(self) -> None:
        self._clear_start_timers()
        stagger = int(self.config.rtsp().get("stagger_ms") or 150)
        for i, player in enumerate(self.players.values()):

            def start(p=player):
                p.play()
                return False

            if stagger <= 0 or i == 0:
                start()
            else:
                sid = GLib.timeout_add(stagger * i, start)
                self._start_idle_ids.append(sid)

    def reconnect_all(self) -> None:
        """Full rebuild of every grid pipeline (fresh RTSP sessions)."""
        self._clear_start_timers()
        for p in self.players.values():
            p.stop()
        self._start_players_staggered_hard()

    def _start_players_staggered_hard(self) -> None:
        """Staggered hard_restart (new RTSP session per camera)."""
        self._clear_start_timers()
        stagger = int(self.config.rtsp().get("stagger_ms") or 150)
        for i, player in enumerate(self.players.values()):

            def start(p=player):
                p.hard_restart()
                return False

            if stagger <= 0 or i == 0:
                start()
            else:
                sid = GLib.timeout_add(stagger * i, start)
                self._start_idle_ids.append(sid)

    def refresh_labels_from_config(self) -> None:
        cmap = self.config.camera_map()
        for cam_id, cell in self.cells.items():
            cam = cmap.get(cam_id)
            if not cam:
                continue
            cell.player.set_name(cam.name)
            cell.label.set_text(cam.name)
            cell.label.set_tooltip_text(
                f"{cam.name}\nmain={cam.channel_main}  sub={cam.channel_sub}"
            )

    def sync_names_from_nvr(self, quiet: bool = False) -> str:
        """Fetch channel names via ISAPI and apply. Returns status message."""
        from .nvr import sync_names

        n, msg = sync_names(self.config.data)
        if n > 0:
            try:
                self.config.save()
            except Exception:
                log.exception("save after NVR name sync failed")
            self.refresh_labels_from_config()
        if not quiet:
            log.info("NVR name sync: %s", msg)
        return msg

    def _open_single(self, player: CameraPlayer) -> None:
        """Open main-stream detail view. Grid streams keep running underneath."""
        cam = self.config.camera_map().get(player.cam_id)
        if cam is None:
            return
        for cell in self.cells.values():
            cell.set_selected(cell.player.cam_id == player.cam_id)

        # Drop previous detail only — do NOT stop grid (avoids empty rebuild)
        self._clear_detail()

        main_url = self.config.url_for_camera(cam, "main")
        self._fullscreen_cam = player.cam_id
        self.stack.set_visible_child_name("single")

        detail = CameraPlayer(
            cam_id=f"{cam.id}-detail",
            name=cam.name,
            url=main_url,
            rtsp_opts=self.config.rtsp(),
        )
        try:
            detail.build()
            detail.play()
        except Exception as e:
            log.error("detail start failed: %s", e)
            self._set_detail_placeholder(f"{cam.name}: start failed — {e}")
            return
        self._detail_player = detail

        overlay = Gtk.Overlay()
        overlay.set_hexpand(True)
        overlay.set_vexpand(True)
        overlay.set_child(detail.picture)
        label = Gtk.Label(
            label=f"{cam.name}  ·  main  ·  click to return", xalign=0
        )
        label.add_css_class("camera-label")
        label.set_halign(Gtk.Align.START)
        label.set_valign(Gtk.Align.START)
        label.set_can_target(False)
        overlay.add_overlay(label)
        hint = Gtk.Label(
            label="Click video or press Esc to return to overview", xalign=0.5
        )
        hint.add_css_class("camera-status")
        hint.set_halign(Gtk.Align.CENTER)
        hint.set_valign(Gtk.Align.END)
        hint.set_margin_bottom(12)
        hint.set_can_target(False)
        overlay.add_overlay(hint)
        self.single_picture_box.append(overlay)

    def _set_detail_placeholder(self, text: str) -> None:
        child = self.single_picture_box.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            self.single_picture_box.remove(child)
            child = nxt
        lab = Gtk.Label(label=text)
        lab.add_css_class("placeholder")
        lab.set_hexpand(True)
        lab.set_vexpand(True)
        self.single_picture_box.append(lab)

    def show_grid(self) -> None:
        self._fullscreen_cam = None
        # Only tear down the extra main-stream detail player
        self._clear_detail()
        self.stack.set_visible_child_name("grid")
        for cell in self.cells.values():
            cell.set_selected(False)
        # Grid pipelines were kept running — nothing to rebuild

    def destroy_all(self) -> None:
        if self._settings_win is not None:
            self._settings_win.destroy()
            self._settings_win = None
        self.teardown_players()


class CctvApplication(Adw.Application):
    def __init__(self, config: Config):
        super().__init__(
            application_id=__app_id__,
            flags=Gio.ApplicationFlags.FLAGS_NONE,
        )
        self.config = config
        self.win: CctvWindow | None = None

    def do_startup(self):
        Adw.Application.do_startup(self)
        # WM class / top-bar identity
        GLib.set_prgname(__app_id__)
        GLib.set_application_name("CCTV Lite")
        _install_icon_search_paths()
        try:
            Gtk.Window.set_default_icon_name(ICON_NAME)
        except Exception:
            try:
                Gtk.Window.set_default_icon_name("cctv-lite")
            except Exception:
                pass

        css = Gtk.CssProvider()
        css.load_from_data(CSS.encode("utf-8"))
        display = Gdk.Display.get_default()
        if display is not None:
            Gtk.StyleContext.add_provider_for_display(
                display, css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
            )
        if self.config.ui().get("dark", True):
            style = self.get_style_manager()
            style.set_color_scheme(Adw.ColorScheme.FORCE_DARK)

        quit_action = Gio.SimpleAction.new("quit", None)
        quit_action.connect("activate", lambda *_: self.quit())
        self.add_action(quit_action)
        self.set_accels_for_action("app.quit", ["<primary>q"])

        settings_action = Gio.SimpleAction.new("settings", None)
        settings_action.connect(
            "activate",
            lambda *_: self.win.open_settings() if self.win else None,
        )
        self.add_action(settings_action)
        self.set_accels_for_action("app.settings", ["<primary>comma"])

    def do_activate(self):
        if self.win is None:
            self.win = CctvWindow(self, self.config)
            self.win.connect("close-request", self._on_close)
        self.win.present()

    def _on_close(self, *_):
        if self.win:
            self.win.destroy_all()
        return False

    def do_shutdown(self):
        if self.win:
            self.win.destroy_all()
        Adw.Application.do_shutdown(self)


def run_app(config: Config | None = None) -> int:
    cfg = config or ensure_config(import_if_missing=True)
    app = CctvApplication(cfg)
    return app.run(None)
