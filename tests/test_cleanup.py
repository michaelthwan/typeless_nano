from __future__ import annotations

import unittest

from dictation.cleanup import clean_transcript


class CleanupTests(unittest.TestCase):
    def test_strips_and_collapses_spaces(self) -> None:
        self.assertEqual(
            clean_transcript("  Keep   MLflow   and XGBoost.  "),
            "Keep MLflow and XGBoost.",
        )

    def test_removes_control_characters_but_keeps_newlines(self) -> None:
        self.assertEqual(clean_transcript("first\x00\nsecond\x07"), "first\nsecond")

    def test_optional_new_paragraph(self) -> None:
        self.assertEqual(
            clean_transcript(
                "First new paragraph Second", convert_new_paragraph=True
            ),
            "First\n\nSecond",
        )

    def test_does_not_change_negation_or_numbers(self) -> None:
        text = "Do not change 12.5% or false negatives."
        self.assertEqual(clean_transcript(text), text)


if __name__ == "__main__":
    unittest.main()

