from __future__ import annotations

import logging
from pathlib import Path

import numpy as np
import torch
from transformers import AutoModelForSpeechSeq2Seq, AutoProcessor

from dictation.asr.base import LocalASR, iter_audio_chunks
from dictation.asr.device import DeviceSpec

LOGGER = logging.getLogger("dictation.asr.whisper")


class WhisperASR(LocalASR):
    name = "whisper"

    def __init__(
        self,
        model_path: Path,
        device: DeviceSpec,
        *,
        max_new_tokens: int,
    ) -> None:
        self.model_path = model_path
        self.device = device
        self.max_new_tokens = max_new_tokens
        self.processor = AutoProcessor.from_pretrained(
            model_path,
            local_files_only=True,
        )
        self.model = AutoModelForSpeechSeq2Seq.from_pretrained(
            model_path,
            local_files_only=True,
            dtype=device.dtype,
        ).to(device.name)
        # Transformers 5.x prefers the task flag over legacy forced decoder IDs.
        self.model.generation_config.forced_decoder_ids = None
        self.model.config.forced_decoder_ids = None
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
            input_features = inputs.input_features.to(
                self.device.name, dtype=self.device.dtype
            )
            with torch.inference_mode():
                predicted_ids = self.model.generate(
                    input_features,
                    do_sample=False,
                )
            decoded = self.processor.batch_decode(
                predicted_ids,
                skip_special_tokens=True,
                clean_up_tokenization_spaces=False,
            )[0]
            if decoded.strip():
                parts.append(decoded.strip())
        return " ".join(parts)

    def _move_to_cpu(self) -> None:
        from dictation.asr.device import DeviceSpec

        self.model.to(device="cpu", dtype=torch.float32)
        self.device = DeviceSpec("cpu", torch.float32)
        torch.cuda.empty_cache()
