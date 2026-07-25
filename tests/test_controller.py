from __future__ import annotations

import queue
import unittest

import numpy as np

from dictation.audio import AudioStats
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


class FakeASR:
    name = "fake"

    def transcribe(self, audio: np.ndarray, sample_rate: int) -> str:
        self.audio_samples = audio.size
        self.sample_rate = sample_rate
        return "  Keep   XGBoost. "


class FakeInjector:
    def __init__(self) -> None:
        self.text: str | None = None
        self.window: int | None = None

    def inject(self, text: str, expected_window: int | None) -> InjectionResult:
        self.text = text
        self.window = expected_window
        return InjectionResult(True)


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

    def show_recording(self, target_window: int | None) -> None:
        self.state = "recording"
        self.window = target_window

    def show_processing(self) -> None:
        self.state = "processing"

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


if __name__ == "__main__":
    unittest.main()
