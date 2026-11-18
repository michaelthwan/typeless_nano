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

    def test_default_is_moonshine(self) -> None:
        selected = select_model(AppConfig())
        self.assertEqual(selected.backend, "moonshine")
        self.assertEqual(selected.path, Path("models/moonshine-streaming-small"))

    def test_moonshine_model_path_override(self) -> None:
        config = AppConfig(model="moonshine", model_path=Path("elsewhere"))
        selected = select_model(config)
        self.assertEqual(selected.backend, "moonshine")
        self.assertEqual(selected.path, Path("elsewhere"))

    def test_model_path_without_model_infers_whisper(self) -> None:
        # app.py maps "--model-path X" without "--model" to model="auto".
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "config.json").write_text(
                json.dumps({"model_type": "whisper"}), encoding="utf-8"
            )
            selected = select_model(AppConfig(model="auto", model_path=root))
            self.assertEqual(selected.backend, "whisper")
            self.assertEqual(selected.path, root)

    def test_infers_moonshine_from_config(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "config.json").write_text(
                json.dumps({"model_type": "moonshine_streaming"}),
                encoding="utf-8",
            )
            self.assertEqual(infer_backend_from_directory(root), "moonshine")

    def test_auto_prefers_qwen(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            qwen = root / "qwen"
            whisper = root / "whisper"
            qwen.mkdir()
            whisper.mkdir()
            selected = select_model(
                AppConfig(model="auto", qwen_path=qwen, whisper_path=whisper)
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

