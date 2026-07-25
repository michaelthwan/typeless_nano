from __future__ import annotations

from dataclasses import dataclass

import torch


@dataclass(frozen=True, slots=True)
class DeviceSpec:
    name: str
    dtype: torch.dtype


def resolve_device(requested: str) -> DeviceSpec:
    if requested == "cuda":
        if not torch.cuda.is_available():
            raise RuntimeError("cuda_requested_but_unavailable")
        return DeviceSpec("cuda", torch.float16)
    if requested == "cpu":
        return DeviceSpec("cpu", torch.float32)
    if torch.cuda.is_available():
        return DeviceSpec("cuda", torch.float16)
    return DeviceSpec("cpu", torch.float32)

