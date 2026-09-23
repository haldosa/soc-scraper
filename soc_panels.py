"""Multiline visible-panel OCR and conservative Tarot parsing."""
import re

import cv2
import pytesseract

from soc_navigation import crop


TAROT_STATS = ("p_atk", "m_atk", "p_def", "m_def", "max_hp")


def read_block(api, frame, spec):
    """Keep OCR line boundaries/confidence for stat rows and wrapped effects."""
    if not spec:
        return None, {"raw": "", "confidence": 0., "status": "not_calibrated", "lines": []}
    try:
        candidates = []
        for processed in api.create_ocr_versions(crop(frame, spec)):
            data = pytesseract.image_to_data(processed, config="--psm 6", timeout=6,
                                            output_type=pytesseract.Output.DICT)
            groups = {}
            for i, word in enumerate(data["text"]):
                confidence = float(data["conf"][i])
                if not word.strip() or confidence < 0:
                    continue
                group = tuple(data[k][i] for k in ("block_num", "par_num", "line_num"))
                groups.setdefault(group, []).append((word.strip(), confidence / 100))
            lines = [{"raw": " ".join(word for word, _ in words),
                      "confidence": round(sum(c for _, c in words) / len(words), 3)}
                     for words in groups.values()]
            confidence = sum(line["confidence"] for line in lines) / len(lines) if lines else 0.
            candidates.append({"raw": "\n".join(line["raw"] for line in lines),
                               "confidence": round(confidence, 3), "lines": lines})
        diagnostic = max(candidates, key=lambda item: item["confidence"], default={"raw": "", "confidence": 0., "lines": []})
        reliable = bool(diagnostic["raw"]) and all(
            line["confidence"] >= spec.get("min_confidence", 60) / 100 for line in diagnostic["lines"])
        diagnostic["status"] = "ok" if reliable else "uncertain"
        return diagnostic["raw"] if reliable else None, diagnostic
    except (RuntimeError, ValueError, cv2.error) as error:
        return None, {"raw": "", "confidence": 0., "status": "error", "error": str(error), "lines": []}


def parse_tarot_stats(diagnostic, minimum=70):
    stats = dict.fromkeys(TAROT_STATS)
    observations = {name: [] for name in TAROT_STATS}
    pattern = re.compile(r"^\s*[\W_]*(P\s*\.?\s*ATK|M\s*\.?\s*ATK|P\s*\.?\s*DEF|M\s*\.?\s*DEF|Max\s*HP)"
                         r"\s*[:\]\)]?\s+([0-9]+|[0-9]{1,3}(?:,[0-9]{3})+)\s*$", re.I)
    for line in diagnostic.get("lines", []):
        match = pattern.fullmatch(line["raw"])
        if not match:
            continue
        label = re.sub(r"[^a-z]", "", match[1].lower())
        name = {"patk": "p_atk", "matk": "m_atk", "pdef": "p_def", "mdef": "m_def", "maxhp": "max_hp"}[label]
        observations[name].append((int(match[2].replace(",", "")), line))
    diagnostics = {}
    for name, rows in observations.items():
        diagnostics[name] = {"raw": "\n".join(row["raw"] for _, row in rows),
                             "confidence": min((row["confidence"] for _, row in rows), default=0.),
                             "status": "missing_or_invalid"}
        if len(rows) == 1:
            if rows[0][1]["confidence"] >= minimum / 100:
                stats[name] = rows[0][0]
                diagnostics[name]["status"] = "ok"
            else:
                diagnostics[name]["status"] = "uncertain"
        elif rows:
            diagnostics[name]["status"] = "ambiguous"
    return stats, diagnostics


def effect_entries(text):
    """Join wrapped lines; keep separate visible effect statements in order."""
    if not text:
        return []
    # A replacement skill can say "into: Increases ..." within one effect.
    # Split at OCR line starts, not every occurrence of an effect verb.
    text = re.sub(r":\s*\n\s*", ": ", text)
    return [" ".join(part.strip(" •♦\n").split()) for part in re.split(
        r"\n[ \t•♦]*(?=(?:Increases|Decreases|Changes the effect|Grants|Reduces)\b)", text) if part.strip()]


def panel_routes(config):
    """Resolve click targets against shared geometry without duplicating saved ROIs.

    Legacy calibrations remain readable until the user replaces them with the wizard.
    """
    if "character_details" not in config:
        return config.get("build_panels", {})
    routes = {}
    for slot, target in config.get("build_targets", {}).items():
        group = "skills" if slot.startswith("skill_") else "tarot" if slot == "tarot" else "gear"
        panel = config["character_details"][group]
        fields = {name: value for name, value in panel.items() if name not in ("detail_marker",)}
        if "engraving_type" in fields:
            fields["engraving"] = fields.pop("engraving_type")
        routes[slot] = {"open": target, "page": panel["detail_marker"], "fields": fields, "panel_kind": group}
    return routes
