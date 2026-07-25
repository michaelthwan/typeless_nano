from __future__ import annotations

import queue
import threading
from dataclasses import dataclass
from typing import Any

import numpy as np
import sounddevice as sd

from dictation.config import AppConfig


class AudioError(RuntimeError):
    """Base class for expected microphone and utterance errors."""


class EmptyAudioError(AudioError):
    pass


class AudioTooShortError(AudioError):
    pass


class SilentAudioError(AudioError):
    pass


@dataclass(frozen=True, slots=True)
class AudioStats:
    seconds: float
    rms: float
    samples: int


class AudioRecorder:
    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self._blocks: queue.Queue[np.ndarray] = queue.Queue()
        self._stream: Any | None = None
        self._lock = threading.Lock()
        self._callback_error: str | None = None

    @staticmethod
    def list_devices() -> Any:
        return sd.query_devices()

    def start(self) -> None:
        with self._lock:
            if self._stream is not None:
                raise AudioError("recording_already_active")
            self._clear_blocks()
            self._callback_error = None
            self._stream = sd.InputStream(
                samplerate=self.config.sample_rate,
                channels=self.config.channels,
                dtype="float32",
                blocksize=self.config.block_size,
                device=self.config.microphone,
                callback=self._callback,
            )
            try:
                self._stream.start()
            except Exception:
                self._stream.close()
                self._stream = None
                raise

    def stop(self, *, discard: bool = False) -> tuple[np.ndarray, AudioStats] | None:
        with self._lock:
            stream = self._stream
            self._stream = None

        if stream is None:
            if discard:
                self._clear_blocks()
                return None
            raise EmptyAudioError("no_active_recording")

        try:
            stream.stop()
        finally:
            stream.close()

        if discard:
            self._clear_blocks()
            return None

        if self._callback_error:
            self._clear_blocks()
            raise AudioError(self._callback_error)

        blocks = self._drain_blocks()
        if not blocks:
            raise EmptyAudioError("empty_audio_buffer")
        audio = np.concatenate(blocks, axis=0).reshape(-1).astype(np.float32, copy=False)
        stats = validate_audio(
            audio,
            sample_rate=self.config.sample_rate,
            min_seconds=self.config.min_seconds,
            silence_rms=self.config.silence_rms,
        )
        return audio, stats

    def close(self) -> None:
        try:
            self.stop(discard=True)
        except Exception:
            self._clear_blocks()

    @property
    def is_recording(self) -> bool:
        with self._lock:
            return self._stream is not None

    def discard_buffered_audio(self) -> None:
        """Drop audio captured during the short recording-start cue."""
        self._clear_blocks()

    def _callback(
        self,
        indata: np.ndarray,
        frames: int,
        time_info: Any,
        status: sd.CallbackFlags,
    ) -> None:
        del frames, time_info
        if status:
            self._callback_error = "audio_callback_status"
        try:
            self._blocks.put_nowait(indata.copy())
        except queue.Full:
            self._callback_error = "audio_buffer_full"

    def _clear_blocks(self) -> None:
        while True:
            try:
                self._blocks.get_nowait()
            except queue.Empty:
                return

    def _drain_blocks(self) -> list[np.ndarray]:
        blocks: list[np.ndarray] = []
        while True:
            try:
                blocks.append(self._blocks.get_nowait())
            except queue.Empty:
                return blocks


def validate_audio(
    audio: np.ndarray,
    *,
    sample_rate: int,
    min_seconds: float,
    silence_rms: float,
) -> AudioStats:
    samples = int(audio.size)
    if samples == 0:
        raise EmptyAudioError("empty_audio_buffer")
    seconds = samples / sample_rate
    if seconds < min_seconds:
        raise AudioTooShortError("utterance_too_short")
    rms = float(np.sqrt(np.mean(np.square(audio, dtype=np.float64))))
    if not np.isfinite(rms) or rms < silence_rms:
        raise SilentAudioError("utterance_is_silent")
    return AudioStats(seconds=seconds, rms=rms, samples=samples)
