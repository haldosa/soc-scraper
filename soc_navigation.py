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


def crop(frame, region):
    height, width = frame.shape[:2]
    x, y, w, h = (region[k] for k in ("x", "y", "w", "h"))
    if not (0 <= x < 1 and 0 <= y < 1 and w > 0 and h > 0
            and x + w <= 1.001 and y + h <= 1.001):
        raise ValueError("Invalid normalized calibration rectangle.")
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

    def foreground(self):
        if not win32gui.IsWindow(self.hwnd):
            raise NavigationError("Game window closed.")
        if win32gui.IsIconic(self.hwnd):
            raise NavigationError("Restore the game before starting automation.")
        try:
            win32gui.SetForegroundWindow(self.hwnd)
        except Exception as error:
            raise NavigationError("Could not foreground the game. Select it and retry.") from error
        if win32gui.GetForegroundWindow() != self.hwnd:
            raise NavigationError("Windows did not foreground the game. Select it and retry.")

    def bounds(self):
        # WGC excludes invisible resize borders. GetWindowRect includes them.
        rect = wintypes.RECT()
        dwm = ctypes.WinDLL("dwmapi")
        dwm.DwmGetWindowAttribute.argtypes = [wintypes.HWND, wintypes.DWORD,
                                            ctypes.c_void_p, wintypes.DWORD]
        if dwm.DwmGetWindowAttribute(self.hwnd, 9, ctypes.byref(rect), ctypes.sizeof(rect)) != 0:
            raise NavigationError("Could not map WGC pixels to the visible window bounds.")
        return rect.left, rect.top, rect.right, rect.bottom

    def move(self, point):
        if win32gui.GetForegroundWindow() != self.hwnd:
            raise NavigationError("Game lost focus. No input was sent; collected data is saved.")
        if not all(0 <= point[k] <= 1 for k in ("x", "y")):
            raise NavigationError("Click point is outside the calibrated window.")
        left, top, right, bottom = self.bounds()
        position = (round(left + point["x"] * (right - left)),
                    round(top + point["y"] * (bottom - top)))
        target = win32gui.WindowFromPoint(position)
        if target != self.hwnd and not win32gui.IsChild(self.hwnd, target):
            raise NavigationError("Another window covers the input target. No click was sent.")
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


def visible_slots(grid, offset):
    """Stable (row, column) identities even after partial-row scrolls."""
    viewport = grid["viewport"]
    first = center(grid["first_card"])
    pitch = grid["row_pitch"]
    half_height = grid["first_card"]["h"] / 2
    first_row = max(0, math.ceil((viewport["y"] + half_height + offset - first["y"] - 1e-5) / pitch))
    last_row = math.floor((viewport["y"] + viewport["h"] - half_height + offset - first["y"] + 1e-5) / pitch)
    for row in range(first_row, last_row + 1):
        for column in range(grid["columns"]):
            yield (row, column), {"x": first["x"] + column * grid["column_pitch"],
                                  "y": first["y"] + row * pitch - offset}


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
