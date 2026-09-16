from __future__ import annotations

import unittest
import wave
from io import BytesIO
from unittest.mock import patch

from dictation.cues import (
    CANCEL_NOTES,
    JOIN_NOTES,
    LEAVE_NOTES,
    NOTE_GAP_SECONDS,
    NOTE_SECONDS,
    PEAK_AMPLITUDE,
    SAMPLE_RATE,
    SoundCuePlayer,
    _loudness_gain,
    render_cue_wav,
)


class SoundCueTests(unittest.TestCase):
    def test_start_rises_and_stop_falls(self) -> None:
        start_frequencies = [frequency for frequency, _ in JOIN_NOTES]
        stop_frequencies = [frequency for frequency, _ in LEAVE_NOTES]
        self.assertLess(start_frequencies[0], start_frequencies[-1])
        self.assertGreater(stop_frequencies[0], stop_frequencies[-1])

    @patch("dictation.cues.winsound.PlaySound")
    def test_muted_cues_do_not_play(self, play_sound) -> None:
        cues = SoundCuePlayer(enabled=False)
        cues.play_start()
        cues.play_stop()
        cues.play_cancel()
        self.assertFalse(play_sound.called)

    @patch("dictation.cues.winsound.PlaySound")
    def test_each_cue_plays_synchronously_from_memory(self, play_sound) -> None:
        import winsound

        cues = SoundCuePlayer()
        cues.play_start()
        cues.play_stop()
        cues.play_cancel()

        self.assertEqual(play_sound.call_count, 3)
        payloads = set()
        for call in play_sound.call_args_list:
            data, flags = call.args
            # SND_ASYNC would let the cue bleed into the recording.
            self.assertEqual(flags, winsound.SND_MEMORY)
            self.assertTrue(data.startswith(b"RIFF"))
            payloads.add(data)
        self.assertEqual(len(payloads), 3)

    @patch("dictation.cues.winsound.PlaySound")
    def test_rendered_wav_is_cached_per_cue(self, play_sound) -> None:
        cues = SoundCuePlayer()
        cues.play_start()
        cues.play_start()
        first, second = (call.args[0] for call in play_sound.call_args_list)
        self.assertIs(first, second)

    @patch("dictation.cues.winsound.PlaySound", side_effect=OSError)
    @patch("dictation.cues.winsound.MessageBeep")
    def test_falls_back_to_message_beep(self, message_beep, _play_sound) -> None:
        SoundCuePlayer().play_start()
        self.assertTrue(message_beep.called)

    def test_rendered_wav_is_mono_16_bit_and_short(self) -> None:
        for notes in (JOIN_NOTES, LEAVE_NOTES, CANCEL_NOTES):
            with wave.open(BytesIO(render_cue_wav(notes)), "rb") as handle:
                self.assertEqual(handle.getnchannels(), 1)
                self.assertEqual(handle.getsampwidth(), 2)
                self.assertEqual(handle.getframerate(), SAMPLE_RATE)
                seconds = handle.getnframes() / SAMPLE_RATE
                # Cue blocks the start of recording, so keep it brief.
                self.assertLessEqual(
                    seconds, NOTE_SECONDS + NOTE_GAP_SECONDS + 0.01
                )
                self.assertLess(seconds, 0.3)

    def test_cues_sit_in_the_vocal_register(self) -> None:
        # Above ~180 Hz so laptop speakers reproduce it, below ~500 Hz so it
        # reads as voice-adjacent rather than as an alarm.
        for notes in (JOIN_NOTES, LEAVE_NOTES, CANCEL_NOTES):
            for frequency, _ in notes:
                self.assertGreater(frequency, 180.0)
                self.assertLess(frequency, 500.0)

    def test_lower_notes_get_more_loudness_compensation(self) -> None:
        self.assertGreater(_loudness_gain(220.0), _loudness_gain(440.0))

    def test_amplitude_stays_below_full_scale(self) -> None:
        import struct

        data = render_cue_wav(JOIN_NOTES)
        with wave.open(BytesIO(data), "rb") as handle:
            frames = handle.readframes(handle.getnframes())
        samples = struct.unpack(f"<{len(frames) // 2}h", frames)
        self.assertLessEqual(
            max(abs(sample) for sample in samples), 32767 * (PEAK_AMPLITUDE + 0.01)
        )


if __name__ == "__main__":
    unittest.main()
