# Sword of Convallaria account scanner

A Windows-only, external UI scanner that exports visible character builds and owned
equipment to JSON for review and AI recommendations. It extends the original
single-character scraper; `windows`, `calibrate`, `scrape`, and `watch` still work.

**Weapons and trinkets are scanned together from the same Equipment page** —
Inventory → Gear in the English UI. There are no separate weapon/trinket workflows.

The scanner deliberately does **not** collect Tarots, currencies, or upgrade
resources. It does not change builds, equip items, enhance gear, or spend resources.

## What it collects

| Character data | Equipment data |
| --- | --- |
| Name, rank, stars | Name, weapon/trinket category |
| Equipped weapon and trinket names | Item level, stars/dupe level |
| Weapon/trinket engraving types | Equipped status and visible owner name |
| Up to three equipped skill names | Separate entries for separate physical copies |
| Level and power | Scan position and generated copy ID |
| Final displayed HP, P.ATK, M.ATK, P.DEF, M.DEF, Speed | |

Fields require reliable text or calibrated visual samples. Unknown values are
`null`; an empty skills array means no skill names were read, not necessarily that
no skills are equipped. OCR diagnostics and warnings are included. A portrait alone
does not identify a character name. Engraving types are collected, not all rolls.

This exports recommendation input; it is not a built-in build optimizer or game
knowledge database. Unlocked but unequipped skills, team synergy, and item effects
require information beyond the visible fields collected here.

## Installation

Use Windows 10/11 with Windows Graphics Capture support and Python. The project
has been tested with Python 3.13 and `windows-capture` 2.0.1.

From PowerShell in `C:\soc-scraper`:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\Activate.ps1
```

Reuse the existing virtual environment if present. Activation is optional: replace
`python` below with `.\.venv\Scripts\python.exe` when needed.

Dependencies: `opencv-python`, `numpy`, `pytesseract`, `pywin32`, and
`windows-capture==2.0.1`. `mss` is not used or required.

### Tesseract

Install Tesseract separately with English language data. The script checks PATH,
then `C:\Program Files\Tesseract-OCR\tesseract.exe` and
`C:\Program Files (x86)\Tesseract-OCR\tesseract.exe`. For another installation
location, add its directory to PATH. Installing `pytesseract` alone does not install
the OCR engine.

The calibration bootstrap recognizes the English **Character List** / **My Characters**
headings and the Equipment/Gear label. It isolates the header, tries enlarged and
thresholded OCR, and accepts merged `MyCharacters` text or small OCR spelling errors.
The bare **Characters** heading belongs to character details and is not accepted
as the roster. Failed calibration checks print the heading OCR for troubleshooting.
Other UI languages need adjusted bootstrap labels and Tesseract language settings.

## First-time calibration

```powershell
python soc_scraper.py calibrate-roster
python soc_scraper.py calibrate-equipment
```

Each command tells you which page to open and waits for **ENTER**; **Q** cancels.
It checks a captured game frame before proceeding. If the expected page is not
recognized, it asks you to switch to it and press ENTER to retry.

Calibration does not navigate automatically. Open each requested game panel
manually, return to the terminal, and press ENTER. In each selection window, drag
a rectangle and press ENTER or Space. **C** skips optional selections. Required
selections offer retry/cancel. Previews are scaled to fit your display; saved
coordinates are normalized to the original HWND frame, including its title bar.

Both calibrations are stored in `scanner_regions.json`, under separate `roster`
and `equipment` sections. The original `regions.json` is unchanged. Cancellation
or an invalid calibration preserves the previous scanner configuration.

### Roster calibration

1. Open **Character List** at the top, using a uniform grid with at least two visible rows.
2. Select the **Character List** heading, including both words, or a stable
   list-only control such as the sort label. Do not select the character-details
   heading **Characters**.
3. Select the scrollable card area, excluding controls and scrollbar. Enter its
   column count; select the first card, last card in row one, and first card in
   row two. Include each card's full bounds consistently.
4. Open the first character. Select a distinct details-page anchor and its Back
   target. Reuse existing name/rank/level/power regions or select new ones. Crop
   numeric values tightly, excluding labels and decorative icons.
   The wizard also offers the six **final combat stat** numbers on this main
   character-details screen. Existing single-character stat regions can be reused.
5. Optionally calibrate stars: select each slot and identify filled and empty
   references. A missing reference can be supplied from another character in the
   same layout. Both types are required; otherwise stars remain null.
6. For each equipped weapon, trinket, and skill slot, select its information target
   on the character frame. Open the panel manually; select its stable anchor,
   name, and Close/Back target. **Do not select Equip, Replace, Enhance, Upgrade,
   or another action that changes the account.** Optional panels can be skipped.
7. For gear panels, select visible engraving **type** text or its icon. For an
   icon, supply its known label and optionally add other examples at the same
   location. Unseen icon types remain null.
8. Return to the roster and scroll to the top for the final page check.

### Equipment calibration — one inventory for both categories

1. Open Inventory → Gear, containing **weapons and trinkets**, apply the normal
   filter/sort you want, and scroll to the top.
2. Use the **selected Gear tab including its highlight** as the list anchor. This
   distinguishes Gear from the neighboring Tarot, Material, and other tabs.
3. Calibrate the grid as above.
4. The usual Equipment layout has a persistent details panel beside the grid.
   Answer **Y** to the inline-details question. Select the first item, the details
   anchor, and a small distinctive section of the first card's **selected border
   or corner**, excluding artwork. The wizard checks that this does not also match
   a neighboring unselected card. No Back click is used between equipment entries.
5. If your layout opens a separate details page, answer **N** and calibrate its Back
   target instead.
6. Select the name, level, and optional stars/ownership fields. For type, select the
   **equipment category label or icon**. Never use the item name, rarity, or engraving
   type: weapons and trinkets can share the same engraving type.
7. Text categories such as Weapon, Sword, Bow, Staff, Trinket, and Accessory are
   classified automatically. For icon-only types, label examples `weapon` or
   `trinket`; include every different weapon-family icon you expect to scan.
   The same region must work for both categories. Unknown types are skipped and
   reported, rather than guessed from an item's name or artwork.
8. Return to the Equipment list at the top for the final check.

Optional configuration settings include `timeout` (10 seconds), `scroll_delta`
(-120; reduce its magnitude if scrolling loses overlap), per-field `min_confidence`
(50 on Tesseract's 0–100 scale), and `type_words` to override category labels.
Page anchors and icon references are compact pixel arrays in calibration JSON,
not exported screenshots.

Recalibrate after changing window mode, UI scale, aspect ratio, grid, or panel
layout. Normalized coordinates tolerate proportional resizing, not arbitrary layout
changes. Resizing during a scan stops it.

### Final displayed combat stats

Every character exported by `roster --count N` includes:

```json
"stats": {
  "hp": 6269,
  "p_atk": 3505,
  "m_atk": 1722,
  "p_def": 740,
  "m_def": 745,
  "speed": 180
}
```

These illustrate the numbers in the supplied Rawiyah details reference. The
scanner reads the **displayed totals directly**, before opening gear/skill panels;
it does not calculate totals from base stats or add equipment/engraving bonuses.

During `calibrate-roster`, select only the numeric value for HP, P.ATK, M.ATK,
P.DEF, M.DEF, and Speed. On the reference main Attributes panel, the top row is
P.ATK / M.ATK, middle row P.DEF / M.DEF, bottom row HP / Speed. Do not select a
weapon's individual stats or the percentage bonuses in an engraving popup.
Each selected region is stored in the roster configuration's `stats` section;
there are no built-in stat coordinates. A skipped region remains unknown.

The original `calibrate` command now offers these same six numeric regions through
`fields.json`, saving them in `regions.json`. `scrape` and `watch` put the resulting
values under `data.stats`. Roster calibration can reuse those selections, so they
need not be drawn again. Older calibrations still work; uncalibrated stats are null.
Rerun `calibrate-roster` to add stats to an existing scanner configuration.

Each stat is independent. Low-confidence, malformed, empty, or failed OCR becomes
`null` without discarding other stats or the character. Raw text, confidence, and
status are retained in `ocr.stats`, for example:

```json
"stats": {"p_atk": null},
"ocr": {
  "stats": {
    "p_atk": {"raw": "1842", "confidence": 0.42, "status": "uncertain"}
  }
}
```

All six keys are present in actual output. Stat OCR defaults to a minimum confidence
of 70/100 (configurable per region via `min_confidence`). Only whole numbers or
properly grouped thousands are accepted; the parser does not guess digits or
interpret fractions, percentages, or bonus expressions as combat totals.

## Character scan

1. Launch Sword of Convallaria.
2. Run:

   ```powershell
   python soc_scraper.py roster --count 10
   ```

3. Follow the on-screen prompt.
4. Open the Characters page and scroll to the top.
5. Return to the terminal.
6. Press ENTER.
7. Let the scan complete. Avoid moving the mouse or switching apps during automation.

After validation, the scanner foregrounds the game, visits cards left-to-right
and top-to-bottom, reads details and calibrated build panels, returns to the roster,
and scrolls when necessary. It counts **unique successfully identified character
names**, ignoring repeats after scrolling. Name matching ignores case and repeated
whitespace; it does not merge an SP nickname with a different full in-game name.

```text
Found Sword of Convallaria window.
Starting character scan...
[1/10] Inanna of Convallaria scraped
[2/10] Rawiyah scraped
```

## Equipment scan

1. Run:

   ```powershell
   python soc_scraper.py equipment --count 30
   ```

2. Follow the on-screen prompt.
3. Open the Equipment page containing weapons and trinkets, set your desired normal
   filter/sort, and scroll to the top.
4. Return to the terminal.
5. Press ENTER.
6. Let the scan complete.

This scans the first 30 successfully identified **equipment entries in the current
ordering**, regardless of category. Two copies remain separate even with identical
names, levels, and stars. Identity comes from row/column positions and measured
scroll overlap, never name or image deduplication.

```text
[1/30] Newborn Blade - weapon
[2/30] Newborn Blade - weapon
[3/30] Fancy Hat - trinket
```

## Counts, cancellation, and partial results

Any positive integer is accepted:

```powershell
python soc_scraper.py roster --count 5
python soc_scraper.py roster --count 20
python soc_scraper.py equipment --count 10
python soc_scraper.py equipment --count 50
```

Zero/negative counts produce `Error: --count must be greater than 0.`

Each successful entry is saved immediately with atomic file replacement. **Ctrl+C
preserves collected data.** Failed entries are recorded in the scan's `errors` and
skipped when the list can be recovered safely. Optional panel failures produce
warnings/nulls where possible. Unknown pages, lost focus, ambiguous scroll overlap,
and unverified selections stop the scan without blind clicks.

Two verified no-movement scrolls mark `end_of_list`. Fewer entries than requested
can indicate an exhausted list, unrecognized entries, or a navigation problem;
inspect status and errors. `complete` means the requested count was reached,
**not** that the whole account was scanned.

Restart from the top; there is no automatic mid-list resume. Roster runs update
matching characters and retain others from earlier runs. Equipment runs replace
the prior inventory snapshot **on the first successful entry**, preventing a rerun
from doubling owned copies. An interrupted run retains its partial snapshot and
status. A run with zero successful items leaves the previous equipment snapshot intact.

## Generated JSON

Both commands update `soc_account.json`. Use the same custom output for both to
combine their results:

```powershell
python soc_scraper.py roster --count 10 --output my_account.json
python soc_scraper.py equipment --count 30 --output my_account.json
```

Abbreviated illustrative output, not a claim about your account:

```json
{
  "game": "Sword of Convallaria",
  "characters": [
    {
      "name": "SP Inanna",
      "rank": 11,
      "stars": 3,
      "stats": {"hp": 6421, "p_atk": 1842, "m_atk": 957, "p_def": 811, "m_def": 704, "speed": 162},
      "equipped": {"weapon": "Weapon A", "trinket": "Trinket B"},
      "engravings": {"weapon": "Cup", "trinket": null},
      "skills": ["Skill A", "Skill B", "Skill C"],
      "level": 55,
      "power": 4821,
      "equipment_ids": {"weapon": "equipment_001", "trinket": null}
    }
  ],
  "equipment": [
    {
      "id": "equipment_001",
      "name": "Weapon A",
      "type": "weapon",
      "level": 60,
      "stars": 5,
      "equipped": true,
      "equipped_by": "SP Inanna"
    },
    {
      "id": "equipment_002",
      "name": "Weapon A",
      "type": "weapon",
      "level": 60,
      "stars": 3,
      "equipped": false,
      "equipped_by": null
    }
  ]
}
```

Actual output adds timestamps, raw OCR/confidence, grid positions and scan IDs,
warnings, and `scans` metadata. Equipment IDs belong to an inventory snapshot;
they are not permanent game IDs.

`equipped: null` means unknown, unlike verified `false`. A null owner does not prove
an item is free. Reconciliation links a physical copy only when name, type, and
visible owner identify one copy. A unique visible inventory owner can fill a
missing equipped name, marked in `reconciled_fields`. Ambiguous matches remain in
`equipment_candidates`; no copy is arbitrarily assigned.

## Original single-character commands

```powershell
python soc_scraper.py windows
python soc_scraper.py calibrate
python soc_scraper.py scrape --output soc_data.json
python soc_scraper.py watch --interval 2 --output soc_data.json
```

Manually open character details for `calibrate`, `scrape`, and `watch`. These use
`fields.json` and `regions.json`; they do not navigate or run account page checks.
A wrong screen can produce incorrect OCR. `watch` refreshes single-character JSON,
not the roster.

Capture/navigation commands accept `--title`. Exact case-insensitive matches take
priority; ambiguous matches report candidates. New scanner commands also accept
`--config scanner_regions.json`.

## Troubleshooting

| Symptom | Action |
| --- | --- |
| Expected page not detected | Use Character List for roster calibration, not character details. Read the heading OCR diagnostic; check language/anchors. After updating the script, stop the old process and restart it to load the fix. Q cancels. |
| Calibration missing | Run the matching `calibrate-roster` or `calibrate-equipment` command. |
| Details mistaken for list | Select distinct anchors; use inline mode for the side-by-side Equipment layout. |
| Identical copies fail selection | Recalibrate a distinctive selected border without artwork. |
| Type unknown/wrong | Select actual equipment category, not engraving; add missing category-icon samples. |
| Stars/engravings/skills missing | Calibrate their slots/panels and reference icons. Unknown symbols stay null. |
| Empty rank / extra power digits | Crop digits tightly, excluding labels and decorative icons. |
| Combat stat is null | Check `ocr.stats` for raw text/confidence/status, then calibrate just the numeric total on the main Attributes panel. Old calibration files have no stat regions until selected. |
| Owner unknown | Calibrate visible owner text if available; a portrait alone is insufficient. |
| Scroll overlap fails | Keep a uniform grid and fixed sort/filter, reduce `scroll_delta`, restart at top. Identical rows can be ambiguous. |
| Focus lost / click covered | Stop interacting during scans, restore/uncover the game, and restart. |
| Foreground/input refused | Start from the terminal prompt; keep game/terminal at compatible privilege levels. Windows restrictions are not bypassed. |
| Minimized / capture timeout | Restore the game and keep it rendering. There is no desktop fallback. |
| Wrong aspect ratio / resized | Restore the calibrated layout or recalibrate, then restart at top. |
| OCR engine missing | Install Tesseract with English data; check PATH or Program Files locations. |
| Existing JSON cannot be read | Repair it or choose another `--output`; corrupt data is not silently overwritten. |

## Architecture and safety

```text
Exact-title HWND selection
  → Windows Graphics Capture of that window
  → calibrated page validation
  → ordinary Windows foreground/mouse input
  → capture and crop visible regions
  → OCR / calibrated visual samples
  → parsed records and conservative reconciliation
  → incremental soc_account.json
```

Capture remains `WindowsCapture(window_hwnd=hwnd)` with an owned BGR copy. It can
capture the game under overlapping apps. **Automation still needs an uncovered,
foreground game**; input targets are checked. Coordinates use visible DWM bounds,
avoiding invisible resize-border offsets. Page checks and short polling waits
replace long fixed sleeps.

No memory reading, DLL injection, hooks, network interception, process modification,
or desktop-region capture is used. Normal workflows save no screenshots. All
account output stays in local JSON files.

`soc_scraper.py` retains capture/OCR/CLI; `soc_account.py` handles scans and saving;
`soc_navigation.py` handles mouse input/scroll tracking; `soc_calibration.py` handles
the interactive wizard.

## Verification and current limits

```powershell
python -m unittest -v
```

Tests cover capture ownership/timeouts, title selection, prompts/retries, counts,
duplicate copies across scroll overlap, unique characters, failed entries, Ctrl+C,
atomic saving, reconciliation, classification, stars, and ambiguous scroll rejection.
Stat tests also cover direct displayed totals, per-stat OCR failure/low confidence,
strict parsing, calibration reuse, and nested stats/diagnostics in both output formats.

HWND capture was previously verified with VS Code overlapping the game. The
current Equipment/Gear page was inspected live and recognized by the calibration
bootstrap. Character List detection was also verified live after fixing the
merged-word/header OCR rejection. The new multi-entry workflows still need calibration and an end-to-end
run on your layout; synthetic tests do not replace that check. Start with a small
count, compare JSON against the UI, then increase the count.

## Example AI use

Give the combined `soc_account.json` to your AI assistant:

> Recommend the best builds and strongest teams using only these characters and
> equipment. Respect separate copy IDs; never assign one physical item to two
> characters simultaneously. Show gear conflicts, suggested engraving pairs, and
> equipped-skill changes. Treat nulls as unknown, check scan coverage and OCR
> warnings, and ask about missing ownership/unlocked skills instead of guessing.
> Do not include Tarots or resource costs.
