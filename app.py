from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

# These must be set before Transformers or huggingface_hub is imported.
os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
os.environ.setdefault("DO_NOT_TRACK", "1")

from dictation.asr.factory import create_asr
from dictation.audio import AudioRecorder
from dictation.config import AppConfig
from dictation.controller import DictationController
from dictation.cues import SoundCuePlayer
from dictation.hotkey import WindowsHotkeyHook
from dictation.inject import TextInjector
from dictation.overlay import RecordingOverlay


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Offline Windows toggle-to-record English dictation."
    )
    parser.add_argument(
        "--model",
        choices=("auto", "qwen", "whisper"),
        default="auto",
        help="ASR backend. auto prefers Qwen when both local models exist.",
    )
    parser.add_argument(
        "--model-path",
        type=Path,
        help="Override the local model directory for the selected backend.",
    )
    parser.add_argument(
        "--qwen-path",
        type=Path,
        default=Path("models/qwen3-asr-0.6b-hf"),
    )
    parser.add_argument(
        "--whisper-path",
        type=Path,
        default=Path("models/whisper-small.en"),
    )
    parser.add_argument(
        "--device",
        choices=("auto", "cpu", "cuda"),
        default="auto",
    )
    parser.add_argument(
        "--microphone",
        help="sounddevice input device name or numeric device index.",
    )
    parser.add_argument(
        "--no-clipboard-fallback",
        action="store_true",
        help="Do not copy text to the clipboard if SendInput fails.",
    )
    parser.add_argument(
        "--mute-cues",
        action="store_true",
        help="Disable recording start, stop, and cancel sounds.",
    )
    parser.add_argument(
        "--new-paragraph",
        action="store_true",
        help='Convert the spoken phrase "new paragraph" to a blank line.',
    )
    parser.add_argument(
        "--verify-checksums",
        action="store_true",
        help="Require and verify SHA256SUMS in the chosen model directory.",
    )
    parser.add_argument(
        "--list-microphones",
        action="store_true",
        help="List audio devices and exit without loading an ASR model.",
    )
    return parser


def _parse_microphone(value: str | None) -> str | int | None:
    if value is None:
        return None
    try:
        return int(value)
    except ValueError:
        return value


def configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        datefmt="%H:%M:%S",
    )


def main(argv: list[str] | None = None) -> int:
    if sys.platform != "win32":
        print("This application requires Windows.", file=sys.stderr)
        return 2

    configure_logging()
    args = build_parser().parse_args(argv)

    if args.list_microphones:
        print(AudioRecorder.list_devices())
        return 0

    config = AppConfig(
        model=args.model,
        model_path=args.model_path,
        qwen_path=args.qwen_path,
        whisper_path=args.whisper_path,
        device=args.device,
        microphone=_parse_microphone(args.microphone),
        sound_cues=not args.mute_cues,
        clipboard_fallback=not args.no_clipboard_fallback,
        convert_new_paragraph=args.new_paragraph,
        verify_checksums=args.verify_checksums,
    )

    event_queue = DictationController.create_event_queue()
    try:
        asr = create_asr(config)
    except Exception as exc:
        logging.getLogger("dictation").error(
            "model_initialization_failed error=%s", type(exc).__name__
        )
        print(
            f"Could not load a local ASR model: {exc}\n"
            "See README.md for the expected model directory layout.",
            file=sys.stderr,
        )
        return 1

    recorder = AudioRecorder(config)
    injector = TextInjector(clipboard_fallback=config.clipboard_fallback)
    cues = SoundCuePlayer(enabled=config.sound_cues)
    hotkey = WindowsHotkeyHook(event_queue)
    overlay = RecordingOverlay(event_queue, level_source=lambda: recorder.level)
    controller = DictationController(
        config=config,
        events=event_queue,
        recorder=recorder,
        asr=asr,
        injector=injector,
        hotkey=hotkey,
        overlay=overlay,
        cues=cues,
    )

    print("Typeless Nano is ready.", flush=True)
    print("Press Right Alt once to record; press it again to transcribe.", flush=True)
    print("Press Ctrl+C to stop.", flush=True)

    try:
        overlay.start()
        hotkey.start()
        controller.run()
    except KeyboardInterrupt:
        logging.getLogger("dictation").info("shutdown_requested")
    finally:
        controller.close()
        hotkey.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
