from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from dictation.asr.factory import infer_backend_from_directory, select_model
from dictation.config import AppConfig


class ModelSelectionTests(unittest.TestCase):
    def test_explicit_whisper(self) -> None:
        config = AppConfig(model="whisper", whisper_path=Path("chosen-whisper"))
        selected = select_model(config)
        self.assertEqual(selected.backend, "whisper")
        self.assertEqual(selected.path, Path("chosen-whisper"))

    def test_auto_prefers_qwen(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            qwen = root / "qwen"
            whisper = root / "whisper"
            qwen.mkdir()
            whisper.mkdir()
            selected = select_model(
                AppConfig(qwen_path=qwen, whisper_path=whisper)
            )
            self.assertEqual(selected.backend, "qwen")

    def test_infers_backend_from_config(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "config.json").write_text(
                json.dumps({"model_type": "whisper"}), encoding="utf-8"
            )
            self.assertEqual(infer_backend_from_directory(root), "whisper")


if __name__ == "__main__":
    unittest.main()

