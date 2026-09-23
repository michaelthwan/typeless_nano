from __future__ import annotations

import queue
import unittest
from types import SimpleNamespace

import numpy as np

from dictation.cues import JOIN_NOTES, render_cue_wav, wav_to_samples
from dictation.events import EventKind
from dictation.macos import hotkey as mac_hotkey
from dictation.macos import inject as mac_inject
from dictation.macos import overlay as mac_overlay
from dictation.macos import quartz
from dictation.macos.hotkey import (
    KVK_RIGHT_OPTION,
    NX_DEVICERALTKEYMASK,
    MacHotkeyTap,
    right_option_transition,
)
from dictation.macos.inject import utf16_chunks

KEYCODE_FIELD = quartz.CG_KEYBOARD_EVENT_KEYCODE
PID_FIELD = quartz.CG_EVENT_SOURCE_UNIX_PROCESS_ID
LEFT_OPTION = 58
ALTERNATE_MASK = 0x00080000
LEFT_ALT_DEVICE_BIT = 0x00000020


class FakeQuartz:
    """Stands in for the CoreGraphics bindings; events are dicts."""

    def __init__(self) -> None:
        self.enabled: list[bool] = []

    def namespace(self) -> SimpleNamespace:
        return SimpleNamespace(
            CGEventGetIntegerValueField=lambda event, field: event[field],
            CGEventGetFlags=lambda event: event["flags"],
            CGEventTapEnable=lambda tap, on: self.enabled.append(on),
        )


class RightOptionDecodeTests(unittest.TestCase):
    def test_right_option_down_and_up(self) -> None:
        down = ALTERNATE_MASK | NX_DEVICERALTKEYMASK
        self.assertIs(right_option_transition(KVK_RIGHT_OPTION, down), True)
        self.assertIs(right_option_transition(KVK_RIGHT_OPTION, 0), False)

    def test_other_keys_are_ignored(self) -> None:
        flags = ALTERNATE_MASK | LEFT_ALT_DEVICE_BIT
        self.assertIsNone(right_option_transition(LEFT_OPTION, flags))
        self.assertIsNone(right_option_transition(0, 0))


class MacTapCallbackTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fake = FakeQuartz()
        self._saved = (quartz.load, quartz.frontmost_pid)
        mac_hotkey.quartz.load = self.fake.namespace
        mac_hotkey.quartz.frontmost_pid = lambda: 4242
        self.events: queue.Queue = queue.Queue()
        self.tap = MacHotkeyTap(self.events)

    def tearDown(self) -> None:
        quartz.load, quartz.frontmost_pid = self._saved

    def _flags_event(self, keycode: int, flags: int, pid: int = 1) -> dict:
        return {KEYCODE_FIELD: keycode, PID_FIELD: pid, "flags": flags}

    def _call(self, event: dict, event_type: int = quartz.CG_EVENT_FLAGS_CHANGED):
        return self.tap._tap_callback(0, event_type, event, 0)

    def test_toggle_start_then_stop_and_swallow_key(self) -> None:
        down = self._flags_event(KVK_RIGHT_OPTION, ALTERNATE_MASK | NX_DEVICERALTKEYMASK)
        up = self._flags_event(KVK_RIGHT_OPTION, 0)

        self.assertIsNone(self._call(down))
        self.assertIsNone(self._call(up))
        self.assertIsNone(self._call(down))
        self.assertIsNone(self._call(up))

        start = self.events.get_nowait()
        stop = self.events.get_nowait()
        self.assertIs(start.kind, EventKind.START_RECORDING)
        self.assertEqual(start.foreground_window, 4242)
        self.assertEqual(start.source, "right_option")
        self.assertIs(stop.kind, EventKind.STOP_RECORDING)
        self.assertIsNone(stop.foreground_window)
        self.assertTrue(self.events.empty())

    def test_autorepeat_down_does_not_toggle_twice(self) -> None:
        down = self._flags_event(KVK_RIGHT_OPTION, NX_DEVICERALTKEYMASK)
        self._call(down)
        self._call(down)
        self.assertEqual(self.events.qsize(), 1)

    def test_left_option_passes_through(self) -> None:
        event = self._flags_event(LEFT_OPTION, ALTERNATE_MASK | LEFT_ALT_DEVICE_BIT)
        self.assertIs(self._call(event), event)
        self.assertTrue(self.events.empty())

    def test_own_process_events_pass_through(self) -> None:
        event = self._flags_event(
            KVK_RIGHT_OPTION, NX_DEVICERALTKEYMASK, pid=self.tap._own_pid
        )
        self.assertIs(self._call(event), event)
        self.assertTrue(self.events.empty())

    def test_disabled_tap_is_reenabled(self) -> None:
        self.tap._tap = 99
        event = {"flags": 0}
        result = self._call(event, quartz.CG_EVENT_TAP_DISABLED_BY_TIMEOUT)
        self.assertIs(result, event)
        self.assertEqual(self.fake.enabled, [True])


    def test_reenable_releases_held_key_but_keeps_recording(self) -> None:
        down = self._flags_event(KVK_RIGHT_OPTION, NX_DEVICERALTKEYMASK)
        self._call(down)
        # Key-up lost while the tap was disabled.
        self._call({"flags": 0}, quartz.CG_EVENT_TAP_DISABLED_BY_TIMEOUT)
        self._call(down)
        kinds = [self.events.get_nowait().kind for _ in range(2)]
        self.assertEqual(kinds, [EventKind.START_RECORDING, EventKind.STOP_RECORDING])


class MacInjectorTests(unittest.TestCase):
    def setUp(self) -> None:
        self._saved = (
            quartz.frontmost_pid,
            quartz.secure_input_enabled,
            mac_inject.send_unicode_text,
            mac_inject.copy_to_clipboard,
        )
        self.secure = False
        self.typed: list[str] = []
        self.copied: list[str] = []
        quartz.frontmost_pid = lambda: 50
        quartz.secure_input_enabled = lambda: self.secure
        mac_inject.send_unicode_text = lambda text: self.typed.append(text) or True
        mac_inject.copy_to_clipboard = lambda text: self.copied.append(text) or True

    def tearDown(self) -> None:
        (
            quartz.frontmost_pid,
            quartz.secure_input_enabled,
            mac_inject.send_unicode_text,
            mac_inject.copy_to_clipboard,
        ) = self._saved

    def test_types_into_same_app(self) -> None:
        result = mac_inject.MacTextInjector().inject("hello", 50)
        self.assertTrue(result.injected)
        self.assertEqual(self.typed, ["hello"])

    def test_skips_when_frontmost_app_changed(self) -> None:
        result = mac_inject.MacTextInjector().inject("hello", 51)
        self.assertFalse(result.injected)
        self.assertEqual(result.reason, "foreground_window_changed")
        self.assertEqual(self.typed, [])

    def test_secure_input_falls_back_instead_of_typing(self) -> None:
        self.secure = True
        result = mac_inject.MacTextInjector().inject("hello", 50)
        self.assertFalse(result.injected)
        self.assertTrue(result.copied_to_clipboard)
        self.assertEqual(result.reason, "secure_input_active")
        self.assertEqual(self.typed, [])
        self.assertEqual(self.copied, ["hello"])

    def test_secure_input_without_fallback_reports_failure(self) -> None:
        self.secure = True
        injector = mac_inject.MacTextInjector(clipboard_fallback=False)
        result = injector.inject("hello", 50)
        self.assertFalse(result.injected)
        self.assertFalse(result.copied_to_clipboard)
        self.assertEqual(result.reason, "secure_input_active")


class Utf16ChunkTests(unittest.TestCase):
    def test_chunks_respect_size(self) -> None:
        chunks = utf16_chunks("a" * 45, size=20)
        self.assertEqual([len(chunk) for chunk in chunks], [20, 20, 5])

    def test_surrogate_pair_is_never_split(self) -> None:
        text = "a" * 19 + "\U0001F600" + "b"
        chunks = utf16_chunks(text, size=20)
        self.assertEqual(len(chunks[0]), 21)
        joined = b"".join(
            unit.to_bytes(2, "little") for chunk in chunks for unit in chunk
        )
        self.assertEqual(joined.decode("utf-16-le"), text)

    def test_empty_text(self) -> None:
        self.assertEqual(utf16_chunks(""), [])


class CueDecodeTests(unittest.TestCase):
    def test_wav_round_trip_for_sounddevice(self) -> None:
        samples, rate = wav_to_samples(render_cue_wav(JOIN_NOTES))
        self.assertEqual(rate, 44_100)
        self.assertEqual(samples.dtype, np.float32)
        self.assertGreater(samples.size, 0)
        self.assertLessEqual(float(np.max(np.abs(samples))), 1.0)
        self.assertGreater(float(np.max(np.abs(samples))), 0.1)


class ConsoleOverlayTests(unittest.TestCase):
    def test_rescue_goes_to_clipboard(self) -> None:
        copied: list[str] = []
        saved = mac_overlay.copy_to_clipboard
        mac_overlay.copy_to_clipboard = lambda text: copied.append(text) or True
        try:
            mac_overlay.ConsoleOverlay().show_rescue("keep this text", 7)
        finally:
            mac_overlay.copy_to_clipboard = saved
        self.assertEqual(copied, ["keep this text"])


if __name__ == "__main__":
    unittest.main()
