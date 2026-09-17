"""Per-camera GStreamer pipeline with gtk4paintablesink.

Important: never block the GTK main loop with pipeline.get_state() waits.
set_state(NULL/PLAYING) is asynchronous; RTSP teardown happens in the
streaming thread.
"""

from __future__ import annotations

import logging
from typing import Any, Callable

import gi

gi.require_version("Gst", "1.0")
gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gst, Gtk  # noqa: E402

log = logging.getLogger("cctv_lite.player")

Gst.init(None)


def _protocols_value(name: str) -> int:
    mapping = {
        "udp": 1,
        "udp-mcast": 2,
        "tcp": 4,
        "http": 8,
    }
    return mapping.get((name or "tcp").lower(), 4)


class CameraPlayer:
    """Owns one RTSP pipeline and a Gtk.Picture for display."""

    def __init__(
        self,
        cam_id: str,
        name: str,
        url: str,
        rtsp_opts: dict[str, Any] | None = None,
        on_status: Callable[[str, str], None] | None = None,
    ):
        self.cam_id = cam_id
        self.name = name
        self.url = url
        self.opts = dict(rtsp_opts or {})
        self.on_status = on_status
        self._pipeline: Gst.Element | None = None
        self._sink: Gst.Element | None = None
        self._bus = None
        self._bus_handler_id = None
        self._reconnect_id = None
        self._wanted_playing = False
        self._status = "idle"
        self._glbin: Gst.Element | None = None
        self.picture = Gtk.Picture()
        self.picture.set_content_fit(Gtk.ContentFit.CONTAIN)
        self.picture.set_hexpand(True)
        self.picture.set_vexpand(True)
        self.picture.add_css_class("camera-picture")
        self.picture.connect("notify::width", self._sync_window_size)
        self.picture.connect("notify::height", self._sync_window_size)
        # GTK 4.14+: compositor presents the video texture directly.
        # Without this, GSK rasterizes I420 frames and the large view looks muddy.
        if hasattr(Gtk, "GraphicsOffload"):
            off = Gtk.GraphicsOffload(child=self.picture)
            try:
                off.set_enabled(Gtk.GraphicsOffloadEnabled.ENABLED)
            except Exception:
                pass
            try:
                off.set_black_background(True)
            except Exception:
                pass
            off.set_hexpand(True)
            off.set_vexpand(True)
            self.widget: Gtk.Widget = off
        else:
            self.widget = self.picture
        self.widget.connect("notify::width", self._sync_window_size)
        self.widget.connect("notify::height", self._sync_window_size)

    @property
    def status(self) -> str:
        return self._status

    def _set_status(self, status: str) -> None:
        self._status = status
        if self.on_status:
            try:
                self.on_status(self.cam_id, status)
            except Exception:
                log.exception("status callback failed")

    def set_name(self, name: str) -> None:
        self.name = name

    def _sync_window_size(self, *_args) -> None:
        sink = self._sink
        if sink is None:
            return
        try:
            scale = max(1, int(self.picture.get_scale_factor()))
            w = max(0, int(self.picture.get_width()) * scale)
            h = max(0, int(self.picture.get_height()) * scale)
        except Exception:
            return
        if w <= 1 or h <= 1:
            return
        try:
            if int(sink.get_property("window-width") or 0) != w:
                sink.set_property("window-width", w)
            if int(sink.get_property("window-height") or 0) != h:
                sink.set_property("window-height", h)
        except Exception:
            pass

    def _drop_pipeline(self) -> None:
        """NULL + drop refs without waiting (non-blocking)."""
        if self._bus is not None:
            if self._bus_handler_id is not None:
                try:
                    self._bus.disconnect(self._bus_handler_id)
                except Exception:
                    pass
                self._bus_handler_id = None
            try:
                self._bus.remove_signal_watch()
            except Exception:
                pass
        pipe = self._pipeline
        self._pipeline = None
        self._sink = None
        self._glbin = None
        self._bus = None
        try:
            self.picture.set_paintable(None)
        except Exception:
            pass
        if pipe is not None:
            try:
                # Async teardown — do NOT get_state() on the UI thread
                pipe.set_state(Gst.State.NULL)
            except Exception:
                pass

    def build(self) -> None:
        self._drop_pipeline()
        latency = int(self.opts.get("latency_ms") or 120)
        drop = bool(self.opts.get("drop_on_latency", True))
        proto = _protocols_value(str(self.opts.get("protocols") or "tcp"))
        timeout = int(self.opts.get("timeout_us") or 5_000_000)

        pipeline = Gst.ElementFactory.make("playbin", f"playbin-{self.cam_id}")
        if pipeline is None:
            pipeline = Gst.ElementFactory.make("playbin3", f"playbin-{self.cam_id}")
        if pipeline is None:
            raise RuntimeError("GStreamer playbin not available")

        sink = Gst.ElementFactory.make("gtk4paintablesink", f"sink-{self.cam_id}")
        if sink is None:
            raise RuntimeError(
                "gtk4paintablesink missing — install gstreamer1.0-gtk4"
            )

        try:
            sink.set_property("sync", False)
        except Exception:
            pass
        try:
            sink.set_property("qos", False)
        except Exception:
            pass
        try:
            # Keep native decoder size; GTK/compositor scales the texture.
            sink.set_property("reconfigure-on-window-resize", 0)
        except Exception:
            pass

        # Convert to BGRA before the sink. glsinkbin would negotiate YU12
        # DMABuf; Hyprland/GTK then often composites the 4:2:0 chroma plane
        # (half resolution), which makes the large view look as soft as the
        # 360p grid. XRGB/BGRA matches the compositor format.
        video_sink: Gst.Element = sink
        glbin = None
        conv = Gst.ElementFactory.make("videoconvert", f"vconv-{self.cam_id}")
        capsf = Gst.ElementFactory.make("capsfilter", f"vcaps-{self.cam_id}")
        if conv is not None and capsf is not None:
            capsf.set_property(
                "caps", Gst.Caps.from_string("video/x-raw,format=BGRA")
            )
            vbin = Gst.Bin.new(f"vsink-{self.cam_id}")
            vbin.add(conv)
            vbin.add(capsf)
            vbin.add(sink)
            if conv.link(capsf) and capsf.link(sink):
                gpad = Gst.GhostPad.new("sink", conv.get_static_pad("sink"))
                gpad.set_active(True)
                vbin.add_pad(gpad)
                video_sink = vbin
            else:
                log.error("BGRA sink link failed for %s", self.cam_id)

        pipeline.set_property("uri", self.url)
        pipeline.set_property("video-sink", video_sink)

        if bool(self.opts.get("mute_audio", True)):
            fakesink = Gst.ElementFactory.make("fakesink", f"asink-{self.cam_id}")
            if fakesink is not None:
                fakesink.set_property("sync", False)
                pipeline.set_property("audio-sink", fakesink)
            try:
                pipeline.set_property("flags", 1)  # video only
            except Exception:
                pass

        detail = self.cam_id.endswith("-detail")
        for prop, value in (
            ("buffer-size", (2 * 1024 * 1024) if detail else 256 * 1024),
            ("buffer-duration", (400 * Gst.MSECOND) if detail else 200 * Gst.MSECOND),
        ):
            try:
                pipeline.set_property(prop, value)
            except Exception:
                pass

        def on_source_setup(_pb, source):
            try:
                factory = source.get_factory()
                if factory is None or factory.get_name() != "rtspsrc":
                    return
                source.set_property("latency", latency)
                source.set_property("drop-on-latency", drop)
                source.set_property("protocols", proto)
                source.set_property("timeout", timeout)
                source.set_property("tcp-timeout", timeout)
                try:
                    source.set_property("do-rtsp-keep-alive", True)
                except Exception:
                    pass
            except Exception:
                log.exception("rtspsrc setup failed for %s", self.cam_id)

        try:
            pipeline.connect("source-setup", on_source_setup)
        except Exception:
            pass

        try:
            paintable = sink.get_property("paintable")
            try:
                paintable.set_property("force-aspect-ratio", True)
            except Exception:
                pass
            try:
                paintable.set_property("use-scaling-filter", True)
                # Nearest for 1:1 large view; linear for the small grid tiles.
                filt = 1 if self.cam_id.endswith("-detail") else 0
                paintable.set_property("scaling-filter", filt)
            except Exception:
                pass
            self.picture.set_paintable(paintable)
        except Exception:
            log.exception("could not attach paintable for %s", self.cam_id)

        bus = pipeline.get_bus()
        bus.add_signal_watch()
        handler_id = bus.connect("message", self._on_bus_message)

        self._pipeline = pipeline
        self._sink = sink
        self._glbin = glbin
        self._bus = bus
        self._bus_handler_id = handler_id
        self._set_status("ready")
        self._sync_window_size()

    def play(self) -> None:
        self._wanted_playing = True
        self._cancel_reconnect()
        if self._pipeline is None:
            try:
                self.build()
            except Exception as e:
                log.error("build failed %s: %s", self.cam_id, e)
                self._set_status(f"error: {e}")
                self._schedule_reconnect()
                return
        ret = self._pipeline.set_state(Gst.State.PLAYING)
        if ret == Gst.StateChangeReturn.FAILURE:
            self._set_status("error: start failed")
            self._schedule_reconnect()
        else:
            self._set_status("connecting")

    def pause(self) -> None:
        self._wanted_playing = False
        self._cancel_reconnect()
        if self._pipeline is not None:
            self._pipeline.set_state(Gst.State.PAUSED)
            self._set_status("paused")

    def stop(self) -> None:
        """Stop RTSP (NULL) without blocking the UI thread."""
        self._wanted_playing = False
        self._cancel_reconnect()
        if self._pipeline is not None:
            try:
                self._pipeline.set_state(Gst.State.NULL)
            except Exception:
                pass
            self._set_status("stopped")

    def hard_restart(self) -> None:
        """New pipeline + play (non-blocking state changes)."""
        self._wanted_playing = True
        self._cancel_reconnect()
        self._drop_pipeline()
        try:
            self.build()
            self.play()
        except Exception as e:
            log.error("hard_restart failed %s: %s", self.cam_id, e)
            self._set_status(f"error: {e}")
            self._schedule_reconnect()

    def destroy(self) -> None:
        self._wanted_playing = False
        self._cancel_reconnect()
        self._drop_pipeline()
        self._set_status("idle")

    def set_url(self, url: str, autoplay: bool = True) -> None:
        self.url = url
        was = self._wanted_playing
        self.destroy()
        if autoplay and was:
            self.play()

    def _cancel_reconnect(self) -> None:
        if self._reconnect_id is not None:
            try:
                GLib.source_remove(self._reconnect_id)
            except Exception:
                pass
            self._reconnect_id = None

    def _schedule_reconnect(self) -> None:
        if not self._wanted_playing:
            return
        self._cancel_reconnect()
        delay = float(self.opts.get("reconnect_delay_s") or 3.0)
        delay_ms = max(1000, int(delay * 1000))

        def _re(_user=None):
            self._reconnect_id = None
            if not self._wanted_playing:
                return False
            log.info("reconnecting %s", self.cam_id)
            try:
                self.hard_restart()
            except Exception:
                log.exception("reconnect failed %s", self.cam_id)
                self._schedule_reconnect()
            return False

        self._set_status(f"reconnect in {delay:.0f}s")
        self._reconnect_id = GLib.timeout_add(delay_ms, _re)

    def _log_negotiated_caps(self) -> None:
        sink = self._sink
        if sink is None:
            return
        try:
            pad = sink.get_static_pad("sink")
            caps = pad.get_current_caps() if pad is not None else None
            log.info(
                "%s caps %s window=%sx%s widget=%sx%s",
                self.cam_id,
                caps.to_string() if caps is not None else "none",
                sink.get_property("window-width"),
                sink.get_property("window-height"),
                self.picture.get_width(),
                self.picture.get_height(),
            )
        except Exception:
            log.exception("caps log failed for %s", self.cam_id)

    def _on_bus_message(self, _bus, message) -> None:
        # Guard: pipeline may already be dropped
        if self._pipeline is None and message.src is not None:
            return
        t = message.type
        if t == Gst.MessageType.ERROR:
            err, debug = message.parse_error()
            msg = err.message if err else "unknown"
            log.warning("%s error: %s (%s)", self.cam_id, msg, debug)
            short = msg
            if "Could not read from resource" in msg or "resource" in msg.lower():
                short = "stream lost — reconnecting"
            self._set_status(f"error: {short}")
            if self._wanted_playing:
                # Defer restart so we don't rebuild inside the bus callback stack
                self._schedule_reconnect()
            else:
                if self._pipeline is not None:
                    try:
                        self._pipeline.set_state(Gst.State.NULL)
                    except Exception:
                        pass
        elif t == Gst.MessageType.EOS:
            log.info("%s EOS — reconnect", self.cam_id)
            if self._wanted_playing:
                self._schedule_reconnect()
        elif t == Gst.MessageType.STATE_CHANGED:
            if message.src == self._pipeline:
                _old, new, _pending = message.parse_state_changed()
                if new == Gst.State.PLAYING:
                    self._set_status("playing")
                    self._sync_window_size()
                    self._log_negotiated_caps()
                elif new == Gst.State.PAUSED and self._wanted_playing:
                    self._set_status("buffering")
        elif t == Gst.MessageType.BUFFERING:
            # Live RTSP: do not pause/play on buffering — freezes UI and kills sessions
            percent = message.parse_buffering()
            if self._wanted_playing and percent < 100:
                self._set_status(f"buffering {percent}%")
            elif self._wanted_playing:
                self._set_status("playing")
