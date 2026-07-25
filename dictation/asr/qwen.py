from __future__ import annotations

import logging
from pathlib import Path

import numpy as np
import torch
from transformers import AutoModelForMultimodalLM, AutoProcessor

from dictation.asr.base import LocalASR, iter_audio_chunks
from dictation.asr.device import DeviceSpec

LOGGER = logging.getLogger("dictation.asr.qwen")


class QwenASR(LocalASR):
    name = "qwen"

    def __init__(
        self,
        model_path: Path,
        device: DeviceSpec,
        *,
        language: str,
        prompt: str,
        max_new_tokens: int,
    ) -> None:
        self.model_path = model_path
        self.device = device
        self.language = language
        self.prompt = prompt
        self.max_new_tokens = max_new_tokens
        self.processor = AutoProcessor.from_pretrained(
            model_path,
            local_files_only=True,
        )
        self.model = AutoModelForMultimodalLM.from_pretrained(
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
            inputs = self.processor.apply_transcription_request(
                audio=chunk,
                language=self.language,
                prompt=self.prompt,
            ).to(self.device.name, self.device.dtype)
            with torch.inference_mode():
                output_ids = self.model.generate(
                    **inputs,
                    max_new_tokens=self.max_new_tokens,
                    do_sample=False,
                )
            generated_ids = output_ids[:, inputs["input_ids"].shape[1] :]
            decoded = self.processor.decode(
                generated_ids,
                return_format="transcription_only",
            )[0]
            if decoded.strip():
                parts.append(decoded.strip())
        return " ".join(parts)

    def _move_to_cpu(self) -> None:
        from dictation.asr.device import DeviceSpec

        self.model.to(device="cpu", dtype=torch.float32)
        self.device = DeviceSpec("cpu", torch.float32)
        torch.cuda.empty_cache()

