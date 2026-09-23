import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import numpy as np

import soc_account
import soc_calibration
import soc_scraper as scraper


REGION = {"x": 0., "y": 0., "w": 1., "h": 1., "type": "int"}


class CombatStatTests(unittest.TestCase):
    def setUp(self):
        self.frame = np.zeros((80, 160, 3), dtype=np.uint8)
        self.regions = {name: dict(REGION) for name in scraper.STAT_FIELDS}

    def test_final_displayed_values_are_read_without_recomputing(self):
        displayed = {"hp": 6269, "p_atk": 3505, "m_atk": 1722,
                     "p_def": 740, "m_def": 745, "speed": 180}
        with patch.object(scraper, "ocr_region", side_effect=[(str(value), 96) for value in displayed.values()]):
            stats, diagnostics = scraper.read_combat_stats(self.frame, self.regions)
        self.assertEqual(stats, displayed)
        self.assertEqual(diagnostics["p_atk"], {"raw": "3505", "confidence": .96, "status": "ok"})

    def test_one_failed_stat_does_not_discard_remaining_fields(self):
        results = [("6421", 96), ("1842", 22), RuntimeError("OCR timeout"),
                   ("811", 95), ("704 + 10", 93), ("162", 91)]
        with patch.object(scraper, "ocr_region", side_effect=results):
            stats, diagnostics = scraper.read_combat_stats(self.frame, self.regions)
        self.assertEqual(stats, {"hp": 6421, "p_atk": None, "m_atk": None,
                                 "p_def": 811, "m_def": None, "speed": 162})
        self.assertEqual(diagnostics["p_atk"]["raw"], "1842")
        self.assertEqual(diagnostics["p_atk"]["confidence"], .22)
        self.assertEqual(diagnostics["m_atk"]["error"], "OCR timeout")
        self.assertEqual(diagnostics["m_def"]["status"], "invalid_number")

    def test_unconfigured_stats_are_null_and_not_ocrd(self):
        with patch.object(scraper, "ocr_region") as ocr:
            stats, diagnostics = scraper.read_combat_stats(self.frame, {})
        self.assertEqual(stats, dict.fromkeys(scraper.STAT_FIELDS))
        self.assertTrue(all(item["status"] == "not_calibrated" for item in diagnostics.values()))
        ocr.assert_not_called()

    def test_strict_numeric_parsing_and_thousands_separators(self):
        for raw, expected in (("6,421", 6421), ("6 421", 6421), ("6\u202f421", 6421),
                              ("0", 0), ("60/60", None), ("1842.5", None),
                              ("+88", None), ("7%", None), ("P.ATK 1842", None),
                              ("1,84", None), ("1842 88", None), ("", None)):
            with self.subTest(raw=raw), patch.object(scraper, "ocr_region", return_value=(raw, 95)):
                stats, diagnostics = scraper.read_combat_stats(self.frame, {"hp": dict(REGION)})
                self.assertEqual(stats["hp"], expected)
                self.assertEqual(diagnostics["hp"]["raw"], raw)

    def test_bad_crop_does_not_stop_other_stats(self):
        self.regions["hp"]["x"] = 2
        with patch.object(scraper, "ocr_region", return_value=("123", 95)):
            stats, diagnostics = scraper.read_combat_stats(self.frame, self.regions)
        self.assertIsNone(stats["hp"])
        self.assertEqual(diagnostics["hp"]["status"], "error")
        self.assertEqual(stats["speed"], 123)

    def test_roster_record_and_incremental_json_include_stats_and_diagnostics(self):
        config = {"fields": {"name": dict(REGION)}, "stats": self.regions}
        scanner = soc_account.Scanner(scraper, 1, "roster", config, Mock(), Mock())
        with patch.object(scraper, "extract_fields", return_value=(
                {"name": "Rawiyah - Nostalgia"}, {"name": {"raw": "Rawiyah - Nostalgia", "confidence": .95}})), \
                patch.object(scraper, "ocr_region", side_effect=[("6269", 95), ("3505", 95), ("1722", 95),
                                                                 ("740", 95), ("745", 10), ("180", 95)]):
            record = scanner.read_character(self.frame)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "soc_account.json"
            store = soc_account.AccountStore(path, "roster", 1)
            store.add(record)
            saved = json.loads(path.read_text(encoding="utf-8"))["characters"][0]
        self.assertEqual(saved["stats"]["hp"], 6269)
        self.assertIsNone(saved["stats"]["m_def"])
        self.assertEqual(saved["ocr"]["stats"]["m_def"]["raw"], "745")
        self.assertEqual(saved["name"], "Rawiyah - Nostalgia")
        self.assertIn("equipped", saved)

    def test_calibration_selects_each_stat_number_once(self):
        with patch.object(soc_calibration, "optional_field", side_effect=[dict(REGION) for _ in range(6)]) as select:
            regions = soc_calibration.stat_regions(scraper, self.frame, {}, False)
        self.assertEqual(select.call_count, 6)
        self.assertEqual(set(regions), set(scraper.STAT_FIELDS))
        self.assertTrue(all(region["min_confidence"] == 70 for region in regions.values()))
        with patch.object(soc_calibration, "optional_field") as select:
            reused = soc_calibration.stat_regions(scraper, self.frame, {"fields": regions}, True)
        select.assert_not_called()
        self.assertEqual(reused, regions)

    def test_single_character_scrape_also_nests_stats(self):
        fields = {"character_name": {"type": "text"}, **{name: {"type": "int"} for name in scraper.STAT_FIELDS}}
        regions = {"_screen": {"capture_backend": scraper.CAPTURE_BACKEND},
                   "fields": {"character_name": dict(REGION), **self.regions}}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "soc_data.json"
            with patch.object(scraper, "load_fields", return_value=fields), \
                    patch.object(scraper, "load_regions", return_value=regions), \
                    patch.object(scraper, "find_window", return_value=(1, "Sword of Convallaria")), \
                    patch.object(scraper, "capture_window", return_value=self.frame), \
                    patch.object(scraper, "ocr_region", side_effect=[("Rawiyah", 95)] + [("123", 95)] * 6):
                scraper.scrape("Sword of Convallaria", str(path))
            output = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(output["data"]["character_name"], "Rawiyah")
        self.assertEqual(output["data"]["stats"]["hp"], 123)
        self.assertNotIn("hp", output["data"])
        self.assertEqual(output["ocr"]["stats"]["speed"]["raw"], "123")


if __name__ == "__main__":
    unittest.main()
