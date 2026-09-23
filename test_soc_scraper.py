import unittest
from unittest.mock import Mock, patch

import numpy as np
from windows_capture import Frame

import soc_scraper as scraper


class WindowSelectionTests(unittest.TestCase):
    def test_exact_game_title_wins_over_foreground_editor(self):
        windows = [(1, "Sword of Convallaria - Visual Studio Code"),
                   (2, "Sword of Convallaria")]
        with patch.object(scraper, "get_windows", return_value=windows):
            self.assertEqual(scraper.find_window(" sword OF convallaria ")[0], 2)

    def test_unique_substring(self):
        with patch.object(scraper, "get_windows", return_value=[(2, "Game - SoC")]):
            self.assertEqual(scraper.find_window("SoC")[0], 2)

    def test_missing_empty_and_ambiguous_titles_fail(self):
        with patch.object(scraper, "get_windows", return_value=[(1, "SoC A"), (2, "SoC B")]):
            for title in ("", "missing", "SoC"):
                with self.subTest(title=title), self.assertRaises(RuntimeError):
                    scraper.find_window(title)


class CaptureTests(unittest.TestCase):
    def setUp(self):
        for name, result in (("IsWindow", True), ("IsIconic", False)):
            patcher = patch.object(scraper.win32gui, name, return_value=result)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_returns_owned_bgr_and_stops(self):
        pixels = np.zeros((4, 5, 4), dtype=np.uint8)
        pixels[:] = [10, 20, 30, 255]
        internal_control = Mock()
        handlers = {}
        with patch.object(scraper, "WindowsCapture") as factory:
            capture = factory.return_value
            capture.event.side_effect = lambda fn: handlers.setdefault(fn.__name__, fn)
            control = capture.start_free_threaded.return_value
            control.is_finished.return_value = True

            def start():
                handlers["on_frame_arrived"](Frame(pixels, 5, 4, 0), internal_control)
                return control

            capture.start_free_threaded.side_effect = start
            image = scraper.capture_window(123)
            factory.assert_called_once_with(cursor_capture=False, draw_border=False,
                                            monitor_index=None, window_name=None,
                                            window_hwnd=123)
            control.stop.assert_called_once()
        internal_control.stop.assert_called_once()
        pixels[:] = 0
        self.assertEqual(image.shape, (4, 5, 3))
        self.assertEqual(image[0, 0].tolist(), [10, 20, 30])
        self.assertTrue(image.flags.c_contiguous)

    def test_timeout_stops_capture(self):
        with patch.object(scraper, "WindowsCapture") as factory, \
                patch.object(scraper.time, "monotonic", side_effect=[0, 11]):
            control = factory.return_value.start_free_threaded.return_value
            control.is_finished.return_value = False
            with self.assertRaisesRegex(RuntimeError, "Timed out"):
                scraper.capture_window(123)
            control.stop.assert_called_once()

    def test_closed_without_frame_fails(self):
        with patch.object(scraper, "WindowsCapture") as factory:
            control = factory.return_value.start_free_threaded.return_value
            control.is_finished.return_value = True
            with self.assertRaisesRegex(RuntimeError, "Could not capture"):
                scraper.capture_window(123)
            control.stop.assert_called_once()

    def test_invalid_or_minimized_window_never_starts_capture(self):
        for name, value in (("IsWindow", False), ("IsIconic", True)):
            with self.subTest(name=name), \
                    patch.object(scraper.win32gui, name, return_value=value), \
                    patch.object(scraper, "WindowsCapture") as factory:
                with self.assertRaises(RuntimeError):
                    scraper.capture_window(123)
                factory.assert_not_called()

    def test_legacy_calibration_is_rejected_before_capture(self):
        with patch.object(scraper, "load_fields", return_value={}), \
                patch.object(scraper, "load_regions", return_value={"fields": {}}), \
                patch.object(scraper, "capture_window") as capture:
            with self.assertRaisesRegex(RuntimeError, "Run calibrate first"):
                scraper.scrape("SoC", "unused.json")
            capture.assert_not_called()


if __name__ == "__main__":
    unittest.main()
