"""Ordinary Windows input and visual grid tracking. No game-process access."""

import ctypes
from ctypes import wintypes
import math
import time

import cv2
import numpy as np
import win32con
import win32gui
import win32api


class NavigationError(RuntimeError):
    pass


def normalized_rectangle(region, field="rectangle", epsilon=1e-5):
    """Clamp numeric roundoff only; report the exact invalid configuration path."""
    try:
        x, y, w, h = (float(region[k]) for k in ("x", "y", "w", "h"))
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError(f"Invalid rectangle: {field}; expected numeric x, y, w, h; got {region!r}") from error
    right, bottom = x + w, y + h
    diagnostic = (f"Invalid rectangle: {field}\n"
                  f"x={x:.9f}, y={y:.9f}, w={w:.9f}, h={h:.9f}, "
                  f"right={right:.9f}, bottom={bottom:.9f}")
    if (not all(math.isfinite(v) for v in (x, y, w, h, right, bottom))
            or w <= 0 or h <= 0 or x < -epsilon or y < -epsilon
            or right > 1 + epsilon or bottom > 1 + epsilon):
        raise ValueError(diagnostic + "\nRectangle outside the window or nonpositive size.")
    left, top, right, bottom = (max(0., min(1., v)) for v in (x, y, right, bottom))
    if right <= left or bottom <= top:
        raise ValueError(diagnostic + "\nRectangle is empty after clamping.")
    return {**region, "x": left, "y": top, "w": right - left, "h": bottom - top}


def crop(frame, region):
    height, width = frame.shape[:2]
    checked = normalized_rectangle(region)
    x, y, w, h = (checked[k] for k in ("x", "y", "w", "h"))
    image = frame[round(y * height):round((y + h) * height),
                  round(x * width):round((x + w) * width)]
    if image.size == 0:
        raise ValueError("Calibration rectangle is empty at this resolution.")
    return image


def center(region):
    return {"x": region["x"] + region["w"] / 2,
            "y": region["y"] + region["h"] / 2}


def signature(image):
    return cv2.resize(cv2.cvtColor(image, cv2.COLOR_BGR2GRAY), (160, 100))


def difference(first, second):
    return float(np.mean(np.abs(signature(first).astype(float) - signature(second))))


class _MouseInput(ctypes.Structure):
    _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG),
                ("mouseData", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


class _InputUnion(ctypes.Union):
    # MOUSEINPUT is the largest member of Win32's INPUT union.
    _fields_ = [("mi", _MouseInput)]


class _Input(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("payload", _InputUnion)]


def _mouse_event(flags, data=0):
    event = _Input(type=0, payload=_InputUnion(mi=_MouseInput(
        0, 0, data & 0xffffffff, flags, 0, 0)))
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(_Input), ctypes.c_int]
    user32.SendInput.restype = wintypes.UINT
    if user32.SendInput(1, ctypes.byref(event), ctypes.sizeof(event)) != 1:
        raise NavigationError("Windows refused mouse input. Check game/terminal privilege levels.")


class WindowInput:
    def __init__(self, hwnd):
        self.hwnd = hwnd

    def foreground(self, timeout=8.0):
        """
        Ensure Sword of Convallaria is the foreground window.

        Attempt automatic activation first. If Windows refuses the request,
        wait for the user to click the game manually.
        """

        if not win32gui.IsWindow(self.hwnd):
            raise NavigationError("Game window closed.")

        if win32gui.IsIconic(self.hwnd):
            try:
                win32gui.ShowWindow(self.hwnd, win32con.SW_RESTORE)
                time.sleep(0.25)
            except Exception as error:
                raise NavigationError(
                    "Could not restore the Sword of Convallaria window."
                ) from error

        if win32gui.GetForegroundWindow() == self.hwnd:
            return

        try:
            win32gui.ShowWindow(self.hwnd, win32con.SW_SHOW)
            win32gui.BringWindowToTop(self.hwnd)
            win32gui.SetForegroundWindow(self.hwnd)
        except Exception:
            pass

        automatic_deadline = time.monotonic() + 0.75

        while time.monotonic() < automatic_deadline:
            if win32gui.GetForegroundWindow() == self.hwnd:
                time.sleep(0.10)
                return

            time.sleep(0.05)

        print()
        print("Windows did not automatically focus Sword of Convallaria.")
        print("Click the Sword of Convallaria window now.")
        print(f"Waiting up to {timeout:.0f} seconds...")

        manual_deadline = time.monotonic() + timeout

        while time.monotonic() < manual_deadline:
            if not win32gui.IsWindow(self.hwnd):
                raise NavigationError("Game window closed.")

            if win32gui.GetForegroundWindow() == self.hwnd:
                print("Sword of Convallaria focused. Continuing...")
                time.sleep(0.15)
                return

            time.sleep(0.10)

        raise NavigationError(
            "Sword of Convallaria was not focused in time. "
            "Start the scan again and click the game window when prompted."
        )

    def bounds(self):
        rect = wintypes.RECT()
        dwm = ctypes.WinDLL("dwmapi")

        dwm.DwmGetWindowAttribute.argtypes = [
            wintypes.HWND,
            wintypes.DWORD,
            ctypes.c_void_p,
            wintypes.DWORD,
        ]

        if (
            dwm.DwmGetWindowAttribute(
                self.hwnd,
                9,
                ctypes.byref(rect),
                ctypes.sizeof(rect),
            )
            != 0
        ):
            raise NavigationError(
                "Could not map WGC pixels to the visible window bounds."
            )

        return rect.left, rect.top, rect.right, rect.bottom

    def move(self, point):
        if win32gui.GetForegroundWindow() != self.hwnd:
            raise NavigationError(
                "Game lost focus. No input was sent; collected data is saved."
            )

        if not all(0 <= point[k] <= 1 for k in ("x", "y")):
            raise NavigationError(
                "Click point is outside the calibrated window."
            )

        left, top, right, bottom = self.bounds()

        position = (
            round(left + point["x"] * (right - left)),
            round(top + point["y"] * (bottom - top)),
        )

        target = win32gui.WindowFromPoint(position)

        if target != self.hwnd and not win32gui.IsChild(self.hwnd, target):
            raise NavigationError(
                "Another window covers the input target. No click was sent."
            )

        win32api.SetCursorPos(position)

    def click(self, point):
        self.move(point)

        _mouse_event(win32con.MOUSEEVENTF_LEFTDOWN)

        try:
            time.sleep(0.04)
        finally:
            _mouse_event(win32con.MOUSEEVENTF_LEFTUP)

    def scroll(self, point, delta=-120):
        self.move(point)
        _mouse_event(win32con.MOUSEEVENTF_WHEEL, delta)

def slot_geometry(grid, row, column, offset=0.0):
    """Check click safety and visible area, allowing decorative card overhang."""
    viewport = grid["viewport"]
    first = center(grid["first_card"])
    card = grid["first_card"]
    point = {"x": first["x"] + column * grid["column_pitch"],
             "y": first["y"] + row * grid["row_pitch"] - offset}
    margin = grid.get("center_margin", .003)
    tolerance = grid.get("edge_tolerance", .008)
    minimum = grid.get("min_visible_fraction", .65)
    rectangle = {"x": point["x"] - card["w"] / 2, "y": point["y"] - card["h"] / 2,
                 "w": card["w"], "h": card["h"]}
    overlap_width = max(0., min(rectangle["x"] + card["w"], viewport["x"] + viewport["w"] + tolerance, 1.)
                        - max(rectangle["x"], viewport["x"] - tolerance, 0.))
    overlap_height = max(0., min(rectangle["y"] + card["h"], viewport["y"] + viewport["h"] + tolerance, 1.)
                         - max(rectangle["y"], viewport["y"] - tolerance, 0.))
    fraction = overlap_width * overlap_height / (card["w"] * card["h"])
    inside = (viewport["x"] + margin <= point["x"] <= viewport["x"] + viewport["w"] - margin
              and viewport["y"] + margin <= point["y"] <= viewport["y"] + viewport["h"] - margin)
    reason = ("center outside safe viewport" if not inside else
              f"visible card fraction {fraction:.3f} below {minimum:.3f}" if fraction < minimum else "ok")
    return {"point": point, "card": rectangle, "visible_fraction": fraction,
            "usable": inside and fraction >= minimum, "reason": reason}


def visible_slots(grid, offset):
    """Stable row/column IDs; decorations need not fit perfectly in the viewport."""
    viewport = grid["viewport"]
    first = center(grid["first_card"])
    pitch = grid["row_pitch"]
    first_row = max(0, math.floor((viewport["y"] + offset - first["y"]) / pitch))
    last_row = math.ceil((viewport["y"] + viewport["h"] + offset - first["y"]) / pitch)
    for row in range(first_row, last_row + 1):
        for column in range(grid["columns"]):
            slot = slot_geometry(grid, row, column, offset)
            if slot["usable"]:
                yield (row, column), slot["point"]


def grid_diagnostics(grid, label="Roster", offset=0.0):
    viewport = grid["viewport"]
    lines = [f"{label} viewport: " + ", ".join(f"{axis}={viewport[axis]:.5f}" for axis in ("x", "y", "w", "h")),
             f"Detected/inferred columns: {grid['columns']}",
             f"First-row Y center: {center(grid['first_card'])['y'] - offset:.5f}",
             f"Row pitch: {grid['row_pitch']:.5f}; column pitch: {grid['column_pitch']:.5f}",
             "Candidate first-row slot centers (normalized window coordinates):"]
    for column in range(grid["columns"]):
        slot = slot_geometry(grid, 0, column, offset)
        point = slot["point"]
        lines.append(f"  slot {column}: ({point['x']:.5f}, {point['y']:.5f}) "
                     f"usable={'yes' if slot['usable'] else 'no'} visible={slot['visible_fraction']:.3f}; {slot['reason']}")
    return "\n".join(lines)


def validate_grid(grid):
    if (not isinstance(grid["columns"], int) or not 1 <= grid["columns"] <= 100
            or not 0 < grid["row_pitch"] < grid["viewport"]["h"]
            or grid["column_pitch"] < 0 or (grid["columns"] > 1 and grid["column_pitch"] == 0)):
        raise ValueError("Invalid columns or row/column pitch")
    if (not 0 <= grid.get("edge_tolerance", .008) <= .02
            or not 0 <= grid.get("center_margin", .003) <= .02
            or not .5 <= grid.get("min_visible_fraction", .65) <= 1):
        raise ValueError("Invalid grid visibility tolerance")
    rejected = [str(column) for column in range(grid["columns"])
                if not slot_geometry(grid, 0, column)["usable"]]
    if rejected:
        raise ValueError("First-row slots " + ", ".join(rejected)
                         + " have unsafe centers or insufficient visible area; see geometry diagnostics")


def estimate_scroll_shift(before, after, viewport, row_pitch):
    """Measure vertical list translation; refuse ambiguous identical row matches.

    Returns normalized window displacement, never derives identity from item names.
    Uses two textured horizontal bands so selection glows need not match perfectly.
    """
    old, new = crop(before, viewport), crop(after, viewport)
    if old.shape != new.shape:
        raise NavigationError("Window resized during scanning. Recalibrate or restore its size.")
    if difference(old, new) < 1.2:
        return 0.0
    old = cv2.cvtColor(old, cv2.COLOR_BGR2GRAY)
    new = cv2.cvtColor(new, cv2.COLOR_BGR2GRAY)
    height, width = old.shape
    band_height = max(12, height // 5)
    shifts = []
    for fraction in (0.50, 0.72):
        start = int(height * fraction)
        band = old[start:start + band_height, width // 12:width - width // 12]
        if float(band.std()) < 8:
            continue
        search = new[:, width // 12:width - width // 12]
        scores = cv2.matchTemplate(search, band, cv2.TM_CCOEFF_NORMED).ravel()
        best = int(np.argmax(scores))
        score = float(scores[best])
        other = scores.copy()
        other[max(0, best - 5):best + 6] = -1
        if score >= 0.82 and score - float(other.max()) >= 0.025:
            shifts.append(start - best)
    if not shifts or max(shifts) - min(shifts) > 3:
        raise NavigationError("Could not verify scroll overlap. Progress saved; reduce scroll_delta or recalibrate.")
    shift = float(np.mean(shifts)) / before.shape[0]
    if shift < -0.003 or shift > viewport["h"] - row_pitch:
        raise NavigationError("Unexpected scroll direction or skipped rows. Progress saved.")
    return max(0.0, shift)
