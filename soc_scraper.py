import argparse
import ctypes
import datetime
import json
import os
import re
import shutil
import sys
import time

import cv2
import pytesseract
import win32gui
from windows_capture import WindowsCapture, Frame, InternalCaptureControl


CAPTURE_BACKEND = "windows-graphics-capture-hwnd"
STAT_FIELDS = {
    "hp": "HP", "p_atk": "P.ATK", "m_atk": "M.ATK",
    "p_def": "P.DEF", "m_def": "M.DEF", "speed": "Speed",
}


# -----------------------------------------------------------
# DPI handling
# -----------------------------------------------------------

try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass


# -----------------------------------------------------------
# Tesseract
# -----------------------------------------------------------

def setup_tesseract():
    if shutil.which("tesseract"):
        return

    possible_paths = [
        r"C:\Program Files\Tesseract-OCR\tesseract.exe",
        r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
    ]

    for path in possible_paths:
        if os.path.exists(path):
            pytesseract.pytesseract.tesseract_cmd = path
            return

    print("ERROR: Tesseract was not found.")
    print("Install it or add tesseract.exe to PATH.")
    sys.exit(1)


# -----------------------------------------------------------
# Windows
# -----------------------------------------------------------

def get_windows():
    windows = []

    def callback(hwnd, _):
        if not win32gui.IsWindowVisible(hwnd):
            return

        title = win32gui.GetWindowText(hwnd).strip()

        if title:
            windows.append((hwnd, title))

    win32gui.EnumWindows(callback, None)

    return windows


def list_windows():
    print("\nVisible windows:\n")

    for hwnd, title in get_windows():
        print(f"{hwnd:<12} {title}")


def find_window(title_fragment):
    fragment = title_fragment.casefold().strip()
    if not fragment:
        raise RuntimeError("Window title must not be empty.")

    windows = get_windows()
    matches = [
        (hwnd, title) for hwnd, title in windows
        if title.casefold().strip() == fragment
    ]
    if not matches:
        matches = [
            (hwnd, title) for hwnd, title in windows
            if fragment in title.casefold()
        ]

    if not matches:
        raise RuntimeError(
            f'Could not find window "{title_fragment}". '
            "Run: python soc_scraper.py windows"
        )

    if len(matches) > 1:
        candidates = ", ".join(f"{hwnd}: {title}" for hwnd, title in matches)
        raise RuntimeError(
            f"Multiple matching windows: {candidates}. "
            "Use --title with the exact game window title."
        )

    hwnd, title = matches[0]

    print(f'Using window: "{title}"')

    return hwnd, title


# -----------------------------------------------------------
# Capture the actual HWND, including any window decorations
# -----------------------------------------------------------

def capture_window(hwnd, timeout=10.0):
    """Return an owned BGR frame from WGC; never fall back to desktop pixels."""
    if not win32gui.IsWindow(hwnd):
        raise RuntimeError("Game window no longer exists. Find the window again.")
    if win32gui.IsIconic(hwnd):
        raise RuntimeError("Game window is minimized. Restore it before capturing.")
    if timeout <= 0:
        raise ValueError("Capture timeout must be positive.")

    result = {"image": None}
    capture = WindowsCapture(
        cursor_capture=False,
        draw_border=False,
        monitor_index=None,
        window_name=None,
        window_hwnd=hwnd,
    )

    @capture.event
    def on_frame_arrived(frame: Frame, capture_control: InternalCaptureControl):
        try:
            # Native frame storage is only borrowed. Copy before stopping capture.
            result["image"] = frame.convert_to_bgr().frame_buffer.copy()
        finally:
            capture_control.stop()

    @capture.event
    def on_closed():
        pass

    control = capture.start_free_threaded()
    try:
        deadline = time.monotonic() + timeout
        while not control.is_finished():
            if time.monotonic() >= deadline:
                raise RuntimeError(
                    "Timed out capturing the game window. Keep it restored "
                    "and rendering, then try again."
                )
            time.sleep(0.01)
    finally:
        # stop also joins the native thread and surfaces callback errors.
        control.stop()

    image = result["image"]
    if image is None or image.size == 0:
        raise RuntimeError("Could not capture the Sword of Convallaria window.")
    return image


# -----------------------------------------------------------
# Image processing
# -----------------------------------------------------------

def create_ocr_versions(image):
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)

    # Make small game text much larger for OCR
    gray = cv2.resize(
        gray,
        None,
        fx=3,
        fy=3,
        interpolation=cv2.INTER_CUBIC
    )

    # Improve contrast
    gray = cv2.GaussianBlur(gray, (3, 3), 0)

    _, threshold = cv2.threshold(
        gray,
        0,
        255,
        cv2.THRESH_BINARY + cv2.THRESH_OTSU
    )

    inverted = cv2.bitwise_not(threshold)

    return [
        gray,
        threshold,
        inverted
    ]


def run_tesseract(image, field_type):
    if field_type == "int":
        config = (
            "--oem 3 "
            "--psm 7 "
            "-c tessedit_char_whitelist=0123456789,./"
        )
    else:
        config = "--oem 3 --psm 7"

    data = pytesseract.image_to_data(
        image,
        config=config,
        output_type=pytesseract.Output.DICT,
        timeout=10
    )

    words = []
    confidences = []

    for text, confidence in zip(data["text"], data["conf"]):
        text = text.strip()

        try:
            confidence = float(confidence)
        except ValueError:
            confidence = -1

        if text and confidence >= 0:
            words.append(text)
            confidences.append(confidence)

    full_text = " ".join(words).strip()

    if confidences:
        confidence = sum(confidences) / len(confidences)
    else:
        confidence = 0

    return full_text, confidence


def ocr_region(image, field_type):
    best_text = ""
    best_confidence = -1

    for processed in create_ocr_versions(image):
        text, confidence = run_tesseract(
            processed,
            field_type
        )

        if confidence > best_confidence:
            best_text = text
            best_confidence = confidence

    return best_text, best_confidence


# -----------------------------------------------------------
# Parsing
# -----------------------------------------------------------

def parse_value(raw_text, field_type):
    raw_text = raw_text.strip()

    if field_type == "text":
        raw_text = re.sub(r"\s+", " ", raw_text)
        return raw_text

    if field_type == "int":
        cleaned = raw_text.replace(",", "")

        match = re.search(r"\d+", cleaned)

        if not match:
            return None

        return int(match.group())

    return raw_text


def read_combat_stats(frame, regions):
    """OCR final displayed numbers, independently; never calculate or guess stats."""
    from soc_navigation import crop

    stats, diagnostics = {}, {}
    for name in STAT_FIELDS:
        stats[name] = None
        diagnostic = {"raw": "", "confidence": 0.0, "status": "not_calibrated"}
        diagnostics[name] = diagnostic
        region = regions.get(name)
        if not region:
            continue
        try:
            raw, confidence = ocr_region(crop(frame, region), "int")
            diagnostic.update(raw=raw, confidence=round(confidence / 100, 3), status="uncertain")
            if confidence < region.get("min_confidence", 70):
                continue
            # A complete integer or properly grouped thousands only. The general
            # level parser accepts fractions; a combat stat must not accept 60/60,
            # 1842 + 88, decimal values, percentages, or a stray digit in a label.
            if not re.fullmatch(r"(?:[0-9]+|[0-9]{1,3}(?:,[0-9]{3})+|[0-9]{1,3}(?:[ \u00a0\u202f][0-9]{3})+)", raw.strip()):
                diagnostic["status"] = "invalid_number"
                continue
            stats[name] = int(re.sub(r"[,\s]", "", raw))
            diagnostic["status"] = "ok"
        except (RuntimeError, ValueError, cv2.error) as error:
            diagnostic.update(status="error", error=str(error))
    return stats, diagnostics


# -----------------------------------------------------------
# Files
# -----------------------------------------------------------

def load_fields():
    if not os.path.exists("fields.json"):
        print("fields.json not found.")
        sys.exit(1)

    with open("fields.json", "r", encoding="utf-8") as f:
        return json.load(f)


def load_regions():
    if not os.path.exists("regions.json"):
        print("regions.json does not exist.")
        print("Run calibration first.")
        sys.exit(1)

    with open("regions.json", "r", encoding="utf-8") as f:
        return json.load(f)


# -----------------------------------------------------------
# Calibration
# -----------------------------------------------------------

def calibrate(title_fragment):
    fields = load_fields()

    hwnd, window_title = find_window(title_fragment)

    frame = capture_window(hwnd)

    height, width = frame.shape[:2]

    print()
    print(f"Captured game window: {width}x{height}")
    print()
    print("You will now select each field.")
    print("Drag a rectangle around ONLY the value.")
    print()
    print("After drawing:")
    print("  ENTER or SPACE = accept")
    print("  C              = cancel selection")
    print()

    regions = {
        "_screen": {
            "capture_backend": CAPTURE_BACKEND,
            "reference_width": width,
            "reference_height": height
        },
        "fields": {}
    }

    for field_name, field_info in fields.items():
        label = (f"FINAL {STAT_FIELDS[field_name]} number ONLY (main character Attributes panel)"
                 if field_name in STAT_FIELDS else field_name)
        print(f"Select: {label}")

        roi = cv2.selectROI(
            f"Select: {label}",
            frame,
            fromCenter=False,
            showCrosshair=True
        )

        cv2.destroyWindow(f"Select: {label}")

        x, y, w, h = roi

        if w == 0 or h == 0:
            print(f"Skipped {field_name}")
            continue

        # Save normalized coordinates so minor resizing still works.
        regions["fields"][field_name] = {
            "x": x / width,
            "y": y / height,
            "w": w / width,
            "h": h / height,
            "type": field_info.get("type", "text")
        }
        if field_name in STAT_FIELDS:
            regions["fields"][field_name]["min_confidence"] = 70

        print(
            f"Saved {field_name}: "
            f"x={x}, y={y}, w={w}, h={h}"
        )

    cv2.destroyAllWindows()

    with open("regions.json", "w", encoding="utf-8") as f:
        json.dump(
            regions,
            f,
            indent=2,
            ensure_ascii=False
        )

    print()
    print("Calibration complete.")
    print("Created regions.json")


# -----------------------------------------------------------
# Scraping
# -----------------------------------------------------------

def scrape(title_fragment, output_file):
    fields = load_fields()
    regions = load_regions()

    if (regions.get("_screen", {}).get("capture_backend") != CAPTURE_BACKEND
            or not regions.get("fields")):
        raise RuntimeError(
            "Run calibrate first: regions must be selected on the HWND capture, "
            "which may include a title bar unlike the old desktop client crop."
        )

    hwnd, window_title = find_window(title_fragment)

    frame = capture_window(hwnd)

    frame_height, frame_width = frame.shape[:2]

    basic_fields = {name: info for name, info in fields.items() if name not in STAT_FIELDS}
    results, diagnostics = extract_fields(frame, basic_fields, regions)
    results["stats"], diagnostics["stats"] = read_combat_stats(frame, regions["fields"])

    output = {
        "game": "Sword of Convallaria",

        "captured_at": (
            datetime.datetime.now()
            .astimezone()
            .isoformat()
        ),

        "window": window_title,

        "resolution": {
            "width": frame_width,
            "height": frame_height
        },

        "data": results,

        "ocr": diagnostics
    }

    with open(
        output_file,
        "w",
        encoding="utf-8"
    ) as f:
        json.dump(
            output,
            f,
            indent=2,
            ensure_ascii=False
        )

    print()
    print(json.dumps(
        output,
        indent=2,
        ensure_ascii=False
    ))

    print()
    print(f"Saved to {output_file}")


def extract_fields(frame, fields, regions):
    """Shared single-frame OCR used by scrape/watch and the account scanner."""
    height, width = frame.shape[:2]
    results, diagnostics = {}, {}
    for name, info in fields.items():
        region = regions["fields"].get(name)
        if region is None:
            continue
        x, y = int(region["x"] * width), int(region["y"] * height)
        w, h = int(region["w"] * width), int(region["h"] * height)
        crop = frame[max(0, y):min(height, y + h), max(0, x):min(width, x + w)]
        if crop.size == 0:
            continue
        field_type = info.get("type", "text")
        raw, confidence = ocr_region(crop, field_type)
        results[name] = parse_value(raw, field_type)
        diagnostics[name] = {"raw": raw, "confidence": round(confidence / 100, 3)}
    return results, diagnostics


# -----------------------------------------------------------
# Continuous mode
# -----------------------------------------------------------

def watch(title_fragment, output_file, interval):
    print(
        f"Watching Sword of Convallaria "
        f"every {interval} seconds."
    )

    print("Press Ctrl+C to stop.")

    try:
        while True:
            scrape(
                title_fragment,
                output_file
            )

            time.sleep(interval)

    except KeyboardInterrupt:
        print("\nStopped.")


# -----------------------------------------------------------
# CLI
# -----------------------------------------------------------

def main():
    parser = argparse.ArgumentParser()

    sub = parser.add_subparsers(
        dest="command",
        required=True
    )

    sub.add_parser(
        "windows",
        help="List visible Windows windows"
    )

    calibration = sub.add_parser(
        "calibrate",
        help="Calibrate screen regions"
    )

    calibration.add_argument(
        "--title",
        default="Sword of Convallaria"
    )

    scraping = sub.add_parser(
        "scrape",
        help="Extract the current screen to JSON"
    )

    scraping.add_argument(
        "--title",
        default="Sword of Convallaria"
    )

    scraping.add_argument(
        "--output",
        default="soc_data.json"
    )

    watching = sub.add_parser(
        "watch",
        help="Continuously update JSON"
    )

    watching.add_argument(
        "--title",
        default="Sword of Convallaria"
    )

    watching.add_argument(
        "--output",
        default="soc_data.json"
    )

    watching.add_argument(
        "--interval",
        type=float,
        default=2.0
    )

    for command, description in (
        ("roster", "Scan unique characters in the current roster order"),
        ("equipment", "Scan weapons and trinkets together in inventory order"),
        ("calibrate-roster", "Calibrate the roster and character build panels"),
        ("calibrate-equipment", "Calibrate the combined Equipment page"),
    ):
        account_command = sub.add_parser(command, help=description)
        account_command.add_argument("--title", default="Sword of Convallaria")
        account_command.add_argument("--config", default="scanner_regions.json")
        if not command.startswith("calibrate-"):
            account_command.add_argument("--count", type=int, required=True)
            account_command.add_argument("--output", default="soc_account.json")

    args = parser.parse_args()

    if hasattr(args, "count") and args.count <= 0:
        parser.exit(2, "Error: --count must be greater than 0.\n")

    if args.command == "windows":
        list_windows()

    elif args.command == "calibrate":
        calibrate(args.title)

    elif args.command == "scrape":
        setup_tesseract()
        scrape(
            args.title,
            args.output
        )

    elif args.command == "watch":
        setup_tesseract()
        watch(
            args.title,
            args.output,
            args.interval
        )

    else:
        import soc_account
        setup_tesseract()
        if args.command.startswith("calibrate-"):
            soc_account.calibrate_scanner(args, sys.modules[__name__])
        else:
            soc_account.scan_account(args, sys.modules[__name__])


if __name__ == "__main__":
    try:
        main()
    except RuntimeError as error:
        sys.exit(f"ERROR: {error}")
    except KeyboardInterrupt:
        print("\nStopped. Previously saved data is preserved.")
    except OSError as error:
        sys.exit(f"ERROR: File or Windows operation failed: {error}")
