from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import torch

from dictation.asr.base import LocalASR
from dictation.asr.checksum import verify_sha256_manifest
from dictation.asr.device import DeviceSpec, resolve_device
from dictation.config import AppConfig

LOGGER = logging.getLogger("dictation.asr")


@dataclass(frozen=True, slots=True)
class ModelSelection:
    backend: str
    path: Path


def select_model(config: AppConfig) -> ModelSelection:
    if config.model_path is not None:
        backend = config.model
        if backend == "auto":
            backend = infer_backend_from_directory(config.model_path)
        return ModelSelection(backend, config.model_path)

    if config.model == "qwen":
        return ModelSelection("qwen", config.qwen_path)
    if config.model == "whisper":
        return ModelSelection("whisper", config.whisper_path)

    if config.qwen_path.is_dir():
        return ModelSelection("qwen", config.qwen_path)
    if config.whisper_path.is_dir():
        return ModelSelection("whisper", config.whisper_path)
    raise FileNotFoundError(
        "No local model found. Expected "
        f"{config.qwen_path} or {config.whisper_path}."
    )


def infer_backend_from_directory(path: Path) -> str:
    name = path.name.lower()
    config_path = path / "config.json"
    if config_path.is_file():
        text = config_path.read_text(encoding="utf-8", errors="ignore").lower()
        if "qwen3_asr" in text:
            return "qwen"
        if "whisper" in text:
            return "whisper"
    if "qwen" in name:
        return "qwen"
    if "whisper" in name:
        return "whisper"
    raise ValueError("Cannot infer backend from --model-path; specify --model.")


def create_asr(config: AppConfig) -> LocalASR:
    from transformers.utils import logging as transformers_logging

    transformers_logging.set_verbosity_error()
    transformers_logging.disable_progress_bar()
    selection = select_model(config)
    model_path = selection.path.resolve()
    if not model_path.is_dir():
        raise FileNotFoundError(f"Local model directory not found: {model_path}")
    if config.verify_checksums:
        verify_sha256_manifest(model_path)

    device = resolve_device(config.device)
    LOGGER.info(
        "loading_model backend=%s device=%s", selection.backend, device.name
    )
    try:
        backend = _load_with_device_fallback(
            selection.backend, model_path, device, config
        )
    except Exception as exc:
        whisper_path = config.whisper_path.resolve()
        can_fallback = (
            config.model == "auto"
            and config.model_path is None
            and selection.backend == "qwen"
            and whisper_path.is_dir()
        )
        if not can_fallback:
            raise
        LOGGER.warning(
            "qwen_initialization_failed error=%s fallback=whisper",
            type(exc).__name__,
        )
        if config.verify_checksums:
            verify_sha256_manifest(whisper_path)
        backend = _load_with_device_fallback(
            "whisper", whisper_path, device, config
        )
    LOGGER.info("model_loaded backend=%s device=%s", backend.name, backend.device.name)
    return backend


def _load_with_device_fallback(
    backend: str,
    model_path: Path,
    device: DeviceSpec,
    config: AppConfig,
) -> LocalASR:
    try:
        return _load_backend(backend, model_path, device, config)
    except torch.cuda.OutOfMemoryError:
        if device.name != "cuda":
            raise
        LOGGER.warning("model_load_cuda_oom fallback=cpu")
        torch.cuda.empty_cache()
        return _load_backend(
            backend,
            model_path,
            DeviceSpec("cpu", torch.float32),
            config,
        )


def _load_backend(
    backend: str,
    model_path: Path,
    device: DeviceSpec,
    config: AppConfig,
) -> LocalASR:
    if backend == "qwen":
        from dictation.asr.qwen import QwenASR

        return QwenASR(
            model_path,
            device,
            language=config.language,
            prompt=config.vocabulary_prompt,
            max_new_tokens=config.max_new_tokens,
        )
    if backend == "whisper":
        from dictation.asr.whisper import WhisperASR

        return WhisperASR(
            model_path,
            device,
            max_new_tokens=config.max_new_tokens,
        )
    raise ValueError(f"Unsupported ASR backend: {backend}")
