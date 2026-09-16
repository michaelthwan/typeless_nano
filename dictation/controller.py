from __future__ import annotations

import logging
import queue
import time
from concurrent.futures import Future, ThreadPoolExecutor
from enum import Enum, auto
from typing import Protocol

import numpy as np

from dictation.audio import AudioError, AudioRecorder
from dictation.cleanup import clean_transcript
from dictation.config import AppConfig
from dictation.events import AppEvent, EventKind

LOGGER = logging.getLogger("dictation.controller")


class ControllerState(Enum):
    IDLE = auto()
    RECORDING = auto()
    TRANSCRIBING = auto()
    STOPPED = auto()


class StateMachine:
    def __init__(self) -> None:
        self.state = ControllerState.IDLE

    def start_recording(self) -> bool:
        if self.state is not ControllerState.IDLE:
            return False
        self.state = ControllerState.RECORDING
        return True

    def start_transcribing(self) -> bool:
        if self.state is not ControllerState.RECORDING:
            return False
        self.state = ControllerState.TRANSCRIBING
        return True

    def finish_transcribing(self) -> bool:
        if self.state is not ControllerState.TRANSCRIBING:
            return False
        self.state = ControllerState.IDLE
        return True

    def cancel_recording(self) -> bool:
        if self.state is not ControllerState.RECORDING:
            return False
        self.state = ControllerState.IDLE
        return True

    def stop(self) -> None:
        self.state = ControllerState.STOPPED


class ASRBackend(Protocol):
    name: str

    def transcribe(self, audio: np.ndarray, sample_rate: int) -> str: ...


class Injector(Protocol):
    def inject(self, text: str, expected_window: int | None): ...


class HotkeyControl(Protocol):
    def reset_pressed(self) -> None: ...

    def set_recording(self, active: bool) -> None: ...


class CueControl(Protocol):
    def play_start(self) -> None: ...

    def play_stop(self) -> None: ...

    def play_cancel(self) -> None: ...


class OverlayControl(Protocol):
    def show_recording(self, target_window: int | None) -> None: ...

    def show_processing(self) -> None: ...

    def show_notice(
        self, text: str = "", target_window: int | None = None
    ) -> None: ...

    def show_rescue(self, text: str, target_window: int | None = None) -> None: ...

    def hide(self) -> None: ...

    def close(self) -> None: ...


class DictationController:
    def __init__(
        self,
        *,
        config: AppConfig,
        events: queue.Queue[AppEvent],
        recorder: AudioRecorder,
        asr: ASRBackend,
        injector: Injector,
        hotkey: HotkeyControl,
        overlay: OverlayControl,
        cues: CueControl,
    ) -> None:
        self.config = config
        self.events = events
        self.recorder = recorder
        self.asr = asr
        self.injector = injector
        self.hotkey = hotkey
        self.overlay = overlay
        self.cues = cues
        self.machine = StateMachine()
        self._recording_started_at = 0.0
        self._target_window: int | None = None
        self._executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="asr-worker"
        )
        self._future: Future[None] | None = None
        self._closed = False

    @staticmethod
    def create_event_queue() -> queue.Queue[AppEvent]:
        return queue.Queue()

    def run(self) -> None:
        while self.machine.state is not ControllerState.STOPPED:
            try:
                event = self.events.get(timeout=0.1)
            except queue.Empty:
                self._check_watchdog()
                continue
            self._handle(event)
            self._check_watchdog()

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self.machine.state is ControllerState.RECORDING:
            self._cancel_recording("shutdown")
        self.machine.stop()
        self.recorder.close()
        self._executor.shutdown(wait=True, cancel_futures=True)
        self.overlay.hide()
        self.overlay.close()

    def _handle(self, event: AppEvent) -> None:
        if event.kind is EventKind.START_RECORDING:
            self._start_recording(event)
        elif event.kind is EventKind.STOP_RECORDING:
            self._stop_recording(event.source)
        elif event.kind is EventKind.CANCEL_RECORDING:
            self._cancel_recording(event.source)
        elif event.kind is EventKind.ASR_FINISHED:
            if self.machine.finish_transcribing():
                self.overlay.hide()
                if event.error_code == "empty_transcript":
                    self.overlay.show_notice()
                LOGGER.info(
                    "state=idle event=asr_finished error=%s",
                    event.error_code or "none",
                )
        elif event.kind is EventKind.SHUTDOWN:
            self.machine.stop()

    def _start_recording(self, event: AppEvent) -> None:
        if not self.machine.start_recording():
            self.hotkey.set_recording(False)
            LOGGER.info("recording_ignored reason=busy")
            return
        try:
            self.recorder.start()
        except Exception as exc:
            self.machine.cancel_recording()
            self.hotkey.set_recording(False)
            LOGGER.error("recording_start_failed error=%s", type(exc).__name__)
            return
        self._target_window = event.foreground_window
        self.overlay.show_recording(event.foreground_window)
        self.cues.play_start()
        self.recorder.discard_buffered_audio()
        self._recording_started_at = time.monotonic()
        LOGGER.info("state=recording source=%s", event.source)

    def _stop_recording(self, source: str) -> None:
        if self.machine.state is not ControllerState.RECORDING:
            self.hotkey.set_recording(False)
            return
        self.hotkey.set_recording(False)
        self.overlay.show_processing()
        try:
            try:
                result = self.recorder.stop()
            finally:
                self.cues.play_stop()
        except AudioError as exc:
            self.machine.cancel_recording()
            self._target_window = None
            self.overlay.hide()
            self.overlay.show_notice()
            LOGGER.info("utterance_rejected reason=%s", str(exc))
            return
        except Exception as exc:
            self.machine.cancel_recording()
            self._target_window = None
            self.overlay.hide()
            LOGGER.error("recording_stop_failed error=%s", type(exc).__name__)
            return

        if result is None:
            self.machine.cancel_recording()
            self.overlay.hide()
            return
        audio, stats = result
        if not self.machine.start_transcribing():
            return

        target_window = self._target_window
        self._target_window = None
        LOGGER.info(
            "state=transcribing source=%s audio_ms=%d",
            source,
            round(stats.seconds * 1000),
        )
        self._future = self._executor.submit(
            self._transcribe_and_inject, audio, target_window
        )

    def _cancel_recording(self, reason: str) -> None:
        if not self.machine.cancel_recording():
            self.hotkey.set_recording(False)
            self.overlay.hide()
            return
        self.hotkey.set_recording(False)
        try:
            self.recorder.stop(discard=True)
        except Exception:
            pass
        if reason == "overlay_cancel":
            self.cues.play_cancel()
        self._target_window = None
        self.overlay.hide()
        LOGGER.info("state=idle event=recording_cancelled reason=%s", reason)

    def _check_watchdog(self) -> None:
        if self.machine.state is not ControllerState.RECORDING:
            return
        elapsed = time.monotonic() - self._recording_started_at
        if elapsed >= self.config.max_seconds:
            LOGGER.warning("recording_timeout seconds=%d", int(elapsed))
            self.hotkey.reset_pressed()
            self._stop_recording("watchdog")

    def _transcribe_and_inject(
        self, audio: np.ndarray, target_window: int | None
    ) -> None:
        started = time.monotonic()
        error_code: str | None = None
        try:
            transcript = self.asr.transcribe(audio, self.config.sample_rate)
            transcript = clean_transcript(
                transcript,
                convert_new_paragraph=self.config.convert_new_paragraph,
            )
            if not transcript:
                error_code = "empty_transcript"
            else:
                result = self.injector.inject(transcript, target_window)
                if result.injected:
                    LOGGER.info(
                        "injection_succeeded latency_ms=%d",
                        round((time.monotonic() - started) * 1000),
                    )
                elif result.copied_to_clipboard:
                    error_code = "copied_to_clipboard"
                    LOGGER.warning("injection_failed fallback=clipboard")
                else:
                    error_code = result.reason
                    LOGGER.warning("injection_skipped reason=%s", result.reason)
                    # The text had nowhere to go. Keep it on screen rather
                    # than dropping it; the panel owns it from here.
                    self.overlay.show_rescue(transcript, target_window)
        except Exception as exc:
            error_code = type(exc).__name__
            LOGGER.error("transcription_failed error=%s", error_code)
        finally:
            # Audio and transcript references are intentionally not retained.
            self.events.put(
                AppEvent(EventKind.ASR_FINISHED, error_code=error_code)
            )
