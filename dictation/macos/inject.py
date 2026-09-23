from __future__ import annotations

import ctypes
import os
import subprocess
import time

from dictation.macos import quartz
from dictation.platform_common import InjectionResult

# CGEventKeyboardSetUnicodeString silently truncates long strings, so text is
# typed in chunks of at most this many UTF-16 code units per key event.
CHUNK_UNITS = 20
CHUNK_PAUSE_SECONDS = 0.002


class MacTextInjector:
    """Types into the app that was frontmost when recording started.

    ``expected_window`` carries that app's PID on macOS (the Windows backend
    uses an HWND in the same slot).
    """

    def __init__(self, *, clipboard_fallback: bool = True) -> None:
        self.clipboard_fallback = clipboard_fallback

    def inject(self, text: str, expected_window: int | None) -> InjectionResult:
        if not text:
            return InjectionResult(False, reason="empty_transcript")
        if not expected_window:
            return InjectionResult(False, reason="target_window_invalid")
        if quartz.frontmost_pid() != expected_window:
            return InjectionResult(False, reason="foreground_window_changed")

        # CGEventPost reports nothing, so the one known silent-drop case is
        # checked up front and routed to the fallback instead of "success".
        if quartz.secure_input_enabled():
            reason = "secure_input_active"
        elif send_unicode_text(text):
            return InjectionResult(True)
        else:
            reason = "cgevent_post_failed"
        if self.clipboard_fallback and copy_to_clipboard(text):
            return InjectionResult(False, copied_to_clipboard=True, reason=reason)
        return InjectionResult(False, reason=reason)


def utf16_chunks(text: str, size: int = CHUNK_UNITS) -> list[list[int]]:
    """Split text into UTF-16 unit chunks without cutting a surrogate pair."""
    data = text.encode("utf-16-le")
    units = [
        int.from_bytes(data[index : index + 2], "little")
        for index in range(0, len(data), 2)
    ]
    chunks: list[list[int]] = []
    current: list[int] = []
    for unit in units:
        is_low_surrogate = 0xDC00 <= unit <= 0xDFFF
        if len(current) >= size and not is_low_surrogate:
            chunks.append(current)
            current = []
        current.append(unit)
    if current:
        chunks.append(current)
    return chunks


def send_unicode_text(text: str) -> bool:
    q = quartz.load()
    for chunk in utf16_chunks(text):
        buffer = (ctypes.c_uint16 * len(chunk))(*chunk)
        for key_down in (True, False):
            event = q.CGEventCreateKeyboardEvent(None, 0, key_down)
            if not event:
                return False
            try:
                # Clear modifiers so a still-held key cannot turn text into
                # shortcuts.
                q.CGEventSetFlags(event, 0)
                q.CGEventKeyboardSetUnicodeString(event, len(chunk), buffer)
                q.CGEventPost(quartz.CG_HID_EVENT_TAP, event)
            finally:
                q.CFRelease(event)
        time.sleep(CHUNK_PAUSE_SECONDS)
    return True


def copy_to_clipboard(text: str) -> bool:
    try:
        subprocess.run(
            ["pbcopy"],
            input=text.encode("utf-8"),
            check=True,
            timeout=5,
            env={**os.environ, "LANG": "en_US.UTF-8"},
        )
        return True
    except (OSError, subprocess.SubprocessError):
        return False
