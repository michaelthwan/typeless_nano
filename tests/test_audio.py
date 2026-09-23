from __future__ import annotations

import unittest

import numpy as np

from dictation.audio import (
    AudioRecorder,
    AudioTooShortError,
    SilentAudioError,
    block_rms,
    meter_level,
    validate_audio,
)
from dictation.config import AppConfig
from dictation.overlay import LevelHistory


class AudioValidationTests(unittest.TestCase):
    def test_accepts_audible_audio(self) -> None:
        audio = np.full(8_000, 0.02, dtype=np.float32)
        stats = validate_audio(
            audio, sample_rate=16_000, min_seconds=0.25, silence_rms=0.001
        )
        self.assertEqual(stats.seconds, 0.5)

    def test_rejects_short_audio(self) -> None:
        with self.assertRaises(AudioTooShortError):
            validate_audio(
                np.ones(100, dtype=np.float32),
                sample_rate=16_000,
                min_seconds=0.25,
                silence_rms=0.001,
            )

    def test_rejects_silence(self) -> None:
        with self.assertRaises(SilentAudioError):
            validate_audio(
                np.zeros(8_000, dtype=np.float32),
                sample_rate=16_000,
                min_seconds=0.25,
                silence_rms=0.001,
            )


class MeterLevelTests(unittest.TestCase):
    def _level(self, rms: float) -> float:
        return meter_level(rms, floor_db=-55.0, ceiling_db=-20.0)

    def test_silence_and_below_floor_are_zero(self) -> None:
        self.assertEqual(self._level(0.0), 0.0)
        self.assertEqual(self._level(0.0001), 0.0)

    def test_loud_input_clamps_to_one(self) -> None:
        self.assertEqual(self._level(0.5), 1.0)

    def test_level_rises_with_loudness(self) -> None:
        quiet = self._level(0.003)
        speech = self._level(0.03)
        self.assertGreater(quiet, 0.0)
        self.assertGreater(speech, quiet)
        self.assertLess(speech, 1.0)

    def test_block_rms_handles_empty_and_non_finite(self) -> None:
        self.assertEqual(block_rms(np.zeros(0, dtype=np.float32)), 0.0)
        self.assertEqual(block_rms(np.array([np.nan], dtype=np.float32)), 0.0)
        self.assertAlmostEqual(block_rms(np.full(10, 0.1, dtype=np.float32)), 0.1, 6)


class RecorderLevelTests(unittest.TestCase):
    def test_callback_publishes_level_and_stop_resets_it(self) -> None:
        recorder = AudioRecorder(AppConfig())
        self.assertEqual(recorder.level, 0.0)
        recorder._callback(np.full((1_600, 1), 0.05, dtype=np.float32), 1_600, None, 0)
        self.assertGreater(recorder.level, 0.5)
        recorder.stop(discard=True)
        self.assertEqual(recorder.level, 0.0)


class LevelHistoryTests(unittest.TestCase):
    def test_starts_silent(self) -> None:
        self.assertEqual(LevelHistory(4).values(), (0.0, 0.0, 0.0, 0.0))

    def test_new_samples_enter_right_and_shift_left(self) -> None:
        history = LevelHistory(4)
        history.push(0.5)
        self.assertEqual(history.values(), (0.0, 0.0, 0.0, 0.5))
        history.push(0.9)
        self.assertEqual(history.values(), (0.0, 0.0, 0.5, 0.9))
        for level in (0.1, 0.2, 0.3):
            history.push(level)
        self.assertEqual(history.values(), (0.9, 0.1, 0.2, 0.3))

    def test_clamps_and_clears(self) -> None:
        history = LevelHistory(3)
        history.push(7.0)
        history.push(-1.0)
        self.assertEqual(history.values(), (0.0, 1.0, 0.0))
        history.clear()
        self.assertEqual(history.values(), (0.0, 0.0, 0.0))


if __name__ == "__main__":
    unittest.main()

