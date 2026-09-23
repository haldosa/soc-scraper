"""Regression checks for the reference grid and persistent UI panels."""
import unittest
from unittest.mock import Mock, patch

import numpy as np

import soc_account as account
import soc_scraper
from soc_navigation import NavigationError, grid_diagnostics, slot_geometry, validate_grid, visible_slots
from test_soc_account import configuration


def reference_grid():
    # Reference screenshot dimensions; viewport clips a little decorative overhang.
    return {"viewport": {"x": 300 / 1920, "y": 150 / 1020, "w": 1320 / 1920, "h": 740 / 1020},
            "first_card": {"x": 282 / 1920, "y": 137 / 1020, "w": 233 / 1920, "h": 308 / 1020},
            "columns": 5, "column_pitch": 277 / 1920, "row_pitch": 365 / 1020}


class ReferenceGridTests(unittest.TestCase):
    def test_decorative_overhang_keeps_first_two_rows_in_reading_order(self):
        grid = reference_grid()
        validate_grid(grid)
        self.assertEqual([position for position, _ in visible_slots(grid, 0)],
                         [(row, col) for row in range(2) for col in range(5)])
        config = configuration()
        config["grid"] = grid
        account.validate_config(config, "roster", soc_scraper.CAPTURE_BACKEND)

    def test_scroll_keeps_global_row_ids(self):
        grid = reference_grid()
        self.assertEqual([position for position, _ in visible_slots(grid, grid["row_pitch"])],
                         [(row, col) for row in (1, 2) for col in range(5)])

    def test_tolerance_never_admits_center_outside_viewport(self):
        grid = reference_grid()
        grid["viewport"]["y"] = 300 / 1020
        grid["viewport"]["h"] = 590 / 1020
        grid["edge_tolerance"] = .02
        self.assertFalse(slot_geometry(grid, 0, 0)["usable"])
        with self.assertRaisesRegex(ValueError, "unsafe centers"):
            validate_grid(grid)

    def test_meaningful_visibility_still_required(self):
        grid = reference_grid()
        grid["viewport"]["y"] = 280 / 1020
        grid["viewport"]["h"] = 610 / 1020
        slot = slot_geometry(grid, 0, 2)
        self.assertFalse(slot["usable"])
        self.assertIn("visible card fraction", slot["reason"])
        grid["min_visible_fraction"] = .5
        self.assertTrue(slot_geometry(grid, 0, 2)["usable"])

    def test_rejection_prints_geometry_and_reasons(self):
        config = configuration()
        config["grid"] = reference_grid()
        config["grid"]["column_pitch"] = .2
        with self.assertRaises(RuntimeError) as caught:
            account.validate_config(config, "roster", soc_scraper.CAPTURE_BACKEND)
        message = str(caught.exception)
        for expected in ("Roster viewport: x=", "columns: 5", "First-row Y center:",
                         "slot 0:", "usable=yes", "slot 4:", "usable=no", "Reason rejected:"):
            self.assertIn(expected, message)
        self.assertIn("normalized window coordinates", grid_diagnostics(reference_grid()))


class CharacterOverlayTests(unittest.TestCase):
    def setUp(self):
        self.config = configuration()
        self.config["list_page"]["code"] = 0
        # configuration() shares anchor objects; replace the detail anchor explicitly.
        self.config["details_page"] = {**self.config["details_page"], "code": 1}
        self.config["overlay_dismiss"] = {"x": .9, "y": .3}
        self.slots = ["weapon", "trinket", "skill_1", "skill_2", "skill_3", "skill_4"]
        self.config["build_panels"] = {
            slot: {"open": {"x": .1 * (i + 1), "y": .8},
                   "page": {"code": i + 2},
                   "fields": {"name": {"x": 0., "y": 0., "w": 1., "h": 1.}}}
            for i, slot in enumerate(self.slots)}
        self.current = 1
        self.api = Mock()
        self.api.extract_fields.return_value = ({"name": "Rawiyah"}, {"name": {"confidence": .99}})
        self.api.read_combat_stats.return_value = ({"hp": 6269}, {"hp": {"raw": "6269", "confidence": .96}})
        self.scanner = account.Scanner(self.api, 1, "roster", self.config, Mock(), Mock())
        self.scanner.capture = Mock(side_effect=self.frame)
        self.scanner.input.click.side_effect = self.click
        self.scanner.wait_for = Mock(side_effect=lambda *args, **kwargs: self.frame())
        self.scanner.wait_for_detail_name_change = Mock(side_effect=lambda *args, **kwargs: self.frame())
        self.page_patch = patch.object(account, "matches_page", side_effect=lambda frame, page: int(frame[0, 0, 0]) == page["code"])
        self.page_patch.start()
        self.addCleanup(self.page_patch.stop)

    def frame(self):
        return np.full((20, 20, 3), self.current, np.uint8)

    def click(self, point):
        if point == self.config["overlay_dismiss"]:
            self.current = 1
        elif point == self.config["back"]:
            self.current = 0
        else:
            self.current = next(route["page"]["code"] for route in self.config["build_panels"].values()
                                if route["open"] == point)

    def test_siblings_direct_then_one_dismiss_and_roster_back(self):
        with patch.object(account, "read_field", side_effect=lambda *args: (f"name_{self.current}", {})):
            record = self.scanner.read_character(self.frame())
        targets = [call.args[0] for call in self.scanner.input.click.call_args_list]
        self.assertEqual(targets, [self.config["build_panels"][slot]["open"] for slot in self.slots])
        self.assertEqual(record["equipped"], {"weapon": "name_2", "trinket": "name_3", "tarot": None})
        self.assertEqual(record["skills"], ["name_4", "name_5", "name_6", "name_7"])
        self.assertEqual(record["stats"]["hp"], 6269)
        self.scanner.recover_list()
        targets = [call.args[0] for call in self.scanner.input.click.call_args_list]
        self.assertEqual(targets[-2:], [self.config["overlay_dismiss"], self.config["back"]])
        self.assertEqual(targets.count(self.config["overlay_dismiss"]), 1)

    def test_ocr_failure_does_not_close_recognized_overlay(self):
        def read(*args):
            if self.current == 2:
                raise ValueError("unreadable weapon")
            return f"name_{self.current}", {}
        with patch.object(account, "read_field", side_effect=read):
            record = self.scanner.read_character(self.frame())
        self.assertIsNone(record["equipped"]["weapon"])
        self.assertEqual(record["equipped"]["trinket"], "name_3")
        self.assertEqual(len(record["skills"]), 4)
        self.assertEqual(self.scanner.input.click.call_count, 6)
        self.assertIn("unreadable weapon", record["ocr"]["weapon"]["name"]["error"])

    def test_legacy_group_dismiss_flag_no_longer_interrupts_direct_sequence(self):
        self.config["dismiss_gear_before_skills"] = True
        with patch.object(account, "read_field", return_value=("Name", {})):
            self.scanner.read_character(self.frame())
        targets = [call.args[0] for call in self.scanner.input.click.call_args_list]
        expected = [self.config["build_panels"][slot]["open"] for slot in self.slots]
        self.assertEqual(targets, expected)

    def test_unknown_page_never_clicks(self):
        self.current = 99
        with self.assertRaises(NavigationError):
            self.scanner.read_popup(self.config["build_panels"]["weapon"])
        self.scanner.input.click.assert_not_called()

    def test_shared_skill_anchor_requires_name_change(self):
        route = self.config["build_panels"]["skill_2"]
        self.current = route["page"]["code"]
        with patch.object(account, "read_field", return_value=("Skill B", {})):
            self.scanner.read_popup(route)
        previous = self.scanner.wait_for_detail_name_change.call_args.args[0]
        self.assertIsNotNone(previous)
        self.assertTrue(np.all(previous == self.current))
        self.assertNotIn("active_popup", self.config)


class PersistentPaneTests(unittest.TestCase):
    def test_inline_inventory_recovery_never_clicks_back(self):
        scanner = account.Scanner(Mock(), 1, "equipment", configuration("equipment"), Mock(), Mock())
        frame = np.zeros((400, 500, 3), np.uint8)
        scanner.capture = Mock(return_value=frame)
        scanner.is_page = Mock(return_value=True)
        scanner.wait_for_list = Mock(return_value=frame)
        for _ in range(3):
            self.assertIs(scanner.recover_list(), frame)
        scanner.input.click.assert_not_called()

    def test_old_separate_equipment_mode_requires_recalibration(self):
        config = configuration("equipment")
        config["details_mode"] = "separate"
        with self.assertRaisesRegex(RuntimeError, "persistent inline details pane"):
            account.validate_config(config, "equipment", soc_scraper.CAPTURE_BACKEND)

    def test_stale_shared_anchor_text_is_not_accepted(self):
        scanner = account.Scanner(Mock(), 1, "roster", configuration(), Mock(), Mock())
        old = np.zeros((400, 500, 3), np.uint8)
        new = np.full_like(old, 200)
        scanner.capture = Mock(side_effect=[old] * 6 + [new] * 10)
        scanner.is_page = Mock(return_value=True)
        ticks = iter(i / 10 for i in range(100))
        with patch.object(account.time, "monotonic", side_effect=lambda: next(ticks)), patch.object(account.time, "sleep"):
            result = scanner.wait_for("details_page", changed_from=old)
        self.assertIs(result, new)
        self.assertGreater(scanner.capture.call_count, 6)


if __name__ == "__main__":
    unittest.main()
