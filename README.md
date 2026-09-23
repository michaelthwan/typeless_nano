# Typeless Nano

*English | [繁體中文](README.zh-TW.md)*

Offline English dictation for Windows. Press **Right Alt**, speak, press **Right Alt** again --
the text is typed into whatever window you were in. Speech recognition runs entirely on your
machine with Whisper or Qwen3-ASR; nothing is sent anywhere and nothing is saved to disk.

<p align="center">
  <img src="docs/images/recording.png" width="176" alt="Recording capsule with live waveform">
</p>

## Features

- **One key, toggle to record.** Right Alt starts and stops. Every other key -- including F1
  and Left Alt -- passes through untouched.
- **Types where you were.** The text goes to the window that was in front when you started.
  If you switched windows in the meantime, nothing is typed into the wrong place.
- **Never loses your words.** If the text cannot be typed, it waits in a small panel with a
  `Copy` button until you deal with it.
- **Live waveform.** The recording capsule shows your actual microphone level, scrolling
  right to left, so you can see at a glance that the mic is picking you up.
- **Fully offline.** Models load from a local folder only; the runtime makes no network calls.
- **Small footprint.** Four direct dependencies, one Python process, no installer.

## What you will see

| State | Looks like | What it means |
|---|---|---|
| Recording | <img src="docs/images/recording.png" width="176" alt="Recording capsule"> | Speak now. The bars follow your voice. `×` cancels, `✓` finishes. |
| Thinking | <img src="docs/images/thinking.png" width="146" alt="Thinking capsule"> | Transcribing. The text is typed as soon as it is done. |
| No speech | <img src="docs/images/no-speech.png" width="183" alt="No speech notice"> | The recording was silent or too short. Fades by itself. |
| Nowhere to type | <img src="docs/images/rescue.png" width="472" alt="Rescue panel with Copy button"> | The target window is gone or changed. `Copy` puts the text on the clipboard. |

None of these windows take focus, so the app you are typing into stays active. Short sound
cues mark the transitions: a rising two-note chime to start, the same pair falling to stop,
and a single low note to cancel.

## Quick start

Requires Windows and Python 3.12.

**1. Install**

```powershell
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

**2. Get a model** (the only step that touches the internet)

```powershell
hf download openai/whisper-small.en --local-dir models\whisper-small.en
```

No Hub access on that machine? Download the files by hand from
<https://huggingface.co/openai/whisper-small.en/tree/main> into `models\whisper-small.en`.
You need these; skip `.gitattributes` and the `.msgpack` / `.h5` / `.ot` weights:

```text
config.json  generation_config.json  merges.txt  model.safetensors
normalizer.json  preprocessor_config.json  special_tokens_map.json
tokenizer.json  tokenizer_config.json  vocab.json  added_tokens.json
```

**3. Run** (from the project folder -- model paths are relative to it)

```powershell
python app.py
```

Wait for `Typeless Nano is ready.`, click into any text box, and press Right Alt.

Using Conda instead:

```powershell
conda create -n typeless_nano python=3.12 pip -y
conda run -n typeless_nano python -m pip install -r requirements.txt
conda run --no-capture-output -n typeless_nano python app.py
```

Keep `--no-capture-output`; without it Conda buffers the console and the app looks frozen.

## Using it

1. Put the cursor where you want the text.
2. Press **Right Alt** and speak English.
3. Press **Right Alt** again, or click `✓`.
4. Stay in the same window until the text appears.

Click `×` to throw away a recording. Recordings stop automatically after 60 seconds and are
transcribed as usual. Press `Ctrl+C` in the console to quit.

## Options

| Option | Effect |
|---|---|
| `--model auto\|qwen\|whisper` | Which model to use. `auto` (default) prefers Qwen and falls back to Whisper if Qwen is missing or fails to load. |
| `--model-path PATH` | Load the chosen model from a different folder. |
| `--device auto\|cpu\|cuda` | Where to run the model. CUDA falls back to CPU on out-of-memory. |
| `--microphone NAME_OR_INDEX` | Pick a microphone. See `--list-microphones`. |
| `--list-microphones` | Print input devices and exit. |
| `--mute-cues` | Turn off the start/stop sounds. |
| `--no-clipboard-fallback` | If typing fails, do not copy the text to the clipboard. |
| `--new-paragraph` | Turn the spoken phrase "new paragraph" into a line break. |
| `--verify-checksums` | Check model files against pinned SHA-256 hashes before loading. |

## Models

The app never downloads models. Put approved checkpoints here:

```text
models/
  qwen3-asr-0.6b-hf/
    config.json
    model.safetensors
    ...
  whisper-small.en/
    config.json
    model.safetensors
    ...
```

Or point at any local folder:

```powershell
python app.py --model whisper --model-path D:\ApprovedModels\whisper-small.en
```

To pin checksums once and verify them on every start:

```powershell
python scripts/model_sha256.py D:\ApprovedModels\whisper-small.en --write
python app.py --model whisper --model-path D:\ApprovedModels\whisper-small.en --verify-checksums
```

## Privacy

- Runs with `HF_HUB_OFFLINE=1` and `TRANSFORMERS_OFFLINE=1`; every model load uses
  `local_files_only=True`.
- Audio lives in RAM only. Neither audio nor transcripts are ever written to disk.
- Logs hold state names, latency and error codes -- never what you said.
- The foreground window is re-checked after transcription; text is typed only if it is still
  the window you started in.

## Troubleshooting

| Symptom | Fix |
|---|---|
| Console stays blank under Conda | Add `--no-capture-output` to `conda run`. |
| `can't open file ... app.py` | Run from the project folder, not your home folder. |
| Waveform stays flat while you talk | Wrong or muted mic. Run `--list-microphones`, then pass `--microphone`. |
| Bars move while you are silent | Noisy room or high mic gain. Raise `meter_floor_db` in `dictation/config.py`. |
| Text never appears in one specific app | Elevated (admin) apps reject input from normal apps. Run both at the same privilege level. |
| First dictation is slow | The model is loading or warming up on CPU. Later ones are faster; a CUDA GPU helps most. |

## Development

Tests need Windows but no model:

```powershell
python -m unittest discover -s tests -v
python scripts/dependency_gate.py
```

`requirements.txt` lists only the four company-approved direct dependencies (`sounddevice`,
`pywin32`, `torch`, `transformers`); `dependency_gate.py` checks their exact versions and
reports the transitive ones. NumPy is used directly but arrives transitively.

End-to-end check with speech generated by the Windows SAPI voice. The temporary WAV is deleted
afterwards and only latency and word count are printed:

```powershell
python -m scripts.smoke_transcription --model whisper
```

Manual checks before relying on a new machine:

1. `dependency_gate.py` reports all four direct dependencies at the exact versions.
2. `--list-microphones` shows the expected device.
3. In Notepad, Right Alt starts and stops recording with the rising and falling cues.
4. A silent or sub-250 ms recording logs `utterance_rejected` and shows "No speech detected".
5. Switching windows right after stopping logs
   `injection_skipped reason=foreground_window_changed` and shows the rescue panel.
6. A recording past 60 seconds is stopped by the watchdog.
7. Dictate a few times each in Outlook, Teams, Chrome and VS Code.

## Platform

Windows is the supported platform. The hotkey (a Win32 low-level keyboard hook), text typing
(`SendInput`), the non-activating overlay and the sound cues (`winsound`) all use Windows APIs
directly.

### macOS (experimental)

The `experiment/macos` branch has a first macOS backend. It uses **Right Option** as the
hotkey, types with CoreGraphics key events and plays the cues through `sounddevice`; it adds no
dependency (macOS APIs are called through `ctypes`, `pywin32` is skipped). What is missing:

- No floating capsule yet -- the sound cues are the only recording/Thinking feedback.
- No rescue panel: text that cannot be typed is copied to the clipboard instead.
- No cancel on sleep or screen lock; the 60-second watchdog still applies.
- Password fields and Terminal's Secure Keyboard Entry block typed text; the text goes to the
  clipboard instead.
- Runs on CPU (Apple GPU support is not wired up).

Setup is the same as on Windows, plus two permissions. In **System Settings > Privacy &
Security**, allow the terminal app you launch from (Terminal, iTerm, VS Code) under both
**Accessibility** and **Input Monitoring**, and allow the **Microphone** when asked. Restart the
terminal after granting them. Without them the app exits with `keyboard_hook_start_failed`.

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python app.py
```
