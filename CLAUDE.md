# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

Typeless Nano: a Windows-only, fully offline English dictation tool. Press Right Alt once to
start recording, press again to stop; local Whisper or Qwen3-ASR transcribes and the text is
typed into whatever window was in the foreground when recording started.

`PRD.md` is the authoritative spec; `README.md` is the user-facing guide.
Both encode hard constraints -- read the relevant section before changing behavior.

`README.md` (English) and `README.zh-TW.md` (Traditional Chinese) are translations of each
other and must be updated together. Their headings and code blocks are kept identical, so a
structural drift check is `diff <(grep '^#' README.md) <(grep '^#' README.zh-TW.md)` --
section titles are themselves translated, so expect prose headings to differ while the code
blocks stay identical.

## Hard constraints

- **Only four direct dependencies are allowed**: `sounddevice==0.5.5`, `pywin32==312`,
  `torch==2.9.0`, `transformers==5.14.1` (company allowlist, PRD section 3). NumPy is used
  directly but arrives transitively and is deliberately not listed. Do not add packages --
  `pynput`, `faster-whisper`, `pystray`, `Pillow`, `PyInstaller` and similar are explicitly
  banned. `scripts/dependency_gate.py` enforces the exact versions.
- **Only Right Alt may be intercepted.** F1, Left Alt and every other key must pass through;
  the user runs other tools bound to F1.
- **Fully offline.** `app.py` sets `HF_HUB_OFFLINE` / `TRANSFORMERS_OFFLINE` before any
  Transformers import (import order in `app.py` matters). All model loads pass
  `local_files_only=True`. Never add a network call or model download.
- **No persistence of user speech.** Audio stays in RAM; logs carry state names, latency and
  error codes only -- never transcript text. Keep new logging in that shape.

## Commands

Environment is a conda env named `typeless_nano` (Python 3.12 exactly):

```powershell
conda run -n typeless_nano python -m pip install -r requirements.txt
conda run -n typeless_nano python scripts/dependency_gate.py
```

Tests (no model checkpoint required, but they import `win32con` so they need Windows):

```powershell
conda run -n typeless_nano python -m unittest discover -s tests -v
```

Single test module / single test:

```powershell
conda run -n typeless_nano python -m unittest tests.test_controller -v
conda run -n typeless_nano python -m unittest tests.test_controller.ControllerIntegrationTests.test_record_transcribe_cleanup_and_inject -v
```

Run the app (`--no-capture-output` is required, otherwise `conda run` buffers the console and
the app looks dead):

```powershell
conda run --no-capture-output -n typeless_nano python app.py
```

End-to-end ASR smoke test using Windows SAPI-generated speech (needs a local Whisper model):

```powershell
conda run -n typeless_nano python -m scripts.smoke_transcription --model whisper
```

Models live under `models/` (gitignored) and are never downloaded by the app.

## Architecture

Four threads coordinate through a single `queue.Queue[AppEvent]` created in `app.py` and passed
to every component. `AppEvent` / `EventKind` (`dictation/events.py`) is the only cross-thread
message type.

- **Main thread** runs `DictationController.run()` (`dictation/controller.py`): drains the
  queue, drives a four-state `StateMachine` (IDLE / RECORDING / TRANSCRIBING / STOPPED) and
  polls a 60 s recording watchdog on each 100 ms queue timeout.
- **`keyboard-hook` thread** (`dictation/hotkey.py`) owns a hidden top-level window plus a
  `WH_KEYBOARD_LL` hook, and pumps Win32 messages. It only ever puts events on the queue.
- **Overlay thread** (`dictation/overlay.py`) hosts a Tk capsule on its own UI thread, driven by
  its own command queue; its cancel/confirm buttons emit `AppEvent`s back to the controller.
- **`asr-worker` executor** (single worker, owned by the controller) runs transcribe + inject
  off the event loop and posts `ASR_FINISHED` when done.

Flow: hook emits `START_RECORDING` carrying the foreground HWND -> controller starts
`AudioRecorder`, shows the overlay, plays the start cue, then discards blocks captured during
that cue -> `STOP_RECORDING` validates the utterance (`validate_audio`: min 0.25 s, RMS floor)
and hands the buffer to the worker -> worker transcribes, runs `clean_transcript`, and
`TextInjector` re-checks that the original HWND is still foreground before `SendInput`.

### Notes on specific modules

- `dictation/hotkey.py`: PyWin32 312 does not wrap `SetWindowsHookExW`, `CallNextHookEx` or
  `UnhookWindowsHookEx`, so those three are called through `ctypes`. Everything else (message
  loop, foreground window, session notifications) uses PyWin32. The `HOOK_CALLBACK` reference is
  held on the instance deliberately -- letting it be collected crashes the hook. The window must
  be a real top-level window, not `HWND_MESSAGE`, to receive power-broadcast messages. Injected
  keystrokes (`LLKHF_INJECTED`) are ignored so the app cannot trigger itself.
- `dictation/inject.py`: builds `INPUT` structs by hand for Unicode `SendInput`; on failure it
  falls back to the clipboard unless `--no-clipboard-fallback`. Injection into a
  higher-integrity process will fail -- that is expected, not a bug to work around by elevating.
- `dictation/asr/factory.py`: `--model auto` prefers Qwen, falls back to Whisper if the Qwen
  directory is missing *or* Qwen initialization raises. Both backends also fall back CUDA -> CPU
  on `torch.cuda.OutOfMemoryError`, at load time and mid-transcription.
- `dictation/overlay.py` hosts two windows on one Tk thread: the recording capsule (the Tk
  root) and the rescue panel (a `Toplevel`). Both are non-activating and are driven by the same
  command queue. They are independent -- `hide()` hides only the capsule, which is what lets the
  panel outlive the `ASR_FINISHED` that ends a dictation. Every Tk reference must be dropped on
  the UI thread in `_ui_thread`'s `finally`, or Tcl aborts at exit with "async handler deleted
  by the wrong thread".
- `dictation/config.py`: `AppConfig` is a frozen dataclass and the single source of tunables
  (sample rate, thresholds, watchdog, vocabulary prompt). Add settings there rather than
  threading extra arguments through components.
- `dictation/cues.py` synthesises its two-note cues as in-memory WAVs and plays them with
  stdlib `winsound.PlaySound(..., SND_MEMORY)` -- audio cues must not add a dependency, and no
  third-party sound file is shipped. Playback must stay synchronous (no `SND_ASYNC`): the
  controller discards the mic blocks captured during the start cue, so an async cue would leak
  into the recording. Cues must never be played from the keyboard hook thread.

### Testing style

Tests are stdlib `unittest` with hand-written fakes (see `FakeRecorder`, fake injector/overlay
in `tests/test_controller.py`) rather than a mocking library. Controller, state machine, audio
validation, cleanup, cues, hotkey toggle and model selection are all testable without hardware
or a model; keep new logic in that shape and add a fake instead of a new test dependency.

## Conventions

- `from __future__ import annotations` at the top of every module; full type hints; frozen
  dataclasses with `slots=True` for value types.
- Log lines are `snake_case` key=value events (`state=recording source=right_alt`), no prose and
  no transcript content.
- No emoji or ASCII art in code or output.
- `.kilo/worktrees/` holds a Kilo-managed worktree copy of the tree; ignore it when searching.
