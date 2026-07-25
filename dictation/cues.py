from __future__ import annotations

import time
import winsound
from collections.abc import Sequence

Tone = tuple[int, int]

START_TONES: tuple[Tone, ...] = ((660, 55), (880, 80))
STOP_TONES: tuple[Tone, ...] = ((880, 55), (520, 90))
CANCEL_TONES: tuple[Tone, ...] = ((360, 90),)


class SoundCuePlayer:
    """Short synchronous cues; never called from the keyboard hook thread."""

    def __init__(self, *, enabled: bool = True) -> None:
        self.enabled = enabled

    def play_start(self) -> None:
        self._play(START_TONES)

    def play_stop(self) -> None:
        self._play(STOP_TONES)

    def play_cancel(self) -> None:
        self._play(CANCEL_TONES)

    def _play(self, tones: Sequence[Tone]) -> None:
        if not self.enabled:
            return
        try:
            for index, (frequency, duration_ms) in enumerate(tones):
                winsound.Beep(frequency, duration_ms)
                if index < len(tones) - 1:
                    time.sleep(0.025)
        except (OSError, RuntimeError):
            # Beep can be unavailable on some remote/audio-less Windows
            # sessions. Keep dictation functional with a system-sound fallback.
            try:
                winsound.MessageBeep(winsound.MB_OK)
            except (OSError, RuntimeError):
                pass

