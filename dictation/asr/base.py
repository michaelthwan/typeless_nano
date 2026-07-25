from __future__ import annotations

from abc import ABC, abstractmethod

import numpy as np


class LocalASR(ABC):
    name = "base"

    @abstractmethod
    def transcribe(self, audio: np.ndarray, sample_rate: int) -> str:
        raise NotImplementedError


def iter_audio_chunks(
    audio: np.ndarray, *, sample_rate: int, chunk_seconds: int = 30
):
    chunk_size = sample_rate * chunk_seconds
    for offset in range(0, audio.size, chunk_size):
        chunk = audio[offset : offset + chunk_size]
        if chunk.size:
            yield chunk

