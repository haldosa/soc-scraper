import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import numpy as np

import soc_account as account
import soc_scraper
from soc_navigation import NavigationError, WindowInput, crop, estimate_scroll_shift, visible_slots


def configuration(kind="roster"):
    region = {"x": 0., "y": 0., "w": 1., "h": 1., "type": "text"}
    anchor = {"region": region, "template": [[0, 255], [255, 0]]}
    config = {"screen": {"width": 500, "height": 400, "capture_backend": soc_scraper.CAPTURE_BACKEND},
            "list_page": anchor, "details_page": anchor,
            "grid": {"viewport": region, "first_card": {"x": .05, "y": .05, "w": .35, "h": .2},
                     "row_pitch": .3, "column_pitch": .5, "columns": 2},
            "fields": {"name": region, "type": region}, "back": {"x": .03, "y": .03}}
    if kind == "equipment":
        config["details_mode"] = "inline"
    return config


class GuidedStartTests(unittest.TestCase):
    def test_roster_headings_and_ocr_variants_are_accepted(self):
        for text in ("Character List", "My Characters 76", "MyCharacters 76", "Character\nList", "Characler List", "CharacterList"):
            with self.subTest(text=text):
                self.assertTrue(account.heading_matches(text, "roster"))

    def test_details_and_other_panels_are_not_roster(self):
        for text in ("Characters Rank Level", "Class Skill", "Weapon Sword", "Inventory Gear: 82/500", ""):
            with self.subTest(text=text):
                self.assertFalse(account.heading_matches(text, "roster"))

    def test_bootstrap_retries_preprocessing_without_disabling_validation(self):
        frame = np.zeros((1020, 1920, 3), np.uint8)
        diagnostics = []
        with patch.object(account.pytesseract, "image_to_string", side_effect=["", "Character List"]) as ocr:
            self.assertTrue(account.bootstrap_page(frame, "roster", diagnostics))
        self.assertEqual(ocr.call_count, 2)
        self.assertLess(ocr.call_args.args[0].shape[0], frame.shape[0] // 2)
        self.assertEqual(diagnostics, ["", "Character List"])
        with patch.object(account.pytesseract, "image_to_string", return_value="Characters") as ocr:
            self.assertFalse(account.bootstrap_page(frame, "roster"))
            self.assertEqual(ocr.call_count, 6)

    def test_only_enter_proceeds(self):
        self.assertTrue(account.confirm_ready("Ready", Mock(side_effect=["yes", "", ])))
        self.assertFalse(account.confirm_ready("Ready", Mock(return_value="q")))
        self.assertFalse(account.confirm_ready("Ready", Mock(side_effect=EOFError)))

    def test_cancel_before_any_window_access(self):
        api = Mock(CAPTURE_BACKEND=soc_scraper.CAPTURE_BACKEND)
        args = SimpleNamespace(command="roster", count=3, config="unused", title="SoC", output="unused")
        with patch.object(account, "load_json", return_value={"roster": configuration()}), \
                patch.object(account, "guided_prompt", return_value=False):
            account.scan_account(args, api)
        api.find_window.assert_not_called()
        api.capture_window.assert_not_called()

    def test_wrong_page_retries_without_input(self):
        api = Mock(CAPTURE_BACKEND=soc_scraper.CAPTURE_BACKEND)
        api.find_window.return_value = (1, "SoC")
        args = SimpleNamespace(command="equipment", count=3, config="unused", title="SoC", output="unused")
        with patch.object(account, "load_json", return_value={"equipment": configuration("equipment")}), \
                patch.object(account, "guided_prompt", return_value=True), \
                patch.object(account, "matches_page", return_value=False), \
                patch.object(account, "retry_page", side_effect=[True, False]) as retry, \
                patch.object(account, "Scanner") as scanner:
            account.scan_account(args, api)
        self.assertEqual(retry.call_count, 2)
        self.assertEqual(api.capture_window.call_count, 2)
        scanner.assert_not_called()

    def test_count_validation_precedes_tesseract_or_input(self):
        for command in ("roster", "equipment"):
            for value in ("0", "-1"):
                with self.subTest(command=command, count=value), \
                        patch("sys.argv", ["soc_scraper.py", command, "--count", value]), \
                        patch.object(soc_scraper, "setup_tesseract") as setup, \
                        self.assertRaises(SystemExit) as result:
                    soc_scraper.main()
                self.assertEqual(result.exception.code, 2)
                setup.assert_not_called()


class PersistenceTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "account.json"

    def read(self):
        return json.loads(self.path.read_text(encoding="utf-8"))

    def test_copies_are_preserved_and_new_inventory_scan_does_not_duplicate_prior_snapshot(self):
        roster = account.AccountStore(self.path, "roster", 1)
        roster.add({"name": "Inanna", "equipped": {"weapon": "Sword"}})
        inventory = account.AccountStore(self.path, "equipment", 2)
        inventory.add({"name": "Sword", "type": "weapon", "equipped_by": "Inanna"})
        self.assertEqual(len(self.read()["equipment"]), 1)
        inventory.add({"name": "Sword", "type": "weapon", "equipped_by": None})
        saved = self.read()
        self.assertEqual([item["id"] for item in saved["equipment"]], ["equipment_001", "equipment_002"])
        self.assertEqual(saved["characters"][0]["equipment_ids"]["weapon"], "equipment_001")
        again = account.AccountStore(self.path, "equipment", 1)
        again.add({"name": "Hat", "type": "trinket", "equipped_by": None})
        self.assertEqual(len(self.read()["equipment"]), 1)
        self.assertEqual(len(self.read()["characters"]), 1)
        self.assertIsNone(self.read()["characters"][0]["equipment_ids"]["weapon"])

    def test_ambiguous_copies_are_not_assigned(self):
        data = {"characters": [{"name": "Inanna", "equipped": {"weapon": "Sword"}}],
                "equipment": [{"id": "a", "name": "Sword", "type": "weapon", "equipped_by": None},
                              {"id": "b", "name": "Sword", "type": "weapon", "equipped_by": None}]}
        account.reconcile(data)
        self.assertIsNone(data["characters"][0]["equipment_ids"]["weapon"])
        self.assertEqual(data["characters"][0]["equipment_candidates"]["weapon"], ["a", "b"])

    def test_derived_equipped_name_is_invalidated_when_inventory_changes(self):
        data = {"characters": [{"name": "Inanna", "equipped": {"weapon": None}}],
                "equipment": [{"id": "a", "name": "Sword", "type": "weapon", "equipped_by": "Inanna"}]}
        account.reconcile(data)
        self.assertEqual(data["characters"][0]["equipped"]["weapon"], "Sword")
        data["equipment"] = []
        account.reconcile(data)
        self.assertIsNone(data["characters"][0]["equipped"]["weapon"])

    def test_atomic_failure_keeps_last_valid_json(self):
        account.atomic_json(self.path, {"previous": True})
        with patch.object(account.os, "replace", side_effect=OSError("disk error")):
            with self.assertRaises(OSError):
                account.atomic_json(self.path, {"new": True})
        self.assertEqual(self.read(), {"previous": True})
        self.assertEqual(list(self.path.parent.glob("*.tmp")), [])

    def test_invalid_account_is_not_overwritten(self):
        self.path.write_text("not json", encoding="utf-8")
        with self.assertRaises(RuntimeError):
            account.AccountStore(self.path, "equipment", 5)
        self.assertEqual(self.path.read_text(), "not json")


class RecognitionTests(unittest.TestCase):
    def test_types_are_classified_from_explicit_labels_not_item_names(self):
        self.assertEqual(account.classify_type("Sword"), "weapon")
        self.assertEqual(account.classify_type("Trinket"), "trinket")
        self.assertIsNone(account.classify_type("Legendary"))
        self.assertIsNone(account.classify_type("Sword Trinket"))

    def test_low_confidence_is_null_with_diagnostic(self):
        api = Mock()
        api.ocr_region.return_value = ("guess", 12)
        value, diagnostic = account.read_field(api, np.zeros((40, 40, 3), np.uint8),
                                               {"x": 0, "y": 0, "w": 1, "h": 1})
        self.assertIsNone(value)
        self.assertEqual(diagnostic["raw"], "guess")
        api.parse_value.assert_not_called()

    def test_visual_type_samples_with_same_label_do_not_compete(self):
        white = np.full((24, 24, 3), 255, np.uint8)
        samples = [{"value": "weapon", "pixels": white.tolist()},
                   {"value": "weapon", "pixels": (white - 1).tolist()},
                   {"value": "trinket", "pixels": np.zeros_like(white).tolist()}]
        self.assertEqual(account.sample_value(white, samples)[0], "weapon")

    def test_star_count_rejects_noncontiguous_or_unknown_symbols(self):
        filled, empty = np.full((24, 24, 3), 250, np.uint8), np.zeros((24, 24, 3), np.uint8)
        spec = {"slots": [{"x": i / 5, "y": 0, "w": .2, "h": 1} for i in range(5)],
                "samples": [{"value": "filled", "pixels": filled.tolist()},
                            {"value": "empty", "pixels": empty.tolist()}]}
        self.assertEqual(account.read_stars(np.hstack([filled] * 3 + [empty] * 2), spec), 3)
        self.assertIsNone(account.read_stars(np.hstack([filled, empty, filled, empty, empty]), spec))
        self.assertIsNone(account.read_stars(np.full((24, 120, 3), 127, np.uint8), spec))

    def test_scroll_registration_and_partial_row_slot_identity(self):
        random = np.random.default_rng(31)
        content = random.integers(0, 255, (1200, 500, 3), dtype=np.uint8)
        region = {"x": 0, "y": 0, "w": 1, "h": 1}
        self.assertAlmostEqual(estimate_scroll_shift(content[:400], content[120:520], region, .3), .3)
        self.assertEqual(estimate_scroll_shift(content[:400], content[:400], region, .3), 0)
        slots = list(visible_slots(configuration()["grid"], .3))
        self.assertEqual([identity for identity, _ in slots], [(1, 0), (1, 1), (2, 0), (2, 1), (3, 0), (3, 1)])
        partial = list(visible_slots(configuration()["grid"], .15))
        self.assertEqual(partial[0][0], (1, 0))

    def test_unrelated_scroll_frame_is_rejected(self):
        random = np.random.default_rng(4)
        before = random.integers(0, 255, (400, 500, 3), dtype=np.uint8)
        after = random.integers(0, 255, (400, 500, 3), dtype=np.uint8)
        with self.assertRaises(NavigationError):
            estimate_scroll_shift(before, after, {"x": 0, "y": 0, "w": 1, "h": 1}, .3)

    def test_calibration_out_of_window_is_rejected(self):
        config = configuration()
        config["grid"]["column_pitch"] = 3
        with self.assertRaises(RuntimeError):
            account.validate_config(config, "roster", soc_scraper.CAPTURE_BACKEND)

    def test_inline_details_do_not_depend_on_selected_border(self):
        config = configuration()
        config["details_mode"] = "inline"
        config["selection_marker"] = {"x": 0, "y": 0, "w": .1, "h": 1,
                                       "pixels": np.full((24, 24, 3), 250, np.uint8).tolist()}
        frame = np.zeros((400, 500, 3), np.uint8)
        crop(frame, {"x": .05, "y": .05, "w": .035, "h": .2})[:] = 250
        scanner = account.Scanner(Mock(), 1, "equipment", config, Mock(), Mock())
        with patch.object(account, "matches_page", return_value=True):
            self.assertTrue(scanner.is_page(frame, "list_page"))
            self.assertTrue(scanner.is_page(frame, "details_page"))
            # A bad legacy marker is deliberately ignored, even by validation.
            config["selection_marker"]["x"] = -10
            account.validate_config(config, "equipment", soc_scraper.CAPTURE_BACKEND)
            self.assertTrue(scanner.is_page(frame, "details_page"))

    def test_inline_equipment_recovery_does_not_click_back(self):
        config = configuration()
        config["details_mode"] = "inline"
        scanner = account.Scanner(Mock(), 1, "equipment", config, Mock(), Mock())
        scanner.capture = Mock(return_value=np.zeros((400, 500, 3), np.uint8))
        scanner.wait_for_list = Mock()
        with patch.object(account, "matches_page", return_value=True):
            scanner.recover_list()
        scanner.input.click.assert_not_called()


class ScanLoopTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "account.json"

    def scanner(self, kind, count, records):
        store = account.AccountStore(self.path, kind, count)
        scanner = account.Scanner(Mock(), 1, kind, configuration(kind), store, Mock())
        frame = np.zeros((400, 500, 3), np.uint8)
        scanner.wait_for_list = Mock(return_value=frame)
        scanner.wait_for_character_details = Mock(return_value=frame)
        scanner.wait_for_equipment_details = Mock(return_value=frame)
        scanner.recover_list = Mock(return_value=frame)
        scanner.read_character = Mock(side_effect=records)
        scanner.read_equipment = Mock(side_effect=records)
        return scanner

    def test_roster_counts_unique_successes_continues_after_bad_entry(self):
        scanner = self.scanner("roster", 2, [{"name": "A"}, {"name": "A"}, ValueError("bad OCR"), {"name": "B"}])
        scanner.run(2)
        saved = json.loads(self.path.read_text())
        self.assertEqual([item["name"] for item in saved["characters"]], ["A", "B"])
        self.assertEqual(saved["scans"]["roster"]["status"], "complete")
        self.assertEqual(len(saved["scans"]["roster"]["errors"]), 1)
        self.assertEqual(scanner.input.click.call_count, 4)

    def test_equipment_preserves_identical_copies_across_overlapping_scroll(self):
        scanner = self.scanner("equipment", 8, [{"name": "Sword", "type": "weapon"} for _ in range(8)])
        scrolling = [False]
        scanner.input.scroll.side_effect = lambda *args: scrolling.__setitem__(0, True)

        def shift(*args):
            if scrolling[0]:
                scrolling[0] = False
                return .3
            return 0

        with patch.object(account, "estimate_scroll_shift", side_effect=shift):
            scanner.run(8)
        saved = json.loads(self.path.read_text())
        items = saved["equipment"]
        self.assertEqual(len(items), 8)
        self.assertEqual(len({item["id"] for item in items}), 8)
        self.assertEqual([(item["source"]["row"], item["source"]["column"]) for item in items],
                         [(0, 0), (0, 1), (1, 0), (1, 1), (2, 0), (2, 1), (3, 0), (3, 1)])
        self.assertEqual(scanner.input.click.call_count, 8)

    def test_ctrl_c_preserves_first_saved_entry(self):
        scanner = self.scanner("equipment", 3, [{"name": "Sword", "type": "weapon"}, KeyboardInterrupt()])
        scanner.run(3)
        saved = json.loads(self.path.read_text())
        self.assertEqual(len(saved["equipment"]), 1)
        self.assertEqual(saved["scans"]["equipment"]["status"], "cancelled")

    def test_end_of_list_is_bounded_and_marked_partial(self):
        scanner = self.scanner("equipment", 50, [{"name": "Sword", "type": "weapon"} for _ in range(6)])
        scanner.run(50)
        self.assertEqual(scanner.input.scroll.call_count, 2)
        self.assertEqual(scanner.store.run["status"], "end_of_list")
        self.assertEqual(scanner.store.run["collected"], 6)

    def test_unknown_recovery_state_stops_with_saved_data(self):
        scanner = self.scanner("roster", 3, [{"name": "A"}])
        scanner.recover_list.side_effect = NavigationError("unknown page")
        scanner.run(3)
        self.assertEqual(scanner.store.run["status"], "stopped")
        self.assertEqual(len(json.loads(self.path.read_text())["characters"]), 1)
        self.assertEqual(scanner.input.click.call_count, 1)

    def test_details_wait_discards_a_briefly_stale_name(self):
        store = account.AccountStore(self.path, "roster", 1)
        scanner = account.Scanner(Mock(), 1, "roster", configuration(), store, Mock())
        stale = np.zeros((400, 500, 3), np.uint8)
        updated = np.full((400, 500, 3), 200, np.uint8)
        scanner.capture = Mock(side_effect=[stale, stale, updated, updated, updated])
        scanner.is_page = Mock(return_value=True)
        ticks = iter(index / 10 for index in range(100))
        with patch.object(account.time, "monotonic", side_effect=lambda: next(ticks)), \
                patch.object(account.time, "sleep"):
            result = scanner.wait_for_character_details()
        self.assertIs(result, updated)
        self.assertEqual(scanner.capture.call_count, 5)


class InputTests(unittest.TestCase):
    def test_focus_loss_sends_no_input(self):
        with patch("soc_navigation.win32gui.GetForegroundWindow", return_value=2), \
                patch("soc_navigation._mouse_event") as event, \
                self.assertRaises(NavigationError):
            WindowInput(1).click({"x": .2, "y": .3})
        event.assert_not_called()


if __name__ == "__main__":
    unittest.main()
