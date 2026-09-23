"""Guided, calibrated account scans built on soc_scraper's existing OCR/capture."""

import datetime
from difflib import SequenceMatcher
import json
import os
from pathlib import Path
import re
import tempfile
import time
import uuid

import cv2
import numpy as np
import pytesseract

from soc_panels import effect_entries, panel_routes, parse_tarot_stats, read_block

from soc_navigation import (NavigationError, WindowInput, center, crop, difference,
                            estimate_scroll_shift, visible_slots, grid_diagnostics, validate_grid,
                            normalized_rectangle)


PAGE_NAMES = {"roster": "Character List", "equipment": "Inventory → Gear"}
TYPE_WORDS = {"weapon": ["weapon", "sword", "blade", "spear", "lance", "axe", "bow", "staff", "wand"],
              "trinket": ["trinket", "accessory"]}


def now():
    return datetime.datetime.now().astimezone().isoformat()


def key(text):
    return re.sub(r"\s+", " ", str(text or "")).strip().casefold()


def atomic_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=path.name + ".", suffix=".tmp", delete=False) as stream:
            temporary = stream.name
            json.dump(data, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary and os.path.exists(temporary):
            os.unlink(temporary)


def load_json(path, default=None):
    if not Path(path).exists():
        if default is not None:
            return default
        raise RuntimeError(f"{path} is missing. Run the matching calibrate command first.")
    try:
        with open(path, encoding="utf-8") as stream:
            return json.load(stream)
    except (OSError, ValueError) as error:
        raise RuntimeError(f"Cannot read {path}: {error}. Existing data was not overwritten.") from error


def confirm_ready(message, input_fn=None):
    input_fn = input_fn or input
    print("\n" + message + "\nPress Q to cancel.")
    while True:
        try:
            answer = input_fn("> ").strip().casefold()
        except (EOFError, KeyboardInterrupt):
            print("\nCancelled.")
            return False
        if not answer:
            return True
        if answer == "q":
            print("Cancelled.")
            return False
        print("Press ENTER when ready, or Q to cancel.")


def guided_prompt(kind, calibration=False):
    page = PAGE_NAMES[kind]
    if calibration:
        title = "Roster" if kind == "roster" else kind.title()
        detail = " containing both weapons and trinkets" if kind == "equipment" else ""
        return confirm_ready(
            f"{title} calibration\n\nOpen Sword of Convallaria and navigate to the {page} page{detail}.\n"
            "Make sure the list is visible and scrolled to the top.\n\nPress ENTER when ready.")
    if kind == "roster":
        return confirm_ready(
            "Character scan\n\n1. Open Sword of Convallaria.\n2. Go to the Character List (roster) page.\n"
            "3. Make sure the roster is scrolled to the top.\n"
            "4. Do not cover or minimize the game if that interferes with automation.\n\n"
            "When the Character List page is ready, return here and press ENTER.")
    return confirm_ready(
        "Equipment scan\n\n1. Open Sword of Convallaria.\n"
        "2. Go to the Equipment page containing weapons and trinkets.\n"
        "3. Make sure the list is scrolled to the top.\n"
        "4. Apply whatever normal in-game filter/sort order you want scanned.\n\n"
        "When the Equipment page is ready, return here and press ENTER.")


def retry_page(kind):
    page = PAGE_NAMES[kind]
    return confirm_ready(
        f"The expected {page} page was not detected.\n\n"
        f"Please switch back to Sword of Convallaria, open the {page} page,\n"
        "then return here and press ENTER to retry.")


def template(image, size=(80, 32)):
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    return cv2.resize(gray, size, interpolation=cv2.INTER_AREA).tolist()


def template_score(image, saved):
    reference = np.asarray(saved, dtype=np.uint8)
    if reference.ndim != 2 or float(reference.std()) < 3:
        return 0.0
    actual = np.asarray(template(image, (reference.shape[1], reference.shape[0])), dtype=np.uint8)
    return float(cv2.matchTemplate(actual, reference, cv2.TM_CCOEFF_NORMED)[0, 0])


def matches_page(frame, page):
    return template_score(crop(frame, page["region"]), page["template"]) >= page.get("threshold", 0.88)


def heading_matches(text, kind):
    words = re.findall(r"[a-z]+", text.casefold())
    if kind == "equipment":
        return bool(set(words) & {"inventory", "equipment", "equipments", "gear"})
    # The roster says Character List / My Characters; character DETAILS says
    # Characters. Accept merged words and small OCR errors, not that bare word.
    for length in (1, 2):
        for index in range(len(words) - length + 1):
            phrase = "".join(words[index:index + length])
            if any(SequenceMatcher(None, phrase, expected).ratio() >= .90
                   for expected in ("characterlist", "mycharacters")
                   if expected != "mycharacters" or phrase.startswith("my")):
                return True
    return False


def bootstrap_page(frame, kind, diagnostics=None):
    # Isolate the header before any calibration exists. Mixing the title and
    # card artwork into one sparse OCR pass caused false roster rejections.
    header_regions = (
        {"x": .15, "y": .04, "w": .41, "h": .08},
        {"x": .78, "y": .045, "w": .215, "h": .075},
    )
    for region in header_regions:
        gray = cv2.cvtColor(crop(frame, region), cv2.COLOR_BGR2GRAY)
        gray = cv2.resize(gray, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
        _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        for image in (gray, binary, cv2.bitwise_not(binary)):
            try:
                text = pytesseract.image_to_string(image, config="--psm 7", timeout=6)
            except RuntimeError as error:
                if diagnostics is not None:
                    diagnostics.append(f"OCR error: {error}")
                continue
            if diagnostics is not None:
                diagnostics.append(" ".join(text.split()))
            if heading_matches(text, kind):
                return True
    return False


def classify_type(value, words=None):
    tokens = set(re.findall(r"[a-z]+", key(value)))
    matches = [kind for kind, options in (words or TYPE_WORDS).items()
               if kind in ("weapon", "trinket") and any(key(option) in tokens for option in options)]
    return matches[0] if len(matches) == 1 else None


def sample_value(image, samples):
    actual = cv2.resize(image, (24, 24), interpolation=cv2.INTER_AREA).astype(float) / 255
    by_label = {}
    for sample in samples:
        distance = float(np.mean(np.abs(actual - np.asarray(sample["pixels"]) / 255)))
        label = sample["value"]
        by_label[label] = min(by_label.get(label, 1.0), distance)
    scores = sorted((distance, label) for label, distance in by_label.items())
    if scores and scores[0][0] <= .14 and (len(scores) == 1 or scores[1][0] - scores[0][0] >= .035):
        return scores[0][1], round(1 - scores[0][0], 3)
    return None, 0.0


def read_field(api, frame, spec):
    if not spec:
        return None, {"raw": "", "confidence": 0, "status": "not_calibrated"}
    image = crop(frame, spec)
    if spec.get("samples"):
        value, confidence = sample_value(image, spec["samples"])
        if value is not None:
            return value, {"raw": str(value), "confidence": confidence, "method": "visual_sample"}
        if spec.get("visual_only"):
            return None, {"raw": "", "confidence": 0, "status": "unknown_icon"}
    raw, confidence = api.ocr_region(image, spec.get("type", "text"))
    diagnostic = {"raw": raw, "confidence": round(confidence / 100, 3), "method": "ocr"}
    if confidence < spec.get("min_confidence", 50) or not raw.strip():
        diagnostic["status"] = "uncertain"
        return None, diagnostic
    return api.parse_value(raw, spec.get("type", "text")), diagnostic


def read_stars(frame, spec):
    if not spec:
        return None
    values = [sample_value(crop(frame, region), spec["samples"])[0] for region in spec["slots"]]
    if any(value not in ("filled", "empty") for value in values):
        return None
    # Stars must form a contiguous filled prefix, not arbitrary yellow decorations.
    count = values.count("filled")
    if values != ["filled"] * count + ["empty"] * (len(values) - count):
        return None
    return count


def reconcile(account):
    """Link only an unambiguous named copy with a visible owner; retain ambiguity."""
    for character in account["characters"]:
        for kind in character.pop("reconciled_fields", {}):
            character.get("equipped", {})[kind] = None
        character["equipment_ids"] = {"weapon": None, "trinket": None}
        character["equipment_candidates"] = {}
        character.setdefault("equipped", {"weapon": None, "trinket": None})
        for kind in ("weapon", "trinket"):
            name = character.get("equipped", {}).get(kind)
            if not name:
                explicitly_owned = [item for item in account["equipment"]
                                    if item.get("type") == kind
                                    and key(item.get("equipped_by")) == key(character["name"])]
                if len(explicitly_owned) != 1:
                    continue
                name = explicitly_owned[0]["name"]
                character["equipped"][kind] = name
                character.setdefault("reconciled_fields", {})[kind] = "equipment.equipped_by"
            matches = [item for item in account["equipment"]
                       if item.get("type") == kind and key(item.get("name")) == key(name)]
            owned = [item for item in matches if key(item.get("equipped_by")) == key(character["name"])]
            if len(owned) == 1:
                character["equipment_ids"][kind] = owned[0]["id"]
            elif matches:
                character["equipment_candidates"][kind] = [item["id"] for item in matches]


class AccountStore:
    def __init__(self, path, kind, count):
        self.path, self.kind = path, kind
        self.account = load_json(path, {"game": "Sword of Convallaria", "characters": [], "equipment": []})
        if (not isinstance(self.account, dict) or self.account.get("game") != "Sword of Convallaria"
                or any(not isinstance(self.account.get(k), list) for k in ("characters", "equipment"))
                or any(not isinstance(item, dict) or not item.get("name")
                       for k in ("characters", "equipment") for item in self.account[k])
                or not isinstance(self.account.get("scans", {}), dict)
                or any(not isinstance(item.get("equipped", {}), dict) for item in self.account["characters"])):
            raise RuntimeError("Existing account JSON has an invalid schema; it was not overwritten.")
        self.run = {"id": uuid.uuid4().hex, "kind": kind, "requested": count,
                    "collected": 0, "status": "running", "started_at": now(), "errors": []}
        self.account.setdefault("scans", {})[kind] = self.run
        self.inventory_started = False

    def save(self):
        self.account["updated_at"] = now()
        reconcile(self.account)
        atomic_json(self.path, self.account)

    def add(self, record):
        if self.kind == "roster":
            existing = next((i for i, item in enumerate(self.account["characters"])
                             if key(item["name"]) == key(record["name"])), None)
            if existing is None:
                self.account["characters"].append(record)
            else:
                self.account["characters"][existing] = record
        else:
            # A fresh top-of-list scan replaces this inventory snapshot, never appends it
            # to a prior run (which would fabricate duplicate physical copies).
            collection = "equipment"
            if not self.inventory_started:
                self.account[collection] = []
                self.inventory_started = True
            record["id"] = f"{self.kind}_{len(self.account[collection]) + 1:03d}"
            self.account[collection].append(record)
        self.run["collected"] += 1
        self.save()

    def error(self, position, message):
        self.run["errors"].append({"position": list(position) if position else None,
                                   "message": str(message), "at": now()})
        self.save()

    def finish(self, status):
        self.run.update(status=status, finished_at=now())
        self.save()


class Scanner:
    def __init__(self, api, hwnd, kind, config, store, controller=None):
        self.api, self.hwnd, self.kind = api, hwnd, kind
        self.config, self.store = config, store
        self.input = controller or WindowInput(hwnd)
        self.resolution = None
        self.detail_before = None
        self.last_panel_update = {}

    def capture(self):
        frame = self.api.capture_window(self.hwnd)
        height, width = frame.shape[:2]
        screen = self.config["screen"]
        if abs((width / height) / (screen["width"] / screen["height"]) - 1) > .015:
            raise NavigationError("Game aspect ratio differs from calibration. Recalibrate this layout.")
        if self.resolution is None:
            self.resolution = (width, height)
        if self.resolution != (width, height):
            raise NavigationError("Game resized during scan. Progress saved; restart at the top.")
        return frame

    def is_page(self, frame, state):
        if not matches_page(frame, self.config[state]):
            return False
        if (state == "list_page" and self.config.get("details_mode") == "inline"
                and not matches_page(frame, self.config["details_page"])):
            return False
        if (state == "list_page" and self.config.get("details_mode") != "inline"
                and matches_page(frame, self.config["details_page"])):
            return False
        if state == "details_page" and any(matches_page(frame, route["page"])
                                            for route in panel_routes(self.config).values()):
            return False
        return True

    def wait_for(self, state, settle=None, changed_from=None):
        deadline = time.monotonic() + self.config.get("timeout", 10)
        previous = None
        stable_since = None
        while time.monotonic() < deadline:
            frame = self.capture()
            if self.is_page(frame, state):
                region = settle or self.config[state]["region"]
                current = crop(frame, region)
                if changed_from is not None and difference(changed_from, current) < 1.8:
                    previous = None
                    stable_since = None
                    time.sleep(.15)
                    continue
                if previous is not None and difference(previous, current) < 1.8:
                    if stable_since is not None and time.monotonic() - stable_since >= .35:
                        return frame
                else:
                    stable_since = time.monotonic()
                previous = current
            else:
                previous = None
                stable_since = None
            time.sleep(.15)
        raise NavigationError(f"Timed out waiting for {state}. Check page anchors and navigation calibration.")

    def wait_for_character_details(self):
        return self.wait_for("details_page", self.config["fields"]["name"])

    def wait_for_equipment_details(self):
        return self.wait_for_detail_name_change(self.detail_before, self.config["details_page"],
                                               self.config["fields"], allow_unchanged=True)

    def wait_for_detail_name_change(self, before, page, fields, allow_unchanged=False):
        """Poll name/content/marker changes, then require stable readable details.

        Inventory copies may be visually identical. After the bounded timeout a
        stable unchanged pane is retained with an explicit diagnostic, never
        deduplicated by name. Sibling skills require an observed update.
        """
        timeout = self.config.get("detail_timeout", 5)
        deadline = time.monotonic() + timeout
        was_open = before is not None and matches_page(before, page)
        previous_name = read_field(self.api, before, fields["name"])[0] if was_open else None
        regions = [page["region"], *fields.values()]

        def images(frame):
            return [crop(frame, region).copy() for region in regions]

        baseline = images(before) if was_open else None
        previous, stable_since, last_readable = None, None, None
        changed = not was_open
        last_name = None
        while time.monotonic() < deadline:
            frame = self.capture()
            if not matches_page(frame, page):
                changed = True  # An actual panel/page transition was observed.
                previous, stable_since, last_readable = None, None, None
                time.sleep(.12)
                continue
            current = images(frame)
            visual_change = baseline is not None and any(difference(a, b) >= 1.8 for a, b in zip(baseline, current))
            changed = changed or visual_change
            stable = previous is not None and all(difference(a, b) < 1.8 for a, b in zip(previous, current))
            if not stable:
                stable_since, last_readable = time.monotonic(), None
            elif time.monotonic() - stable_since >= .35:
                last_name, _ = read_field(self.api, frame, fields["name"])
                if last_name:
                    last_readable = frame
                    name_changed = previous_name is not None and key(last_name) != key(previous_name)
                    if changed or name_changed:
                        self.last_panel_update = {"status": "updated", "previous_name": previous_name,
                                                  "current_name": last_name}
                        return frame
                else:
                    last_readable = None
            previous = current
            time.sleep(.12)
        if allow_unchanged and last_readable is not None:
            self.last_panel_update = {"status": "unchanged_after_timeout", "previous_name": previous_name,
                                      "current_name": last_name,
                                      "warning": "No visible update observed after click; possibly an identical copy or already-selected card."}
            return last_readable
        raise NavigationError("Timed out waiting for a readable updated detail panel. Entry not counted.")

    def wait_for_roster_page(self):
        return self.wait_for("list_page", self.config["grid"]["viewport"])

    def wait_for_equipment_page(self):
        return self.wait_for("list_page", self.config["grid"]["viewport"])

    def wait_for_list(self):
        return self.wait_for_roster_page() if self.kind == "roster" else self.wait_for_equipment_page()

    def overlay_visible(self, frame):
        return any(matches_page(frame, route["page"]) for route in panel_routes(self.config).values())

    def dismiss_overlay(self, frame):
        if self.overlay_visible(frame):
            self.input.click(self.config["overlay_dismiss"])
            return self.wait_for_character_details()
        if self.is_page(frame, "details_page"):
            return frame
        raise NavigationError("Cannot dismiss an unrecognized character page.")

    def read_popup(self, route):
        current = self.capture()
        if not self.is_page(current, "details_page") and not self.overlay_visible(current):
            raise NavigationError("Character page/overlay could not be verified before the next selection.")
        self.input.click(route["open"])
        frame = self.wait_for_detail_name_change(current, route["page"], route["fields"])
        if route.get("panel_kind") == "tarot":
            record = self.read_tarot(frame, route["fields"])
            return record, record["ocr"]
        values, diagnostics = {}, {"panel_update": dict(self.last_panel_update)}
        for field, spec in route["fields"].items():
            reader = read_block if field == "engraving_stats" else read_field
            try:
                values[field], diagnostics[field] = reader(self.api, frame, spec)
            except (RuntimeError, ValueError, cv2.error) as error:
                values[field], diagnostics[field] = None, {"raw": "", "confidence": 0., "status": "error", "error": str(error)}
        return values, diagnostics

    def read_character(self, frame):
        fields = self.config["fields"]
        # Reuse the same multi-preprocessing OCR and parser as the existing scrape command.
        values, diagnostics = self.api.extract_fields(frame, fields, {"fields": fields})
        for field, value in list(values.items()):
            if diagnostics[field]["confidence"] < fields[field].get("min_confidence", 50) / 100:
                values[field] = None
        name = values.get("name")
        if not name or not any(character.isalpha() for character in name):
            raise ValueError("Character name could not be identified confidently.")
        record = {"name": name, "rank": values.get("rank"), "stars": read_stars(frame, self.config.get("stars")),
                  "equipped": {"weapon": None, "trinket": None, "tarot": None}, "tarot": None,
                  "engravings": {"weapon": None, "trinket": None}, "skills": [],
                  "level": values.get("level"), "power": values.get("power"),
                  "ocr": diagnostics, "warnings": [], "captured_at": now()}
        record["stats"], record["ocr"]["stats"] = self.api.read_combat_stats(frame, self.config.get("stats", {}))
        panels = panel_routes(self.config)
        skills = sorted((slot for slot in panels if re.fullmatch(r"skill_[1-9][0-9]*", slot)),
                        key=lambda slot: int(slot.split("_")[1])) or ["skill_1", "skill_2", "skill_3"]
        for slot in ["weapon", "trinket", *skills, "tarot"]:
            route = panels.get(slot)
            if not route:
                record["warnings"].append(f"{slot}: not calibrated")
                continue
            try:
                values, detail = self.read_popup(route)
                record["ocr"][slot] = detail
                if slot in ("weapon", "trinket"):
                    record["equipped"][slot] = values.get("name")
                    record["engravings"][slot] = values.get("engraving")
                    record.setdefault("engraving_stats", {})[slot] = values.get("engraving_stats")
                elif slot == "tarot":
                    # This is the equipped slot of the character just identified,
                    # so ownership comes from that context, not portrait recognition.
                    values["equipped_by"] = name
                    detail["equipped_by"] = {"value": name, "method": "character_context",
                                             "confidence": diagnostics["name"]["confidence"]}
                    record["tarot"] = values
                    record["equipped"]["tarot"] = values.get("name")
                elif values.get("name"):
                    record["skills"].append(values["name"])
            except (RuntimeError, ValueError, cv2.error) as error:
                record["warnings"].append(f"{slot}: {error}")
                # An OCR failure is not a reason to close a recognized sibling
                # overlay. Keep inspecting directly; stop only on unknown state.
                try:
                    current = self.capture()
                    if not self.overlay_visible(current) and not self.is_page(current, "details_page"):
                        raise NavigationError("Page state unknown after build-panel failure.")
                except (RuntimeError, ValueError, cv2.error) as recovery_error:
                    record["warnings"].append(f"Remaining panels skipped: {recovery_error}")
                    break
        return record

    def read_tarot(self, frame, fields):
        values, diagnostics = {}, {"panel_update": dict(self.last_panel_update)}
        for field in ("name", "level"):
            try:
                values[field], diagnostics[field] = read_field(self.api, frame, fields.get(field))
            except (RuntimeError, ValueError, cv2.error) as error:
                values[field], diagnostics[field] = None, {"raw": "", "confidence": 0., "status": "error", "error": str(error)}
        if not values["name"] or not any(c.isalpha() for c in str(values["name"])):
            raise ValueError("Tarot name could not be identified confidently.")
        for field in ("main_stats", "details", "skill"):
            values[field], diagnostics[field] = read_block(self.api, frame, fields.get(field))
        stats, diagnostics["stats"] = parse_tarot_stats(
            diagnostics["main_stats"], fields.get("main_stats", {}).get("min_confidence", 70))
        return {"name": values["name"], "level": values["level"], "equipped_by": None,
                "stats": stats, "details": effect_entries(values["details"]), "skill": values["skill"],
                "ocr": diagnostics, "captured_at": now()}

    def read_equipment(self, frame):
        values, diagnostics = {}, {}
        for field, spec in self.config["fields"].items():
            values[field], diagnostics[field] = read_field(self.api, frame, spec)
        name = values.get("name")
        equipment_type = classify_type(values.get("type"), self.config.get("type_words"))
        if not name or not any(character.isalpha() for character in str(name)):
            raise ValueError("Equipment name could not be identified confidently.")
        if equipment_type is None:
            raise ValueError(f"{name}: weapon/trinket type was not recognized; entry not counted.")
        owner = values.get("equipped_by")
        if key(owner) in ("none", "not equipped", "unequipped", "unassigned", "-"):
            owner = None
        status = key(values.get("equipped_status"))
        unequipped = status in ("not equipped", "unequipped", "not equipped by anyone")
        equipped = False if unequipped else (True if status == "equipped" or owner else None)
        if unequipped:
            owner = None
        return {"name": name, "type": equipment_type, "level": values.get("level"),
                "stars": read_stars(frame, self.config.get("stars")), "equipped": equipped,
                "equipped_by": owner, "ocr": diagnostics, "captured_at": now()}

    def recover_list(self):
        frame = self.capture()
        if self.kind == "roster" and self.overlay_visible(frame):
            frame = self.dismiss_overlay(frame)
        if self.is_page(frame, "list_page"):
            return self.wait_for_list()
        if self.kind == "roster" and self.is_page(frame, "details_page"):
            self.input.click(self.config["back"])
            return self.wait_for_list()
        raise NavigationError("Unknown page after entry failure. Stopped without blind back/click actions.")

    def run(self, count):
        seen_characters, visited = set(), set()
        offset, no_movement = 0.0, 0
        try:
            self.input.foreground()
            frame = self.wait_for_list()
            while self.store.run["collected"] < count:
                slots = list(visible_slots(self.config["grid"], offset))
                if not slots:
                    raise NavigationError("No usable grid centers.\n" + grid_diagnostics(self.config["grid"], self.kind.title(), offset))
                for position, point in slots:
                    if position in visited:
                        continue
                    visited.add(position)
                    # Observe immediately before every click, not just after ENTER.
                    frame = self.wait_for_list()
                    before_entry = frame
                    try:
                        self.detail_before = before_entry
                        self.input.click(point)
                        detail = self.wait_for_character_details() if self.kind == "roster" else self.wait_for_equipment_details()
                        record = self.read_character(detail) if self.kind == "roster" else self.read_equipment(detail)
                        if self.kind != "roster":
                            record.setdefault("ocr", {})["panel_update"] = dict(self.last_panel_update)
                        identity = key(record["name"])
                        if self.kind != "roster" or identity not in seen_characters:
                            record["source"] = {"scan_id": self.store.run["id"],
                                                "row": position[0], "column": position[1]}
                            self.store.add(record)
                            seen_characters.add(identity)
                            suffix = f" - {record['type']}" if self.kind == "equipment" else " scraped"
                            print(f"[{self.store.run['collected']}/{count}] {record['name']}{suffix}")
                    except (RuntimeError, ValueError, cv2.error) as error:
                        print(f"Entry {position}: {error}")
                        self.store.error(position, error)
                    frame = self.recover_list()
                    if self.store.run["collected"] >= count:
                        self.store.finish("complete")
                        return
                    displacement = estimate_scroll_shift(before_entry, frame, self.config["grid"]["viewport"],
                                                         self.config["grid"]["row_pitch"])
                    if displacement > .003:
                        raise NavigationError("List position changed while viewing details. Restart from the top.")
                before = frame
                self.input.scroll(center(self.config["grid"]["viewport"]), self.config.get("scroll_delta", -120))
                frame = self.wait_for_list()
                shift = estimate_scroll_shift(before, frame, self.config["grid"]["viewport"],
                                              self.config["grid"]["row_pitch"])
                if shift < .002:
                    no_movement += 1
                    if no_movement >= 2:
                        print("End of list, or the list no longer moves. Partial results saved.")
                        self.store.finish("end_of_list")
                        return
                else:
                    no_movement = 0
                    offset += shift
        except KeyboardInterrupt:
            self.store.finish("cancelled")
            print("\nStopped. Collected data is preserved.")
        except (RuntimeError, ValueError, cv2.error) as error:
            self.store.error(None, error)
            self.store.finish("stopped")
            print(f"Scan stopped: {error}")


def validate_config(config, kind, backend):
    try:
        def rectangle(region, path):
            region.update(normalized_rectangle(region, f"{kind}.{path}"))

        def point(value):
            if not all(0 <= value[axis] <= 1 for axis in ("x", "y")):
                raise ValueError("Input target outside the window")

        def page(value, path):
            rectangle(value["region"], path + ".region")
            array = np.asarray(value["template"])
            if array.ndim != 2 or array.std() < 3 or not 0 < value.get("threshold", .88) <= 1:
                raise ValueError("Invalid/blank page anchor")

        if config["screen"]["capture_backend"] != backend:
            raise ValueError("Capture backend differs")
        if config["screen"]["width"] <= 0 or config["screen"]["height"] <= 0:
            raise ValueError("Invalid reference resolution")
        grid = config["grid"]
        rectangle(grid["viewport"], "grid.viewport")
        rectangle(grid["first_card"], "grid.first_card")
        validate_grid(grid)
        for state in ("list_page", "details_page"):
            page(config[state], state)
        for field in (["name", "type"] if kind == "equipment" else ["name"]):
            if field not in config["fields"]:
                raise ValueError(f"Missing {field} region")
        for field, region in config["fields"].items():
            rectangle(region, "fields." + field)
        for field, region in config.get("stats", {}).items():
            rectangle(region, "stats." + field)
        for group, panel in config.get("character_details", {}).items():
            page(panel["detail_marker"], f"character_details.{group}.detail_marker")
            for field, region in panel.items():
                if field != "detail_marker":
                    rectangle(region, f"character_details.{group}.{field}")
        for slot, route in panel_routes(config).items():
            point(route["open"])
            if "character_details" not in config:
                page(route["page"], f"build_panels.{slot}.page")
            if not route["fields"].get("name"):
                raise ValueError("Build panel is missing its name region")
            if "character_details" not in config:
                for field, region in route["fields"].items():
                    rectangle(region, f"build_panels.{slot}.fields.{field}")
        if panel_routes(config):
            if "overlay_dismiss" not in config:
                raise ValueError("Calibrate ONE generic overlay_dismiss point; legacy per-panel close points are not used")
            point(config["overlay_dismiss"])
        if config.get("stars"):
            for index, slot in enumerate(config["stars"]["slots"]):
                rectangle(slot, f"stars.slots[{index}]")
        if not (-120 <= config.get("scroll_delta", -120) < 0):
            raise ValueError("scroll_delta must be between -120 and -1")
        if not 1 <= config.get("timeout", 10) <= 60:
            raise ValueError("timeout must be between 1 and 60 seconds")
        if not 1 <= config.get("detail_timeout", 5) <= 60:
            raise ValueError("detail_timeout must be between 1 and 60 seconds")
        if kind == "equipment":
            if config.get("details_mode") != "inline":
                raise ValueError(f"{kind.title()} requires the persistent inline details pane; run calibrate-{kind}")
        else:
            point(config["back"])
    except (KeyError, TypeError, ValueError) as error:
        report = ""
        try:
            report = "\n" + grid_diagnostics(config["grid"], kind.title())
        except (KeyError, TypeError, ValueError, ZeroDivisionError):
            pass
        raise RuntimeError(f"Invalid {kind} calibration: {error}.{report}\nReason rejected: {error}. Run calibrate-{kind}.") from error


def scan_account(args, api):
    kind = args.command
    if args.count <= 0:
        raise RuntimeError("--count must be greater than 0.")
    saved = load_json(args.config, {})
    if not isinstance(saved, dict):
        raise RuntimeError(f"Invalid {args.config}: expected a calibration JSON object.")
    config = saved.get(kind)
    if not config:
        raise RuntimeError(f"Run python soc_scraper.py calibrate-{kind} first.")
    validate_config(config, kind, api.CAPTURE_BACKEND)
    if not guided_prompt(kind):
        return
    while True:
        try:
            hwnd, _ = api.find_window(args.title)
            frame = api.capture_window(hwnd)
            valid = (matches_page(frame, config["list_page"])
                     and (matches_page(frame, config["details_page"]) if config.get("details_mode") == "inline"
                          else not matches_page(frame, config["details_page"])))
        except RuntimeError as error:
            print(error)
            valid = False
        if valid:
            break
        if not retry_page(kind):
            return
    print("Found Sword of Convallaria window.")
    print("Starting character scan..." if kind == "roster" else f"Starting {kind} scan...")
    store = AccountStore(args.output, kind, args.count)
    Scanner(api, hwnd, kind, config, store).run(args.count)
    print(f"Saved {store.run['collected']}/{args.count} entries to {args.output} ({store.run['status']}).")


def calibrate_scanner(args, api):
    from soc_calibration import calibrate_scanner as calibrate
    calibrate(args, api)
