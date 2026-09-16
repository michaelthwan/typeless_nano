from __future__ import annotations

import queue
import unittest

import numpy as np

from dictation.audio import AudioError, AudioStats
from dictation.config import AppConfig
from dictation.controller import ControllerState, DictationController
from dictation.events import AppEvent, EventKind
from dictation.inject import InjectionResult


class FakeRecorder:
    def __init__(self) -> None:
        self.started = False

    def start(self) -> None:
        self.started = True

    def stop(self, *, discard: bool = False):
        self.started = False
        if discard:
            return None
        return (
            np.ones(8_000, dtype=np.float32),
            AudioStats(seconds=0.5, rms=0.1, samples=8_000),
        )

    def discard_buffered_audio(self) -> None:
        pass

    def close(self) -> None:
        self.started = False


class SilentRecorder(FakeRecorder):
    def stop(self, *, discard: bool = False):
        self.started = False
        if discard:
            return None
        raise AudioError("utterance_is_silent")


class FakeASR:
    name = "fake"
    text = "  Keep   XGBoost. "

    def transcribe(self, audio: np.ndarray, sample_rate: int) -> str:
        self.audio_samples = audio.size
        self.sample_rate = sample_rate
        return self.text


class FakeInjector:
    def __init__(self, result: InjectionResult | None = None) -> None:
        self.text: str | None = None
        self.window: int | None = None
        self.result = result or InjectionResult(True)

    def inject(self, text: str, expected_window: int | None) -> InjectionResult:
        self.text = text
        self.window = expected_window
        return self.result


class FakeHotkey:
    def __init__(self) -> None:
        self.recording = False

    def reset_pressed(self) -> None:
        self.recording = False

    def set_recording(self, active: bool) -> None:
        self.recording = active


class FakeOverlay:
    def __init__(self) -> None:
        self.state = "hidden"
        self.window: int | None = None
        self.rescued: str | None = None
        self.rescue_window: int | None = None
        self.notices = 0

    def show_recording(self, target_window: int | None) -> None:
        self.state = "recording"
        self.window = target_window

    def show_processing(self) -> None:
        self.state = "processing"

    def show_notice(self, text: str = "", target_window: int | None = None) -> None:
        self.notices += 1

    def show_rescue(self, text: str, target_window: int | None = None) -> None:
        self.rescued = text
        self.rescue_window = target_window

    def hide(self) -> None:
        self.state = "hidden"

    def close(self) -> None:
        self.state = "closed"


class FakeCues:
    def __init__(self) -> None:
        self.played: list[str] = []

    def play_start(self) -> None:
        self.played.append("start")

    def play_stop(self) -> None:
        self.played.append("stop")

    def play_cancel(self) -> None:
        self.played.append("cancel")


class ControllerIntegrationTests(unittest.TestCase):
    def test_record_transcribe_cleanup_and_inject(self) -> None:
        events: queue.Queue[AppEvent] = queue.Queue()
        recorder = FakeRecorder()
        asr = FakeASR()
        injector = FakeInjector()
        overlay = FakeOverlay()
        cues = FakeCues()
        controller = DictationController(
            config=AppConfig(),
            events=events,
            recorder=recorder,  # type: ignore[arg-type]
            asr=asr,
            injector=injector,
            hotkey=FakeHotkey(),
            overlay=overlay,
            cues=cues,
        )
        try:
            controller._handle(  # noqa: SLF001 - focused state integration test
                AppEvent(
                    EventKind.START_RECORDING,
                    source="right_alt",
                    foreground_window=123,
                )
            )
            controller._handle(  # noqa: SLF001
                AppEvent(EventKind.STOP_RECORDING, source="right_alt")
            )
            finished = events.get(timeout=2)
            controller._handle(finished)  # noqa: SLF001

            self.assertEqual(controller.machine.state, ControllerState.IDLE)
            self.assertEqual(asr.audio_samples, 8_000)
            self.assertEqual(asr.sample_rate, 16_000)
            self.assertEqual(injector.text, "Keep XGBoost.")
            self.assertEqual(injector.window, 123)
            self.assertEqual(overlay.state, "hidden")
            self.assertEqual(cues.played, ["start", "stop"])
        finally:
            controller.close()


class EmptyResultNoticeTests(unittest.TestCase):
    """A dictation that produced no words must say so, not vanish silently."""

    def _controller(self, events, overlay, recorder, transcript="Keep XGBoost."):
        asr = FakeASR()
        asr.text = transcript
        return DictationController(
            config=AppConfig(),
            events=events,
            recorder=recorder,  # type: ignore[arg-type]
            asr=asr,
            injector=FakeInjector(),  # type: ignore[arg-type]
            hotkey=FakeHotkey(),
            overlay=overlay,
            cues=FakeCues(),
        )

    def _record(self, controller, events, drain=True):
        controller._handle(  # noqa: SLF001
            AppEvent(
                EventKind.START_RECORDING,
                source="right_alt",
                foreground_window=123,
            )
        )
        controller._handle(  # noqa: SLF001
            AppEvent(EventKind.STOP_RECORDING, source="right_alt")
        )
        if drain:
            controller._handle(events.get(timeout=2))  # noqa: SLF001

    def test_empty_transcript_shows_a_notice(self) -> None:
        events: queue.Queue[AppEvent] = queue.Queue()
        overlay = FakeOverlay()
        controller = self._controller(events, overlay, FakeRecorder(), transcript="   ")
        try:
            self._record(controller, events)
            self.assertEqual(overlay.notices, 1)
            self.assertEqual(overlay.state, "hidden")
        finally:
            controller.close()

    def test_silent_utterance_shows_a_notice(self) -> None:
        events: queue.Queue[AppEvent] = queue.Queue()
        overlay = FakeOverlay()
        controller = self._controller(events, overlay, SilentRecorder())
        try:
            self._record(controller, events, drain=False)
            self.assertEqual(overlay.notices, 1)
        finally:
            controller.close()

    def test_successful_dictation_shows_no_notice(self) -> None:
        events: queue.Queue[AppEvent] = queue.Queue()
        overlay = FakeOverlay()
        controller = self._controller(events, overlay, FakeRecorder())
        try:
            self._record(controller, events)
            self.assertEqual(overlay.notices, 0)
        finally:
            controller.close()


class RescuePanelTests(unittest.TestCase):
    """A transcript that cannot be typed must survive on screen."""

    def _run(self, result: InjectionResult) -> tuple[FakeOverlay, str | None]:
        events: queue.Queue[AppEvent] = queue.Queue()
        overlay = FakeOverlay()
        injector = FakeInjector(result)
        controller = DictationController(
            config=AppConfig(),
            events=events,
            recorder=FakeRecorder(),  # type: ignore[arg-type]
            asr=FakeASR(),
            injector=injector,  # type: ignore[arg-type]
            hotkey=FakeHotkey(),
            overlay=overlay,
            cues=FakeCues(),
        )
        try:
            controller._handle(  # noqa: SLF001
                AppEvent(
                    EventKind.START_RECORDING,
                    source="right_alt",
                    foreground_window=123,
                )
            )
            controller._handle(  # noqa: SLF001
                AppEvent(EventKind.STOP_RECORDING, source="right_alt")
            )
            finished = events.get(timeout=2)
            controller._handle(finished)  # noqa: SLF001
            return overlay, finished.error_code
        finally:
            controller.close()

    def test_lost_text_is_kept_when_there_is_nowhere_to_type(self) -> None:
        for reason in ("foreground_window_changed", "target_window_invalid"):
            with self.subTest(reason=reason):
                overlay, error_code = self._run(
                    InjectionResult(False, reason=reason)
                )
                self.assertEqual(overlay.rescued, "Keep XGBoost.")
                self.assertEqual(overlay.rescue_window, 123)
                self.assertEqual(error_code, reason)

    def test_successful_injection_shows_no_panel(self) -> None:
        overlay, error_code = self._run(InjectionResult(True))
        self.assertIsNone(overlay.rescued)
        self.assertIsNone(error_code)

    def test_clipboard_fallback_does_not_also_show_a_panel(self) -> None:
        overlay, error_code = self._run(
            InjectionResult(False, copied_to_clipboard=True, reason="sendinput_failed")
        )
        self.assertIsNone(overlay.rescued)
        self.assertEqual(error_code, "copied_to_clipboard")


if __name__ == "__main__":
    unittest.main()
