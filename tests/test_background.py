import sys
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from cctv_lite.app import CctvWindow, Gdk


class Background(unittest.TestCase):
    def window(self, enabled=True):
        return SimpleNamespace(
            config=SimpleNamespace(ui=lambda: {'pause_when_unmapped': enabled}),
            _background_suspended=False, get_mapped=lambda: True,
            _on_unmap=Mock(), _on_map=Mock())

    def test_suspend_stops_once_and_visible_resumes(self):
        w = self.window()
        surface = SimpleNamespace(get_state=lambda: Gdk.ToplevelState.SUSPENDED)
        CctvWindow._on_surface_state(w, surface)
        CctvWindow._on_surface_state(w, surface)
        w._on_unmap.assert_called_once()
        self.assertTrue(w._background_suspended)
        surface.get_state = lambda: 0
        CctvWindow._on_surface_state(w, surface)
        w._on_map.assert_called_once()
        self.assertFalse(w._background_suspended)

    def test_minimized_also_stops(self):
        w = self.window()
        CctvWindow._on_surface_state(w, SimpleNamespace(get_state=lambda: Gdk.ToplevelState.MINIMIZED))
        w._on_unmap.assert_called_once()

    def test_opt_out_keeps_playing(self):
        w = self.window(False)
        CctvWindow._on_surface_state(w, SimpleNamespace(get_state=lambda: Gdk.ToplevelState.SUSPENDED))
        w._on_unmap.assert_not_called()

    def test_unmap_cancels_stagger_timers_and_stops_grid_and_detail(self):
        w = self.window()
        w._clear_start_timers = Mock()
        w.players = {'one': Mock(), 'two': Mock()}
        w._detail_player = Mock()
        CctvWindow._on_unmap(w)
        w._clear_start_timers.assert_called_once()
        for p in [*w.players.values(), w._detail_player]:
            p.stop.assert_called_once()

    def test_background_cannot_restart_players(self):
        w = self.window()
        w._background_suspended = True
        w._clear_start_timers = Mock()
        # These early returns must not need or touch players at all.
        CctvWindow._on_map(w)
        CctvWindow._start_players_staggered(w)
        CctvWindow._start_players_staggered_hard(w)
        CctvWindow.reconnect_all(w)


if __name__ == '__main__':
    unittest.main()
