from __future__ import annotations

import math
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
        # Latest block loudness in 0..1, written by the audio callback and read
        # by the overlay thread; a single float store is atomic under the GIL.
        self._level = 0.0

    @staticmethod
    def list_devices() -> Any:
        return sd.query_devices()

    def start(self) -> None:
        with self._lock:
            if self._stream is not None:
                raise AudioError("recording_already_active")
            self._clear_blocks()
            self._callback_error = None
            self._level = 0.0
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
        self._level = 0.0

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

    @property
    def level(self) -> float:
        """Loudness of the most recent microphone block, 0 (silent) to 1."""
        return self._level

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
        self._level = meter_level(
            block_rms(indata),
            floor_db=self.config.meter_floor_db,
            ceiling_db=self.config.meter_ceiling_db,
        )
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


def block_rms(block: np.ndarray) -> float:
    if block.size == 0:
        return 0.0
    rms = float(np.sqrt(np.mean(np.square(block, dtype=np.float64))))
    return rms if np.isfinite(rms) else 0.0


def meter_level(rms: float, *, floor_db: float, ceiling_db: float) -> float:
    """Map an RMS amplitude onto 0..1 on a dBFS scale, clamped at both ends."""
    if rms <= 0.0:
        return 0.0
    db = 20.0 * math.log10(rms)
    return min(1.0, max(0.0, (db - floor_db) / (ceiling_db - floor_db)))


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
    rms = block_rms(audio)
    if rms < silence_rms:
        raise SilentAudioError("utterance_is_silent")
    return AudioStats(seconds=seconds, rms=rms, samples=samples)
