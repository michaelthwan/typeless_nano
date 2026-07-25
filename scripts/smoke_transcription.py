from __future__ import annotations

import argparse
import os
import tempfile
import time
import wave
from pathlib import Path

os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")

import numpy as np
import win32com.client

from dictation.asr.factory import create_asr
from dictation.cleanup import clean_transcript
from dictation.config import AppConfig

SAPI_STREAM_CREATE_FOR_WRITE = 3
SAPI_16_KHZ_16_BIT_MONO = 18
SYNTHETIC_TEXT = (
    "We trained the XGBoost model using PySpark. "
    "The false positive rate decreased from twelve percent to eight percent."
)


def create_synthetic_wav(path: Path) -> None:
    voice = win32com.client.Dispatch("SAPI.SpVoice")
    stream = win32com.client.Dispatch("SAPI.SpFileStream")
    stream.Format.Type = SAPI_16_KHZ_16_BIT_MONO
    stream.Open(str(path), SAPI_STREAM_CREATE_FOR_WRITE, False)
    try:
        voice.AudioOutputStream = stream
        voice.Speak(SYNTHETIC_TEXT)
    finally:
        stream.Close()


def read_wav(path: Path, target_rate: int = 16_000) -> np.ndarray:
    with wave.open(str(path), "rb") as wav_file:
        channels = wav_file.getnchannels()
        sample_width = wav_file.getsampwidth()
        source_rate = wav_file.getframerate()
        frames = wav_file.readframes(wav_file.getnframes())
    if sample_width != 2:
        raise RuntimeError(f"unsupported_sample_width_{sample_width}")

    audio = np.frombuffer(frames, dtype="<i2").astype(np.float32) / 32768.0
    if channels > 1:
        audio = audio.reshape(-1, channels).mean(axis=1)
    if source_rate != target_rate:
        target_size = round(audio.size * target_rate / source_rate)
        source_positions = np.arange(audio.size, dtype=np.float64)
        target_positions = np.linspace(0, audio.size - 1, target_size)
        audio = np.interp(target_positions, source_positions, audio).astype(
            np.float32
        )
    return audio


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Offline end-to-end ASR smoke test using Windows SAPI audio."
    )
    parser.add_argument("--model", choices=("qwen", "whisper"), default="whisper")
    parser.add_argument("--model-path", type=Path)
    args = parser.parse_args()

    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as temp_file:
            temp_path = Path(temp_file.name)
        create_synthetic_wav(temp_path)
        audio = read_wav(temp_path)
        backend = create_asr(
            AppConfig(model=args.model, model_path=args.model_path)
        )
        started = time.perf_counter()
        transcript = clean_transcript(backend.transcribe(audio, 16_000))
        latency = time.perf_counter() - started
        if not transcript:
            print("smoke_test=failed reason=empty_transcript")
            return 1
        print(
            "smoke_test=passed "
            f"backend={backend.name} audio_seconds={audio.size / 16_000:.2f} "
            f"latency_seconds={latency:.2f} words={len(transcript.split())}"
        )
        return 0
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)


if __name__ == "__main__":
    raise SystemExit(main())

