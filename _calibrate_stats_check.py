import json
import cv2
import soc_scraper as api
from soc_account import atomic_json, load_json
from soc_calibration import stat_regions

api.setup_tesseract()
hwnd, _ = api.find_window("Sword of Convallaria")
frame = api.capture_window(hwnd)
regions = api.load_regions()
try:
    selected = stat_regions(api, frame, {}, False)
    regions["fields"].update(selected)
    atomic_json("regions.json", regions)
    scanner_config = load_json("scanner_regions.json", {})
    if scanner_config.get("roster"):
        scanner_config["roster"]["stats"] = selected
        atomic_json("scanner_regions.json", scanner_config)
    values, diagnostics = api.read_combat_stats(frame, selected)
    print(json.dumps({"stats": values, "ocr": diagnostics}, indent=2), flush=True)
finally:
    cv2.destroyAllWindows()
