"""Interactive calibration; only reads frames until the user selects ROIs."""

import cv2
import numpy as np

from soc_account import (atomic_json, bootstrap_page, confirm_ready, guided_prompt,
                         load_json, matches_page, retry_page, template, validate_config)
from soc_navigation import center, crop


class CalibrationCancelled(Exception):
    pass


def answer(prompt, default=""):
    try:
        value = input(prompt).strip()
    except (EOFError, KeyboardInterrupt) as error:
        raise CalibrationCancelled() from error
    if value.casefold() == "q":
        raise CalibrationCancelled()
    return value or default


def number(prompt, default, minimum=1, maximum=100):
    while True:
        try:
            value = int(answer(prompt, str(default)))
            if minimum <= value <= maximum:
                return value
        except ValueError:
            pass
        print(f"Enter a whole number from {minimum} to {maximum}, or Q to cancel.")


def select(frame, label, required=True, field_type=None):
    print(f"\n{label}\nDrag a rectangle, then ENTER. C skips an optional region; Q in the terminal cancels calibration.")
    # Scale only the selection preview; saved coordinates are normalized to the original HWND frame.
    scale = min(1.0, 1200 / frame.shape[1], 740 / frame.shape[0])
    preview = cv2.resize(frame, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    window = "SoC calibration - " + label
    while True:
        x, y, width, height = cv2.selectROI(window, preview, fromCenter=False, showCrosshair=True)
        cv2.destroyWindow(window)
        if width and height:
            region = {"x": x / preview.shape[1], "y": y / preview.shape[0],
                      "w": width / preview.shape[1], "h": height / preview.shape[0]}
            if field_type:
                region["type"] = field_type
            return region
        if not required:
            return None
        if not confirm_ready("This region is required. Press ENTER to select it again."):
            raise CalibrationCancelled()


def anchor(frame, label):
    while True:
        region = select(frame, label)
        saved = template(crop(frame, region))
        if np.asarray(saved).std() >= 3:
            return {"region": region, "template": saved, "threshold": .88}
        print("That region is nearly blank. Select a stable, distinctive label or UI symbol.")


def stage(api, hwnd, message):
    if not confirm_ready(message + "\nReturn to the terminal and press ENTER when ready."):
        raise CalibrationCancelled()
    return api.capture_window(hwnd)


def visual_sample(frame, region, value):
    return {"value": value, "pixels": cv2.resize(crop(frame, region), (24, 24),
                                                interpolation=cv2.INTER_AREA).tolist()}


def optional_field(frame, label, field_type="text"):
    return select(frame, label + " (optional)", required=False, field_type=field_type)


def stat_regions(api, detail, existing, reuse):
    print("\nFinal combat stats: select ONLY the number in the main character Attributes panel.")
    print("Use current displayed totals, not item stats, base values, bonuses, or percentages.")
    print("P.ATK / M.ATK are the top row; P.DEF / M.DEF the middle row; HP / Speed the bottom row.")
    regions = {}
    for name, label in api.STAT_FIELDS.items():
        previous = existing.get("fields", {}).get(name) if reuse else None
        region = dict(previous) if previous else optional_field(detail, f"FINAL {label} numeric value ONLY", "int")
        if region:
            region.update(type="int")
            region.setdefault("min_confidence", 70)
            regions[name] = region
    return regions


def stars(api, hwnd, frame):
    if answer("Calibrate star icons on this page? [y/N]: ", "n").casefold() != "y":
        return None
    count = number("Maximum number of star slots [5]: ", 5, maximum=10)
    print("Select each star icon tightly, including empty stars, from left to right.")
    slots = [select(frame, f"Star {index + 1} icon") for index in range(count)]
    filled = number("Which selected star is FILLED? Enter its 1-based position, or 0 if none: ", 0, minimum=0, maximum=count)
    empty = number("Which selected star is EMPTY? Enter its 1-based position, or 0 if none: ", 0, minimum=0, maximum=count)
    if filled == empty and filled:
        print("Filled and empty cannot be the same star. Stars will be null.")
        return None
    samples = []
    for label, position in (("filled", filled), ("empty", empty)):
        if position:
            samples.append(visual_sample(frame, slots[position - 1], label))
        else:
            if answer(f"Show another item/character with a {label} star to collect that sample? [y/N]: ", "n").casefold() != "y":
                print("Both reference types are needed. Stars will be null.")
                return None
            other = stage(api, hwnd, f"Show a {label.upper()} star on another entry in the same details layout.")
            region = select(other, f"One {label.upper()} star icon only")
            samples.append(visual_sample(other, region, label))
    if not filled or not empty:
        stage(api, hwnd, "Return to the original FIRST entry's details before continuing calibration.")
    return {"slots": slots, "samples": samples}


def grid(frame):
    print("\nUse a uniform grid with at least two visible rows. Disable grouping if the game offers it.")
    viewport = select(frame, "Scrollable card area ONLY - exclude headings, sort controls and scrollbar")
    columns = number("Number of columns in this grid: ", 1)
    first = select(frame, "Entire FIRST card at top left (including its outer bounds)")
    last = select(frame, "Entire LAST card in the FIRST row") if columns > 1 else first
    second = select(frame, "Entire FIRST card in the SECOND row")
    row_pitch = center(second)["y"] - center(first)["y"]
    column_pitch = (center(last)["x"] - center(first)["x"]) / (columns - 1) if columns > 1 else 0
    if row_pitch <= 0 or (columns > 1 and column_pitch <= 0):
        raise RuntimeError("Cards must be selected left-to-right and top-to-bottom. Calibration not saved.")
    return {"viewport": viewport, "columns": columns, "first_card": first,
            "row_pitch": row_pitch, "column_pitch": column_pitch}


def add_icon_samples(api, hwnd, spec, frame, prompt, allowed=None):
    print(prompt)
    while True:
        label = answer("Label for this visible icon (ENTER keeps OCR only): ")
        if not label:
            return
        if allowed and label.casefold() not in allowed:
            print("Use one of: " + ", ".join(allowed))
            continue
        label = label.casefold() if allowed else label
        spec.setdefault("samples", []).append(visual_sample(frame, spec, label))
        spec["visual_only"] = True
        if answer("Add another labelled icon example at the SAME screen location? [y/N]: ", "n").casefold() != "y":
            return
        frame = stage(api, hwnd, "In the game, show the next icon example using the same panel and location.")


def build_panels(api, hwnd, detail_frame, details_page):
    routes = {}
    for slot in ("weapon", "trinket", "skill_1", "skill_2", "skill_3"):
        print(f"\nBuild panel: {slot}. Select only an equipped slot's information icon, never Equip/Upgrade/Replace.")
        opening = select(detail_frame, f"Click target for {slot} on CHARACTER DETAILS", required=False)
        if not opening:
            continue
        frame = stage(api, hwnd,
                      f"Open that equipped {slot} information panel manually. Do not change the build.")
        page = anchor(frame, f"Stable label UNIQUE to the {slot} panel (not the changing item/skill name)")
        if matches_page(detail_frame, page):
            raise RuntimeError(f"{slot} anchor also matches closed character details. Select a distinct popup label.")
        fields = {"name": select(frame, f"{slot} NAME only", field_type="text")}
        if slot in ("weapon", "trinket"):
            engraving = optional_field(frame, f"{slot} engraving TYPE text or single engraving icon")
            if engraving:
                add_icon_samples(api, hwnd, engraving, frame,
                                 "If this is an icon instead of text, supply its known engraving type, e.g. Cup.")
                fields["engraving"] = engraving
        closing = select(frame, f"Close/back target for {slot} panel - returns to character details")
        routes[slot] = {"open": center(opening), "close": center(closing), "page": page, "fields": fields}
        returned = stage(api, hwnd, "Close the information panel and return to CHARACTER DETAILS.")
        if not matches_page(returned, details_page):
            raise RuntimeError("Character details anchor did not match after closing the panel. Calibration not saved.")
    return routes


def calibrate_scanner(args, api):
    kind = args.command.removeprefix("calibrate-")
    try:
        if not guided_prompt(kind, calibration=True):
            return
        while True:
            hwnd, _ = api.find_window(args.title)
            frame = api.capture_window(hwnd)
            heading_diagnostics = []
            if bootstrap_page(frame, kind, heading_diagnostics):
                break
            print("Page heading OCR: " + (" | ".join(heading_diagnostics)[:300] or "(no text detected)"))
            if not retry_page(kind):
                return
        config = {"screen": {"width": frame.shape[1], "height": frame.shape[0],
                             "capture_backend": api.CAPTURE_BACKEND},
                  "timeout": 10, "scroll_delta": -120}
        config["list_page"] = anchor(frame,
            "SELECTED Gear/Equipment tab INCLUDING its highlight (must distinguish it from Tarot/Material tabs)"
            if kind == "equipment" else
            "The Character List heading (include BOTH words) or a stable list-only sort control")
        config["grid"] = grid(frame)
        config["details_mode"] = "separate"
        if kind == "equipment" and answer(
                "Does selecting equipment update a details panel BESIDE the grid, without leaving it? [Y/n]: ", "y").casefold() == "y":
            config["details_mode"] = "inline"
        detail = stage(api, hwnd,
                       "Open the FIRST character's details." if kind == "roster" else
                       "Open the FIRST equipment item's details, showing its name and type. Weapons and trinkets use this same flow.")
        config["details_page"] = anchor(detail,
            "Stable label in the equipment DETAILS PANEL (not the changing item name)"
            if config["details_mode"] == "inline" else
            "Stable DETAILS-ONLY label, absent from the list (e.g. Rank or equipment details label)")
        if config["details_mode"] != "inline" and matches_page(frame, config["details_page"]):
            raise RuntimeError("Details anchor also matches the list. Choose a label unique to details.")
        if config["details_mode"] == "inline":
            print("Select a small section of the FIRST card's SELECTED border/corner, excluding item artwork.")
            marked = select(detail, "Selected FIRST card's distinctive border/corner")
            card = config["grid"]["first_card"]
            marker = {"x": (marked["x"] - card["x"]) / card["w"],
                      "y": (marked["y"] - card["y"]) / card["h"],
                      "w": marked["w"] / card["w"], "h": marked["h"] / card["h"],
                      "pixels": visual_sample(detail, marked, "selected")["pixels"], "tolerance": .06}
            # Check a neighboring unselected card to reject an ordinary, shared border.
            neighbor = dict(marked)
            if config["grid"]["columns"] > 1:
                neighbor["x"] += config["grid"]["column_pitch"]
            else:
                neighbor["y"] += config["grid"]["row_pitch"]
            alternative = np.asarray(visual_sample(detail, neighbor, "unselected")["pixels"])
            if np.mean(np.abs(alternative.astype(float) - np.asarray(marker["pixels"]))) / 255 <= marker["tolerance"]:
                raise RuntimeError("Selected marker also matches an unselected card. Choose a distinctive highlighted border.")
            config["selection_marker"] = marker
        else:
            config["back"] = center(select(detail, "Back/close target returning from DETAILS to the LIST"))
        fields = {}
        existing = load_json("regions.json", {})
        reuse = (kind == "roster" and existing.get("_screen", {}).get("capture_backend") == api.CAPTURE_BACKEND
                 and answer("Reuse current single-character name/rank/level/power and any calibrated stat regions? [Y/n]: ", "y").casefold() == "y")
        if reuse:
            for name, region in existing.get("fields", {}).items():
                if name in ("character_name", "rank", "level", "power"):
                    fields["name" if name == "character_name" else name] = dict(region)
        if "name" not in fields:
            fields["name"] = select(detail, "Character NAME only" if kind == "roster" else "Equipment NAME only", field_type="text")
        for name in (("rank", "level", "power") if kind == "roster" else ("level",)):
            if name not in fields:
                selected = optional_field(detail, name + " number ONLY", "int")
                if selected:
                    fields[name] = selected
        config["fields"] = fields
        if kind == "roster":
            config["stats"] = stat_regions(api, detail, existing, reuse)
        config["stars"] = stars(api, hwnd, detail)
        if kind == "roster":
            config["build_panels"] = build_panels(api, hwnd, detail, config["details_page"])
        else:
            fields["type"] = select(detail,
                "Weapon/trinket CATEGORY label or icon - NEVER the engraving type or item name", field_type="text")
            for field, label in (("equipped_status", "Equipped/Not Equipped status text"),
                                 ("equipped_by", "Equipped character NAME only (skip portraits without a visible name)")):
                selected = optional_field(detail, label)
                if selected:
                    fields[field] = selected
            add_icon_samples(api, hwnd, fields["type"], detail,
                             "If type is icon-only, label a weapon and a trinket example; include each different weapon-family icon.",
                             allowed=("weapon", "trinket"))
        final_frame = stage(api, hwnd, f"Return to the {'Characters' if kind == 'roster' else 'Equipment'} LIST and scroll to the top.")
        if (not matches_page(final_frame, config["list_page"])
                or (config["details_mode"] != "inline" and matches_page(final_frame, config["details_page"]))):
            raise RuntimeError("List/details anchors are not distinct. Calibration not saved; select more specific labels.")
        validate_config(config, kind, api.CAPTURE_BACKEND)
        saved = load_json(args.config, {"version": 1})
        saved[kind] = config
        atomic_json(args.config, saved)
        print(f"Calibration saved to {args.config}. Original regions.json is unchanged.")
    except CalibrationCancelled:
        print("Calibration cancelled. Previous calibration is preserved.")
    finally:
        cv2.destroyAllWindows()
