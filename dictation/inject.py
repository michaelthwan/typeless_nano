from __future__ import annotations

import ctypes
from ctypes import wintypes

import win32clipboard
import win32con
import win32gui

from dictation.platform_common import InjectionResult

INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004

ULONG_PTR = wintypes.WPARAM


class KEYBDINPUT(ctypes.Structure):
    _fields_ = (
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    )


class MOUSEINPUT(ctypes.Structure):
    _fields_ = (
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    )


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = (
        ("uMsg", wintypes.DWORD),
        ("wParamL", wintypes.WORD),
        ("wParamH", wintypes.WORD),
    )


class INPUT_UNION(ctypes.Union):
    _fields_ = (
        ("mi", MOUSEINPUT),
        ("ki", KEYBDINPUT),
        ("hi", HARDWAREINPUT),
    )


class INPUT(ctypes.Structure):
    _anonymous_ = ("union",)
    _fields_ = (("type", wintypes.DWORD), ("union", INPUT_UNION))


_USER32 = ctypes.WinDLL("user32", use_last_error=True)
_USER32.SendInput.argtypes = (
    wintypes.UINT,
    ctypes.POINTER(INPUT),
    ctypes.c_int,
)
_USER32.SendInput.restype = wintypes.UINT


class TextInjector:
    def __init__(self, *, clipboard_fallback: bool = True) -> None:
        self.clipboard_fallback = clipboard_fallback

    def inject(self, text: str, expected_window: int | None) -> InjectionResult:
        if not text:
            return InjectionResult(False, reason="empty_transcript")
        if not expected_window or not win32gui.IsWindow(expected_window):
            return InjectionResult(False, reason="target_window_invalid")
        if win32gui.GetForegroundWindow() != expected_window:
            return InjectionResult(False, reason="foreground_window_changed")

        inserted = send_unicode_text(text)
        if inserted:
            return InjectionResult(True)
        if self.clipboard_fallback and copy_to_clipboard(text):
            return InjectionResult(
                False, copied_to_clipboard=True, reason="sendinput_failed"
            )
        return InjectionResult(False, reason="sendinput_failed")


def _utf16_code_units(text: str) -> list[int]:
    data = text.encode("utf-16-le")
    return [int.from_bytes(data[index : index + 2], "little") for index in range(0, len(data), 2)]


def send_unicode_text(text: str) -> bool:
    units = _utf16_code_units(text)
    if not units:
        return True

    input_count = len(units) * 2
    inputs = (INPUT * input_count)()
    for index, unit in enumerate(units):
        inputs[index * 2] = INPUT(
            type=INPUT_KEYBOARD,
            ki=KEYBDINPUT(0, unit, KEYEVENTF_UNICODE, 0, 0),
        )
        inputs[index * 2 + 1] = INPUT(
            type=INPUT_KEYBOARD,
            ki=KEYBDINPUT(0, unit, KEYEVENTF_UNICODE | KEYEVENTF_KEYUP, 0, 0),
        )

    sent = _USER32.SendInput(input_count, inputs, ctypes.sizeof(INPUT))
    return int(sent) == input_count


def copy_to_clipboard(text: str) -> bool:
    try:
        win32clipboard.OpenClipboard()
        try:
            win32clipboard.EmptyClipboard()
            win32clipboard.SetClipboardData(win32con.CF_UNICODETEXT, text)
        finally:
            win32clipboard.CloseClipboard()
        return True
    except Exception:
        return False
