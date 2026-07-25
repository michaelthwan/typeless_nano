from __future__ import annotations

import unittest

import numpy as np

from dictation.audio import AudioTooShortError, SilentAudioError, validate_audio


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


if __name__ == "__main__":
    unittest.main()

