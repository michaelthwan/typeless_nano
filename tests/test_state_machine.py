from __future__ import annotations

import unittest

from dictation.controller import ControllerState, StateMachine


class StateMachineTests(unittest.TestCase):
    def test_happy_path(self) -> None:
        machine = StateMachine()
        self.assertTrue(machine.start_recording())
        self.assertEqual(machine.state, ControllerState.RECORDING)
        self.assertTrue(machine.start_transcribing())
        self.assertEqual(machine.state, ControllerState.TRANSCRIBING)
        self.assertTrue(machine.finish_transcribing())
        self.assertEqual(machine.state, ControllerState.IDLE)

    def test_repeated_key_down_is_ignored(self) -> None:
        machine = StateMachine()
        self.assertTrue(machine.start_recording())
        self.assertFalse(machine.start_recording())
        self.assertEqual(machine.state, ControllerState.RECORDING)

    def test_cancel_only_applies_while_recording(self) -> None:
        machine = StateMachine()
        self.assertFalse(machine.cancel_recording())
        machine.start_recording()
        self.assertTrue(machine.cancel_recording())
        self.assertEqual(machine.state, ControllerState.IDLE)


if __name__ == "__main__":
    unittest.main()

