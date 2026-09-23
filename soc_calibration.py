"""Interactive calibration; only reads frames until the user selects ROIs."""

import cv2
import numpy as np

from soc_account import (atomic_json, bootstrap_page, confirm_ready, guided_prompt,
                         load_json, matches_page, retry_page, template, validate_config)
from soc_navigation import center, crop, grid_diagnostics, validate_grid


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
    print("For Character List, start below the heading and end ABOVE Power/Compact Mode/Ranking controls.")
    viewport = select(frame, "Scrollable card area ONLY - exclude headings, sort controls and scrollbar")
    columns = number("Number of columns in this grid: ", 1)
    first = select(frame, "Entire FIRST card at top left (including its outer bounds)")
    last = select(frame, "Entire LAST card in the FIRST row") if columns > 1 else first
    second = select(frame, "Entire FIRST card in the SECOND row")
    row_pitch = center(second)["y"] - center(first)["y"]
    column_pitch = (center(last)["x"] - center(first)["x"]) / (columns - 1) if columns > 1 else 0
    return {"viewport": viewport, "columns": columns, "first_card": first,
            "row_pitch": row_pitch, "column_pitch": column_pitch,
            "center_margin": .003, "edge_tolerance": .008, "min_visible_fraction": .65}


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


def tarot_fields(frame, fields=None):
    fields = fields if fields is not None else {}
    if "name" not in fields:
        fields["name"] = select(frame, "Shared Tarot NAME", field_type="text")
    for field, label, field_type in (
        ("level", "Tarot LEVEL number only", "int"),
        ("main_stats", "Tarot MAIN STATS table: labels AND values P.ATK, M.ATK, P.DEF, M.DEF, Max HP", "block"),
        ("details", "Tarot additional rolled DETAILS / effects text, including wrapped lines", "block"),
        ("skill", "Tarot SKILL description text only", "block"),
    ):
        if field not in fields:
            region = optional_field(frame, label, field_type)
            if region:
                region["min_confidence"] = 70 if field == "main_stats" else 60
                fields[field] = region
    return fields


def build_panels(api, hwnd, detail_frame, details_page):
    targets, panels = {}, {}
    skill_count = number("Number of equipped skill icons to inspect [3]: ", 3, minimum=0, maximum=10)
    slots = ["weapon", "trinket", *(f"skill_{i + 1}" for i in range(skill_count)), "tarot"]
    print("Select each click target once. Shared panel regions are calibrated only once per layout.")
    for slot in slots:
        opening = select(detail_frame, f"Click target for equipped {slot} (not Equip/Unequip/Upgrade)", required=False)
        if opening:
            targets[slot] = center(opening)
    groups = (("gear", [s for s in ("weapon", "trinket") if s in targets]),
              ("skills", [s for s in targets if s.startswith("skill_")]),
              ("tarot", [s for s in targets if s == "tarot"]))
    for group, selections in groups:
        if not selections:
            continue
        frame = stage(api, hwnd, f"Click {selections[0]} directly. Calibrate the shared {group} panel once.\n"
                      "Other sibling icons remain clickable; do not change the build.")
        marker_hint = ("a shared frame detail or Skill heading, NOT the changing Weapon/Trinket label"
                       if group == "gear" else "a shared skill panel frame/heading" if group == "skills"
                       else "Tarot Whisper / Tarot-specific panel structure")
        marker = anchor(frame, f"Shared {group} detail marker: {marker_hint}")
        if matches_page(detail_frame, marker):
            raise RuntimeError(f"Shared {group} marker also matches the closed character page. Select a panel-only marker.")
        panel = {"detail_marker": marker}
        if group == "tarot":
            tarot_fields(frame, panel)
        else:
            panel["name"] = select(frame, f"Shared {group} NAME (wide enough for longer names)", field_type="text")
        if group == "gear":
            engraving = optional_field(frame, "Shared gear engraving TYPE text or single type icon")
            if engraving:
                add_icon_samples(api, hwnd, engraving, frame, "For an icon, label its known engraving type, e.g. Cup.")
                panel["engraving_type"] = engraving
            rolls = optional_field(frame, "Shared gear engraving STATS / bonus text", "block")
            if rolls:
                panel["engraving_stats"] = rolls
        panels[group] = panel
    if panels:
        returned = stage(api, hwnd, "Inspection complete. Click the ONE generic dead-area dismiss point to return to character details.")
        if not matches_page(returned, details_page):
            raise RuntimeError("Character details anchor did not match after the generic dismiss. Calibration not saved.")
    return targets, panels


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
                  "timeout": 10, "detail_timeout": 5, "scroll_delta": -120}
        config["list_page"] = anchor(frame,
            "Inventory heading or Gear: count label (exclude the changing count; NO tab highlight)"
            if kind == "equipment" else
            "The Character List heading (include BOTH words) or a stable list-only sort control")
        while True:
            config["grid"] = grid(frame)
            print(grid_diagnostics(config["grid"], kind.title()))
            try:
                validate_grid(config["grid"])
                break
            except ValueError as error:
                print(f"Reason rejected: {error}")
                if not confirm_ready("Press ENTER to reselect the grid before continuing calibration."):
                    raise CalibrationCancelled()
        config["details_mode"] = "inline" if kind != "roster" else "separate"
        detail = stage(api, hwnd,
                       "Open the FIRST character's details." if kind == "roster" else
                       "Click the FIRST equipment card to update its persistent details pane. Stay on Inventory -> Gear.")
        config["details_page"] = anchor(detail,
            f"Stable {kind}-specific DETAILS PANEL label/layout (not the changing item name or selected border)"
            if config["details_mode"] == "inline" else
            "Stable DETAILS-ONLY label, absent from the list (e.g. Rank or equipment details label)")
        if config["details_mode"] != "inline" and matches_page(frame, config["details_page"]):
            raise RuntimeError("Details anchor also matches the list. Choose a label unique to details.")
        if kind == "roster":
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
            fields["name"] = select(detail, "Character NAME only" if kind == "roster" else f"{kind.title()} NAME only", field_type="text")
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
            config["overlay_dismiss"] = center(select(detail,
                "ONE generic overlay-dismiss point: safe dead area, outside panels/icons/Back (not Unequip)"))
            config["build_targets"], config["character_details"] = build_panels(
                api, hwnd, detail, config["details_page"])
        elif kind == "equipment":
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
        final_frame = stage(api, hwnd, f"Return to the {'Character List' if kind == 'roster' else 'Inventory -> Gear'} and scroll to the top.")
        if (not matches_page(final_frame, config["list_page"])
                or (config["details_mode"] != "inline" and matches_page(final_frame, config["details_page"]))):
            raise RuntimeError("List/details anchors are not distinct. Calibration not saved; select more specific labels.")
        validate_config(config, kind, api.CAPTURE_BACKEND)
        saved = load_json(args.config, {"version": 2})
        saved["version"] = 2
        saved[kind] = config
        atomic_json(args.config, saved)
        print(f"Calibration saved to {args.config}. Original regions.json is unchanged.")
    except CalibrationCancelled:
        print("Calibration cancelled. Previous calibration is preserved.")
    finally:
        cv2.destroyAllWindows()
