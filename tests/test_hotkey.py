from __future__ import annotations

import ctypes
import queue
import unittest

import win32con

from dictation.events import EventKind
from dictation.hotkey import (
    KBDLLHOOKSTRUCT,
    VK_RMENU,
    RightAltToggleState,
    WindowsHotkeyHook,
)


class RightAltToggleTests(unittest.TestCase):
    def test_first_press_starts_and_second_press_stops(self) -> None:
        toggle = RightAltToggleState()
        self.assertEqual(toggle.key_down(), EventKind.START_RECORDING)
        toggle.key_up()
        self.assertEqual(toggle.key_down(), EventKind.STOP_RECORDING)
        toggle.key_up()

    def test_auto_repeat_is_ignored(self) -> None:
        toggle = RightAltToggleState()
        self.assertEqual(toggle.key_down(), EventKind.START_RECORDING)
        self.assertIsNone(toggle.key_down())

    def test_reset_clears_physical_and_recording_state(self) -> None:
        toggle = RightAltToggleState()
        toggle.key_down()
        toggle.reset()
        self.assertEqual(toggle.key_down(), EventKind.START_RECORDING)


class HookFilteringTests(unittest.TestCase):
    def test_f1_is_passed_through_without_an_app_event(self) -> None:
        events = queue.Queue()
        hook = WindowsHotkeyHook(events)
        data = KBDLLHOOKSTRUCT(0x70, 0, 0, 0, 0)
        result = hook._keyboard_callback(  # noqa: SLF001
            0, win32con.WM_KEYDOWN, ctypes.addressof(data)
        )
        self.assertNotEqual(result, 1)
        self.assertTrue(events.empty())

    def test_right_alt_key_up_does_not_stop_recording(self) -> None:
        events = queue.Queue()
        hook = WindowsHotkeyHook(events)
        data = KBDLLHOOKSTRUCT(VK_RMENU, 0, 0, 0, 0)

        self.assertEqual(
            hook._keyboard_callback(  # noqa: SLF001
                0, win32con.WM_SYSKEYDOWN, ctypes.addressof(data)
            ),
            1,
        )
        self.assertEqual(events.get_nowait().kind, EventKind.START_RECORDING)
        hook._keyboard_callback(  # noqa: SLF001
            0, win32con.WM_SYSKEYUP, ctypes.addressof(data)
        )
        self.assertTrue(events.empty())

        hook._keyboard_callback(  # noqa: SLF001
            0, win32con.WM_SYSKEYDOWN, ctypes.addressof(data)
        )
        self.assertEqual(events.get_nowait().kind, EventKind.STOP_RECORDING)


if __name__ == "__main__":
    unittest.main()
