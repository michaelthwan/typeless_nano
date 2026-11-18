from __future__ import annotations

import logging
from pathlib import Path

import numpy as np
import torch
from transformers import AutoProcessor, MoonshineStreamingForConditionalGeneration

from dictation.asr.base import LocalASR, iter_audio_chunks
from dictation.asr.device import DeviceSpec

LOGGER = logging.getLogger("dictation.asr.moonshine")

# Model-card cap on output tokens per second of audio; stops repetition loops.
TOKENS_PER_SECOND = 6.5


class MoonshineASR(LocalASR):
    name = "moonshine"

    def __init__(self, model_path: Path, device: DeviceSpec) -> None:
        self.model_path = model_path
        self.device = device
        self.processor = AutoProcessor.from_pretrained(
            model_path,
            local_files_only=True,
        )
        self.model = MoonshineStreamingForConditionalGeneration.from_pretrained(
            model_path,
            local_files_only=True,
            dtype=device.dtype,
        ).to(device.name)
        self.model.eval()

    def transcribe(self, audio: np.ndarray, sample_rate: int) -> str:
        try:
            return self._transcribe(audio, sample_rate)
        except torch.cuda.OutOfMemoryError:
            if self.device.name != "cuda":
                raise
            LOGGER.warning("cuda_out_of_memory fallback=cpu")
            self._move_to_cpu()
            return self._transcribe(audio, sample_rate)

    def _transcribe(self, audio: np.ndarray, sample_rate: int) -> str:
        parts: list[str] = []
        for chunk in iter_audio_chunks(audio, sample_rate=sample_rate):
            inputs = self.processor(
                chunk,
                sampling_rate=sample_rate,
                return_tensors="pt",
            )
            # The attention mask must stay integer; only the waveform is cast.
            inputs = {
                key: value.to(
                    self.device.name,
                    dtype=self.device.dtype if value.is_floating_point() else None,
                )
                for key, value in inputs.items()
            }
            max_length = int(
                inputs["attention_mask"].sum(dim=-1).max().item()
                * TOKENS_PER_SECOND
                / sample_rate
            )
            with torch.inference_mode():
                generated_ids = self.model.generate(
                    **inputs,
                    max_length=max(max_length, 10),
                    do_sample=False,
                )
            decoded = self.processor.decode(
                generated_ids[0], skip_special_tokens=True
            ).strip()
            if decoded:
                parts.append(decoded)
        return " ".join(parts)

    def _move_to_cpu(self) -> None:
        self.model.to(device="cpu", dtype=torch.float32)
        self.device = DeviceSpec("cpu", torch.float32)
        torch.cuda.empty_cache()
