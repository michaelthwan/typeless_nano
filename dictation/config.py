from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


DEFAULT_VOCABULARY_PROMPT = (
    "Vocabulary: Databricks, PySpark, XGBoost, SHAP, MLflow, "
    "precision-recall curve, false positive rate."
)


@dataclass(frozen=True, slots=True)
class AppConfig:
    model: str = "auto"
    model_path: Path | None = None
    qwen_path: Path = Path("models/qwen3-asr-0.6b-hf")
    whisper_path: Path = Path("models/whisper-small.en")
    device: str = "auto"
    microphone: str | int | None = None
    sound_cues: bool = True
    clipboard_fallback: bool = True
    convert_new_paragraph: bool = False
    verify_checksums: bool = False
    sample_rate: int = 16_000
    channels: int = 1
    max_seconds: float = 60.0
    min_seconds: float = 0.25
    silence_rms: float = 0.001
    block_size: int = 1_600
    # Waveform meter: block RMS in dBFS mapped linearly onto bar height 0..1.
    # Floor sits just above typical room noise (about -47 dBFS measured).
    meter_floor_db: float = -45.0
    meter_ceiling_db: float = -20.0
    language: str = "English"
    vocabulary_prompt: str = DEFAULT_VOCABULARY_PROMPT
    max_new_tokens: int = 256
