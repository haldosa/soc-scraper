import contextlib
import copy
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import numpy as np

import soc_account as account
import soc_calibration as calibration
import soc_panels as panels
import soc_scraper
from soc_navigation import NavigationError, crop, normalized_rectangle
from test_soc_account import configuration


REGION = {"x": 0., "y": 0., "w": 1., "h": 1.}
PAGE = {"region": REGION, "template": [[0, 255], [255, 0]]}


class RectangleDiagnosticsTests(unittest.TestCase):
    def test_tiny_overflow_and_negative_roundoff_are_clamped(self):
        spec = {"x": -.000001, "y": 0., "w": 1.000002, "h": 1.000001, "type": "int"}
        result = normalized_rectangle(spec, "equipment.fields.name")
        self.assertEqual(result, {**REGION, "type": "int"})
        self.assertEqual(crop(np.zeros((50, 80, 3), np.uint8), spec).shape, (50, 80, 3))

    def test_genuine_overflow_empty_nan_and_infinite_regions_are_rejected(self):
        for change in ({"x": -.01}, {"w": 1.1}, {"h": 0}, {"x": 1.}, {"x": float("nan")}, {"h": float("inf")}):
            with self.subTest(change=change), self.assertRaisesRegex(ValueError, "Invalid rectangle: equipment.fields.name"):
                normalized_rectangle({**REGION, **change}, "equipment.fields.name")

    def test_exact_offending_field_and_edges_are_reported(self):
        config = configuration("equipment")
        config["fields"]["name"] = {"x": .8, "y": .2, "w": .3, "h": .1}
        with self.assertRaises(RuntimeError) as caught:
            account.validate_config(config, "equipment", soc_scraper.CAPTURE_BACKEND)
        for text in ("Invalid rectangle: equipment.fields.name", "x=0.800000000", "right=1.100000000", "bottom=0.300000000"):
            self.assertIn(text, str(caught.exception))

    def test_shared_and_star_rectangles_have_named_paths(self):
        config = configuration()
        config["character_details"] = {"gear": {"detail_marker": copy.deepcopy(PAGE), "name": {**REGION, "w": 2.}}}
        with self.assertRaisesRegex(RuntimeError, "roster.character_details.gear.name"):
            account.validate_config(config, "roster", soc_scraper.CAPTURE_BACKEND)
        del config["character_details"]
        config["stars"] = {"slots": [{**REGION, "y": -.1}]}
        with self.assertRaisesRegex(RuntimeError, r"roster.stars.slots\[0\]"):
            account.validate_config(config, "roster", soc_scraper.CAPTURE_BACKEND)

    def test_clamped_values_are_stored_and_old_card_relative_marker_is_ignored(self):
        config = configuration("equipment")
        config["fields"]["name"] = {**REGION, "w": 1.000001}
        config["selection_marker"] = {"x": -.07, "y": -.01, "w": .1, "h": .1}
        account.validate_config(config, "equipment", soc_scraper.CAPTURE_BACKEND)
        self.assertEqual(config["fields"]["name"]["w"], 1.)


class SharedCalibrationTests(unittest.TestCase):
    def test_routes_resolve_same_gear_and_skill_regions_but_distinct_tarot(self):
        gear = {"detail_marker": PAGE, "name": dict(REGION), "engraving_type": dict(REGION), "engraving_stats": dict(REGION)}
        skills = {"detail_marker": PAGE, "name": dict(REGION)}
        tarot = {"detail_marker": PAGE, "name": {"x": .4, "y": .1, "w": .3, "h": .1}, "main_stats": dict(REGION)}
        config = {"character_details": {"gear": gear, "skills": skills, "tarot": tarot},
                  "build_targets": {slot: {"x": .5, "y": .8} for slot in ("weapon", "trinket", "skill_1", "skill_2", "tarot")}}
        routes = panels.panel_routes(config)
        self.assertIs(routes["weapon"]["fields"]["name"], routes["trinket"]["fields"]["name"])
        self.assertIs(routes["skill_1"]["fields"]["name"], routes["skill_2"]["fields"]["name"])
        self.assertIsNot(routes["tarot"]["fields"]["name"], routes["weapon"]["fields"]["name"])
        self.assertEqual(routes["tarot"]["panel_kind"], "tarot")
        self.assertNotIn("close", routes["weapon"])

    def test_wizard_calibrates_only_three_shared_panels_for_six_targets(self):
        frame = np.zeros((40, 50, 3), np.uint8)
        with patch.object(calibration, "number", return_value=3), patch.object(calibration, "answer") as answer, \
                patch.object(calibration, "select", side_effect=lambda *a, **kw: dict(REGION)) as select, \
                patch.object(calibration, "optional_field", return_value=None), \
                patch.object(calibration, "anchor", return_value=PAGE) as anchor, \
                patch.object(calibration, "stage", return_value=frame), \
                patch.object(calibration, "matches_page", side_effect=[False, False, False, True]):
            targets, shared = calibration.build_panels(Mock(), 1, frame, PAGE)
        self.assertEqual(len(targets), 6)
        self.assertIn("tarot", targets)
        answer.assert_not_called()  # Equipped Tarot is included without an opt-in question.
        self.assertEqual(set(shared), {"gear", "skills", "tarot"})
        self.assertEqual(anchor.call_count, 3)
        name_prompts = [call.args[1] for call in select.call_args_list if "NAME" in call.args[1]]
        self.assertEqual(len(name_prompts), 3)

    def test_inventory_wizards_never_request_selected_border_or_back(self):
        frame = np.zeros((400, 500, 3), np.uint8)
        for kind in ("equipment",):
            with self.subTest(kind=kind), contextlib.ExitStack() as stack:
                for name, result in (("guided_prompt", True), ("bootstrap_page", True), ("anchor", copy.deepcopy(PAGE)),
                                     ("grid", configuration()["grid"]), ("stage", frame), ("stars", None),
                                     ("optional_field", None), ("load_json", {}), ("matches_page", True)):
                    stack.enter_context(patch.object(calibration, name, return_value=result))
                select = stack.enter_context(patch.object(calibration, "select", side_effect=lambda *a, **kw: dict(REGION)))
                saved = stack.enter_context(patch.object(calibration, "atomic_json"))
                stack.enter_context(patch.object(calibration, "add_icon_samples"))
                stack.enter_context(patch.object(calibration.cv2, "destroyAllWindows"))
                api = Mock(CAPTURE_BACKEND=soc_scraper.CAPTURE_BACKEND)
                api.find_window.return_value = (1, "Sword of Convallaria")
                api.capture_window.return_value = frame
                calibration.calibrate_scanner(SimpleNamespace(command=f"calibrate-{kind}", title="SoC", config="unused.json"), api)
                config = saved.call_args.args[1][kind]
                self.assertNotIn("selection_marker", config)
                self.assertNotIn("back", config)
                self.assertEqual(config["details_mode"], "inline")
                self.assertFalse(any("Selected FIRST" in call.args[1] for call in select.call_args_list))


class PanelUpdateTests(unittest.TestCase):
    def run_wait(self, frames, read, allow_unchanged=False, page_matches=None):
        scanner = account.Scanner(Mock(), 1, "equipment", configuration("equipment"), Mock(), Mock())
        scanner.config["detail_timeout"] = 2
        scanner.capture = Mock(side_effect=frames)
        ticks = iter(i / 10 for i in range(1000))
        with patch.object(account, "matches_page", side_effect=page_matches, return_value=True), \
                patch.object(account, "read_field", side_effect=read), \
                patch.object(account.time, "monotonic", side_effect=lambda: next(ticks)), \
                patch.object(account.time, "sleep"):
            result = scanner.wait_for_detail_name_change(np.zeros((40, 50, 3), np.uint8), PAGE,
                                                        {"name": REGION}, allow_unchanged)
        return scanner, result

    def test_waits_through_stale_name_until_new_name(self):
        old = np.zeros((40, 50, 3), np.uint8)
        # No pixel change: prove that an OCR name change is independently sufficient.
        names = iter(["Old", "Old", "New", "New"])
        scanner, _ = self.run_wait([old] * 20, lambda *a: (next(names), {}))
        self.assertEqual(scanner.last_panel_update["current_name"], "New")
        self.assertGreater(scanner.capture.call_count, 2)

    def test_same_name_changed_panel_is_accepted(self):
        new = np.full((40, 50, 3), 200, np.uint8)
        scanner, result = self.run_wait([new] * 20, lambda *a: ("Sword", {}))
        self.assertIs(result, new)
        self.assertEqual(scanner.last_panel_update["status"], "updated")

    def test_identical_inventory_copy_gets_bounded_unchanged_diagnostic(self):
        old = np.zeros((40, 50, 3), np.uint8)
        scanner, result = self.run_wait([old] * 40, lambda *a: ("Sword", {}), True)
        self.assertIs(result, old)
        self.assertEqual(scanner.last_panel_update["status"], "unchanged_after_timeout")

    def test_unchanged_skill_times_out_instead_of_repeating_old_name(self):
        old = np.zeros((40, 50, 3), np.uint8)
        with self.assertRaisesRegex(NavigationError, "Entry not counted"):
            self.run_wait([old] * 40, lambda *a: ("Skill A", {}))

    def test_unreadable_inventory_never_accepted_as_duplicate(self):
        old = np.zeros((40, 50, 3), np.uint8)
        with self.assertRaises(NavigationError):
            self.run_wait([old] * 40, lambda *a: (None, {}), True)

    def test_marker_transition_allows_same_named_entry(self):
        old = np.zeros((40, 50, 3), np.uint8)
        calls = iter([True, False] + [True] * 40)
        scanner, _ = self.run_wait([old] * 40, lambda *a: ("Sword", {}), page_matches=lambda *a: next(calls))
        self.assertEqual(scanner.last_panel_update["status"], "updated")


class TarotTests(unittest.TestCase):
    def stat_diagnostic(self):
        return {"raw": "", "confidence": .95,
                "lines": [{"raw": row, "confidence": .95} for row in
                          ("P.ATK 230", "M.ATK 230", "P.DEF 86", "M.DEF 86", "Max HP 551")]}

    def test_reference_stats_and_wrapped_effects(self):
        stats, _ = panels.parse_tarot_stats(self.stat_diagnostic())
        self.assertEqual(stats, {"p_atk": 230, "m_atk": 230, "p_def": 86, "m_def": 86, "max_hp": 551})
        effects = panels.effect_entries("Increases [P.ATK] by\n77\nIncreases [P.ATK] by\n10.0%\n"
                                        "Increases [P.ATK] by\n8.0%\nChanges the effect of [Tarot Whisper Skill]\ninto: Increases DMG by 16%.")
        self.assertEqual(len(effects), 4)
        self.assertEqual(effects[0], "Increases [P.ATK] by 77")
        self.assertIn("10.0%", effects[1])
        self.assertIn("16%", effects[-1])

    def test_uncertain_ambiguous_and_noninteger_stats_are_null(self):
        diagnostic = self.stat_diagnostic()
        diagnostic["lines"][1]["confidence"] = .2
        diagnostic["lines"][2]["raw"] = "P.DEF 86 + 10"
        diagnostic["lines"][3]["raw"] = "M.DEF 86%"
        diagnostic["lines"].append({"raw": "P.ATK 999", "confidence": .99})
        stats, details = panels.parse_tarot_stats(diagnostic)
        self.assertEqual(stats, {"p_atk": None, "m_atk": None, "p_def": None, "m_def": None, "max_hp": 551})
        self.assertEqual(details["p_atk"]["status"], "ambiguous")

    def test_tarot_record_and_missing_owner_use_shared_reader(self):
        fields = {name: dict(REGION) for name in ("name", "level", "main_stats", "details", "skill")}
        scanner = account.Scanner(Mock(), 1, "roster", configuration(), Mock(), Mock())
        diagnostic = self.stat_diagnostic()
        with patch.object(account, "read_field", side_effect=[("Dream of The Magician", {}), (60, {})]), \
                patch.object(account, "read_block", side_effect=[(None, diagnostic), ("Increases P.ATK by 77", {}), ("Increases DMG by 8%.", {})]):
            record = scanner.read_tarot(np.zeros((40, 50, 3), np.uint8), fields)
        self.assertEqual(record["name"], "Dream of The Magician")
        self.assertEqual(record["stats"]["max_hp"], 551)
        self.assertIsNone(record["equipped_by"])
        self.assertEqual(record["details"], ["Increases P.ATK by 77"])
        self.assertEqual(record["skill"], "Increases DMG by 8%.")
        self.assertIn("stats", record["ocr"])

    def test_block_ocr_keeps_line_boundaries_and_confidence(self):
        data = {"text": ["P.ATK", "230", "M.ATK", "230"], "conf": [96, 95, 94, 95],
                "block_num": [1] * 4, "par_num": [1] * 4, "line_num": [1, 1, 2, 2]}
        api = Mock()
        api.create_ocr_versions.return_value = [np.zeros((20, 20), np.uint8)]
        with patch.object(panels.pytesseract, "image_to_data", return_value=data):
            raw, diagnostic = panels.read_block(api, np.zeros((40, 50, 3), np.uint8), REGION)
        self.assertEqual(raw, "P.ATK 230\nM.ATK 230")
        self.assertEqual(len(diagnostic["lines"]), 2)

    def test_roster_scans_equipped_tarot_and_saves_it_with_each_character(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "account.json"
            path.write_text(json.dumps({"game": "Sword of Convallaria", "characters": [],
                                        "equipment": [{"id": "equipment_001", "name": "Sword", "type": "weapon"}]}))
            config = configuration()
            config["build_targets"] = {"tarot": {"x": .8, "y": .6}}
            config["character_details"] = {"tarot": {"detail_marker": PAGE, **{name: dict(REGION) for name in
                                              ("name", "level", "main_stats", "details", "skill")}}}
            store = account.AccountStore(path, "roster", 3)
            api = Mock()
            api.extract_fields.side_effect = [({"name": "Rawiyah"}, {"name": {"confidence": .95}}),
                                              ({"name": "Inanna"}, {"name": {"confidence": .96}}), KeyboardInterrupt()]
            api.read_combat_stats.return_value = ({"hp": 6269}, {})
            scanner = account.Scanner(api, 1, "roster", config, store, Mock())
            frame = np.zeros((400, 500, 3), np.uint8)
            scanner.wait_for_list = Mock(return_value=frame)
            scanner.wait_for_character_details = Mock(return_value=frame)
            scanner.wait_for_detail_name_change = Mock(return_value=frame)
            scanner.capture = Mock(return_value=frame)
            scanner.is_page = Mock(return_value=True)
            scanner.recover_list = Mock(return_value=frame)
            with patch.object(account, "read_field", side_effect=[("Dream of The Magician", {}), (60, {})] * 2), \
                    patch.object(account, "read_block", side_effect=[(None, self.stat_diagnostic()),
                        ("Increases P.ATK by 77", {}), ("Increases DMG by 8%.", {})] * 2):
                scanner.run(3)
            saved = json.loads(path.read_text())
            self.assertNotIn("tarots", saved)
            self.assertEqual(saved["equipment"][0]["name"], "Sword")
            self.assertEqual(len(saved["characters"]), 2)
            for character in saved["characters"]:
                self.assertEqual(character["equipped"]["tarot"], "Dream of The Magician")
                self.assertEqual(character["tarot"]["equipped_by"], character["name"])
                self.assertEqual(character["tarot"]["stats"]["max_hp"], 551)
                self.assertEqual(character["tarot"]["ocr"]["equipped_by"]["method"], "character_context")
            self.assertEqual(saved["scans"]["roster"]["status"], "cancelled")
            clicks = [call.args[0] for call in scanner.input.click.call_args_list]
            self.assertEqual(clicks.count(config["build_targets"]["tarot"]), 2)

    def test_unconfigured_tarot_is_null_with_a_warning(self):
        api = Mock()
        api.extract_fields.return_value = ({"name": "Rawiyah"}, {"name": {"confidence": .95}})
        api.read_combat_stats.return_value = ({}, {})
        scanner = account.Scanner(api, 1, "roster", configuration(), Mock(), Mock())
        record = scanner.read_character(np.zeros((40, 50, 3), np.uint8))
        self.assertIsNone(record["tarot"])
        self.assertIsNone(record["equipped"]["tarot"])
        self.assertIn("tarot: not calibrated", record["warnings"])


if __name__ == "__main__":
    unittest.main()
