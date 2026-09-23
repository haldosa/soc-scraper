# Sword of Convallaria account scanner

A Windows-only, external UI scanner that exports visible character builds and owned
equipment and Tarot Whispers to JSON for review and AI recommendations. It extends the original
single-character scraper; `windows`, `calibrate`, `scrape`, and `watch` still work.

**Weapons and trinkets are scanned together from the same Equipment page** —
Inventory → Gear in the English UI. There are no separate weapon/trinket workflows.

Each character's **equipped Tarot Whisper** is read from its slot on the character
details page during `roster`, using separate popup regions calibrated in
`calibrate-roster`. The scanner does **not** scan the Tarot inventory, currencies,
or upgrade resources. It does not change builds, equip items, enhance gear, or spend resources.

## What it collects

| Character data | Equipment data |
| --- | --- |
| Name, rank, stars | Name, weapon/trinket category |
| Equipped weapon and trinket names | Item level, stars/dupe level |
| Weapon/trinket engraving types | Equipped status and visible owner name |
| Configurable number of equipped skill names | Separate entries for separate physical copies |
| Level and power | Scan position and generated copy ID |
| Final displayed HP, P.ATK, M.ATK, P.DEF, M.DEF, Speed | |

Fields require reliable text or calibrated visual samples. Unknown values are
`null`; an empty skills array means no skill names were read, not necessarily that
no skills are equipped. OCR diagnostics and warnings are included. A portrait alone
does not identify a character name. Shared gear calibration can also capture visible
engraving bonus text. Equipped Tarot data includes name, level, owning character, main stats,
rolled effects, and skill text. Only visible/calibrated content is collected;
off-screen or clipped descriptions are not reconstructed.

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
headings and Inventory/Gear labels. It isolates the header, tries enlarged and
thresholded OCR, and accepts merged `MyCharacters` text or small OCR spelling errors.
The bare **Characters** heading belongs to character details and is not accepted
as the roster. Failed calibration checks print the heading OCR for troubleshooting.
Other UI languages need adjusted bootstrap labels and Tesseract language settings.
Inventory bootstrap is a plausibility check following your guided page selection.
Calibrated scans check a stable inventory heading/count label and the corresponding
details-pane marker; they do not depend on selected-tab highlights or card borders.

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

Calibrations are stored in `scanner_regions.json`, under separate `roster` and
`equipment` sections. Equipped Tarot geometry belongs to the roster configuration.
The original `regions.json` is unchanged. Cancellation
or an invalid calibration preserves the previous scanner configuration.

### Roster calibration

1. Open **Character List** at the top, using a uniform grid with at least two visible rows.
2. Select the **Character List** heading, including both words, or a stable
   list-only control such as the sort label. Do not select the character-details
   heading **Characters**.
3. Select only the scrollable character-card viewport: start below the **Character
   List** header and end above **Power / Compact Mode / Ranking**. Exclude the
   fixed bottom controls and scrollbar. Enter the visible column count (**5** in
   the reference layout); select the first card, last card in row one, and first
   card in row two consistently. Their centers establish the grid. Small decorative
   overhangs outside the viewport are allowed; pixel-perfect containment is not
   required. The wizard prints slot diagnostics and offers a retry immediately
   if the geometry is invalid, before continuing the rest of calibration.
4. Open the first character. Select a distinct details-page anchor and its Back
   target. Reuse existing name/rank/level/power regions or select new ones. Crop
   numeric values tightly, excluding labels and decorative icons.
   The wizard also offers the six **final combat stat** numbers on this main
   character-details screen. Existing single-character stat regions can be reused.
5. Optionally calibrate stars: select each slot and identify filled and empty
   references. A missing reference can be supplied from another character in the
   same layout. Both types are required; otherwise stars remain null.
6. Select **one generic overlay-dismiss point** in a safe dead area on character
   details. It must close information panels without changing gear/skills or
   navigating back. The separate character-page **Back** target returns to the
   Character List; it is not an overlay dismiss target.
7. Enter the number of equipped skill icons to inspect. Equipped Tarot is included
   in the wizard. Select each **click target** on the character frame once.
   Then calibrate **one shared gear panel**: a stable marker, name, engraving type,
   and optional engraving stats/bonus text. Weapon and trinket reuse those exact
   regions. For the marker, select a shared frame feature or Skill heading,
   not the changing Weapon/Trinket title. Icon-only engraving types can use labelled
   visual samples; unseen types remain null.
8. Calibrate **one shared skill panel**: marker and name. All equipped skill
   targets read those same regions. Equipped Tarot gets its own marker,
   name, level, main-stat table, additional details, and skill text.
   Ownership comes from the character being scraped; no owner ROI is needed.
   Its geometry is separate from both gear and skill panels.
9. Dismiss the last overlay using the generic dead-area point, return to the
   Character List, and scroll to the top for the final page check.

Navigation is **weapon → trinket → skill 1 → skill 2 → …**, with direct clicks and
no dismiss between selections. Equipped Tarot is inspected last. The
single generic dismiss point is used only to close the remaining overlay before
returning to the Character List. Choose exposed information targets, never
Equip, Unequip, Replace, Refine, Enhance, Upgrade, or another action that changes
the account.

New roster calibration stores geometry once under `character_details.gear`,
`character_details.skills`, and `character_details.tarot`; separate
`build_targets` hold only click points. Each panel contains `detail_marker` and
`name`; gear adds `engraving_type` / `engraving_stats`, and Tarot adds `level`,
`main_stats`, `details`, and `skill` when calibrated. For example:

```text
roster
  character_details
    gear:   detail_marker, name, engraving_type, engraving_stats
    skills: detail_marker, name
    tarot:  detail_marker, name, level, main_stats, details, skill
  build_targets: weapon, trinket, skill_1, skill_2, ..., tarot
  overlay_dismiss: one point
```

Existing `build_panels` configurations remain readable, but run `calibrate-roster`
to replace duplicated regions with the shared format. Legacy per-panel `close`
and `dismiss_gear_before_skills` settings are ignored. A generic `overlay_dismiss`
point is still required when build panels are configured.

### Equipment calibration — one inventory for both categories

1. Open Inventory → Gear, containing **weapons and trinkets**, apply the normal
   filter/sort you want, and scroll to the top.
2. Use a stable **Inventory heading** or **Gear:** count label as the list anchor.
   Exclude changing count digits. There is no selected Gear highlight requirement.
3. Calibrate the grid as above.
4. Equipment uses the persistent **right-side details pane** beside the grid.
   Select the first item and a stable gear-details marker. **Selected-card-border
   calibration is no longer needed.** No Back click is used between equipment entries.
   Each click updates this pane: **item 1 → read pane → item 2 → read pane → …**.
   The scanner polls the previous/current name, detail marker, and panel content,
   then waits for readable stable content before OCR.
5. Select the name, level, and optional stars/ownership fields. For type, select the
   **equipment category label or icon**. Never use the item name, rarity, or engraving
   type: weapons and trinkets can share the same engraving type.
6. Text categories such as Weapon, Sword, Bow, Staff, Trinket, and Accessory are
   classified automatically. For icon-only types, label examples `weapon` or
   `trinket`; include every different weapon-family icon you expect to scan.
   The same region must work for both categories. Unknown types are skipped and
   reported, rather than guessed from an item's name or artwork.
7. Leave Inventory → Gear at the top for the final check. Old calibrations using
   a separate equipment details page must be replaced with `calibrate-equipment`.

### Equipped Tarot calibration

Run `python soc_scraper.py calibrate-roster` and follow its character-details
steps. Select the equipped Tarot icon alongside weapon and trinket. When prompted,
click that icon to open the **equipped Tarot popup on the character page**;
stay on that character, without navigating to Inventory or selecting another copy.

Select a Tarot-specific detail marker, name, and numeric level. For
`main_stats`, select the **whole labelled table** containing P.ATK, M.ATK, P.DEF,
M.DEF, and Max HP. This uses multiline OCR to associate numbers with their labels;
unreadable/ambiguous rows become null independently. Select the additional rolled
effects and skill description in their own text regions, including wrapped lines.
Do not include buttons or neighboring gear stats. Names are required; optional
unavailable fields can be skipped.

Every character's equipped Tarot uses these same popup regions, independently of
the shared weapon/trinket layout. A skipped Tarot target or unreadable Tarot name
produces `tarot: null` and a warning; the rest of the character still saves.

Optional configuration settings include `timeout` (10 seconds), `scroll_delta`
(-120; reduce its magnitude if scrolling loses overlap), per-field `min_confidence`
(50 on Tesseract's 0–100 scale), and `type_words` to override category labels.
Page anchors and icon references are compact pixel arrays in calibration JSON,
not exported screenshots.

`detail_timeout` defaults to 5 seconds. A changed OCR name, changed detail content,
or marker transition can confirm an update. Identical inventory copies (or the
already-selected first card) may produce no visible change: after this timeout,
a still-readable stable pane is retained with
`ocr.panel_update.status: "unchanged_after_timeout"`. This preserves separate
copies, but cannot distinguish a missed click from an identical panel; review
those diagnostics. Unreadable panes and unchanged sibling skills time out as errors.

Rectangle validation identifies the exact field path and prints x/y/w/h/right/bottom.
Numeric roundoff up to `0.00001` is clamped to the window; substantial overflow,
zero/negative sizes, and non-finite values are rejected. A valid grid cannot hide
an invalid name, stat, marker, or other field region.

Grid settings inside each calibration's `grid` object also include
`center_margin` (0.003 of the window dimensions), `edge_tolerance` (0.008), and
`min_visible_fraction` (0.65). A click center must stay inside the viewport with
the safety margin; at least the configured fraction of the inferred card must
overlap the viewport, allowing the small edge tolerance for decorations. These
defaults apply to older grids too. They preserve row/column identities after
scrolling and reject mostly hidden rows or centers over the bottom controls.
Scrolling is directed at the center of the calibrated viewport.

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

## Equipped Tarot in a character scan

Use the normal character workflow:

```powershell
python soc_scraper.py calibrate-roster
python soc_scraper.py roster --count 10
```

For each character, the scanner reads weapon/trinket and skill panels, clicks the
equipped Tarot icon, reads its Tarot-specific popup, and dismisses the final
overlay before returning to the Character List. Results are saved incrementally
under `characters[].tarot`; `characters[].equipped.tarot` contains the name.
`equipped_by` uses the current character's identified name, marked as
`character_context` in diagnostics. Same-named Tarots on different characters
remain attached to their respective character records.

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
warnings/nulls where possible. Unknown pages, lost focus, and ambiguous scroll overlap
stop the scan without blind recovery clicks. Unchanged-but-readable inventory
panes follow the duplicate-copy policy described above and retain a warning.

Two verified no-movement scrolls mark `end_of_list`. Fewer entries than requested
can indicate an exhausted list, unrecognized entries, or a navigation problem;
inspect status and errors. `complete` means the requested count was reached,
**not** that the whole account was scanned.

Restart from the top; there is no automatic mid-list resume. Roster runs update
matching characters and retain others from earlier runs, including their equipped
Tarot data. Equipment runs replace their inventory snapshot **on the first successful entry**, preventing a rerun
from doubling owned copies. An interrupted run retains its partial snapshot and
status. A run with zero successful items leaves its previous snapshot intact.

## Generated JSON

Both commands update `soc_account.json`. Use the same custom output to
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
      "equipped": {"weapon": "Weapon A", "trinket": "Trinket B", "tarot": "Dream of The Magician"},
      "tarot": {
        "name": "Dream of The Magician",
        "level": 60,
        "equipped_by": "SP Inanna",
        "stats": {"p_atk": 230, "m_atk": 230, "p_def": 86, "m_def": 86, "max_hp": 551},
        "details": ["Increases [P.ATK] by 77", "Increases [P.ATK] by 10.0%", "Increases [P.ATK] by 8.0%"],
        "skill": "Increases DMG by 8%. When casting skills, for each additional enemy hit, the DMG is increased by 4%, up to 16%."
      },
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

Tarot records keep the complete visible text that OCR can read, including a fourth
replacement-skill effect when present. The example abbreviates those effects.
`ocr.main_stats`, `ocr.stats`, `ocr.details`, and `ocr.skill` retain raw text and
confidence; missing stats/skill are null and unreadable effect blocks yield
an empty details array with diagnostics. The data belongs to the character record;
no Tarot inventory snapshot or copy IDs are generated. Previously saved extra
JSON sections are preserved rather than deleted.

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
| Calibration missing | Run `calibrate-roster` for character builds, including equipped Tarot, or `calibrate-equipment` for Gear inventory. |
| Details mistaken for list | Select distinct Character List/details anchors. Gear inventory uses a stable heading/count label and a corresponding detail-pane marker. |
| Invalid rectangle | Read the named field and its x/y/w/h/right/bottom values. Tiny rounding overshoot is clamped; reselect genuinely misplaced regions. Legacy selected-border rectangles are ignored. |
| No fully visible first-row slots / invalid grid | Restart the updated script. First-row validation now uses safe centers and sufficient visible area rather than full-card containment. Read the printed viewport, columns, first-row Y, candidate centers, visibility fractions, and rejection reasons. Recalibrate misplaced centers/pitch; exclude the header and bottom controls from the viewport. |
| Generic overlay-dismiss point missing | Run `calibrate-roster` to select one safe dead area. Per-panel close targets are no longer used. |
| Identical copies / unchanged detail pane | No border matching is used. Check `ocr.panel_update`: a readable stable pane with no observable update is retained after `detail_timeout` with a warning. Check focus and click targets if repeated names look wrong. |
| Type unknown/wrong | Select actual equipment category, not engraving; add missing category-icon samples. |
| Stars/engravings/skills missing | Calibrate star slots and the shared gear/skill panels. Unknown icons stay null. Use a gear marker common to both weapon and trinket. |
| Tarot stats/details missing | Run `calibrate-roster` and select the equipped Tarot icon and popup regions on character details. Include labels AND numbers in its main-stat table, and visible effect/skill text in separate regions. |
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
the interactive wizard; `soc_panels.py` resolves shared panel geometry and parses
multiline Tarot/engraving text.

## Verification and current limits

```powershell
python -m unittest -v
```

Tests cover capture ownership/timeouts, title selection, prompts/retries, counts,
duplicate copies across scroll overlap, unique characters, failed entries, Ctrl+C,
atomic saving, reconciliation, classification, stars, and ambiguous scroll rejection.
Stat tests also cover direct displayed totals, per-stat OCR failure/low confidence,
strict parsing, calibration reuse, and nested stats/diagnostics in both output formats.
Navigation regressions cover the five-column reference geometry with decorative
overhang, unsafe/mostly hidden slots, stable row IDs after scrolling, direct sibling
panel transitions, one final dismiss, stale skill text, and persistent inventory panes.
Additional tests cover named rectangle failures and epsilon clamping, calibration
without selected borders, shared gear/skill/Tarot regions, panel-update polling and
timeouts, equipped Tarot parsing/ownership, and interrupted incremental character saving.

HWND capture was previously verified with VS Code overlapping the game. The
current Equipment/Gear page was inspected live and recognized by the calibration
bootstrap. Character List detection was also verified live after fixing the
merged-word/header OCR rejection. The new multi-entry workflows still need calibration and an end-to-end
run on your layout; synthetic tests do not replace that check. Start with a small
count, compare JSON against the UI, then increase the count.

## Example AI use

Give the combined `soc_account.json` to your AI assistant:

> Recommend the best builds and strongest teams using only these characters and
> equipment and equipped Tarot Whispers. Respect equipment copy IDs; never assign one physical item to two
> characters simultaneously. Show gear conflicts, suggested engraving pairs, and
> equipped-skill changes. Treat nulls as unknown, check scan coverage and OCR
> warnings, and ask about missing ownership/unlocked skills instead of guessing.
> Include Tarot rolls/skill effects that were read, but do not infer missing effects
> or resource costs.
