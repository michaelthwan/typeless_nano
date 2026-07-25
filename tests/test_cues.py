from __future__ import annotations

import unittest
from unittest.mock import patch

from dictation.cues import SoundCuePlayer


class SoundCueTests(unittest.TestCase):
    @patch("dictation.cues.time.sleep")
    @patch("dictation.cues.winsound.Beep")
    def test_start_rises_and_stop_falls(self, beep, _sleep) -> None:
        cues = SoundCuePlayer()
        cues.play_start()
        start_frequencies = [call.args[0] for call in beep.call_args_list]
        beep.reset_mock()

        cues.play_stop()
        stop_frequencies = [call.args[0] for call in beep.call_args_list]

        self.assertLess(start_frequencies[0], start_frequencies[-1])
        self.assertGreater(stop_frequencies[0], stop_frequencies[-1])

    @patch("dictation.cues.winsound.Beep")
    def test_muted_cues_do_not_play(self, beep) -> None:
        cues = SoundCuePlayer(enabled=False)
        cues.play_start()
        cues.play_stop()
        self.assertFalse(beep.called)


if __name__ == "__main__":
    unittest.main()
